# -*- coding: utf-8 -*-
# =====================
#
# Author: liumin.423
# Date:   2025/7/9
# =====================
"""多搜索引擎实现与混合检索（MixSearch）。

引擎：DDG / Bing / Jina / Sogou / Serper / Exa；
MixSearch 并发调用并去重，供 DeepSearch 使用。
"""

import asyncio
import base64
import html
import json
import os
import threading
import urllib.request
from datetime import datetime, timezone
from loguru import logger
from abc import ABC, abstractmethod
from typing import Any, List
from urllib.parse import parse_qs, parse_qsl, quote, unquote, urlencode, urlparse, urlunparse
import aiohttp
from bs4 import BeautifulSoup

try:
    from ddgs import DDGS
except ImportError:
    DDGS = None

from ai4s_tool.model.document import Doc
from ai4s_tool.util.log_util import timer


def _search_url_ok(url) -> bool:
    """校验 URL 是否可被 aiohttp 请求（避免 InvalidUrlClientError）。"""
    if not url or not str(url).strip():
        return False
    s = str(url).strip()
    return s.startswith(("http://", "https://"))


def _configured_proxy() -> str | None:
    """读取 DeepSearch/WebFetch 共用的网页出站代理。"""
    proxy = os.getenv("AI4S_WEB_FETCH_PROXY", "").strip()
    return proxy or None


def _proxy_required() -> bool:
    """显式强制代理时禁止直连回退；默认代理仅作为可选传输路径。"""
    return os.getenv("AI4S_WEB_FETCH_PROXY_REQUIRED", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }


def _request_kwargs(**kwargs):
    """为 aiohttp 请求补充显式代理，避免依赖进程级代理环境变量。"""
    proxy = _configured_proxy()
    if proxy:
        kwargs["proxy"] = proxy
    return kwargs


async def _bounded_search_call(function, seconds):
    """Keep third-party synchronous search retries out of asyncio shutdown.

    asyncio.to_thread is joined by asyncio.run even after cancellation. A
    short-lived daemon plus bounded socket timeouts lets the caller's total
    budget remain authoritative without leaving a blocking executor behind.
    """
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    def deliver(value, error):
        if not future.done():
            if error is not None: future.set_exception(error)
            else: future.set_result(value)
    def run():
        try: value, error = function(), None
        except Exception as exc: value, error = None, exc
        try: loop.call_soon_threadsafe(deliver, value, error)
        except RuntimeError: pass  # The bounded caller's loop already closed.
    threading.Thread(target=run, name='bounded-public-search', daemon=True).start()
    return await asyncio.wait_for(future, timeout=seconds)


