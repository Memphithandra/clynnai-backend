from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from ..config import Settings


class OpenAICompatibleLLM:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.upstream_api_key:
            headers["Authorization"] = f"Bearer {self.settings.upstream_api_key}"
        return headers

    def _chat_payload(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, stream: bool = False, model: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"messages": messages}
        effective_model = (model or "").strip() or self.settings.default_model
        if effective_model:
            payload["model"] = effective_model
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if stream:
            payload["stream"] = True
        return payload

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, model: str | None = None) -> dict[str, Any]:
        payload = self._chat_payload(messages, tools=tools, stream=False, model=model)
        url = self.settings.upstream_base_url.rstrip("/") + "/chat/completions"
        async with httpx.AsyncClient(timeout=None) as client:
            resp = await client.post(url, json=payload, headers=self._headers())
        if resp.status_code >= 400:
            raise RuntimeError(f"upstream chat HTTP {resp.status_code}: {resp.text[:2000]}")
        return resp.json()

    async def stream_chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, model: str | None = None) -> AsyncIterator[dict[str, Any]]:
        payload = self._chat_payload(messages, tools=tools, stream=True, model=model)
        url = self.settings.upstream_base_url.rstrip("/") + "/chat/completions"
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", url, json=payload, headers=self._headers()) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    raise RuntimeError(f"upstream stream HTTP {resp.status_code}: {body.decode(errors='replace')[:2000]}")
                async for line in resp.aiter_lines():
                    line = (line or "").strip()
                    if not line:
                        continue
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    if line == "[DONE]":
                        break
                    try:
                        yield json.loads(line)
                    except Exception:
                        continue


def extract_message(upstream: dict[str, Any]) -> dict[str, Any]:
    return upstream.get("choices", [{}])[0].get("message", {}) or {}


def extract_text_from_message(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                chunks.append(str(item.get("text") or ""))
        return "\n".join(chunks)
    return ""


def parse_tool_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {"_raw": str(raw)}
