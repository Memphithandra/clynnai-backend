from fastapi.testclient import TestClient


def test_conversation_crud_list_rename_delete(monkeypatch, tmp_path):
    from clynnai_backend import main
    from clynnai_backend.agent.schema import AgentRunResult
    from clynnai_backend.config import get_settings

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'sessions.db'}")
    get_settings.cache_clear()

    class FakeRuntime:
        def __init__(self, settings, image_generate_fn):
            pass
        async def run(self, history, user_text, options):
            return AgentRunResult(text=f"回答 {user_text}", parts=[{"type": "text", "text": f"回答 {user_text}"}])

    monkeypatch.setattr(main, "ClynnAgentRuntime", FakeRuntime)
    client = TestClient(main.app)
    client.post("/api/conversations/message", json={"session_id": "s1", "message": "第一句"})
    client.post("/api/conversations/message", json={"session_id": "s2", "message": "第二句"})

    listed = client.get("/api/conversations").json()["sessions"]
    assert [row["id"] for row in listed] == ["s2", "s1"]

    rename = client.patch("/api/conversations/s1", json={"title": "新标题"})
    assert rename.status_code == 200
    assert rename.json()["session"]["title"] == "新标题"

    delete = client.delete("/api/conversations/s1")
    assert delete.status_code == 200
    assert delete.json()["deleted"] is True
    assert client.get("/api/conversations/s1/messages").json()["messages"] == []
    get_settings.cache_clear()


def test_phone_action_result_is_recorded_as_tool_message(monkeypatch, tmp_path):
    from clynnai_backend.config import get_settings
    from clynnai_backend import main

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'phone.db'}")
    get_settings.cache_clear()
    main.init_storage()
    session_id = main._ensure_session("phone-session", "phone")

    client = TestClient(main.app)
    resp = client.post(
        "/api/conversations/phone-session/phone-actions/action-1/result",
        json={"ok": True, "observation": "已经打开网页", "action": "open_url"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["message"]["role"] == "tool"
    assert body["message"]["parts"][0]["type"] == "phone_action_result"
    assert body["message"]["parts"][0]["action_id"] == "action-1"
    assert body["message"]["parts"][0]["ok"] is True

    messages = client.get(f"/api/conversations/{session_id}/messages").json()["messages"]
    assert messages[-1]["parts"][0]["observation"] == "已经打开网页"
    get_settings.cache_clear()