class SearchBase(ABC):
    """搜索引擎基类：统一 count/timeout，子类实现 search。"""

    def __init__(self):
        self._count = int(os.getenv("SEARCH_COUNT", 10))
        self._timeout = min(60, max(3, int(os.getenv("SEARCH_TIMEOUT", 30))))
        # 单 URL 抓取超时（秒），过大会导致墙内访问 Reddit/Threads/X 等长时间挂起后卡死
        self._parser_timeout = min(30, max(3, int(os.getenv("SEARCH_PARSER_TIMEOUT", 15))))
        self._use_jd_gateway = os.getenv("USE_JD_SEARCH_GATEWAY", "true") == "true"

    @abstractmethod
    async def search(
        self, query: str, request_id: str = None, *args, **kwargs
    ) -> List[Doc]:
        """抽象搜索方法：返回 Doc 列表。"""
        raise NotImplementedError

    @staticmethod
    async def _fetch_content_with_jina_reader(source_url: str, timeout: int) -> str:
        """优先通过 Jina Reader 抓取清洗后的正文。"""
        if not _search_url_ok(source_url):
            return ""
        headers = {
            "Content-Type": "application/json",
            "X-Return-Format": "text",
            "X-Timeout": str(timeout),
        }
        jina_api_key = (os.getenv("JINA_API_KEY") or "").strip()
        if jina_api_key:
            headers["Authorization"] = f"Bearer {jina_api_key}"
        client_timeout = aiohttp.ClientTimeout(connect=5, total=timeout)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://r.jina.ai/",
                    **_request_kwargs(
                        json={"url": source_url},
                        headers=headers,
                        timeout=client_timeout,
                    ),
                ) as response:
                    if response.status != 200:
                        logger.debug(f"jina reader skipped: status={response.status}")
                        return ""
                    content = (await response.text()).strip()
                    return content
        except Exception as e:
            logger.debug(f"jina reader error: {e}")
            return ""

    @staticmethod
    async def _fetch_content_with_direct_http(source_url: str, timeout: int) -> str:
        """Jina Reader 不可用时，回退到直接抓取原始页面。"""
        if not _search_url_ok(source_url):
            return ""
        client_timeout = aiohttp.ClientTimeout(connect=5, total=timeout)
        request_kwargs = _request_kwargs(timeout=client_timeout)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(source_url, **request_kwargs) as response:
                    content_type = (response.content_type or "").lower()
                    if content_type not in [
                        "text/html", "text/plain", "text/markdown", "text/xml", "application/json",
                        "application/xml", "application/octet-stream",
                    ]:
                        logger.debug(f"parser content-type not supported: {response.content_type}")
                        return ""
                    raw_bytes = await response.read()
        except Exception as e:
            # 本地代理经常只在开发机存在；代理失败后立即直连，避免命中结果被整体丢弃。
            if "proxy" in request_kwargs and not _proxy_required():
                logger.warning(f"parser proxy unavailable, retry direct: {type(e).__name__}")
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(source_url, timeout=client_timeout) as response:
                            content_type = (response.content_type or "").lower()
                            if content_type not in [
                                "text/html", "text/plain", "text/markdown", "text/xml", "application/json",
                                "application/xml", "application/octet-stream",
                            ]:
                                return ""
                            raw_bytes = await response.read()
                except Exception as direct_error:
                    logger.debug(f"parser direct error: {type(direct_error).__name__}")
                    return ""
            else:
                logger.debug(f"parser error: {type(e).__name__}")
                return ""

        try:
            raw_text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raw_text = raw_bytes.decode("gb2312", errors="ignore")

        soup = BeautifulSoup(raw_text, "html.parser")
        return soup.get_text(" ", strip=True)

    @staticmethod
    @timer()
    async def parser(docs: List[Doc], timeout: int = 15, **kwargs) -> List[Doc]:
        use_jina_reader = kwargs.get("use_jina_reader", True)

        async def _resolve_content(doc: Doc) -> tuple[str, str]:
            # Jina 负责清洗正文但属于外部依赖；空结果或异常时必须回退直连，不能丢弃搜索命中。
            # 深度搜索默认改为直连抓取，只有显式开启时才尝试 Jina Reader。
            if use_jina_reader:
                jina_timeout = int(os.getenv("JINA_READER_TIMEOUT", timeout))
                jina_content = await SearchBase._fetch_content_with_jina_reader(
                    doc.link, jina_timeout
                )
                if jina_content and jina_content.strip():
                    return jina_content.strip(), "jina_reader"
            direct_content = await SearchBase._fetch_content_with_direct_http(
                doc.link, timeout
            )
            return (direct_content.strip(), "direct_http") if direct_content else ("", "search_snippet")

        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(_resolve_content(doc)) for doc in docs]

        for doc, task in zip(docs, tasks):
            result, method = task.result()
            doc.data = dict(doc.data or {})
            if result:
                doc.content = result
                doc.data["content_scope"] = "page_text"
                doc.data["fetch_method"] = method
                doc.data["fetched_at"] = datetime.now(timezone.utc).isoformat()
            else:
                doc.data.setdefault("content_scope", "search_snippet")
            doc.data["content_chars"] = len(doc.content or "")
        return docs

    @timer()
    async def search_and_dedup(
        self, query: str, request_id: str = None, *args, **kwargs
    ) -> List[Doc]:
        """
        搜索并去重，同时删除没有内容的文档
        """
        try:
            docs = await self.search(
                query=query, request_id=request_id, *args, **kwargs
            )
        except aiohttp.InvalidURL as e:
            logger.warning(f"Search skipped (invalid URL): {e}")
            return []
        except aiohttp.client_exceptions.InvalidUrlClientError as e:
            logger.warning(f"Search skipped (invalid URL): {e}")
            return []
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            # 单个搜索引擎异常时直接降级，避免影响混合搜索整体结果。
            logger.warning(
                f"{self.__class__.__name__} skipped due to request error: {e}"
            )
            return []
        except Exception:
            # 编程错误、返回契约变化等未知异常不能伪装成“检索成功但无结果”。
            logger.exception(f"{self.__class__.__name__} failed unexpectedly")
            raise
        if kwargs.get("parse_content", True):
            docs = await self.parser(
                docs=docs,
                timeout=self._parser_timeout,
                use_jina_reader=kwargs.get("use_jina_reader", True),
            )

        seen_urls = set()
        seen_contents = set()
        deduped_docs = []
        # URL 优先去重；缺 URL 时再用规范化正文，避免同页不同摘要重复占用上下文。
        for doc in docs:
            doc.data = dict(doc.data or {})
            doc.data.setdefault("content_scope", "search_snippet")
            doc.data.setdefault("retrieved_at", datetime.now(timezone.utc).isoformat())
            parsed = urlparse(doc.link or "")
            query = urlencode(sorted(
                (key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                if not key.lower().startswith("utm_")
            ))
            canonical_url = urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", query, ""))
            content_key = " ".join((doc.content or "").split()).lower()
            if (
                doc.content
                and (not canonical_url or canonical_url not in seen_urls)
                and content_key not in seen_contents
            ):
                deduped_docs.append(doc)
                if canonical_url:
                    seen_urls.add(canonical_url)
                seen_contents.add(content_key)
        return deduped_docs


class DDGSearch(SearchBase):
    def __init__(self):
        super().__init__()
        self._engine = "ddg"
        self._region = os.getenv("DDG_REGION", "wt-wt")
        self._safesearch = os.getenv("DDG_SAFESEARCH", "moderate")

    async def search(
        self, query: str, request_id: str = None, *args, **kwargs
    ) -> List[Doc]:
        def _run_text_search() -> List[dict]:
            client_kwargs: dict[str, Any] = {"timeout": min(5, self._timeout)}
            proxy = _configured_proxy()
            if proxy:
                client_kwargs["proxy"] = proxy
            client = DDGS(**client_kwargs)
            results = client.text(
                query,
                region=self._region,
                safesearch=self._safesearch,
                max_results=self._count,
                backend="duckduckgo",
            )
            return list(results) if results else []

        try:
            raw_results = await _bounded_search_call(_run_text_search, min(5, self._timeout)) if DDGS else []
        except Exception as error:
            logger.warning(f"DDG library search failed, retry public HTML: {type(error).__name__}")
            raw_results = []
        if not raw_results:
            try:
                public = await asyncio.wait_for(self._search_public_html(query), timeout=8)
            except (asyncio.TimeoutError, OSError):
                public = []
            if public:
                return public
            # The project already uses Bing's public index as DDG fallback.
            # Keep this path reachable inside strategic-map's 25s total budget.
            return await self._search_public_bing(query)
        return [
            Doc(
                doc_type="web_page",
                content=item.get("body", "") or item.get("snippet", ""),
                title=item.get("title", ""),
                link=item.get("href", item.get("url", "")),
                data={"search_engine": self._engine},
            )
            for item in raw_results
            if item.get("href", item.get("url", ""))
        ]

    async def _search_public_html(self, query: str) -> List[Doc]:
        """公开 DDG HTML 兜底，不依赖 ddgs 包或 API key。"""
        url = "https://html.duckduckgo.com/html/?q=" + quote(query, safe="")
        kwargs = _request_kwargs(
            timeout=aiohttp.ClientTimeout(connect=3, total=min(6, self._timeout))
        )

        async def _read(request_kwargs):
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers={"User-Agent": "AI4SAgentWebSearch/1.0"}, **request_kwargs) as response:
                    if response.status != 200:
                        return ""
                    return await response.text(errors="ignore")

        try:
            content = await _read(kwargs)
        except Exception as error:
            if "proxy" not in kwargs or _proxy_required():
                logger.debug(f"public DDG search failed: {type(error).__name__}")
                content = ""
            else:
                logger.warning(f"public DDG proxy unavailable, retry direct: {type(error).__name__}")
                try:
                    content = await _read({"timeout": kwargs["timeout"]})
                except Exception as direct_error:
                    logger.debug(f"public DDG direct search failed: {type(direct_error).__name__}")
                    content = ""

        # aiohttp may fail to establish a direct connection on machines where
        # the system resolver prefers an unreachable IPv6 route.  urllib's
        # synchronous opener has a more portable fallback; explicitly disable
        # environment proxies so a stale 127.0.0.1 proxy cannot intercept it.
        if not content and not _proxy_required():
            def _urllib_read() -> str:
                request = urllib.request.Request(
                    url,
                    headers={"User-Agent": "AI4SAgentWebSearch/1.0"},
                )
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({})
                )
                with opener.open(request, timeout=min(4, self._timeout)) as response:
                    return response.read(2_000_000).decode("utf-8", errors="ignore")

            try:
                content = await _bounded_search_call(_urllib_read, min(4, self._timeout))
            except Exception as direct_error:
                logger.debug(f"public DDG urllib direct search failed: {type(direct_error).__name__}")
                return []

        if not content:
            return []
        soup = BeautifulSoup(content, "html.parser")
        links = soup.select("a.result__a")
        snippets = soup.select("a.result__snippet, div.result__snippet")
        docs: List[Doc] = []
        for index, anchor in enumerate(links[: self._count]):
            href = (anchor.get("href") or "").strip()
            if "uddg=" in href:
                encoded = parse_qs(urlparse(href).query).get("uddg", [""])[0]
                href = unquote(encoded)
            if not href.startswith(("http://", "https://")):
                continue
            snippet = snippets[index].get_text(" ", strip=True) if index < len(snippets) else ""
            docs.append(Doc(
                doc_type="web_page",
                content=html.unescape(snippet),
                title=html.unescape(anchor.get_text(" ", strip=True)),
                link=href,
                data={"search_engine": "ddg-public"},
            ))
        return docs

    async def _search_public_bing(self, query: str) -> List[Doc]:
        url = "https://www.bing.com/search?q=" + quote(query, safe="")
        proxy = _configured_proxy()
        if _proxy_required() and not proxy:
            raise RuntimeError("public search unavailable: required proxy is not configured")
        def read():
            opener = urllib.request.build_opener(urllib.request.ProxyHandler(
                {"http": proxy, "https": proxy} if proxy else {}
            ))
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 AI4SAgentWebSearch/1.0"})
            with opener.open(request, timeout=5) as response:
                return response.read(2_000_000).decode('utf-8', errors='ignore')
        try:
            content = await _bounded_search_call(read, 6)
        except Exception as exc:
            # Never present exhausted/unavailable transports as a true empty hit.
            raise RuntimeError('public search unavailable: ' + type(exc).__name__) from exc
        return self._parse_public_bing(content)

    def _parse_public_bing(self, content: str) -> List[Doc]:
        docs = []
        for block in BeautifulSoup(content, 'html.parser').select('li.b_algo'):
            anchor = block.select_one('h2 a[href]')
            if anchor is None: continue
            url = anchor.get('href', '')
            parsed = urlparse(url)
            if parsed.hostname and (parsed.hostname == 'bing.com' or parsed.hostname.endswith('.bing.com')):
                encoded = parse_qs(parsed.query).get('u', [''])[0]
                if encoded.startswith('a1'):
                    try: url = base64.urlsafe_b64decode(encoded[2:] + '=' * (-len(encoded[2:]) % 4)).decode()
                    except (ValueError, UnicodeError): continue
            if not _search_url_ok(url): continue
            snippet = block.select_one('.b_caption p, p')
            docs.append(Doc(doc_type='web_page', title=anchor.get_text(' ', strip=True), link=url,
                content=snippet.get_text(' ', strip=True) if snippet else '',
                data={'search_engine':'bing-public','content_scope':'search_snippet'}))
            if len(docs) >= self._count: break
        return docs


