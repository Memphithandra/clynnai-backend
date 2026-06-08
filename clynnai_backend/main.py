from contextlib import asynccontextmanager
import base64
import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from . import __version__
from .agent import AgentRunOptions, ClynnAgentRuntime
from .agent.prompts import CLYNN_AGENT_SYSTEM_PROMPT
from .config import get_settings, redacted_provider_config, save_provider_config


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_storage()
    yield


app = FastAPI(title="ClynnAI Backend", version=__version__, lifespan=lifespan)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sqlite_path_from_database_url(database_url: str) -> Path | None:
    if not database_url.startswith("sqlite:"):
        return None
    raw = database_url.removeprefix("sqlite:")
    if raw.startswith("///"):
        return Path(raw)
    if raw.startswith("//"):
        return Path(raw[1:])
    return Path(raw)


def _connect() -> sqlite3.Connection | None:
    db_path = _sqlite_path_from_database_url(get_settings().database_url)
    if not db_path:
        return None
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(db_path)


def init_storage() -> None:
    settings = get_settings()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    con = _connect()
    if not con:
        return
    with con:
        con.execute(
            """
            create table if not exists uploads (
                id text primary key,
                filename text not null,
                content_type text,
                size integer not null,
                sha256 text not null,
                relative_path text not null,
                created_at text not null
            )
            """
        )
        con.execute(
            """
            create table if not exists sessions (
                id text primary key,
                title text not null,
                persona_name text,
                created_at text not null,
                updated_at text not null
            )
            """
        )
        con.execute(
            """
            create table if not exists messages (
                id text primary key,
                session_id text not null,
                role text not null,
                parts_json text not null,
                created_at text not null,
                foreign key(session_id) references sessions(id)
            )
            """
        )
        con.execute(
            """
            create table if not exists personas (
                name text primary key,
                prompt text not null,
                created_at text not null,
                updated_at text not null
            )
            """
        )
        cols = {row[1] for row in con.execute("pragma table_info(sessions)").fetchall()}
        if "persona_name" not in cols:
            con.execute("alter table sessions add column persona_name text")
        # jailbreak persona columns
        pcols = {row[1] for row in con.execute("pragma table_info(personas)").fetchall()}
        if "jailbreak_prompt" not in pcols:
            con.execute("alter table personas add column jailbreak_prompt text not null default ''")
        if "jailbreak_enabled" not in pcols:
            con.execute("alter table personas add column jailbreak_enabled integer not null default 0")
        if not con.execute("select name from personas where name=?", ("Clynn",)).fetchone():
            con.execute(
                "insert into personas(name,prompt,jailbreak_prompt,jailbreak_enabled,created_at,updated_at) values(?,?,?,?,?,?)",
                ("Clynn", "你是会话 Persona：Clynn。保持 Clynn 的专属 Agent 猫娘语气，温柔、可靠、执行力强，称呼用户为主人。", "", 0, _now(), _now()),
            )


@app.get("/health")
def health() -> dict[str, Any]:
    settings = get_settings()
    db_path = _sqlite_path_from_database_url(settings.database_url)
    return {
        "status": "ok",
        "version": __version__,
        "storage_dir_exists": settings.storage_dir.exists(),
        "database": "sqlite" if db_path else "external",
        "database_path_exists": db_path.exists() if db_path else None,
    }


@app.get("/version")
def version() -> dict[str, str]:
    return {"name": "ClynnAI Backend", "version": __version__}


@app.get("/admin", response_class=HTMLResponse)
def admin_webui() -> str:
    return """<!doctype html>
<html lang=\"zh-CN\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>ClynnAI Provider Config</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 920px; margin: 32px auto; padding: 0 16px; background: #111318; color: #f3f4f6; }
    label { display:block; margin-top: 14px; color: #cbd5e1; }
    input { width: 100%; box-sizing: border-box; padding: 10px; border-radius: 8px; border: 1px solid #334155; background: #0f172a; color: #f8fafc; }
    button { margin-top: 18px; padding: 10px 16px; border-radius: 8px; border: 0; background: #f59e0b; color: #111827; font-weight: 700; cursor: pointer; }
    pre { background: #0f172a; padding: 12px; border-radius: 8px; overflow:auto; }
    .hint { color: #94a3b8; }
  </style>
</head>
<body>
  <h1>ClynnAI Provider Config</h1>
  <p class=\"hint\">简易 WebUI：用于更改后端当前提供商、模型、Firecrawl 配置。密钥只显示是否已设置，不回显明文。</p>
  <form id=\"cfg\">
    <label>PUBLIC_BASE_URL<input name=\"public_base_url\" /></label>
    <label>UPSTREAM_BASE_URL<input name=\"upstream_base_url\" /></label>
    <label>UPSTREAM_API_KEY<input name=\"upstream_api_key\" type=\"password\" placeholder=\"留空则不修改\" /></label>
    <label>DEFAULT_MODEL<input name=\"default_model\" /></label>
    <label>IMAGE_MODEL<input name=\"image_model\" /></label>
    <label>FIRECRAWL_API_KEY<input name=\"firecrawl_api_key\" type=\"password\" placeholder=\"留空则不修改\" /></label>
    <label>FIRECRAWL_BASE_URL<input name=\"firecrawl_base_url\" /></label>
    <label>AGENT_MAX_STEPS<input name=\"agent_max_steps\" type=\"number\" min=\"1\" max=\"20\" /></label>
    <button type=\"submit\">保存配置</button>
  </form>
  <h2>当前配置</h2>
  <pre id=\"out\">loading...</pre>
<script>
const out = document.getElementById('out');
const form = document.getElementById('cfg');
async function load(){
  const r = await fetch('/api/admin/provider-config');
  const body = await r.json();
  out.textContent = JSON.stringify(body.config, null, 2);
  for (const [k,v] of Object.entries(body.config)) {
    const el = form.elements[k];
    if (!el || k.endsWith('_key_set')) continue;
    el.value = v ?? '';
  }
}
form.addEventListener('submit', async e => {
  e.preventDefault();
  const data = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    if ((el.name.endsWith('_api_key') || el.name === 'upstream_api_key') && !el.value) continue;
    data[el.name] = el.type === 'number' ? Number(el.value) : el.value;
  }
  const r = await fetch('/api/admin/provider-config', {method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify(data)});
  const body = await r.json();
  out.textContent = JSON.stringify(body.config ?? body, null, 2);
});
load();
</script>
</body>
</html>"""


