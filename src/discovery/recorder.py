""""
Subscribes to the Executor's ActionEvents and accumulates the raw trace that the artifact builder 
will shape into the final artifact.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from .executor import ActionEvent, Executor
from .tools.definitions import ToolName

@dataclass
class RawStep:
    step_index: int
    tool: ToolName
    input: dict[str, Any]
    observation: str
    success: bool
    timestamp: str
    error: str | None = None
    page_url: str | None = None
    agent_reasoning: str | None = None
    resolved_locator: dict[str, Any] | None = None
    extracted: dict[str, Any] | None = None

OutcomeType = Literal["success", "escalated", "failed", "max_steps"]

@dataclass
class RawTrace:
    goal: str
    entry_url: str
    started_at: str
    outcome_type: OutcomeType
    steps: list[RawStep]
    total_steps: int
    completed_at: str | None = None
    declared_outputs: dict[str, Any] | None = None
    checkpoint_evidence: str | None = None
    handoffs: list[dict[str, Any]] | None = None

class Recorder:
    """
    Listens to ActionEvents from the Executor and build the RawTrace. The agent loop calls 
    set_pending_reasoning() before each LLM's turn to associate LLM's reasoning with the action 
    it produces.
    """
    def __init__(self, executor: Executor) -> None:
        self._steps: list[RawStep] = []
        self._pending_reasoning: str | None = None
        self._started_at = datetime.now(timezone.utc).isoformat()

        executor.on_action(self._on_action)

    def set_pending_reasoning(self, reasoning: str) -> None:
        self._pending_reasoning = reasoning

    async def _on_action(self, event: ActionEvent) -> None:
        step = RawStep(
            step_index=event.step_index,
            tool=event.tool,
            input=event.input,
            observation=event.result.observation,
            success=event.result.success,
            timestamp=event.timestamp,
            page_url=event.result.page_state.url if event.result.page_state else None,
        )
        if event.result.error:
            step.error = event.result.error
        if event.result.resolved_locator:
            step.resolved_locator = event.result.resolved_locator.model_dump()
        if event.result.extracted:
            step.extracted = event.result.extracted
        if self._pending_reasoning:
            step.agent_reasoning = self._pending_reasoning
            self._pending_reasoning = None
        self._steps.append(step)

    def finalize(
            self,
            *,
            goal: str,
            entry_url: str,
            outcome_type: OutcomeType,
            declared_outputs: dict[str, Any] | None = None,
            checkpoint_evidence: str | None = None,
            handoffs: list[dict[str, Any]] | None = None,
    ) -> RawTrace:
        return RawTrace(
            goal=goal,
            entry_url=entry_url,
            started_at=self._started_at,
            outcome_type=outcome_type,
            steps=list(self._steps),
            total_steps=len(self._steps),
            completed_at=datetime.now(timezone.utc).isoformat(),
            declared_outputs=declared_outputs,
            checkpoint_evidence=checkpoint_evidence,
            handoffs=handoffs,
        )

    @property
    def steps(self) -> list[RawStep]:
        return list(self._steps)
