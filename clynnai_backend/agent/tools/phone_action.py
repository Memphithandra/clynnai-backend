from __future__ import annotations

from typing import Any
import uuid

from .base import ClynnTool
from ..schema import PhoneActionRequest


class PhoneActionTool(ClynnTool):
    name = "request_phone_action"
    description = (
        "Ask the Android app to execute a bounded phone action locally through Shizuku. "
        "For returning to the phone launcher/home screen, use action='home' with risk='safe'. "
        "Supported actions: status, read_screen, screenshot, tap, swipe, input_text, keyevent, back, home, recents, open_url, open_app."
    )
    parameters = {
        "type": "object",
        "properties": {
            "backend": {"type": "string", "enum": ["accessibility", "shizuku", "hybrid", "auto"], "default": "shizuku"},
            "action": {
                "type": "string",
                "enum": ["status", "read_screen", "screenshot", "tap", "swipe", "input_text", "keyevent", "back", "home", "recents", "open_url", "open_app"],
                "description": "Bounded action name. Use home for 回到主屏幕/返回桌面; back for 返回; read_screen for observing current focused window; screenshot for visual observation."
            },
            "params": {
                "type": "object",
                "description": "Action parameters: tap{x,y}; swipe{x1,y1,x2,y2,duration}; input_text{text}; keyevent{key}; open_url{url}; open_app{package}. home/back/recents/status need no params."
            },
            "risk": {"type": "string", "enum": ["safe", "low", "medium", "high"], "default": "safe"},
            "requires_confirmation": {"type": "boolean", "default": False},
            "reason": {"type": "string"},
        },
        "required": ["action"],
    }

    async def run(self, arguments: dict[str, Any]) -> Any:
        risk = str(arguments.get("risk") or "medium")
        requires_confirmation = bool(arguments.get("requires_confirmation", risk in {"medium", "high"}))
        action = PhoneActionRequest(
            action_id="act_" + uuid.uuid4().hex[:12],
            backend=arguments.get("backend") or "hybrid",
            action=str(arguments.get("action") or ""),
            params=arguments.get("params") or {},
            risk=risk if risk in {"safe", "low", "medium", "high"} else "medium",
            requires_confirmation=requires_confirmation,
            reason=str(arguments.get("reason") or ""),
        )
        return action.model_dump()