@app.get("/api/admin/provider-config")
def get_provider_config() -> dict[str, Any]:
    return {"config": redacted_provider_config()}


@app.put("/api/admin/provider-config")
def update_provider_config(payload: dict[str, Any]) -> dict[str, Any]:
    save_provider_config(payload)
    get_settings.cache_clear()
    return {"config": redacted_provider_config()}


@app.get("/api/server-info")
def server_info() -> dict[str, Any]:
    settings = get_settings()
    return {
        "name": "ClynnAI",
        "app_name": "Clynn",
        "version": __version__,
        "public_base_url": settings.public_base_url,
        "upstream_base_url": settings.upstream_base_url,
        "default_model": settings.default_model,
        "image_model": settings.image_model,
        "features": {
            "unified_chat_window": True,
            "conversation_context": True,
            "llm_decides_image_generation": True,
            "agent_tool_use": True,
            "agent_web_search": True,
            "chat_completions": True,
            "image_generations": True,
            "models": True,
            "file_upload": True,
            "voice_reserved": True,
        },
    }


def _upstream_headers() -> dict[str, str]:
    settings = get_settings()
    headers = {"Content-Type": "application/json"}
    if settings.upstream_api_key:
        headers["Authorization"] = f"Bearer {settings.upstream_api_key}"
    return headers


async def _post_upstream(path: str, payload: dict[str, Any], timeout: float | None = None) -> Any:
    settings = get_settings()
    url = settings.upstream_base_url.rstrip("/") + path
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.post(url, json=payload, headers=_upstream_headers())
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"upstream request failed: {exc}") from exc
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail={"upstream_status": resp.status_code, "body": resp.text[:2000]})
    return resp.json()


@app.get("/api/models")
async def models() -> dict[str, Any]:
    settings = get_settings()
    url = settings.upstream_base_url.rstrip("/") + "/models"
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(url, headers=_upstream_headers())
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"upstream models request failed: {exc}") from exc
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail={"upstream_status": resp.status_code, "body": resp.text[:1000]})
    return resp.json()


def _ensure_session(session_id: str | None, title_seed: str = "新会话", persona_name: str | None = None) -> str:
    init_storage()
    sid = session_id or str(uuid.uuid4())
    con = _connect()
    if con:
        with con:
            row = con.execute("select id, persona_name from sessions where id=?", (sid,)).fetchone()
            if not row:
                con.execute("insert into sessions(id,title,persona_name,created_at,updated_at) values(?,?,?,?,?)", (sid, title_seed[:40] or "新会话", persona_name, _now(), _now()))
            elif persona_name and not row[1]:
                con.execute("update sessions set persona_name=?, updated_at=? where id=?", (persona_name, _now(), sid))
    return sid


def _save_message(session_id: str, role: str, parts: list[dict[str, Any]]) -> dict[str, Any]:
    msg = {"id": str(uuid.uuid4()), "session_id": session_id, "role": role, "parts": parts, "created_at": _now()}
    con = _connect()
    if con:
        with con:
            con.execute("insert into messages(id,session_id,role,parts_json,created_at) values(?,?,?,?,?)", (msg["id"], session_id, role, json.dumps(parts, ensure_ascii=False), msg["created_at"]))
            con.execute("update sessions set updated_at=? where id=?", (_now(), session_id))
    return msg


def _load_messages(session_id: str, limit: int = 30) -> list[dict[str, Any]]:
    con = _connect()
    if not con:
        return []
    rows = con.execute("select id,role,parts_json,created_at from messages where session_id=? order by created_at desc limit ?", (session_id, limit)).fetchall()
    out = []
    for mid, role, parts_json, created_at in reversed(rows):
        out.append({"id": mid, "session_id": session_id, "role": role, "parts": json.loads(parts_json), "created_at": created_at})
    return out


def _list_sessions(limit: int = 100) -> list[dict[str, Any]]:
    con = _connect()
    if not con:
        return []
    rows = con.execute(
        """
        select s.id, s.title, s.persona_name, s.created_at, s.updated_at, count(m.id) as message_count
        from sessions s
        left join messages m on m.session_id = s.id
        group by s.id
        order by s.updated_at desc
        limit ?
        """,
        (limit,),
    ).fetchall()
    return [
        {"id": sid, "title": title, "persona_name": persona_name, "created_at": created_at, "updated_at": updated_at, "message_count": message_count}
        for sid, title, persona_name, created_at, updated_at, message_count in rows
    ]


