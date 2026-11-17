"""
Orchestrates observer, planner, safety guard, executor, and recorder.

Entry point: run_discovery(config) → DiscoveryOutcome
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from playwright.async_api import Browser, BrowserContext, Dialog, Page, async_playwright

from ..escalation.handler import (
    EscalationHandler,
    EscalationReason,
    EscalationRequest,
)
from ..goal import preserve_dollar_amounts
from ..observability.dump import dump_json
from ..safety.guard import SafetyConfig, SafetyGuard
from .executor import Executor
from .observer import capture_page_state_with_iframes
from .planner import Planner, PlannerDecision, PlannerTermination
from .recorder import RawTrace, Recorder
from .tools.definitions import MUTATING_TOOLS

DEFAULT_MAX_STEPS = 30
NEW_APPLICATION_MAX_STEPS = 80


def infer_max_steps(goal: str, entry_url: str) -> int:
    hay = f"{goal} {entry_url}".lower()
    if (
        "application.html" in hay
        or "new credit card" in hay
        or "new cc application" in hay
        or "new cc app" in hay
    ):
        return NEW_APPLICATION_MAX_STEPS
    return DEFAULT_MAX_STEPS


@dataclass
class DiscoveryConfig:
    goal: str
    entry_url: str
    allowed_domain: str
    max_steps: int | None = None
    evidence_dir: Path | None = None
    headless: bool = True
    mock_operator: bool = False
    handoff_timeout_s: float = 300.0

    def __post_init__(self) -> None:
        self.goal = preserve_dollar_amounts(self.goal)
        if self.max_steps is None:
            self.max_steps = infer_max_steps(self.goal, self.entry_url)


@dataclass
class DiscoverySuccess:
    status: Literal["success"] = "success"
    outputs: dict[str, Any] = field(default_factory=dict)
    trace: RawTrace | None = None


@dataclass
class DiscoveryEscalated:
    status: Literal["escalated"] = "escalated"
    reason: str = ""
    trace: RawTrace | None = None


@dataclass
class DiscoveryFailed:
    status: Literal["failed"] = "failed"
    error: str = ""
    trace: RawTrace | None = None


@dataclass
class DiscoveryMaxSteps:
    status: Literal["max_steps"] = "max_steps"
    trace: RawTrace | None = None


DiscoveryOutcome = DiscoverySuccess | DiscoveryEscalated | DiscoveryFailed | DiscoveryMaxSteps

LogLevel = Literal[
    "info", "observe", "reason", "action", "success", "warn", "error", "blocked", "escalate"
]

_LEVEL_ICONS: dict[str, str] = {
    "info": "📋", "observe": "👁 ", "reason": "🧠", "action": "⚡",
    "success": "✅", "warn": "⚠️ ", "error": "❌", "blocked": "🚫", "escalate": "🙋",
}


@dataclass
class LogEntry:
    level: str
    message: str
    timestamp: str


def _log(entries: list[LogEntry], level: LogLevel, message: str) -> None:
    entry = LogEntry(level=level, message=message, timestamp=datetime.now(timezone.utc).isoformat())
    entries.append(entry)
    icon = _LEVEL_ICONS.get(level, "•")
    print(f"{icon} [{level.upper()}] {message}")


async def _screenshot(page: Page, directory: Path, name: str) -> None:
    try:
        await page.screenshot(path=str(directory / f"{name}.png"), full_page=True)
    except Exception:
        pass


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def _write_steps_json(directory: Path, trace: RawTrace | None, log: list[LogEntry]) -> None:
    """Stable names so a run folder always has JSON next to screenshots."""
    dump_json(directory, "step-log.json", [asdict(e) for e in log])
    if trace is None:
        dump_json(
            directory,
            "steps.json",
            {"steps": [], "note": "run still in progress or no actions yet"},
        )
        return
    dump_json(
        directory,
        "steps.json",
        {
            "goal": trace.goal,
            "entry_url": trace.entry_url,
            "outcome": trace.outcome_type,
            "started_at": trace.started_at,
            "completed_at": trace.completed_at,
            "total_steps": trace.total_steps,
            "declared_outputs": trace.declared_outputs,
            "checkpoint_evidence": trace.checkpoint_evidence,
            "handoffs": trace.handoffs,
            "steps": [asdict(s) for s in trace.steps],
        },
    )
    dump_json(directory, "raw-trace.json", asdict(trace))


async def _write_evidence(
    directory: Path,
    log: list[LogEntry],
    trace: RawTrace,
    page: Page,
    suffix: str = "",
) -> None:
    label = f"={suffix}" if suffix else ""
    ts = _ts()
    _write_steps_json(directory, trace, log)
    dump_json(directory, f"step-log{label}-{ts}.json", [asdict(e) for e in log])
    dump_json(directory, f"raw-trace{label}-{ts}.json", asdict(trace))
    await _screenshot(page, directory, f"final{label}")
    print(f"Evidence written to: {directory} (steps.json, step-log.json, screenshots)")


def _sanitize(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower())[:60].strip("-")


async def _accept_dialog(dialog: Dialog) -> None:
    await dialog.accept()


async def run_discovery(config: DiscoveryConfig) -> DiscoveryOutcome:
    evidence_dir = config.evidence_dir or Path("evidence") / _sanitize(config.goal)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    step_log: list[LogEntry] = []
    extracted: dict[str, Any] = {}
    handoffs: list[dict[str, Any]] = []

    guard = SafetyGuard(SafetyConfig(allowed_domains=[config.allowed_domain], block_irreversible=False))

    async with async_playwright() as pw:
        browser: Browser = await pw.chromium.launch(
            headless=config.headless,
            args=[] if config.headless else ["--start-maximized"],
        )
        if config.headless:
            context = await browser.new_context(viewport={"width": 1280, "height": 900})
        else:
            context = await browser.new_context(no_viewport=True)
        page: Page = await context.new_page()
        page.on("dialog", _accept_dialog)

        executor = Executor(page)
        recorder = Recorder(executor)
        planner = Planner(config.goal, config.entry_url)
        handler = EscalationHandler(
            page,
            config.goal,
            config.entry_url,
            intervention_dir=evidence_dir / "interventions",
            mock_operator=config.mock_operator,
            poll_timeout_s=config.handoff_timeout_s,
        )

        await page.goto(config.entry_url, wait_until="domcontentloaded", timeout=15_000)
        _log(step_log, "info", f'Discovery started. Goal: "{config.goal}" Entry: {config.entry_url}')

        last_tool_use_id: str | None = None
        last_observation: str | None = None
        outcome: DiscoveryOutcome | None = None

        try:
            for turn in range(1, config.max_steps + 1):
                page_state = await capture_page_state_with_iframes(page)
                _log(step_log, "observe", f'Turn {turn}: {page_state.url} - "{page_state.title}"')

                try:
                    decision = await planner.decide(
                        page_state,
                        recorder.steps,
                        last_tool_use_id,
                        last_observation,
                    )
                except Exception as exc:
                    msg = str(exc)
                    _log(step_log, "error", f"Planner error: {msg}")
                    trace = recorder.finalize(
                        goal=config.goal, entry_url=config.entry_url, outcome_type="failed",
                        handoffs=handoffs or None,
                    )
                    await _write_evidence(evidence_dir, step_log, trace, page)
                    return DiscoveryFailed(error=f"Planner error: {msg}", trace=trace)

                if isinstance(decision, PlannerTermination):
                    if decision.type == "goal_complete":
                        evidence = decision.input.get("checkpoint_evidence")
                        declared = decision.input.get("outputs") or {}
                        outputs = {**extracted, **declared}
                        _log(step_log, "success", f"Goal complete. {evidence}")
                        trace = recorder.finalize(
                            goal=config.goal,
                            entry_url=config.entry_url,
                            outcome_type="success",
                            declared_outputs=outputs,
                            checkpoint_evidence=str(evidence),
                            handoffs=handoffs or None,
                        )
                        await _write_evidence(evidence_dir, step_log, trace, page)
                        return DiscoverySuccess(outputs=outputs, trace=trace)

                    if decision.type == "escalate":
                        reason = str(decision.input.get("reason") or "STUCK")
                        desc = str(decision.input.get("description") or "")
                        _log(step_log, "escalate", f"Escalation [{reason}]: {desc}")
                        await _screenshot(page, evidence_dir, f"escalation-turn-{turn}")
                        try:
                            esc_reason = EscalationReason(reason)
                        except ValueError:
                            esc_reason = EscalationReason.STUCK
                        record = await handler.handle(
                            EscalationRequest(
                                reason=esc_reason,
                                description=desc,
                                current_step=str(decision.input.get("current_step") or f"turn {turn}"),
                                suggested_action=str(decision.input.get("suggested_action") or ""),
                            ),
                            intervention_id=str(uuid.uuid4())[:8],
                        )
                        if record is not None:
                            handoffs.append(asdict(record))
                        if record is None or not record.resume:
                            why = record.actions_taken if record else "operator did not respond"
                            _log(step_log, "escalate", f"Run stopped after handoff: {why}")
                            trace = recorder.finalize(
                                goal=config.goal, entry_url=config.entry_url,
                                outcome_type="escalated", handoffs=handoffs or None,
                            )
                            await _write_evidence(evidence_dir, step_log, trace, page, "escalation")
                            return DiscoveryEscalated(reason=f"[{reason}] {desc}", trace=trace)
                        last_tool_use_id = decision.tool_use_id or None
                        last_observation = (
                            f"Human operator resumed control. Actions taken: {record.actions_taken}. "
                            "Continue from the current page state."
                        )
                        continue

                assert isinstance(decision, PlannerDecision)
                tool = decision.tool
                inp = decision.input

                if tool in MUTATING_TOOLS:
                    verdict = guard.check(tool, inp)
                    if not verdict.allowed:
                        _log(step_log, "blocked", f"Safety blocked: {verdict.reason}")
                        await _screenshot(page, evidence_dir, f"blocked-turn-{turn}")
                        record = await handler.handle(
                            EscalationRequest(
                                reason=EscalationReason.POLICY_BLOCK,
                                description=verdict.reason or "Safety policy blocked the action",
                                current_step=f"turn {turn}: {tool}",
                            ),
                            intervention_id=str(uuid.uuid4())[:8],
                        )
                        if record is not None:
                            handoffs.append(asdict(record))
                        if record is None or not record.resume:
                            trace = recorder.finalize(
                                goal=config.goal, entry_url=config.entry_url,
                                outcome_type="failed", handoffs=handoffs or None,
                            )
                            await _write_evidence(evidence_dir, step_log, trace, page)
                            return DiscoveryFailed(
                                error=f"Safety policy blocked: {verdict.reason}", trace=trace
                            )
                        last_tool_use_id = decision.tool_use_id
                        last_observation = (
                            f"Operator handled a policy block. Actions: {record.actions_taken}."
                        )
                        continue
                    if verdict.requires_confirmation:
                        _log(
                            step_log,
                            "warn",
                            f'Potentially irreversible: [{tool}] "{inp.get("name")}" — proceeding; confirm() auto-accepted',
                        )

                recorder.set_pending_reasoning(decision.reasoning)
                _log(step_log, "reason", f"[Turn {turn}] {decision.reasoning or '(no reasoning)'}")

                result = await executor.dispatch(tool, inp)
                _log(step_log, "action" if result.success else "error", f"[{tool}] -> {result.observation}")
                if result.extracted:
                    extracted.update(result.extracted)
                if not result.success:
                    await _screenshot(page, evidence_dir, f"error-turn-{turn}")
                dump_json(evidence_dir, "step-log.json", [asdict(e) for e in step_log])
                dump_json(
                    evidence_dir,
                    "steps.json",
                    {
                        "goal": config.goal,
                        "entry_url": config.entry_url,
                        "outcome": "in_progress",
                        "total_steps": len(recorder.steps),
                        "steps": [asdict(s) for s in recorder.steps],
                    },
                )

                last_tool_use_id = decision.tool_use_id
                last_observation = result.observation

            _log(step_log, "warn", f"Max steps ({config.max_steps}) reached without completion.")
            await _screenshot(page, evidence_dir, "max-steps-final")
            trace = recorder.finalize(
                goal=config.goal, entry_url=config.entry_url, outcome_type="max_steps",
                handoffs=handoffs or None,
            )
            await _write_evidence(evidence_dir, step_log, trace, page)
            outcome = DiscoveryMaxSteps(trace=trace)
            return outcome
        finally:
            try:
                await context.close()
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass
