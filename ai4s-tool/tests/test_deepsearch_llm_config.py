# -*- coding: utf-8 -*-
import os
import unittest
from unittest.mock import patch

from ai4s_tool.tool.search_component.answer import answer_question
from ai4s_tool.tool.search_component.query_process import query_decompose
from ai4s_tool.tool.search_component.reasoning import search_reasoning
from ai4s_tool.util.llm_util import resolve_openai_compat_env


class DeepSearchLlmConfigTest(unittest.IsolatedAsyncioTestCase):
    async def test_ai4s_report_query_gets_fixed_evidence_chapters(self):
        responses = iter(
            [
                '[{"title":"技术路线","content":"研究架构",'
                '"search_queries":["AlphaFold 3 architecture"]}]'
            ]
        )

        async def fake_ask_llm(*args, **kwargs):
            yield next(responses)

        with (
            patch.dict(
                os.environ,
                {
                    "DEEPSEARCH_BASE_URL": "https://deepsearch.example.com/v1/chat/completions",
                    "DEEPSEARCH_API_KEY": "deepsearch-key",
                },
                clear=False,
            ),
            patch(
                "ai4s_tool.tool.search_component.query_process.ask_llm",
                new=fake_ask_llm,
            ),
        ):
            chapters = await query_decompose(
                "AlphaFold 3 技术路线与竞争格局研判报告"
            )

        titles = [chapter["title"] for chapter in chapters]
        self.assertEqual(
            [
                "事件概览",
                "技术路线",
                "主要创新",
                "论文团队与机构",
                "前序工作",
                "竞争路线",
                "AI4S意义",
                "待观察问题",
                "来源证据",
            ],
            titles,
        )
        self.assertEqual(9, len(chapters))
        self.assertTrue(all(chapter["search_queries"] for chapter in chapters))

    def test_should_prefer_deepsearch_gateway_over_openai_defaults(self):
        with patch.dict(
            os.environ,
            {
                "DEEPSEARCH_BASE_URL": "https://deepsearch.example.com/v1/chat/completions",
                "DEEPSEARCH_API_KEY": "deepsearch-key",
                "OPENAI_BASE_URL": "https://default.example.com/v1/chat/completions",
                "OPENAI_API_KEY": "default-key",
            },
            clear=False,
        ):
            config = resolve_openai_compat_env("DEEPSEARCH")

        self.assertEqual(
            "https://deepsearch.example.com/v1/chat/completions", config["api_base"]
        )
        self.assertEqual("deepsearch-key", config["api_key"])

    def test_should_fallback_to_openai_defaults_when_deepsearch_gateway_is_blank(self):
        with patch.dict(
            os.environ,
            {
                "DEEPSEARCH_BASE_URL": "   ",
                "DEEPSEARCH_API_KEY": "",
                "OPENAI_BASE_URL": "https://default.example.com/v1/chat/completions",
                "OPENAI_API_KEY": "default-key",
            },
            clear=False,
        ):
            config = resolve_openai_compat_env("DEEPSEARCH")

        self.assertEqual(
            "https://default.example.com/v1/chat/completions", config["api_base"]
        )
        self.assertEqual("default-key", config["api_key"])

    async def test_query_decompose_should_use_one_llm_call_and_parse_search_queries(
        self,
    ):
        captured_kwargs = []
        responses = iter(
            [
                [
                    '[{"title":"子问题一","content":"研究第一个方面",'
                    '"search_queries":["关键词一","关键词二"]},'
                    '{"title":"子问题二","content":"研究第二个方面",'
                    '"search_queries":["关键词三"]}]'
                ],
            ]
        )

        async def fake_ask_llm(*args, **kwargs):
            captured_kwargs.append(kwargs)
            for chunk in next(responses):
                yield chunk

        with (
            patch.dict(
                os.environ,
                {
                    "DEEPSEARCH_BASE_URL": "https://deepsearch.example.com/v1/chat/completions",
                    "DEEPSEARCH_API_KEY": "deepsearch-key",
                },
                clear=False,
            ),
            patch(
                "ai4s_tool.tool.search_component.query_process.ask_llm",
                new=fake_ask_llm,
            ),
        ):
            queries = await query_decompose("帮我分析这个问题")

        self.assertEqual(
            [
                {
                    "title": "子问题一",
                    "content": "研究第一个方面",
                    "search_queries": ["关键词一", "关键词二"],
                },
                {
                    "title": "子问题二",
                    "content": "研究第二个方面",
                    "search_queries": ["关键词三"],
                },
            ],
            queries,
        )
        self.assertEqual(1, len(captured_kwargs))
        self.assertIn("search_queries", captured_kwargs[0]["messages"][0]["content"])
        self.assertNotIn(
            "thinking_result", captured_kwargs[0]["messages"][0]["content"]
        )
        self.assertTrue(
            all(
                item["api_base"] == "https://deepsearch.example.com/v1/chat/completions"
                for item in captured_kwargs
            )
        )
        self.assertTrue(
            all(item["api_key"] == "deepsearch-key" for item in captured_kwargs)
        )

    async def test_search_reasoning_should_forward_deepsearch_gateway(self):
        captured_kwargs = []

        async def fake_ask_llm(*args, **kwargs):
            captured_kwargs.append(kwargs)
            yield '{"is_answer": 1, "rewrite_query": "", "reason": "enough"}'

        with (
            patch.dict(
                os.environ,
                {
                    "DEEPSEARCH_BASE_URL": "https://deepsearch.example.com/v1/chat/completions",
                    "DEEPSEARCH_API_KEY": "deepsearch-key",
                },
                clear=False,
            ),
            patch(
                "ai4s_tool.tool.search_component.reasoning.ask_llm",
                new=fake_ask_llm,
            ),
        ):
            result = await search_reasoning(
                request_id="req-1",
                query="测试问题",
                content="已有资料",
            )

        self.assertEqual("1", result["is_verify"])
        self.assertEqual(
            "https://deepsearch.example.com/v1/chat/completions",
            captured_kwargs[0]["api_base"],
        )
        self.assertEqual("deepsearch-key", captured_kwargs[0]["api_key"])

    async def test_answer_question_should_forward_deepsearch_gateway(self):
        captured_kwargs = []

        async def fake_ask_llm(*args, **kwargs):
            captured_kwargs.append(kwargs)
            yield "这是答案"

        with (
            patch.dict(
                os.environ,
                {
                    "DEEPSEARCH_BASE_URL": "https://deepsearch.example.com/v1/chat/completions",
                    "DEEPSEARCH_API_KEY": "deepsearch-key",
                },
                clear=False,
            ),
            patch(
                "ai4s_tool.tool.search_component.answer.ask_llm",
                new=fake_ask_llm,
            ),
        ):
            answer = []
            async for chunk in answer_question("测试问题", "搜索内容"):
                answer.append(chunk)

        self.assertEqual("这是答案", "".join(answer))
        self.assertEqual(
            "https://deepsearch.example.com/v1/chat/completions",
            captured_kwargs[0]["api_base"],
        )
        self.assertEqual("deepsearch-key", captured_kwargs[0]["api_key"])


if __name__ == "__main__":
    unittest.main()