def _update_session(session_id: str, title: str | None = None, persona_name: str | None = None) -> dict[str, Any]:
    con = _connect()
    if not con:
        raise HTTPException(status_code=501, detail="session update currently requires sqlite database")
    updates: list[str] = []
    params: list[Any] = []
    if title is not None:
        title = title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="title cannot be empty")
        updates.append("title=?")
        params.append(title[:120])
    if persona_name is not None:
        persona_name = persona_name.strip()
        if persona_name and not _get_persona(persona_name):
            raise HTTPException(status_code=404, detail="persona not found")
        updates.append("persona_name=?")
        params.append(persona_name or None)
    if not updates:
        raise HTTPException(status_code=400, detail="nothing to update")
    updated_at = _now()
    updates.append("updated_at=?")
    params.append(updated_at)
    params.append(session_id)
    with con:
        cur = con.execute("update sessions set " + ", ".join(updates) + " where id=?", tuple(params))
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="session not found")
    row = con.execute("select id,title,persona_name,created_at,updated_at from sessions where id=?", (session_id,)).fetchone()
    sid, saved_title, saved_persona, created_at, saved_updated_at = row
    return {"id": sid, "title": saved_title, "persona_name": saved_persona, "created_at": created_at, "updated_at": saved_updated_at}


def _rename_session(session_id: str, title: str) -> dict[str, Any]:
    return _update_session(session_id, title=title)


def _delete_session(session_id: str) -> bool:
    con = _connect()
    if not con:
        raise HTTPException(status_code=501, detail="session delete currently requires sqlite database")
    with con:
        con.execute("delete from messages where session_id=?", (session_id,))
        cur = con.execute("delete from sessions where id=?", (session_id,))
    return cur.rowcount > 0


def _session_persona_name(session_id: str) -> str | None:
    con = _connect()
    if not con:
        return None
    row = con.execute("select persona_name from sessions where id=?", (session_id,)).fetchone()
    return row[0] if row else None


def _get_persona(name: str | None) -> dict[str, Any] | None:
    if not name:
        return None
    con = _connect()
    if not con:
        return None
    row = con.execute("select name,prompt,created_at,updated_at,jailbreak_prompt,jailbreak_enabled from personas where name=?", (name,)).fetchone()
    if not row:
        return None
    n, prompt, created_at, updated_at, jb_prompt, jb_enabled = row
    return {"name": n, "prompt": prompt, "created_at": created_at, "updated_at": updated_at, "jailbreak_prompt": jb_prompt or "", "jailbreak_enabled": bool(jb_enabled)}


def _list_personas() -> list[dict[str, Any]]:
    init_storage()
    con = _connect()
    if not con:
        return []
    rows = con.execute("select name,prompt,created_at,updated_at,jailbreak_prompt,jailbreak_enabled from personas order by name").fetchall()
    return [{"name": n, "prompt": prompt, "created_at": created_at, "updated_at": updated_at, "jailbreak_prompt": jb_prompt or "", "jailbreak_enabled": bool(jb_enabled)} for n, prompt, created_at, updated_at, jb_prompt, jb_enabled in rows]


def _upsert_persona(name: str, prompt: str, jailbreak_prompt: str = "", jailbreak_enabled: bool = False) -> dict[str, Any]:
    init_storage()
    name = name.strip()
    prompt = prompt.strip()
    jb_prompt = (jailbreak_prompt or "").strip()
    jb_enabled = bool(jailbreak_enabled)
    if not name:
        raise HTTPException(status_code=400, detail="persona name cannot be empty")
    if not prompt:
        raise HTTPException(status_code=400, detail="persona prompt cannot be empty")
    con = _connect()
    if not con:
        raise HTTPException(status_code=501, detail="persona presets require sqlite database")
    now = _now()
    with con:
        con.execute(
            "insert into personas(name,prompt,jailbreak_prompt,jailbreak_enabled,created_at,updated_at) values(?,?,?,?,?,?) on conflict(name) do update set prompt=excluded.prompt, jailbreak_prompt=excluded.jailbreak_prompt, jailbreak_enabled=excluded.jailbreak_enabled, updated_at=excluded.updated_at",
            (name[:120], prompt, jb_prompt, 1 if jb_enabled else 0, now, now),
        )
    return _get_persona(name[:120]) or {"name": name[:120], "prompt": prompt, "created_at": now, "updated_at": now, "jailbreak_prompt": jb_prompt, "jailbreak_enabled": jb_enabled}


def _delete_persona(name: str) -> bool:
    con = _connect()
    if not con:
        raise HTTPException(status_code=501, detail="persona presets require sqlite database")
    with con:
        con.execute("update sessions set persona_name=null where persona_name=?", (name,))
        cur = con.execute("delete from personas where name=?", (name,))
    return cur.rowcount > 0


def _load_message(session_id: str, message_id: str) -> dict[str, Any] | None:
    con = _connect()
    if not con:
        return None
    row = con.execute("select id,role,parts_json,created_at from messages where session_id=? and id=?", (session_id, message_id)).fetchone()
    if not row:
        return None
    mid, role, parts_json, created_at = row
    return {"id": mid, "session_id": session_id, "role": role, "parts": json.loads(parts_json), "created_at": created_at}


def _messages_before(session_id: str, created_at: str, limit: int = 30) -> list[dict[str, Any]]:
    con = _connect()
    if not con:
        return []
    rows = con.execute(
        "select id,role,parts_json,created_at from messages where session_id=? and created_at < ? order by created_at desc limit ?",
        (session_id, created_at, limit),
    ).fetchall()
    out = []
    for mid, role, parts_json, msg_created_at in reversed(rows):
        out.append({"id": mid, "session_id": session_id, "role": role, "parts": json.loads(parts_json), "created_at": msg_created_at})
    return out


