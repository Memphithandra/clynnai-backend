from fastapi.testclient import TestClient


def test_provider_config_can_be_updated_and_used(monkeypatch, tmp_path):
    from clynnai_backend.config import get_settings
    from clynnai_backend import main

    monkeypatch.setenv("CLYNN_CONFIG_PATH", str(tmp_path / "provider_config.json"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'config-test.db'}")
    get_settings.cache_clear()

    client = TestClient(main.app)
    update = {
        "upstream_base_url": "http://provider.test/v1",
        "upstream_api_key": "provider-key",
        "default_model": "chat-model",
        "image_model": "image-model",
        "firecrawl_api_key": "fire-key",
        "firecrawl_base_url": "http://firecrawl.test",
    }
    resp = client.put("/api/admin/provider-config", json=update)

    assert resp.status_code == 200
    body = resp.json()
    assert body["config"]["upstream_base_url"] == "http://provider.test/v1"
    assert body["config"]["upstream_api_key_set"] is True
    assert "provider-key" not in resp.text

    get_settings.cache_clear()
    settings = get_settings()
    assert settings.upstream_base_url == "http://provider.test/v1"
    assert settings.upstream_api_key == "provider-key"
    assert settings.default_model == "chat-model"
    assert settings.image_model == "image-model"
    assert settings.firecrawl_api_key == "fire-key"
    assert settings.firecrawl_base_url == "http://firecrawl.test"

    get_settings.cache_clear()


def test_admin_webui_serves_provider_editor():
    from clynnai_backend.main import app

    client = TestClient(app)
    resp = client.get("/admin")

    assert resp.status_code == 200
    assert "ClynnAI Provider Config" in resp.text
    assert "UPSTREAM_BASE_URL" in resp.text
    assert "FIRECRAWL_API_KEY" in resp.text


def test_edit_user_message_truncates_later_messages_and_regenerates(monkeypatch, tmp_path):
    from clynnai_backend import main
    from clynnai_backend.agent.schema import AgentRunResult
    from clynnai_backend.config import get_settings

    db_path = tmp_path / "edit-test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()

    calls = []

    class FakeRuntime:
        def __init__(self, settings, image_generate_fn):
            pass

        async def run(self, history, user_text, options):
            calls.append({"history": history, "user_text": user_text, "options": options})
            return AgentRunResult(
                text=f"回答：{user_text}",
                parts=[{"type": "text", "text": f"回答：{user_text}"}],
            )

    monkeypatch.setattr(main, "ClynnAgentRuntime", FakeRuntime)
    client = TestClient(main.app)

    first = client.post("/api/conversations/message", json={"session_id": "edit-session", "message": "原问题"}).json()
    user_message_id = first["user_message"]["id"]
    client.post("/api/conversations/message", json={"session_id": "edit-session", "message": "第二个问题"})

    edit_resp = client.post(
        f"/api/conversations/edit-session/messages/{user_message_id}/edit",
        json={"message": "修改后的问题", "web_preferred": True},
    )

    assert edit_resp.status_code == 200
    body = edit_resp.json()
    assert body["edited_message"]["parts"][0]["text"] == "修改后的问题"
    assert body["assistant_message"]["parts"][0]["text"] == "回答：修改后的问题"
    assert calls[-1]["user_text"] == "修改后的问题"
    assert calls[-1]["options"].web_preferred is True

    messages = client.get("/api/conversations/edit-session/messages").json()["messages"]
    texts = [part.get("text") for msg in messages for part in msg["parts"] if part.get("type") == "text"]
    assert texts == ["修改后的问题", "回答：修改后的问题"]
    get_settings.cache_clear()