class BingSearch(SearchBase):
    def __init__(self):
        super().__init__()
        self._engine = "bing-search"
        self._url = os.getenv("BING_SEARCH_URL")
        self._api_key = os.getenv("BING_SEARCH_API_KEY")

        self.headers = {
            "Content-Type": "application/json",
        }
        self.set_auth()

    def set_auth(self):
        if self._use_jd_gateway:
            self.headers["Authorization"] = f"Bearer {self._api_key}"
        else:
            self.headers["Ocp-Apim-Subscription-Key"] = self._api_key

    def construct_body(self, query: str, request_id: str = None):
        if self._use_jd_gateway:
            return {
                "request_id": request_id,
                "model": self._engine,
                "messages": [{"role": "user", "content": query}],
                "count": self._count,
                "stream": False,
            }
        else:
            return {"q": query, "textDecorations": True}

    async def search(
        self, query: str, request_id: str = None, *args, **kwargs
    ) -> List[Doc]:
        if not _search_url_ok(self._url):
            logger.warning(
                f"Bing search skipped: BING_SEARCH_URL not configured or invalid"
            )
            return []
        body = self.construct_body(query, request_id)
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._url,
                **_request_kwargs(
                    json=body,
                    headers=self.headers,
                    timeout=self._timeout,
                ),
            ) as response:
                result = json.loads(await response.text())
                return [
                    Doc(
                        doc_type="web_page",
                        content=item.get("snippet", ""),
                        title=item.get("name", ""),
                        link=item.get("url", ""),
                        data={"search_engine": self._engine},
                    )
                    for item in result.get("webPages", {}).get("value", [])
                ]


