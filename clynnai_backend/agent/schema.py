from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


class AgentRunOptions(BaseModel):
    web_preferred: bool = False
    allow_image: bool = True
    phone_control: dict[str, Any] = Field(default_factory=dict)
    max_steps: int = 6
    model: str | None = None
    persona_name: str | None = None
    persona_prompt: str | None = None
    jailbreak_prompt: str | None = None
    jailbreak_enabled: bool = False


class PhoneActionRequest(BaseModel):
    type: Literal["phone_action"] = "phone_action"
    action_id: str
    backend: Literal["accessibility", "shizuku", "hybrid", "auto"] = "hybrid"
    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    risk: Literal["safe", "low", "medium", "high"] = "medium"
    requires_confirmation: bool = True
    reason: str = ""


class AgentToolCallRecord(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    error: str | None = None


class AgentRunResult(BaseModel):
    text: str
    parts: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[AgentToolCallRecord] = Field(default_factory=list)
    pending_actions: list[PhoneActionRequest] = Field(default_factory=list)
    interrupted: bool = False
    raw_messages: list[dict[str, Any]] = Field(default_factory=list)