def _replace_message_and_truncate_after(session_id: str, message_id: str, parts: list[dict[str, Any]]) -> dict[str, Any]:
    con = _connect()
    if not con:
        raise HTTPException(status_code=501, detail="message editing currently requires sqlite database")
    existing = _load_message(session_id, message_id)
    if not existing:
        raise HTTPException(status_code=404, detail="message not found")
    if existing["role"] != "user":
        raise HTTPException(status_code=400, detail="only user messages can be edited")
    edited_at = _now()
    with con:
        con.execute("delete from messages where session_id=? and created_at > ?", (session_id, existing["created_at"]))
        con.execute("update messages set parts_json=?, created_at=? where session_id=? and id=?", (json.dumps(parts, ensure_ascii=False), edited_at, session_id, message_id))
        con.execute("update sessions set updated_at=? where id=?", (_now(), session_id))
    return {"id": message_id, "session_id": session_id, "role": "user", "parts": parts, "created_at": edited_at}


def _parts_to_text(parts: list[dict[str, Any]]) -> str:
    chunks = []
    for part in parts:
        typ = part.get("type")
        if typ == "text":
            chunks.append(part.get("text", ""))
        elif typ == "file":
            chunks.append(_file_part_context(part))
        elif typ == "generated_image":
            chunks.append(f"[生成图片: {part.get('url') or part.get('file_id')}]")
    return "\n".join(x for x in chunks if x)




def _upload_row(file_id: str) -> tuple[str, str | None, str] | None:
    con = _connect()
    if not con or not file_id:
        return None
    row = con.execute("select filename, content_type, relative_path from uploads where id=?", (file_id,)).fetchone()
    return row if row else None


def _is_textual_upload(filename: str, content_type: str | None) -> bool:
    ct = (content_type or "").lower()
    name = (filename or "").lower()
    if ct.startswith("text/") or ct in {"application/json", "application/xml", "application/javascript", "application/x-yaml"}:
        return True
    return any(name.endswith(ext) for ext in [".txt", ".md", ".json", ".yaml", ".yml", ".csv", ".xml", ".py", ".java", ".kt", ".js", ".ts", ".html", ".css", ".log"])


def _is_image_upload(filename: str, content_type: str | None) -> bool:
    ct = (content_type or "").lower()
    name = (filename or "").lower()
    return ct.startswith("image/") or any(name.endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".webp", ".gif"])


def _upload_public_url(file_id: str, fallback_url: str | None = None) -> str:
    if fallback_url:
        return fallback_url
    settings = get_settings()
    return f"{settings.public_base_url.rstrip('/')}/api/uploads/{file_id}"


def _file_part_context(part: dict[str, Any], max_chars: int = 20000) -> str:
    file_id = str(part.get("file_id") or "")
    filename = str(part.get("filename") or file_id or "附件")
    row = _upload_row(file_id)
    if not row:
        return f"[附件: {filename} URL: {part.get('url') or ''}]"
    stored_name, content_type, rel = row
    settings = get_settings()
    abs_path = settings.storage_dir / rel
    if _is_textual_upload(stored_name, content_type) and abs_path.exists():
        raw = abs_path.read_bytes()[: max_chars + 4096]
        text = raw.decode("utf-8", errors="replace")[:max_chars]
        return f"[文本附件: {stored_name} content_type={content_type or 'unknown'}]\n{text}"
    if _is_image_upload(stored_name, content_type):
        return f"[图片附件: {stored_name} URL: {_upload_public_url(file_id, part.get('url'))}]"
    return f"[附件: {stored_name} content_type={content_type or 'unknown'} URL: {_upload_public_url(file_id, part.get('url'))}]"


def _parts_to_model_content(parts: list[dict[str, Any]]) -> str | list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    text_chunks: list[str] = []
    for part in parts:
        typ = part.get("type")
        if typ == "text":
            text_chunks.append(str(part.get("text") or ""))
        elif typ == "file":
            file_id = str(part.get("file_id") or "")
            filename = str(part.get("filename") or file_id or "附件")
            row = _upload_row(file_id)
            if row and _is_image_upload(row[0], row[1]):
                if text_chunks:
                    content.append({"type": "text", "text": "\n".join(x for x in text_chunks if x)})
                    text_chunks.clear()
                content.append({"type": "text", "text": f"下面是一张主人上传的图片：{filename}"})
                content.append({"type": "image_url", "image_url": {"url": _upload_public_url(file_id, part.get("url"))}})
            else:
                text_chunks.append(_file_part_context(part))
        elif typ == "generated_image":
            text_chunks.append(f"[生成图片: {part.get('url') or part.get('file_id')}]")
    if text_chunks:
        content.append({"type": "text", "text": "\n".join(x for x in text_chunks if x)})
    if not content:
        return ""
    if len(content) == 1 and content[0].get("type") == "text":
        return content[0].get("text") or ""
    return content

def _extract_payload_text(payload: dict[str, Any]) -> str:
    if payload.get("text"):
        return str(payload.get("text"))
    if payload.get("message"):
        return str(payload.get("message"))
    return _parts_to_text(payload.get("parts") or [])


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


@app.get("/api/personas")
def list_personas() -> dict[str, Any]:
    return {"personas": _list_personas()}


@app.put("/api/personas/{name}")
def upsert_persona(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"persona": _upsert_persona(
        name,
        str(payload.get("prompt") or ""),
        jailbreak_prompt=str(payload.get("jailbreak_prompt") or ""),
        jailbreak_enabled=payload.get("jailbreak_enabled") or payload.get("jb_enabled") or False,
    )}


@app.delete("/api/personas/{name}")
def delete_persona(name: str) -> dict[str, Any]:
    return {"name": name, "deleted": _delete_persona(name)}