class JinaSearch(BingSearch):
    def __init__(self):
        super().__init__()
        self._engine = "search_pro_jina"
        self._url = os.getenv("JINA_SEARCH_URL")
        self._api_key = os.getenv("JINA_SEARCH_API_KEY")

    def _build_search_url(self, query: str) -> str:
        """Jina Search 使用路径参数而不是 q 查询参数。"""
        return f"{self._url.rstrip('/')}/{quote(query, safe='')}"

    async def search(
        self, query: str, request_id: str = None, *args, **kwargs
    ) -> List[Doc]:
        if not _search_url_ok(self._url):
            logger.warning(
                f"Jina search skipped: JINA_SEARCH_URL not configured or invalid"
            )
            return []
        if self._use_jd_gateway:
            body = self.construct_body(query, request_id)
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._url,
                    **_request_kwargs(
                        json=body,
                        headers=self.headers,
                        timeout=self._timeout,
                    ),
                ) as response:
                    result = json.loads(await response.text())
                    return [
                        Doc(
                            doc_type="web_page",
                            content=item.get("content", ""),
                            title=item.get("title", ""),
                            link=item.get("link", ""),
                            data={"search_engine": self._engine},
                        )
                        for item in result.get("search_result", [])
                    ]
        else:
            headers = {
                "Accept": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            }
            search_url = self._build_search_url(query)
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    search_url,
                    **_request_kwargs(headers=headers, timeout=self._timeout),
                ) as response:
                    if response.status != 200:
                        logger.error(
                            f"Jina search failed: status={response.status}, body={await response.text()}"
                        )
                        return []
                    result = await response.json(content_type=None)
                    return [
                        Doc(
                            doc_type="web_page",
                            content=item.get("content", ""),
                            title=item.get("title", ""),
                            link=item.get("url", ""),
                            data={"search_engine": self._engine},
                        )
                        for item in result.get("data", [])
                    ]


