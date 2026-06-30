"""Node functions for the LangGraph workflow.

Each function receives AgentState and returns a partial state update dict.
Do NOT mutate input state — return new values only.

LLM REQUIREMENT:
- classify_node MUST use a real LLM call (structured output for intent classification)
- answer_node MUST use a real LLM call (grounded response generation)
- evaluate_node SHOULD use LLM-as-judge (bonus points; heuristic acceptable for base score)
"""

from __future__ import annotations

from .state import AgentState, make_event


# ─── EXAMPLE: working node (provided for reference) ──────────────────
def intake_node(state: AgentState) -> dict:
    """Normalize raw query. This node is provided as a working example."""
    query = state.get("query", "").strip()
    return {
        "query": query,
        "messages": [f"intake:{query[:40]}"],
        "events": [make_event("intake", "completed", "query normalized")],
    }


# ─── TODO(student): implement ALL nodes below ────────────────────────
import os
from pydantic import BaseModel, Field
from .state import Route, make_event
from .llm import get_llm

class Classification(BaseModel):
    route: Route = Field(description="The classified route for the support query. Pick the most appropriate and specific category: simple, tool, missing_info, risky, error.")
    risk_level: str = Field(description="Risk level. Must be 'high' if the route is risky, and 'low' otherwise.")


class Evaluation(BaseModel):
    needs_retry: bool = Field(description="True if the tool output indicates an error, timeout, or failure that requires retry. False if successful.")


def classify_node(state: AgentState) -> dict:
    """Classify the query into a route using an LLM.

    *** MUST use a real LLM call — keyword-only heuristics will lose points. ***

    Use .with_structured_output() or equivalent to get reliable enum classification.
    The LLM should classify into one of: simple, tool, missing_info, risky, error.

    Return: {"route": str, "risk_level": str, "events": [make_event(...)]}
    """
    llm = get_llm()
    structured_llm = llm.with_structured_output(Classification)
    
    prompt = (
        "You are an AI support ticket routing system.\n"
        "Analyze the user query and classify it into one of these routes:\n"
        "- 'simple': Common general support questions (e.g., how to reset password, help documentation)\n"
        "- 'tool': Queries requiring information lookups from database/system (e.g., lookup order status, check order 12345, check tracking status)\n"
        "- 'missing_info': Vague, short, or incomplete queries (e.g., 'can you fix it?', 'it fails', 'please help me')\n"
        "- 'risky': Actions that have transactional side effects or alter customer data (e.g., refund customer, delete account, send confirmation email)\n"
        "- 'error': Reports of system failure, database crash, connection timeout, server not responding\n\n"
        "Priority rule: risky > tool > missing_info > error > simple. If multiple apply, pick the highest priority route.\n\n"
        f"Query: {state.get('query')}"
    )
    
    classification = structured_llm.invoke(prompt)
    route = classification.route.value if hasattr(classification.route, "value") else str(classification.route)
    risk_level = classification.risk_level
    
    return {
        "route": route,
        "risk_level": risk_level,
        "events": [make_event("classify", "completed", f"Route classified as {route} with risk {risk_level}")],
    }


def tool_node(state: AgentState) -> dict:
    """Execute a mock tool call.

    Simulate transient failures for error-route scenarios to test retry loops.
    """
    attempt = state.get("attempt", 0)
    route = state.get("route", "")
    query = state.get("query", "")
    
    if route == "error" and attempt < 2:
        result_string = f"ERROR: Connection timeout while querying order database for '{query}'"
    else:
        result_string = f"SUCCESS: Order details found for your lookup request related to '{query}'"
        
    return {
        "tool_results": [result_string],
        "events": [make_event("tool", "completed", f"Tool executed: {result_string[:50]}")],
    }


def evaluate_node(state: AgentState) -> dict:
    """Evaluate tool results — the retry-loop gate.

    Check whether the latest tool result is satisfactory or needs retry.
    """
    tool_results = state.get("tool_results", [])
    latest_result = tool_results[-1] if tool_results else ""
    
    if "ERROR" in latest_result:
        evaluation_result = "needs_retry"
    else:
        try:
            llm = get_llm()
            structured_llm = llm.with_structured_output(Evaluation)
            prompt = (
                "You are an evaluator analyzing tool execution output.\n"
                "Evaluate if the tool result indicates a system failure, timeout, or error that must be retried.\n"
                f"Tool Output: {latest_result}\n"
            )
            res = structured_llm.invoke(prompt)
            evaluation_result = "needs_retry" if res.needs_retry else "success"
        except Exception:
            evaluation_result = "needs_retry" if "ERROR" in latest_result else "success"
            
    return {
        "evaluation_result": evaluation_result,
        "events": [make_event("evaluate", "completed", f"Evaluated tool result: {evaluation_result}")],
    }