def _save_generated_image_bytes(image_bytes: bytes, extension: str = "png") -> dict[str, Any]:
    settings = get_settings()
    init_storage()
    file_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    ext = (extension or "png").lower().lstrip(".")
    if ext not in {"png", "jpg", "jpeg", "webp"}:
        ext = "png"
    content_type = "image/jpeg" if ext in {"jpg", "jpeg"} else f"image/{ext}"
    filename = f"generated-{file_id}.{ext}"
    rel_dir = Path("generated") / now.strftime("%Y") / now.strftime("%m")
    abs_dir = settings.storage_dir / rel_dir
    abs_dir.mkdir(parents=True, exist_ok=True)
    rel_path = rel_dir / filename
    abs_path = settings.storage_dir / rel_path
    abs_path.write_bytes(image_bytes)
    sha = hashlib.sha256(image_bytes).hexdigest()
    con = _connect()
    if con:
        with con:
            con.execute(
                "insert into uploads(id, filename, content_type, size, sha256, relative_path, created_at) values(?,?,?,?,?,?,?)",
                (file_id, filename, content_type, len(image_bytes), sha, rel_path.as_posix(), now.isoformat()),
            )
    return {
        "file_id": file_id,
        "filename": filename,
        "content_type": content_type,
        "size": len(image_bytes),
        "sha256": sha,
        "relative_path": rel_path.as_posix(),
        "url": f"{settings.public_base_url.rstrip('/')}/api/uploads/{file_id}",
    }


def _augment_image_generation_response(upstream: Any) -> Any:
    """Convert OpenAI image b64_json results into locally served URLs for Android display."""
    if not isinstance(upstream, dict):
        return upstream
    data = upstream.get("data")
    if not isinstance(data, list):
        return upstream
    for item in data:
        if not isinstance(item, dict):
            continue
        raw_b64 = item.get("b64_json")
        if not isinstance(raw_b64, str) or not raw_b64.strip():
            continue
        clean = raw_b64.strip()
        if clean.startswith("data:") and "," in clean:
            clean = clean.split(",", 1)[1]
        try:
            image_bytes = base64.b64decode(clean, validate=False)
        except Exception as exc:
            item.setdefault("error", f"failed to decode b64_json: {exc}")
            continue
        if not image_bytes:
            item.setdefault("error", "decoded b64_json is empty")
            continue
        saved = _save_generated_image_bytes(image_bytes, extension="png")
        item["url"] = saved["url"]
        item["image_url"] = saved["url"]
        item["local_file_id"] = saved["file_id"]
        item["filename"] = saved["filename"]
        item["sha256"] = saved["sha256"]
        item["size"] = saved["size"]
        # Keep response lightweight for tool loops / Android JSON parsing.
        item.pop("b64_json", None)
    return upstream


@app.post("/api/chat/completions")
async def chat_completions(payload: dict[str, Any]) -> Any:
    settings = get_settings()
    data = dict(payload)
    if settings.default_model and not data.get("model"):
        data["model"] = settings.default_model
    return await _post_upstream("/chat/completions", data, timeout=None)


@app.post("/api/images/generations")
async def image_generations(payload: dict[str, Any]) -> Any:
    settings = get_settings()
    data = dict(payload)
    if settings.image_model and not data.get("model"):
        data["model"] = settings.image_model
    upstream = await _post_upstream("/images/generations", data, timeout=None)
    return _augment_image_generation_response(upstream)




async def _stream_upstream_chat(messages: list[dict[str, Any]], settings, include_reasoning: bool = True):
    payload: dict[str, Any] = {"messages": messages, "stream": True}
    if settings.default_model:
        payload["model"] = settings.default_model
    # Ask compatible providers for reasoning when supported. Providers that ignore
    # this field still stream normal content.
    if include_reasoning:
        payload["reasoning_effort"] = "medium"
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if settings.upstream_api_key:
        headers["Authorization"] = f"Bearer {settings.upstream_api_key}"
    url = settings.upstream_base_url.rstrip("/") + "/chat/completions"
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as resp:
            if resp.status_code >= 400:
                body = await resp.aread()
                raise RuntimeError(f"upstream stream HTTP {resp.status_code}: {body.decode('utf-8', 'ignore')[:2000]}")
            async for line in resp.aiter_lines():
                if not line:
                    continue
                if line.startswith("data:"):
                    data = line[5:].strip()
                else:
                    data = line.strip()
                if not data:
                    continue
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except Exception:
                    continue
                delta = (chunk.get("choices") or [{}])[0].get("delta") or {}
                reasoning = (
                    delta.get("reasoning_content")
                    or delta.get("reasoning")
                    or delta.get("reasoning_text")
                    or delta.get("thinking")
                    or ""
                )
                content = delta.get("content") or ""
                if isinstance(reasoning, list):
                    reasoning = "".join(str(x.get("text") if isinstance(x, dict) else x) for x in reasoning)
                if isinstance(content, list):
                    content = "".join(str(x.get("text") if isinstance(x, dict) else x) for x in content)
                extra_reasoning, clean_content = _split_reasoning_from_content(str(content or ""))
                merged_reasoning = str(reasoning or "") + ("\n" + extra_reasoning if extra_reasoning and reasoning else extra_reasoning)
                yield merged_reasoning, clean_content


def _sse(event: str, data: dict[str, Any]) -> str:
    return "event: " + event + "\n" + "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"


def _split_reasoning_from_content(text: str) -> tuple[str, str]:
    """Extract provider-emitted reasoning accidentally placed in content.

    Some OpenAI-compatible providers stream reasoning both as reasoning_content and
    inside content using <think>...</think> or a leading 思考过程 block. Keep that
    text in the reasoning channel only so Android doesn't show it twice.
    """
    if not text:
        return "", ""
    reasoning_parts: list[str] = []
    clean = text
    def repl(match):
        reasoning_parts.append(match.group(1))
        return ""
    clean = re.sub(r"(?is)<think>\s*(.*?)\s*</think>", repl, clean)
    clean = re.sub(r"(?is)<reasoning>\s*(.*?)\s*</reasoning>", repl, clean)
    m = re.match(r"(?s)^\s*(?:思考过程|reasoning|Reasoning)[:：]\s*(.*?)(?:\n\s*\n|(?:最终回答|正式回答|answer|Answer)[:：])\s*(.*)$", clean)
    if m:
        reasoning_parts.append(m.group(1).strip())
        clean = m.group(2)
    return "\n".join(x.strip() for x in reasoning_parts if x and x.strip()), clean