class SogouSearch(JinaSearch):
    def __init__(self):
        super().__init__()
        self._engine = "search_pro_sogou"
        self._url = os.getenv("SOGOU_SEARCH_URL")
        self._api_key = os.getenv("SOGOU_SEARCH_API_KEY")


class SerperSearch(JinaSearch):
    def __init__(self):
        super().__init__()
        self._engine = "serper"
        self._url = os.getenv("SERPER_SEARCH_URL")
        self._api_key = os.getenv("SERPER_SEARCH_API_KEY")
        self.set_auth()

    def set_auth(self):
        self.headers["X-API-KEY"] = self._api_key

    def construct_body(self, query: str, request_id: str = None):
        return {
            "q": query,
            "count": self._count,
        }

    async def search(
        self, query: str, request_id: str = None, *args, **kwargs
    ) -> List[Doc]:
        if not _search_url_ok(self._url):
            logger.warning(
                f"Serper search skipped: SERPER_SEARCH_URL not configured or invalid"
            )
            return []
        body = self.construct_body(query, request_id)
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._url,
                **_request_kwargs(
                    json=body,
                    headers=self.headers,
                    timeout=self._timeout,
                ),
            ) as response:
                result = json.loads(await response.text())
                return [
                    Doc(
                        doc_type="web_page",
                        content=item.get("snippet", ""),
                        title=item.get("title", ""),
                        link=item.get("link", ""),
                        data={"search_engine": self._engine},
                    )
                    for item in result.get("organic", [])
                ]


