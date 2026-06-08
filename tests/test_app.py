from fastapi.testclient import TestClient
import pytest
import respx
from httpx import Response

from clynnai_backend.main import app


def test_health_returns_ok():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_server_info_does_not_expose_upstream_api_key():
    client = TestClient(app)
    resp = client.get("/api/server-info")
    assert resp.status_code == 200
    text = resp.text.lower()
    assert "upstream_api_key" not in text
    assert "lyn081027" not in text


@respx.mock
def test_image_generation_forwards_to_unified_upstream(monkeypatch):
    monkeypatch.setenv("UPSTREAM_BASE_URL", "http://upstream.test/v1")
    monkeypatch.setenv("UPSTREAM_API_KEY", "test-key")
    monkeypatch.setenv("IMAGE_MODEL", "test-image-model")
    from clynnai_backend.config import get_settings
    get_settings.cache_clear()

    route = respx.post("http://upstream.test/v1/images/generations").mock(
        return_value=Response(200, json={"data": [{"url": "http://img.test/1.png"}]})
    )
    client = TestClient(app)
    resp = client.post("/api/images/generations", json={"prompt": "a silver cat bell logo"})

    assert resp.status_code == 200
    assert resp.json()["data"][0]["url"] == "http://img.test/1.png"
    assert route.called
    sent = route.calls[0].request
    assert sent.headers["authorization"] == "Bearer test-key"
    assert '"model":"test-image-model"' in sent.content.decode()
    get_settings.cache_clear()


def test_conversation_message_uses_embedded_clynn_agent_core(monkeypatch, tmp_path):
    from clynnai_backend import main
    from clynnai_backend.agent.schema import AgentRunResult, AgentToolCallRecord
    from clynnai_backend.config import get_settings

    captured = {}

    class FakeClynnAgentRuntime:
        def __init__(self, settings, image_generate_fn):
            captured["settings"] = settings
            captured["image_generate_fn"] = image_generate_fn

        async def run(self, history, user_text, options):
            captured["history"] = history
            captured["user_text"] = user_text
            captured["options"] = options
            return AgentRunResult(
                text="ClynnAPP Agent Core 已用 Firecrawl 工具返回结果。",
                parts=[{"type": "text", "text": "ClynnAPP Agent Core 已用 Firecrawl 工具返回结果。"}],
                tool_calls=[AgentToolCallRecord(name="firecrawl_search", arguments={"query": user_text}, result={"ok": True})],
            )

    db_path = tmp_path / "agent-test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    monkeypatch.setattr(main, "ClynnAgentRuntime", FakeClynnAgentRuntime)

    client = TestClient(app)
    resp = client.post(
        "/api/conversations/message",
        json={"session_id": "agent-test", "message": "联网搜索今天的 AI 新闻", "web_preferred": True},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["agent"] is True
    assert body["agent_core"] == "clynn-langgraph"
    assert captured["options"].web_preferred is True
    assert captured["user_text"] == "联网搜索今天的 AI 新闻"
    assert body["assistant_message"]["parts"][0]["text"] == "ClynnAPP Agent Core 已用 Firecrawl 工具返回结果。"
    assert body["tool_calls"][0]["name"] == "firecrawl_search"
    get_settings.cache_clear()