def _direct_phone_action_for_text(text: str) -> dict[str, Any] | None:
    """Fast path for obvious low-risk phone-control commands."""
    normalized = re.sub(r"\s+", "", str(text or "").lower())
    if not normalized:
        return None
    home_phrases = [
        "回到手机主屏幕", "回到主屏幕", "返回主屏幕", "回主屏幕",
        "返回桌面", "回到桌面", "回桌面", "去桌面", "主屏幕",
        "home", "homescreen", "launcher",
    ]
    if any(p in normalized for p in home_phrases):
        return {
            "type": "phone_action",
            "action_id": "act_" + uuid.uuid4().hex[:12],
            "backend": "shizuku",
            "action": "home",
            "params": {},
            "risk": "safe",
            "requires_confirmation": False,
            "reason": "用户要求回到手机主屏幕",
        }
    return None


@app.post("/api/conversations/message")
async def conversation_message(payload: dict[str, Any]) -> dict[str, Any]:
    """Unified ClynnAPP Agent endpoint.

    The backend now owns ClynnAPP's embedded Agent Core. APP messages enter a
    LangGraph-powered multi-step loop with Firecrawl, image generation, and
    phone-action tools; no Hermes CLI or other Agent process is required.
    """
    settings = get_settings()
    parts = payload.get("parts") or [{"type": "text", "text": _extract_payload_text(payload)}]
    user_text = _parts_to_text(parts)
    persona_name = payload.get("persona_name") or payload.get("persona")
    session_id = _ensure_session(payload.get("session_id"), _parts_to_text(parts) or "新会话", persona_name=persona_name)
    persona = _get_persona(persona_name or _session_persona_name(session_id))
    history = _load_messages(session_id, limit=30)
    user_msg = _save_message(session_id, "user", parts)
    direct_phone_action = _direct_phone_action_for_text(_parts_to_text(parts)) if payload.get("phone_control") else None
    if direct_phone_action:
        assistant_msg = _save_message(session_id, "assistant", [{"type": "text", "text": "好的，正在回到手机主屏幕。"}])
        return {
            "session_id": session_id,
            "user_message": user_msg,
            "assistant_message": assistant_msg,
            "agent": True,
            "agent_core": "clynn-direct-phone-action",
            "interrupted": False,
            "tool_calls": [],
            "pending_actions": [direct_phone_action],
        }

    async def _agent_image_generate(image_payload: dict[str, Any]) -> Any:
        data = dict(image_payload)
        if settings.image_model and not data.get("model"):
            data["model"] = settings.image_model
        return _augment_image_generation_response(await _post_upstream("/images/generations", data, timeout=None))

    runtime = ClynnAgentRuntime(settings=settings, image_generate_fn=_agent_image_generate)
    options = AgentRunOptions(
        web_preferred=(
            _truthy(payload.get("web_preferred"))
            or _truthy(payload.get("agent_web_search"))
            or _truthy(payload.get("web_search"))
        ),
        allow_image=_truthy(payload.get("allow_image", True)),
        phone_control=payload.get("phone_control") or {},
        max_steps=int(payload.get("max_steps") or settings.agent_max_steps),
        model=(str(payload.get("model") or "").strip() or None),
        persona_name=persona.get("name") if persona else None,
        persona_prompt=persona.get("prompt") if persona else None,
        jailbreak_prompt=persona.get("jailbreak_prompt") if persona else None,
        jailbreak_enabled=bool(persona.get("jailbreak_enabled", False)) if persona else False,
    )
    history_messages = [
        {
            "role": msg["role"] if msg["role"] in {"user", "assistant", "system"} else "user",
            "content": _parts_to_model_content(msg["parts"]),
        }
        for msg in history
    ]
    result = await runtime.run(history=history_messages, user_text=user_text, options=options)
    assistant_parts = result.parts or [{"type": "text", "text": result.text}]
    assistant_msg = _save_message(session_id, "assistant", assistant_parts)
    return {
        "session_id": session_id,
        "user_message": user_msg,
        "assistant_message": assistant_msg,
        "agent": True,
        "agent_core": "clynn-langgraph",
        "interrupted": result.interrupted,
        "tool_calls": [call.model_dump() for call in result.tool_calls],
        "pending_actions": [action.model_dump() for action in result.pending_actions],
    }