class ExaSearch(SearchBase):
    def __init__(self):
        super().__init__()
        self._engine = "exa"
        self._url = os.getenv("EXA_SEARCH_URL", "https://api.exa.ai/search")
        self._api_key = os.getenv("EXA_API_KEY")
        self.headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "x-api-key": self._api_key,
        }

    async def search(
        self, query: str, request_id: str = None, *args, **kwargs
    ) -> List[Doc]:
        if not _search_url_ok(self._url):
            logger.warning(
                f"Exa search skipped: EXA_SEARCH_URL not configured or invalid"
            )
            return []

        body = {
            "query": query,
            "numResults": self._count,
            "useAutoprompt": True,
            "contents": {"text": {"maxCharacters": 20000}},
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._url,
                    **_request_kwargs(
                        json=body,
                        headers=self.headers,
                        timeout=self._timeout,
                    ),
                ) as response:
                    if response.status != 200:
                        logger.error(f"Exa search failed: status={response.status}")
                        return []

                    data = await response.json()
                    results = data.get("results", [])

                    return [
                        Doc(
                            doc_type="web_page",
                            content=item.get("text", "")
                            or item.get("extract", "")
                            or "",
                            title=item.get("title", ""),
                            link=item.get("url", ""),
                            data={
                                "search_engine": self._engine,
                                "score": item.get("score"),
                            },
                        )
                        for item in results
                    ]
        except Exception as e:
            logger.error(f"Exa search error: {e}")
            return []


class MixSearch(BingSearch):
    def __init__(self):
        super().__init__()
        self._engine = "mix_search"
        self._ddg_engine = DDGSearch()
        self._bing_engine = BingSearch()
        self._jina_engine = JinaSearch()
        self._sogou_engine = SogouSearch()
        self._serp_engine = SerperSearch()
        self._exa_engine = ExaSearch()

    async def search(
        self,
        query: str,
        request_id: str = None,
        use_ddg: bool = True,
        use_bing: bool = False,
        use_jina: bool = False,
        use_sogou: bool = False,
        use_serp: bool = False,
        use_exa: bool = False,
        *args,
        **kwargs,
    ) -> List[Doc]:
        assert use_ddg or use_bing or use_jina or use_sogou or use_serp or use_exa
        use_jina_reader = kwargs.get("use_jina_reader", True)
        engines = []
        if use_ddg:
            engines.append(self._ddg_engine)
        if use_bing:
            engines.append(self._bing_engine)
        if use_jina:
            engines.append(self._jina_engine)
        if use_sogou:
            engines.append(self._sogou_engine)
        if use_serp:
            engines.append(self._serp_engine)
        if use_exa:
            engines.append(self._exa_engine)
        # 每个文档独立抓取；TaskGroup 保证所有任务结束后再把正文写回对应 Doc。
        async with asyncio.TaskGroup() as tg:
            tasks = [
                tg.create_task(
                    engine.search_and_dedup(
                        query=query,
                        request_id=request_id,
                        use_jina_reader=use_jina_reader,
                        parse_content=False,
                    )
                )
                for engine in engines
            ]
        results = [task.result() for task in tasks]
        return [doc for docs in results for doc in docs]
