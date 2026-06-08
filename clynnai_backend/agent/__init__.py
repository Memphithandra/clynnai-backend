"""ClynnAPP's embedded agent core.

This package intentionally owns ClynnAPP runtime behavior. LangGraph is used as
an embedded orchestration primitive, not as an external app/process dependency.
"""

from .runtime import ClynnAgentRuntime
from .schema import AgentRunOptions, AgentRunResult

__all__ = ["ClynnAgentRuntime", "AgentRunOptions", "AgentRunResult"]