@app.post("/api/conversations/message/stream")
async def conversation_message_stream(payload: dict[str, Any]) -> StreamingResponse:
    """Agent SSE endpoint: streams model deltas, tool starts/results, and final answer."""
    async def gen():
        settings = get_settings()
        try:
            parts = payload.get("parts") or [{"type": "text", "text": _extract_payload_text(payload)}]
            user_text = _parts_to_model_content(parts)
            persona_name = payload.get("persona_name") or payload.get("persona")
            session_id = _ensure_session(payload.get("session_id"), _parts_to_text(parts) or "新会话", persona_name=persona_name)
            persona = _get_persona(persona_name or _session_persona_name(session_id))
            history = _load_messages(session_id, limit=30)
            user_msg = _save_message(session_id, "user", parts)
            yield _sse("user", {"session_id": session_id, "user_message": user_msg})
            direct_phone_action = _direct_phone_action_for_text(_parts_to_text(parts)) if payload.get("phone_control") else None
            if direct_phone_action:
                final_text = "好的，正在回到手机主屏幕。"
                assistant_msg = _save_message(session_id, "assistant", [{"type": "text", "text": final_text}])
                yield _sse("delta", {"text": final_text})
                yield _sse("done", {
                    "session_id": session_id,
                    "assistant_message": assistant_msg,
                    "reasoning": "",
                    "text": final_text,
                    "agent": True,
                    "agent_core": "clynn-direct-phone-action",
                    "tool_calls": [],
                    "pending_actions": [direct_phone_action],
                    "interrupted": False,
                })
                return

            async def _agent_image_generate(image_payload: dict[str, Any]) -> Any:
                data = dict(image_payload)
                if settings.image_model and not data.get("model"):
                    data["model"] = settings.image_model
                return _augment_image_generation_response(await _post_upstream("/images/generations", data, timeout=None))

            runtime = ClynnAgentRuntime(settings=settings, image_generate_fn=_agent_image_generate)
            options = AgentRunOptions(
                web_preferred=True,
                allow_image=_truthy(payload.get("allow_image", True)),
                phone_control=payload.get("phone_control") or {},
                max_steps=int(payload.get("max_steps") or settings.agent_max_steps),
                persona_name=persona.get("name") if persona else None,
                persona_prompt=persona.get("prompt") if persona else None,
                jailbreak_prompt=persona.get("jailbreak_prompt") if persona else None,
                jailbreak_enabled=bool(persona.get("jailbreak_enabled", False)) if persona else False,
            )
            history_messages = [
                {
                    "role": msg["role"] if msg["role"] in {"user", "assistant", "system"} else "user",
                    "content": _parts_to_model_content(msg["parts"]),
                }
                for msg in history
            ]
            text_chunks: list[str] = []
            reasoning_chunks: list[str] = []
            final_payload: dict[str, Any] | None = None
            async for event in runtime.run_stream(history=history_messages, user_text=user_text, options=options):
                et = event.get("event")
                if et == "delta":
                    text = str(event.get("text") or "")
                    if text:
                        text_chunks.append(text)
                        yield _sse("delta", {"text": text})
                elif et == "reasoning_delta":
                    text = str(event.get("text") or "")
                    if text:
                        reasoning_chunks.append(text)
                        yield _sse("reasoning_delta", {"text": text})
                elif et == "tool_start":
                    yield _sse("tool_start", {"name": event.get("name"), "arguments": event.get("arguments") or {}, "text": event.get("text") or ""})
                elif et == "tool_result":
                    yield _sse("tool_result", {"name": event.get("name"), "arguments": event.get("arguments") or {}, "result": event.get("result"), "error": event.get("error")})
                elif et == "final":
                    final_payload = event
            final_text = (final_payload or {}).get("text") or "".join(text_chunks).strip() or "[空文本]"
            assistant_parts = (final_payload or {}).get("parts") or [{"type": "text", "text": final_text}]
            reasoning_text = "".join(reasoning_chunks).strip()
            if reasoning_text and not any(p.get("type") == "reasoning" for p in assistant_parts if isinstance(p, dict)):
                assistant_parts.insert(0, {"type": "reasoning", "text": reasoning_text})
            assistant_msg = _save_message(session_id, "assistant", assistant_parts)
            yield _sse("done", {
                "session_id": session_id,
                "assistant_message": assistant_msg,
                "reasoning": reasoning_text,
                "text": final_text,
                "agent": True,
                "agent_core": "clynn-stream-runtime",
                "tool_calls": (final_payload or {}).get("tool_calls") or [],
                "pending_actions": (final_payload or {}).get("pending_actions") or [],
                "interrupted": bool((final_payload or {}).get("interrupted")),
            })
        except Exception as exc:
            yield _sse("error", {"error": str(exc)})
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/conversations")
def list_conversations(limit: int = 100) -> dict[str, Any]:
    init_storage()
    return {"sessions": _list_sessions(limit=limit)}