def answer_node(state: AgentState) -> dict:
    """Generate a final response using an LLM.

    *** MUST use a real LLM call — hardcoded strings will lose points. ***
    """
    llm = get_llm()
    query = state.get("query", "")
    tool_results = state.get("tool_results", [])
    approval = state.get("approval")
    
    prompt = (
        "You are a helpful customer support agent.\n"
        "Draft a friendly and professional final response to the user's query.\n"
        "You must ground your response completely in the provided context (tool results or approval decision, if any). Do not make up info.\n\n"
        f"Customer Query: {query}\n"
        f"Tool Results: {tool_results}\n"
        f"Approval Decision: {approval}\n\n"
        "Drafted Response:"
    )
    
    response = llm.invoke(prompt)
    return {
        "final_answer": response.content.strip(),
        "events": [make_event("answer", "completed", "Generated grounded response")],
    }


def ask_clarification_node(state: AgentState) -> dict:
    """Ask for missing information instead of hallucinating.

    Generate a specific clarification question based on the vague/incomplete query.
    """
    llm = get_llm()
    query = state.get("query", "")
    
    prompt = (
        "You are a customer support agent. The user's query is vague and needs clarification.\n"
        "Politely ask the user a specific question to clarify their needs or request missing information.\n\n"
        f"Query: {query}\n\n"
        "Clarification Question:"
    )
    
    response = llm.invoke(prompt)
    question = response.content.strip()
    return {
        "pending_question": question,
        "final_answer": question,
        "events": [make_event("clarify", "completed", f"Clarification requested: {question[:50]}")],
    }


def risky_action_node(state: AgentState) -> dict:
    """Prepare a risky action for human approval.

    Describe the proposed action and why it requires approval.
    """
    query = state.get("query", "")
    proposed_action = f"Perform sensitive action: '{query}'"
    return {
        "proposed_action": proposed_action,
        "events": [make_event("risky_action", "completed", f"Prepared action: {proposed_action}")],
    }


def approval_node(state: AgentState) -> dict:
    """Human-in-the-loop approval step.

    Default behavior: mock approval (approved=True) so tests and CI run offline.
    Extension: if env LANGGRAPH_INTERRUPT=true, use langgraph.types.interrupt() for real HITL.
    """
    if os.getenv("LANGGRAPH_INTERRUPT") == "true":
        from langgraph.types import interrupt
        try:
            decision = interrupt({
                "message": f"Approval required for: {state.get('proposed_action')}",
                "proposed_action": state.get("proposed_action"),
            })
            if isinstance(decision, dict):
                approved = decision.get("approved", False)
                reviewer = decision.get("reviewer", "human-reviewer")
                comment = decision.get("comment", "")
            else:
                approved = bool(decision)
                reviewer = "human-reviewer"
                comment = str(decision)
        except Exception:
            approved = True
            reviewer = "mock-reviewer-fallback"
            comment = "Interrupt failed/skipped"
            
        decision_dict = {
            "approved": approved,
            "reviewer": reviewer,
            "comment": comment,
        }
    else:
        decision_dict = {
            "approved": True,
            "reviewer": "mock-reviewer",
            "comment": "Auto-approved in mock mode",
        }
        
    return {
        "approval": decision_dict,
        "events": [make_event("approval", "completed", f"Approval result: {decision_dict['approved']}")],
    }


def retry_or_fallback_node(state: AgentState) -> dict:
    """Record a retry attempt."""
    attempt = state.get("attempt", 0)
    new_attempt = attempt + 1
    error_msg = f"Error: Timeout or transient tool error on attempt {new_attempt}."
    return {
        "attempt": new_attempt,
        "errors": [error_msg],
        "events": [make_event("retry", "completed", f"Incremented attempt to {new_attempt}")],
    }


def dead_letter_node(state: AgentState) -> dict:
    """Handle unresolvable failures after max retries exceeded."""
    query = state.get("query", "")
    final_answer = (
        "We are sorry, but system errors prevented us from completing your request "
        f"for '{query}' after multiple attempts. Our engineers have been alerted. "
        "Please contact support if you need immediate assistance."
    )
    return {
        "final_answer": final_answer,
        "events": [make_event("dead_letter", "completed", "Routed to dead-letter queue")],
    }


def finalize_node(state: AgentState) -> dict:
    """Emit a final audit event. All routes must pass through here before END."""
    return {
        "events": [make_event("finalize", "completed", "workflow finished")],
    }
