from __future__ import annotations

from typing import Annotated, Any, TypedDict
from operator import add

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command


class State(TypedDict):
    messages: Annotated[list[dict[str, Any]], add]
    pending_actions: Annotated[list[dict[str, Any]], add]
    observations: Annotated[list[dict[str, Any]], add]
    step: int


def planner(state: State) -> dict[str, Any]:
    text = "\n".join(m.get("content", "") for m in state.get("messages", []) if m.get("role") == "user")
    step = state.get("step", 0)
    if step == 0 and "新闻" in text:
        return {"messages": [{"role": "assistant", "content": "TOOL firecrawl_search"}], "step": 1}
    if step == 1:
        return {"messages": [{"role": "assistant", "content": "TOOL firecrawl_scrape"}], "step": 2}
    if step == 2 and "打开" in text:
        return {"messages": [{"role": "assistant", "content": "TOOL phone_action"}], "step": 3}
    return {"messages": [{"role": "assistant", "content": "FINAL 已完成多轮工具流程。"}], "step": 99}


def route(state: State) -> str:
    last = state["messages"][-1]["content"]
    if "firecrawl_search" in last:
        return "firecrawl_search"
    if "firecrawl_scrape" in last:
        return "firecrawl_scrape"
    if "phone_action" in last:
        return "phone_action"
    return END


def firecrawl_search(state: State) -> dict[str, Any]:
    return {"messages": [{"role": "tool", "name": "firecrawl_search", "content": "搜索结果: AI 新闻 A, AI 新闻 B"}]}


def firecrawl_scrape(state: State) -> dict[str, Any]:
    return {"messages": [{"role": "tool", "name": "firecrawl_scrape", "content": "抓取正文: AI 新闻 A 的详细内容"}]}


def phone_action(state: State) -> dict[str, Any]:
    action = {
        "type": "phone_action",
        "action_id": "act_open_browser_001",
        "backend": "hybrid",
        "action": "open_url",
        "params": {"url": "https://example.com/ai-news"},
        "risk": "low",
        "requires_confirmation": False,
    }
    observation = interrupt(action)
    return {
        "pending_actions": [action],
        "observations": [observation],
        "messages": [{"role": "tool", "name": "phone_action", "content": f"手机执行结果: {observation}"}],
    }


def build_graph():
    graph = StateGraph(State)
    graph.add_node("planner", planner)
    graph.add_node("firecrawl_search", firecrawl_search)
    graph.add_node("firecrawl_scrape", firecrawl_scrape)
    graph.add_node("phone_action", phone_action)
    graph.add_edge(START, "planner")
    graph.add_conditional_edges("planner", route)
    graph.add_edge("firecrawl_search", "planner")
    graph.add_edge("firecrawl_scrape", "planner")
    graph.add_edge("phone_action", "planner")
    return graph.compile(checkpointer=InMemorySaver())


if __name__ == "__main__":
    app = build_graph()
    config = {"configurable": {"thread_id": "spike-thread-001"}}
    initial = {"messages": [{"role": "user", "content": "查 AI 新闻，然后打开网页"}], "pending_actions": [], "observations": [], "step": 0}
    first = app.invoke(initial, config=config)
    print("FIRST:", first)
    if "__interrupt__" in first:
        resumed = app.invoke(Command(resume={"status": "success", "foreground_app": "browser"}), config=config)
        print("RESUMED:", resumed)