@app.patch("/api/conversations/{session_id}")
def rename_conversation(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    title = payload.get("title") if "title" in payload else None
    persona_name = payload.get("persona_name") if "persona_name" in payload else None
    return {"session": _update_session(session_id, title=str(title) if title is not None else None, persona_name=str(persona_name) if persona_name is not None else None)}


@app.delete("/api/conversations/{session_id}")
def delete_conversation(session_id: str) -> dict[str, Any]:
    return {"session_id": session_id, "deleted": _delete_session(session_id)}


@app.post("/api/conversations/{session_id}/phone-actions/{action_id}/result")
def phone_action_result(session_id: str, action_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    _ensure_session(session_id, "手机动作")
    part = {
        "type": "phone_action_result",
        "action_id": action_id,
        "ok": _truthy(payload.get("ok")),
        "action": payload.get("action"),
        "observation": payload.get("observation") or payload.get("result") or "",
        "result": payload.get("result"),
        "error": payload.get("error"),
    }
    message = _save_message(session_id, "tool", [part])
    return {"session_id": session_id, "action_id": action_id, "message": message}


@app.get("/api/conversations/{session_id}/messages")
def conversation_messages(session_id: str) -> dict[str, Any]:
    return {"session_id": session_id, "messages": _load_messages(session_id, limit=200)}


@app.post("/api/conversations/{session_id}/messages/{message_id}/edit")
async def edit_conversation_message(session_id: str, message_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    new_parts = payload.get("parts") or [{"type": "text", "text": _extract_payload_text(payload)}]
    user_text = _parts_to_text(new_parts)
    if not user_text.strip():
        raise HTTPException(status_code=400, detail="edited message cannot be empty")
    existing = _load_message(session_id, message_id)
    if not existing:
        raise HTTPException(status_code=404, detail="message not found")
    history = _messages_before(session_id, existing["created_at"], limit=30)
    edited_msg = _replace_message_and_truncate_after(session_id, message_id, new_parts)

    settings = get_settings()
    persona_name = payload.get("persona_name") or payload.get("persona") or _session_persona_name(session_id)
    persona = _get_persona(persona_name)

    async def _agent_image_generate(image_payload: dict[str, Any]) -> Any:
        data = dict(image_payload)
        if settings.image_model and not data.get("model"):
            data["model"] = settings.image_model
        return _augment_image_generation_response(await _post_upstream("/images/generations", data, timeout=None))

    runtime = ClynnAgentRuntime(settings=settings, image_generate_fn=_agent_image_generate)
    options = AgentRunOptions(
        web_preferred=(
            _truthy(payload.get("web_preferred"))
            or _truthy(payload.get("agent_web_search"))
            or _truthy(payload.get("web_search"))
        ),
        allow_image=_truthy(payload.get("allow_image", True)),
        phone_control=payload.get("phone_control") or {},
        max_steps=int(payload.get("max_steps") or settings.agent_max_steps),
        model=(str(payload.get("model") or "").strip() or None),
        persona_name=persona.get("name") if persona else None,
        persona_prompt=persona.get("prompt") if persona else None,
        jailbreak_prompt=persona.get("jailbreak_prompt") if persona else None,
        jailbreak_enabled=bool(persona.get("jailbreak_enabled", False)) if persona else False,
    )
    history_messages = [
        {
            "role": msg["role"] if msg["role"] in {"user", "assistant", "system"} else "user",
            "content": _parts_to_model_content(msg["parts"]),
        }
        for msg in history
    ]
    result = await runtime.run(history=history_messages, user_text=user_text, options=options)
    assistant_parts = result.parts or [{"type": "text", "text": result.text}]
    assistant_msg = _save_message(session_id, "assistant", assistant_parts)
    return {
        "session_id": session_id,
        "edited_message": edited_msg,
        "assistant_message": assistant_msg,
        "agent": True,
        "agent_core": "clynn-langgraph",
        "interrupted": result.interrupted,
        "tool_calls": [call.model_dump() for call in result.tool_calls],
        "pending_actions": [action.model_dump() for action in result.pending_actions],
    }


@app.post("/api/asr/transcribe")
async def asr_transcribe(file: UploadFile = File(...)) -> dict[str, Any]:
    settings = get_settings()
    if not settings.upstream_api_key:
        raise HTTPException(status_code=400, detail="ASR unavailable: upstream_api_key is not configured")
    filename = file.filename or "voice.m4a"
    content_type = file.content_type or "audio/mp4"
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="ASR upload is empty")
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="ASR upload too large; max 25MB")
    base = (settings.upstream_base_url or "").rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    if base.endswith("/responses"):
        base = base[: -len("/responses")]
    if base.endswith("/v1"):
        audio_url = base + "/audio/transcriptions"
    else:
        audio_url = base + "/v1/audio/transcriptions"
    model = settings.asr_model or "whisper-1"
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                audio_url,
                headers={"Authorization": f"Bearer {settings.upstream_api_key}"},
                data={"model": model, "language": "zh", "response_format": "json"},
                files={"file": (filename, data, content_type)},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"ASR upstream request failed: {exc}") from exc
    if resp.status_code < 200 or resp.status_code >= 300:
        raise HTTPException(status_code=502, detail=f"ASR upstream HTTP {resp.status_code}: {resp.text[:1000]}")
    try:
        payload = resp.json()
    except Exception:
        payload = {"text": resp.text}
    text = str(payload.get("text") or payload.get("transcript") or "").strip()
    return {
        "text": text,
        "model": model,
        "filename": filename,
        "content_type": content_type,
        "size": len(data),
        "upstream": payload,
    }


@app.post("/api/uploads")
async def upload(file: UploadFile = File(...)) -> dict[str, Any]:
    settings = get_settings()
    init_storage()
    file_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    safe_name = Path(file.filename or "upload.bin").name
    rel_dir = Path("files") / now.strftime("%Y") / now.strftime("%m")
    abs_dir = settings.storage_dir / rel_dir
    abs_dir.mkdir(parents=True, exist_ok=True)
    rel_path = rel_dir / f"{file_id}_{safe_name}"
    abs_path = settings.storage_dir / rel_path
    h = hashlib.sha256()
    size = 0
    with abs_path.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            h.update(chunk)
            out.write(chunk)
    sha = h.hexdigest()
    con = _connect()
    if con:
        with con:
            con.execute(
                "insert into uploads(id, filename, content_type, size, sha256, relative_path, created_at) values(?,?,?,?,?,?,?)",
                (file_id, safe_name, file.content_type, size, sha, rel_path.as_posix(), now.isoformat()),
            )
    return {
        "file_id": file_id,
        "filename": safe_name,
        "content_type": file.content_type,
        "size": size,
        "sha256": sha,
        "relative_path": rel_path.as_posix(),
        "url": f"{settings.public_base_url.rstrip('/')}/api/uploads/{file_id}",
    }


@app.get("/api/uploads/{file_id}")
def get_upload(file_id: str) -> FileResponse:
    settings = get_settings()
    con = _connect()
    if not con:
        raise HTTPException(status_code=501, detail="upload lookup currently requires sqlite database")
    row = con.execute("select filename, content_type, relative_path from uploads where id=?", (file_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="file not found")
    filename, content_type, rel = row
    abs_path = settings.storage_dir / rel
    if not abs_path.exists():
        raise HTTPException(status_code=404, detail="file missing on storage")
    return FileResponse(abs_path, media_type=content_type, filename=filename)
