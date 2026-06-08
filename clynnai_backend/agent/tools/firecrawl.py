from __future__ import annotations

from typing import Any
import httpx

from ...config import Settings
from .base import ClynnTool


class FirecrawlSearchTool(ClynnTool):
    name = "firecrawl_search"
    description = "Search the live web using Firecrawl and return LLM-ready search results."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
        },
        "required": ["query"],
    }

    def __init__(self, settings: Settings):
        self.settings = settings

    async def run(self, arguments: dict[str, Any]) -> Any:
        if not self.settings.firecrawl_api_key:
            return {"error": "FIRECRAWL_API_KEY is not configured"}
        query = str(arguments.get("query") or "").strip()
        limit = int(arguments.get("limit") or 5)
        payload = {"query": query, "limit": max(1, min(limit, 10))}
        url = self.settings.firecrawl_base_url.rstrip("/") + "/v2/search"
        headers = {"Authorization": f"Bearer {self.settings.firecrawl_api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=45) as client:
            resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code >= 400:
            return {"error": f"Firecrawl search HTTP {resp.status_code}", "body": resp.text[:1000]}
        return resp.json()


class FirecrawlScrapeTool(ClynnTool):
    name = "firecrawl_scrape"
    description = "Scrape one URL with Firecrawl and return clean markdown/content."
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to scrape."},
        },
        "required": ["url"],
    }

    def __init__(self, settings: Settings):
        self.settings = settings

    async def run(self, arguments: dict[str, Any]) -> Any:
        if not self.settings.firecrawl_api_key:
            return {"error": "FIRECRAWL_API_KEY is not configured"}
        target_url = str(arguments.get("url") or "").strip()
        payload = {"url": target_url, "formats": ["markdown"]}
        url = self.settings.firecrawl_base_url.rstrip("/") + "/v2/scrape"
        headers = {"Authorization": f"Bearer {self.settings.firecrawl_api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code >= 400:
            return {"error": f"Firecrawl scrape HTTP {resp.status_code}", "body": resp.text[:1000]}
        return resp.json()
