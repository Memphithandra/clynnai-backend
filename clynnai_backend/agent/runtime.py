from __future__ import annotations

import json
import re
from operator import add
from typing import Annotated, Any, AsyncIterator, Awaitable, Callable, TypedDict

from langgraph.graph import StateGraph, START, END

from ..config import Settings
from .llm import OpenAICompatibleLLM, extract_message, extract_text_from_message, parse_tool_arguments
from .prompts import CLYNN_AGENT_SYSTEM_PROMPT
from .schema import AgentRunOptions, AgentRunResult, AgentToolCallRecord, PhoneActionRequest
from .tools import ClynnTool, FirecrawlScrapeTool, FirecrawlSearchTool, ImageGenerationTool, PhoneActionTool


class ClynnAgentState(TypedDict):
    messages: Annotated[list[dict[str, Any]], add]
    tool_calls: Annotated[list[dict[str, Any]], add]
    pending_actions: Annotated[list[dict[str, Any]], add]
    final_text: str
    step: int
    max_steps: int
    model: str | None


class ClynnAgentRuntime:
    def __init__(
        self,
        settings: Settings,
        image_generate_fn: Callable[[dict[str, Any]], Awaitable[Any]],
        llm: OpenAICompatibleLLM | None = None,
        tools: list[ClynnTool] | None = None,
    ):
        self.settings = settings
        self.llm = llm or OpenAICompatibleLLM(settings)
        self.tools = tools or [
            FirecrawlSearchTool(settings),
            FirecrawlScrapeTool(settings),
            ImageGenerationTool(image_generate_fn),
            PhoneActionTool(),
        ]
        self.tool_by_name = {tool.name: tool for tool in self.tools}
        self.graph = self._build_graph()

    async def run(self, history: list[dict[str, Any]], user_text: str, options: AgentRunOptions) -> AgentRunResult:
        messages = self._build_initial_messages(history, user_text, options)
        initial: ClynnAgentState = {
            "messages": messages,
            "tool_calls": [],
            "pending_actions": [],
            "final_text": "",
            "step": 0,
            "max_steps": options.max_steps or self.settings.agent_max_steps,
            "model": (options.model or "").strip() or None,
        }
        state = await self.graph.ainvoke(initial)
        final_text = state.get("final_text") or self._last_assistant_text(state.get("messages", [])) or "Clynn Agent 没有返回文本。"
        pending = [PhoneActionRequest(**p) for p in state.get("pending_actions", []) if p.get("type") == "phone_action"]
        parts: list[dict[str, Any]] = [{"type": "text", "text": final_text}]
        image_urls = []
        image_urls.extend(self._extract_image_urls(final_text))
        for call in state.get("tool_calls", []):
            if call.get("name") == "generate_image" and call.get("result") is not None:
                image_urls.extend(self._extract_image_urls_from_tool_result(call.get("result")))
        seen_urls = set()
        for url in image_urls:
            if url in seen_urls:
                continue
            seen_urls.add(url)
            parts.append({"type": "generated_image", "url": url})
        for action in pending:
            parts.append(action.model_dump())
        return AgentRunResult(
            text=final_text,
            parts=parts,
            tool_calls=[AgentToolCallRecord(**c) for c in state.get("tool_calls", [])],
            pending_actions=pending,
            interrupted=bool(pending),
            raw_messages=state.get("messages", []),
        )


    def _build_initial_messages(self, history: list[dict[str, Any]], user_text: Any, options: AgentRunOptions) -> list[dict[str, Any]]:
        system_prompt = CLYNN_AGENT_SYSTEM_PROMPT
        if options.jailbreak_prompt and options.jailbreak_enabled:
            system_prompt = options.jailbreak_prompt + "\n\n" + system_prompt
        if options.persona_prompt:
            system_prompt = system_prompt + "\n\n# 会话 Persona: " + (options.persona_name or "未命名") + "\n" + options.persona_prompt.strip()
        context_lines = []
        if options.phone_control:
            context_lines.append("手机控制能力状态（系统上下文，不是主人消息）：" + json.dumps(options.phone_control, ensure_ascii=False))
        if context_lines:
            system_prompt = system_prompt + "\n\n# 系统运行上下文\n" + "\n".join(context_lines)
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_text})
        return messages

    def _tool_status_text(self, name: str | None) -> str:
        if name in {"firecrawl_search", "firecrawl_scrape"}:
            return "我去搜索一下"
        if name == "generate_image":
            return "我去画一下"
        if name == "request_phone_action":
            return "我需要操作一下手机"
        return "我调用一下工具"

    async def run_stream(self, history: list[dict[str, Any]], user_text: Any, options: AgentRunOptions) -> AsyncIterator[dict[str, Any]]:
        messages = self._build_initial_messages(history, user_text, options)
        records: list[dict[str, Any]] = []
        pending_actions: list[dict[str, Any]] = []
        final_text = ""
        image_urls: list[str] = []
        max_steps = options.max_steps or self.settings.agent_max_steps
        tools = [tool.openai_schema() for tool in self.tools]

        for step in range(max_steps):
            content_chunks: list[str] = []
            reasoning_chunks: list[str] = []
            tool_acc: dict[int, dict[str, Any]] = {}
            async for chunk in self.llm.stream_chat(messages, tools=tools, model=options.model):
                choice = (chunk.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
                if reasoning:
                    reasoning_chunks.append(str(reasoning))
                    yield {"event": "reasoning_delta", "text": str(reasoning)}
                content = delta.get("content")
                if isinstance(content, str) and content:
                    content_chunks.append(content)
                    final_text += content
                    yield {"event": "delta", "text": content}
                for tc in delta.get("tool_calls") or []:
                    idx = int(tc.get("index", 0))
                    acc = tool_acc.setdefault(idx, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                    if tc.get("id"):
                        acc["id"] += str(tc.get("id"))
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        acc["function"]["name"] += str(fn.get("name"))
                    if fn.get("arguments"):
                        acc["function"]["arguments"] += str(fn.get("arguments"))

            assistant_msg: dict[str, Any] = {"role": "assistant", "content": "".join(content_chunks)}
            tool_calls = [tool_acc[i] for i in sorted(tool_acc)]
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            messages.append(assistant_msg)
            if not tool_calls:
                break

            out_tool_messages = []
            for call in tool_calls:
                fn = call.get("function") or {}
                name = fn.get("name") or call.get("name")
                args = parse_tool_arguments(fn.get("arguments") or call.get("arguments"))
                status_text = self._tool_status_text(name)
                current_turn_text = "".join(content_chunks).strip()
                if status_text and status_text not in current_turn_text:
                    if final_text and not final_text.endswith("\n"):
                        final_text += "\n"
                        yield {"event": "delta", "text": "\n"}
                    final_text += status_text + "\n"
                    yield {"event": "delta", "text": status_text + "\n"}
                elif final_text and not final_text.endswith("\n"):
                    final_text += "\n"
                    yield {"event": "delta", "text": "\n"}
                yield {"event": "tool_start", "name": name, "arguments": args, "text": status_text}
                tool = self.tool_by_name.get(name)
                record = {"name": name or "unknown", "arguments": args, "result": None, "error": None}
                if not tool:
                    result = {"error": f"unknown tool: {name}"}
                    record["error"] = result["error"]
                else:
                    try:
                        result = await tool.run(args)
                        record["result"] = result
                        if name == "request_phone_action" and isinstance(result, dict) and result.get("type") == "phone_action":
                            pending_actions.append(result)
                        if name == "generate_image":
                            image_urls.extend(self._extract_image_urls_from_tool_result(result))
                    except Exception as exc:
                        result = {"error": str(exc)}
                        record["error"] = str(exc)
                records.append(record)
                yield {"event": "tool_result", "name": name, "arguments": args, "result": result, "error": record.get("error")}
                out_tool_messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id") or name or "tool_call",
                    "name": name,
                    "content": json.dumps(result, ensure_ascii=False),
                })
            messages.extend(out_tool_messages)
            if pending_actions:
                break
        else:
            final_text = final_text or "已达到 Agent 最大工具轮数，先停在这里。"

        parts: list[dict[str, Any]] = [{"type": "text", "text": final_text.strip()}]
        image_urls.extend(self._extract_image_urls(final_text))
        seen_urls = set()
        for url in image_urls:
            if url in seen_urls:
                continue
            seen_urls.add(url)
            parts.append({"type": "generated_image", "url": url})
        for action in pending_actions:
            parts.append(action)
        yield {
            "event": "final",
            "text": final_text.strip(),
            "parts": parts,
            "tool_calls": records,
            "pending_actions": pending_actions,
            "interrupted": bool(pending_actions),
            "raw_messages": messages,
        }

    def _build_graph(self):
        graph = StateGraph(ClynnAgentState)
        graph.add_node("model", self._model_node)
        graph.add_node("tools", self._tools_node)
        graph.add_edge(START, "model")
        graph.add_conditional_edges("model", self._route_after_model, {"tools": "tools", END: END})
        graph.add_conditional_edges("tools", self._route_after_tools, {"model": "model", END: END})
        return graph.compile()

    async def _model_node(self, state: ClynnAgentState) -> dict[str, Any]:
        if state.get("step", 0) >= state.get("max_steps", 6):
            return {"final_text": "已达到 Agent 最大工具轮数，先停在这里。", "step": state.get("step", 0) + 1}
        upstream = await self.llm.chat(state["messages"], tools=[tool.openai_schema() for tool in self.tools], model=state.get("model"))
        msg = extract_message(upstream)
        assistant_msg = {"role": "assistant", "content": extract_text_from_message(msg)}
        if msg.get("tool_calls"):
            assistant_msg["tool_calls"] = msg["tool_calls"]
            return {"messages": [assistant_msg], "step": state.get("step", 0) + 1}
        text = assistant_msg["content"] or ""
        return {"messages": [assistant_msg], "final_text": text, "step": state.get("step", 0) + 1}

    def _route_after_model(self, state: ClynnAgentState) -> str:
        if state.get("final_text"):
            return END
        last = state["messages"][-1]
        if last.get("tool_calls"):
            return "tools"
        return END

    async def _tools_node(self, state: ClynnAgentState) -> dict[str, Any]:
        last = state["messages"][-1]
        out_messages = []
        records = []
        pending_actions = []
        for call in last.get("tool_calls") or []:
            fn = call.get("function") or {}
            name = fn.get("name") or call.get("name")
            args = parse_tool_arguments(fn.get("arguments") or call.get("arguments"))
            tool = self.tool_by_name.get(name)
            record = {"name": name or "unknown", "arguments": args, "result": None, "error": None}
            if not tool:
                result = {"error": f"unknown tool: {name}"}
                record["error"] = result["error"]
            else:
                try:
                    result = await tool.run(args)
                    record["result"] = result
                    if name == "request_phone_action" and isinstance(result, dict) and result.get("type") == "phone_action":
                        pending_actions.append(result)
                except Exception as exc:
                    result = {"error": str(exc)}
                    record["error"] = str(exc)
            out_messages.append({
                "role": "tool",
                "tool_call_id": call.get("id") or name or "tool_call",
                "name": name,
                "content": json.dumps(result, ensure_ascii=False),
            })
            records.append(record)
        return {"messages": out_messages, "tool_calls": records, "pending_actions": pending_actions}

    def _route_after_tools(self, state: ClynnAgentState) -> str:
        if state.get("pending_actions"):
            return END
        if state.get("step", 0) >= state.get("max_steps", 6):
            return END
        return "model"

    def _last_assistant_text(self, messages: list[dict[str, Any]]) -> str:
        for msg in reversed(messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                return str(msg["content"])
        return ""

    def _extract_image_urls(self, text: str) -> list[str]:
        pattern = re.compile(r"https?://\S+?\.(?:png|jpg|jpeg|webp|gif)(?:\?\S*)?", re.IGNORECASE)
        return [m.group(0).rstrip(").,]}>") for m in pattern.finditer(text or "")]

    def _extract_image_urls_from_tool_result(self, result: Any) -> list[str]:
        urls: list[str] = []
        if isinstance(result, dict):
            for key in ("url", "image_url", "output_url"):
                value = result.get(key)
                if isinstance(value, str) and value.startswith(("http://", "https://")):
                    urls.append(value)
            data = result.get("data")
            if isinstance(data, list):
                for item in data:
                    urls.extend(self._extract_image_urls_from_tool_result(item))
            elif isinstance(data, dict):
                urls.extend(self._extract_image_urls_from_tool_result(data))
            images = result.get("images")
            if isinstance(images, list):
                for item in images:
                    urls.extend(self._extract_image_urls_from_tool_result(item))
        elif isinstance(result, list):
            for item in result:
                urls.extend(self._extract_image_urls_from_tool_result(item))
        elif isinstance(result, str):
            if result.startswith(("http://", "https://")):
                urls.append(result)
            urls.extend(self._extract_image_urls(result))
        return urls
