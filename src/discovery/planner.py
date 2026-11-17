"""
Calls LLM with the current page state each turn and returns a structured decision (which tool to 
call and with what input).
Maintains the full conversation history across turns so LLM has context.
Uses tool_choice={"type": "any"} to force a tool call every turn - no free-text turns allowed 
during discovery.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from dotenv import load_dotenv
import os
import anthropic
from .observer import PageState
from .recorder import RawStep
from config import MODEL, MAX_TOKENS
from .tools.definitions import (
    ALL_TOOLS,
    ToolName,
    DECLARE_GOAL_COMPLETE,
    ESCALATE,
)

load_dotenv()

@dataclass
class PlannerDecision:
    """A regular action the executor should dispatch."""
    tool: ToolName
    input: dict[str, Any]
    reasoning: str
    tool_use_id: str    # needed to send tool_result in the next turn

@dataclass
class PlannerTermination:
    """Goal complete or escalation — no executor dispatch needed."""
    type: str           # "goal_complete" | "escalate"
    input: dict[str, Any]
    reasoning: str
    tool_use_id: str = ""

PlannerResult = PlannerDecision | PlannerTermination

def _system_prompt(goal: str, entry_url: str) -> str:
    return f"""
        You are an expert UI automation agent operating Bank Suite, a bank back-office application.
    
        Your goal: {goal}
        Entry point: {entry_url}
        
        OPERATING PRINCIPLES:
        - Always call a tool — never describe what you would do without calling one.
        - Bank Suite uses hostile HTML (table layouts, unassociated labels, no test IDs).
        - For unlabeled fields, pass the visible label in name or label (e.g. name="Member Number"). \
        The locator chain will find the adjacent input via table-cell proximity; do not click the \
        label cell itself.
        - Unlabeled radios (Approve / Decline / Escalate to Senior Officer / Request More Info) \
        are often missing from the a11y tree. Still click them with role=radio and name set to \
        that visible word (e.g. name="Approve"). Do not escalate solely because radios are absent \
        from the snapshot.
        - Prefer role+name for buttons and links (e.g. role=button, name="Search Member").
        - Use the read tool to extract goal outputs (credit score, status) by the nearby label.
        - One action per turn. Do not batch.
        - After each action you receive the updated page state. Verify before proceeding.
        - If an action fails or produces unexpected results, re-observe and adapt.
        - Native confirm() dialogs are auto-accepted when the action is permitted.
        
        SAFETY RULES:
        - Never navigate outside the permitted domain.
        - Treat form submissions that create or modify records as irreversible — escalate if unsure.
        - Mark SSN, passwords, and full account numbers as sensitive=true in type calls.
        - If you see an error you cannot resolve after 2 retries, escalate.
        
        COMPLETION:
        - Call declare_goal_complete only with positive confirmation: a visible confirmation \
        message, expected data on screen, or an unambiguous state change.
        - Include all data the goal asked you to extract in the outputs field.
        
        ESCALATION:
        - Call escalate when stuck, when the next action is irreversible and needs human \
        judgment, or when a safety rule blocks a required step.
"""

def _turn_message(state: PageState, history: list[RawStep], turn: int) -> str:
    if not history:
        history_text = "No actions taken yet."
    else:
        lines = []
        for s in history[-5:]: # last 5 steps, enough context without overflow
            status = "OK" if s.success else "FAILED"
            lines.append(f"Step {s.step_index}: [{s.tool}] {s.input} -> {status}: {s.observation}")
        history_text = "\n".join(lines)
    return f"""--- TURN {turn} ---
        CURRENT PAGE STATE:
        URL: {state.url}
        TITLE: "{state.title}"
        
        ACCESSIBILITY TREE:
        {state.tree_summary}

        RECENT ACTIONS (last 5):
        {history_text}

        What is your next action?
    """
class Planner:
    """
    Manages the multi-turn conversation with LLM. The Anthropic API requires that after an 
    assistant tool_use block, the next user message must contain a tool_result block with the 
    matching tool_use_id. We track last_tool_use_id across turns to satisfy this.
    """

    def __init__(self, goal: str, entry_url: str) -> None:
        self._client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self._system = _system_prompt(goal, entry_url)
        self._messages: list[dict[str, Any]] = []
        self._turn = 0

    async def decide(
        self,
        page_state: PageState,
        step_history: list[RawStep],
        last_tool_use_id: str | None = None,
        last_observation: str | None = None,
    ) -> PlannerResult:
        self._turn += 1

        # After turn 1 we must send the tool_result before the next user message
        if last_tool_use_id is not None and last_observation is not None:
            self._messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": last_tool_use_id,
                        "content": last_observation,
                    }
                ],
            })

        # Append the the new page observation as text
        self._messages.append({
            "role": "user",
            "content": _turn_message(page_state, step_history, self._turn),
        })

        response = self._client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=self._system,
            tools=ALL_TOOLS,
            tool_choice={"type": "any"},
            messages=self._messages,
        )

        # Append the LLM response to history for next turn
        self._messages.append({
            "role": "assistant",
            "content": response.content,
        })

        # Extract reasoning text (the text block LLM emits before the tool call)
        reasoning = ""
        for block in response.content:
            if hasattr(block, "type") and block.type == "text":
                reasoning = block.text
                break


        # Extract the tool_use block
        tool_block = None
        for block in response.content:
            if hasattr(block, "type") and block.type == "tool_use":
                tool_block = block
                break

        if tool_block is None:
            raise RuntimeError(f"Planner expected a tool_use block but got: {response.content}")

        tool_name: ToolName = tool_block.name
        tool_input: dict[str, Any] = tool_block.input
        tool_use_id: str = tool_block.id

        if tool_name == DECLARE_GOAL_COMPLETE:
            return PlannerTermination(
                type="goal_complete",
                input=tool_input,
                reasoning=reasoning,
                tool_use_id=tool_use_id,
            )
        if tool_name == ESCALATE:
            return PlannerTermination(
                type="escalate",
                input=tool_input,
                reasoning=reasoning,
                tool_use_id=tool_use_id,
            )
        return PlannerDecision(
            tool=tool_name,
            input=tool_input,
            reasoning=reasoning,
            tool_use_id=tool_use_id,
        )