# -*- coding: utf-8 -*-
"""MRAG 文本 LLM 客户端：OpenAI 兼容协议，支持流式与重试。"""

import os
import time
from typing import Any

import dotenv
from openai import DefaultHttpxClient, OpenAI
from ai4s_tool.tool.mrag.utils.retry_utils import stream_with_retry
from ai4s_tool.util.log_util import logger

dotenv.load_dotenv()

OPENAI_COMPAT_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0"
)


def _normalize_openai_compatible_base_url(base_url: str | None) -> str | None:
    """规范化 OpenAI 兼容网关地址，兼容填写到具体接口路径的场景。"""
    if not base_url:
        return base_url

    normalized = base_url.strip().rstrip("/")
    suffixes = (
        "/v1/chat/completions",
        "/chat/completions",
        "/v1/completions",
        "/completions",
        "/v1/responses",
        "/responses",
    )

    lowered = normalized.lower()
    changed = True
    while changed:
        changed = False
        for suffix in suffixes:
            if lowered.endswith(suffix):
                normalized = normalized[: -len(suffix)].rstrip("/")
                lowered = normalized.lower()
                changed = True
                break

    if not lowered.endswith("/v1"):
        normalized = f"{normalized}/v1"
    return normalized


def _extract_stream_delta_text(chunk) -> str:
    """从 OpenAI 兼容流式 chunk 中取出文本增量。"""
    choices = getattr(chunk, "choices", None) or []
    if not choices:
        return ""
    choice = choices[0]
    delta = getattr(choice, "delta", None)
    content = getattr(delta, "content", None) if delta is not None else None
    if content is None:
        message = getattr(choice, "message", None)
        content = getattr(message, "content", None) if message is not None else None
    return content if isinstance(content, str) else ""


class CompletionText(str):
    """String-compatible completion carrying optional provider token usage."""

    usage: dict[str, Any] | None

    def __new__(cls, value: str, usage: dict[str, Any] | None = None):
        result = super().__new__(cls, value)
        result.usage = usage
        return result


def _usage_dict(chunk) -> dict[str, Any] | None:
    usage = getattr(chunk, "usage", None)
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        value = usage.model_dump()
    elif isinstance(usage, dict):
        value = dict(usage)
    else:
        value = {
            key: getattr(usage, key, None)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        }
    return {key: item for key, item in value.items() if item is not None}


def _aggregate_chat_completion_stream(stream) -> CompletionText:
    """把 SSE 分片拼回完整文本，并保留末包的精确 Token 用量。"""
    parts: list[str] = []
    usage = None
    for chunk in stream:
        observed = _usage_dict(chunk)
        if observed:
            usage = observed
        text = _extract_stream_delta_text(chunk)
        if text:
            parts.append(text)
    return CompletionText("".join(parts), usage=usage)


def _bounded_stream(stream, seconds):
    deadline = time.monotonic() + seconds
    try:
        for chunk in stream:
            if time.monotonic() >= deadline:
                raise TimeoutError("LLM stream wall-time budget exhausted")
            yield chunk
    finally:
        close = getattr(stream, "close", None)
        if close:
            close()


def _build_openai_compatible_headers() -> dict[str, str]:
    """补齐第三方 OpenAI 兼容网关要求的默认请求头。"""
    return {
        "User-Agent": os.getenv(
            "OPENAI_COMPAT_USER_AGENT", OPENAI_COMPAT_DEFAULT_USER_AGENT
        )
    }


class LLMClient:
    """大模型客户端类"""

    # 配置环境变量
    # API_KEY llm 大模型apikey
    # LLM_MODEL_NAME 大模型名称
    # LLM_MODEL_BASE_URL 大模型地址
    def __init__(self):
        self.api_key = os.getenv("LLM_API_KEY")
        self.model_name = os.getenv("LLM_MODEL_NAME")
        # 兼容把 base url 误填成 /v1/chat/completions 的配置，避免切换到 OpenAI 兼容网关时拼接出错。
        self.model_base_url = _normalize_openai_compatible_base_url(
            os.getenv("LLM_MODEL_BASE_URL")
        )
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.model_base_url,
            http_client=DefaultHttpxClient(trust_env=False),
            default_headers=_build_openai_compatible_headers(),
        )
        logger.info("init LLM client, {base_url}".format(base_url=self.model_base_url))

    @staticmethod
    def convert_messages(prompt):
        return [{"role": "user", "content": prompt}]

    def _build_extra_body(self):
        """仅对 Qwen / DashScope 文本模型透传专属参数，避免污染 OpenAI 兼容请求。"""
        model_name = (self.model_name or "").lower()
        # DashScope reasoning models (including DeepSeek served through the
        # shared compatible endpoint) otherwise may spend the entire output
        # budget on ``reasoning_content`` and return an empty message.content.
        # Keep this in the shared Agent client so every caller gets the same
        # provider-safe behaviour.
        if (
            model_name.startswith("qwen")
            or model_name.startswith("deepseek-")
            or model_name.startswith("dashscope/")
        ):
            return {
                "enable_thinking": False,
                "chat_template_kwargs": {"enable_thinking": False},
            }
        return None

    def completions(self, messages, max_tokens=8192, temperature=0, stream=False,
                    *, timeout=None, max_retries=None, response_format=None,
                    include_usage=False):
        message_chars = sum(
            len(content) if isinstance((content := message.get("content")), str) else len(str(content or ""))
            for message in messages
        )
        # Full prompts contain large page bodies and schemas. Their hashes/counts
        # are sufficient operational telemetry and avoid duplicate disk I/O.
        logger.info(
            f"chat completion model={self.model_name} messages={len(messages)} input_chars={message_chars}"
        )
        # HTTP 始终走 SSE：调用方 stream=False 时再把分片聚合成字符串，避开 Cloudflare 524。
        request_kwargs = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
            "max_tokens": max_tokens,
        }
        extra_body = self._build_extra_body()
        if timeout is not None:
            request_kwargs["timeout"] = timeout
        if response_format is not None:
            request_kwargs["response_format"] = response_format
        if include_usage:
            request_kwargs["stream_options"] = {"include_usage": True}
        if extra_body:
            request_kwargs["extra_body"] = extra_body

        label = f"mrag-llm:{self.model_name or 'unknown'}"
        # Optional per-call bounds; existing MRAG callers retain their defaults.
        request_client = self.client if max_retries is None else self.client.with_options(max_retries=0)
        stream_iter = stream_with_retry(
            lambda: request_client.chat.completions.create(**request_kwargs),
            label=label,
            max_retries=max_retries,
        )
        if timeout is not None:
            stream_iter = _bounded_stream(stream_iter, float(timeout))
        if stream:
            return stream_iter
        return _aggregate_chat_completion_stream(stream_iter)

    def chat(self, prompt, image_url):
        messages = self.convert_messages(prompt)
        return self.completions(messages)


if __name__ == "__main__":
    os.environ.setdefault("LLM_API_KEY", os.getenv("OPENAI_API_KEY", ""))
    os.environ.setdefault("LLM_MODEL_NAME", "gpt-5.2")
    os.environ.setdefault(
        "LLM_MODEL_BASE_URL", os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    )
    llm = LLMClient()
    print(llm.completions([{"role": "user", "content": "你好"}]))
