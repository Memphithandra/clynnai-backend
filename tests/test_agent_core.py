from __future__ import annotations

from typing import Any

import pytest

from clynnai_backend.agent import AgentRunOptions, ClynnAgentRuntime
from clynnai_backend.agent.llm import parse_tool_arguments
from clynnai_backend.agent.tools.base import ClynnTool
from clynnai_backend.config import Settings


class FakeLLM:
    def __init__(self):
        self.calls = 0

    async def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, model: str | None = None
    ) -> dict[str, Any]:
        _ = messages, tools, model
        self.calls += 1
        if self.calls == 1:
            return {"choices": [{"message": {"content": "", "tool_calls": [{"id": "call_search", "type": "function", "function": {"name": "firecrawl_search", "arguments": '{"query":"AI 新闻","limit":2}'}}]}}]}
        if self.calls == 2:
            return {"choices": [{"message": {"content": "", "tool_calls": [{"id": "call_phone", "type": "function", "function": {"name": "request_phone_action", "arguments": '{"action":"open_url","params":{"url":"https://example.com"},"risk":"low","requires_confirmation":false}'}}]}}]}
        return {"choices": [{"message": {"content": "主人，已查到 AI 新闻，并准备打开网页。"}}]}


class FakeSearchTool(ClynnTool):
    name = "firecrawl_search"
    description = "fake search"
    parameters = {"type": "object", "properties": {"query": {"type": "string"}}}

    async def run(self, arguments: dict[str, Any]) -> Any:
        return {"results": [{"title": "AI 新闻", "url": "https://example.com"}]}


@pytest.mark.asyncio
async def test_langgraph_agent_core_runs_multiple_tools_and_stops_on_phone_action(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'agent.db'}", upstream_base_url="http://upstream.test/v1")

    async def fake_image(_: dict[str, Any]) -> Any:
        return {"data": []}

    runtime = ClynnAgentRuntime(
        settings=settings,
        image_generate_fn=fake_image,
        llm=FakeLLM(),
        tools=[FakeSearchTool(), __import__("clynnai_backend.agent.tools.phone_action", fromlist=["PhoneActionTool"]).PhoneActionTool()],
    )
    result = await runtime.run(history=[], user_text="查 AI 新闻后打开网页", options=AgentRunOptions(web_preferred=True, max_steps=6))

    assert result.interrupted is True
    assert [call.name for call in result.tool_calls] == ["firecrawl_search", "request_phone_action"]
    assert result.pending_actions[0].action == "open_url"
    assert result.parts[-1]["type"] == "phone_action"



class FakeImageLLM:
    def __init__(self):
        self.calls = 0

    async def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, model: str | None = None
    ) -> dict[str, Any]:
        _ = messages, tools, model
        self.calls += 1
        if self.calls == 1:
            return {"choices": [{"message": {"content": "", "tool_calls": [{"id": "call_image", "type": "function", "function": {"name": "generate_image", "arguments": '{"prompt":"amber catgirl agent","size":"1024x1024"}'}}]}}]}
        return {"choices": [{"message": {"content": "主人，图片生成好了。"}}]}


@pytest.mark.asyncio
async def test_image_tool_result_becomes_generated_image_part(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'agent.db'}", upstream_base_url="http://upstream.test/v1")

    async def fake_image(_: dict[str, Any]) -> Any:
        return {"data": [{"url": "https://img.test/clynn.png"}]}

    runtime = ClynnAgentRuntime(
        settings=settings,
        image_generate_fn=fake_image,
        llm=FakeImageLLM(),
    )
    result = await runtime.run(history=[], user_text="画 Clynn", options=AgentRunOptions(max_steps=6))

    assert {"type": "generated_image", "url": "https://img.test/clynn.png"} in result.parts
