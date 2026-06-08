from __future__ import annotations

from typing import Any, Callable, Awaitable

from .base import ClynnTool


class ImageGenerationTool(ClynnTool):
    name = "generate_image"
    description = "Generate an image using ClynnAI backend's configured image generation upstream."
    parameters = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string"},
            "size": {"type": "string", "default": "1024x1024"},
        },
        "required": ["prompt"],
    }

    def __init__(self, generate_fn: Callable[[dict[str, Any]], Awaitable[Any]]):
        self.generate_fn = generate_fn

    async def run(self, arguments: dict[str, Any]) -> Any:
        prompt = str(arguments.get("prompt") or "").strip()
        size = str(arguments.get("size") or "1024x1024")
        return await self.generate_fn({"prompt": prompt, "size": size})
