"""Graph construction.

This module is intentionally import-safe. It imports LangGraph only inside the builder so unit tests
that check schema/metrics can run even if students are still debugging graph wiring.
"""

from __future__ import annotations

from typing import Any

from .state import AgentState


def build_graph(checkpointer: Any | None = None):
    """Build and compile the LangGraph workflow."""
    from langgraph.graph import StateGraph, START, END
    
    # Import nodes
    from .nodes import (
        intake_node,
        classify_node,
        tool_node,
        evaluate_node,
        answer_node,
        ask_clarification_node,
        risky_action_node,
        approval_node,
        retry_or_fallback_node,
        dead_letter_node,
        finalize_node,
    )
    
    # Import routing
    from .routing import (
        route_after_classify,
        route_after_evaluate,
        route_after_retry,
        route_after_approval,
    )
    
    # Create StateGraph
    builder = StateGraph(AgentState)
    
    # Add nodes
    builder.add_node("intake", intake_node)
    builder.add_node("classify", classify_node)
    builder.add_node("tool", tool_node)
    builder.add_node("evaluate", evaluate_node)
    builder.add_node("answer", answer_node)
    builder.add_node("clarify", ask_clarification_node)
    builder.add_node("risky_action", risky_action_node)
    builder.add_node("approval", approval_node)
    builder.add_node("retry", retry_or_fallback_node)
    builder.add_node("dead_letter", dead_letter_node)
    builder.add_node("finalize", finalize_node)
    
    # Add fixed edges
    builder.add_edge(START, "intake")
    builder.add_edge("intake", "classify")
    builder.add_edge("tool", "evaluate")
    builder.add_edge("risky_action", "approval")
    builder.add_edge("answer", "finalize")
    builder.add_edge("clarify", "finalize")
    builder.add_edge("dead_letter", "finalize")
    builder.add_edge("finalize", END)
    
    # Add conditional edges
    builder.add_conditional_edges(
        "classify",
        route_after_classify,
        {
            "answer": "answer",
            "tool": "tool",
            "clarify": "clarify",
            "risky_action": "risky_action",
            "retry": "retry",
        }
    )
    
    builder.add_conditional_edges(
        "evaluate",
        route_after_evaluate,
        {
            "retry": "retry",
            "answer": "answer",
        }
    )
    
    builder.add_conditional_edges(
        "retry",
        route_after_retry,
        {
            "tool": "tool",
            "dead_letter": "dead_letter",
        }
    )
    
    builder.add_conditional_edges(
        "approval",
        route_after_approval,
        {
            "tool": "tool",
            "clarify": "clarify",
        }
    )
    
    # Compile
    return builder.compile(checkpointer=checkpointer)
