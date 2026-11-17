"""
Replay runner: executes a saved Artifact deterministically.

No LLM in the loop. Same inputs → same steps → same outputs, every time.
"""
from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.async_api import Dialog, Page, TimeoutError as PWTimeout, async_playwright

from ..escalation.handler import EscalationHandler, EscalationReason, EscalationRequest
from ..schema.artifact import (
    Artifact,
    ArtifactStep,
    ClickAction,
    LocatorStrategy,
    NavigateAction,
    ReadAction,
    SelectAction,
    TypeAction,
    WaitAction,
    ActionType,
)
from ..schema.result import (
    BusinessOutcome,
    ErrorCode,
    OutcomeCode,
    ReplayFailure,
    ReplayResult,
    ReplaySuccess,
)
from ..artifact.parameterizer import DECISION_CHOICES, expand_application_params, normalize_decision
from ..observability.dump import dump_json
from ..safety.guard import SafetyConfig, SafetyGuard
from ..surface.locator import LocatorError, resolve_locator

_BUSINESS_OUTCOMES: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"member record not found", re.I), OutcomeCode.MEMBER_NOT_FOUND, "Member record not found"),
    (re.compile(r"no members found", re.I), OutcomeCode.MEMBER_NOT_FOUND, "No members found matching criteria"),
    (re.compile(r"already been decided", re.I), OutcomeCode.ALREADY_DECIDED, "Application already decided"),
    (re.compile(r"account frozen", re.I), OutcomeCode.ACCOUNT_FROZEN, "Account is frozen"),
    (re.compile(r"permission denied", re.I), OutcomeCode.PERMISSION_DENIED, "Permission denied"),
    (re.compile(r"session.*expired", re.I), OutcomeCode.SESSION_EXPIRED, "Session expired"),
    (re.compile(r"invalid member number", re.I), OutcomeCode.VALIDATION_ERROR, "Invalid member number"),
    (re.compile(r"error.*required", re.I), OutcomeCode.VALIDATION_ERROR, "Validation error on page"),
]


class ReplayRunner:
    def __init__(
        self,
        artifact: Artifact,
        params: dict[str, Any],
        safety_config: SafetyConfig | None = None,
        evidence_dir: Path | None = None,
        headless: bool = True,
        handoff: bool = False,
        mock_operator: bool = False,
        handoff_timeout_s: float = 300.0,
    ) -> None:
        self._artifact = artifact
        self._params = expand_application_params({str(k): str(v) for k, v in dict(params).items()})
        for param in artifact.parameters:
            if param.name not in self._params and param.default is not None:
                self._params[param.name] = param.default
        if "decision" in self._params:
            label, status = normalize_decision(str(self._params["decision"]))
            self._params["decision"] = label
            self._params["decision_status"] = status
        self._guard = SafetyGuard(
            safety_config
            or SafetyConfig(allowed_domains=[artifact.allowed_domain], block_irreversible=False)
        )
        self._evidence_dir = evidence_dir or (Path("evidence") / f"replay-{artifact.name}-{_ts()}")
        self._headless = headless
        self._handoff = handoff
        self._mock_operator = mock_operator
        self._handoff_timeout_s = handoff_timeout_s
        self._outputs: dict[str, Any] = {}
        self._start_ms: float = 0.0
        self._step_log: list[dict[str, Any]] = []

    async def run(self) -> ReplayResult:
        self._evidence_dir.mkdir(parents=True, exist_ok=True)
        self._start_ms = time.monotonic() * 1000
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=self._headless,
                args=[] if self._headless else ["--start-maximized"],
            )
            if self._headless:
                context = await browser.new_context(viewport={"width": 1280, "height": 900})
            else:
                context = await browser.new_context(no_viewport=True)
            page = await context.new_page()
            page.on("dialog", _accept_dialog)
            result: ReplayResult | None = None
            try:
                result = await self._execute(page)
                return result
            finally:
                self._write_steps_json(result)
                try:
                    await _screenshot(page, self._evidence_dir, "final")
                except Exception:
                    pass
                try:
                    await context.close()
                except Exception:
                    pass
                try:
                    await browser.close()
                except Exception:
                    pass

    async def _execute(self, page: Page) -> ReplayResult:
        try:
            await page.goto(self._artifact.entry_url, wait_until="domcontentloaded", timeout=15_000)
        except Exception as exc:
            return ReplayFailure(
                error_code=ErrorCode.TIMEOUT,
                message=f"Could not load entry URL: {exc}",
                step_index=-1,
            )

        for step in self._artifact.steps:
            if self._should_skip_step(step):
                self._record_step(step, status="skipped")
                continue
            result = await self._execute_step(page, step)
            if result is None:
                self._record_step(step, status="ok")
            else:
                self._record_step(step, status=result.status, result=result)
            if result is not None:
                if self._handoff and isinstance(result, ReplayFailure) and result.recoverable:
                    resumed = await self._try_handoff(page, step, result)
                    if resumed:
                        retry = await self._execute_step(page, step)
                        if retry is None:
                            pass
                        else:
                            return retry
                    else:
                        return result
                else:
                    return result

            page_text = await _get_page_text(page)
            outcome = _detect_business_outcome(page_text, step.step_index)
            if outcome:
                await _screenshot(page, self._evidence_dir, f"business-outcome-step-{step.step_index}")
                if self._step_log:
                    self._step_log[-1]["status"] = "business_outcome"
                    self._step_log[-1]["result"] = outcome.model_dump(mode="json")
                return outcome

        checkpoint_result = await self._verify_checkpoint(page)
        if checkpoint_result is not None:
            return checkpoint_result

        duration = int(time.monotonic() * 1000 - self._start_ms)
        return ReplaySuccess(
            outputs=self._outputs,
            steps_executed=len(self._artifact.steps),
            duration_ms=duration,
        )

    async def _try_handoff(self, page: Page, step: ArtifactStep, failure: ReplayFailure) -> bool:
        handler = EscalationHandler(
            page,
            self._artifact.goal,
            self._artifact.entry_url,
            intervention_dir=self._evidence_dir / "interventions",
            mock_operator=self._mock_operator,
            poll_timeout_s=self._handoff_timeout_s,
        )
        record = await handler.handle(
            EscalationRequest(
                reason=EscalationReason.ERROR_STATE,
                description=failure.message,
                current_step=f"step {step.step_index}: {self._step_label(step)}",
            ),
            intervention_id=str(uuid.uuid4())[:8],
        )
        return bool(record and record.resume)

    def _record_step(
        self,
        step: ArtifactStep,
        *,
        status: str,
        result: ReplayResult | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "step_index": step.step_index,
            "description": self._step_label(step),
            "action": step.action.model_dump(mode="json"),
            "status": status,
        }
        if result is not None:
            entry["result"] = result.model_dump(mode="json")
        self._step_log.append(entry)
        self._write_steps_json(None)

    def _write_steps_json(self, result: ReplayResult | None) -> None:
        dump_json(
            self._evidence_dir,
            "steps.json",
            {
                "kind": "replay",
                "artifact": self._artifact.name,
                "goal": self._interpolate(self._artifact.goal),
                "params": self._params,
                "outputs_so_far": self._outputs,
                "result": result.model_dump(mode="json") if result is not None else None,
                "steps": self._step_log,
            },
        )

    async def _execute_step(self, page: Page, step: ArtifactStep) -> ReplayResult | None:
        print(f"Step {step.step_index}: {self._step_label(step)}")
        last_error: ReplayResult | None = None
        for attempt in range(2):
            try:
                blocked = await self._dispatch_action(page, step)
                if blocked is not None:
                    return blocked
                last_error = None
                break
            except PWTimeout:
                await _screenshot(page, self._evidence_dir, f"timeout-step-{step.step_index}")
                last_error = ReplayFailure(
                    error_code=ErrorCode.TIMEOUT,
                    message=f"Timeout executing step {step.step_index}: {self._step_label(step)}",
                    step_index=step.step_index,
                    step_description=self._step_label(step),
                    recoverable=True,
                )
                if attempt == 0:
                    await page.wait_for_timeout(500)
                    continue
            except LocatorError as exc:
                await _screenshot(page, self._evidence_dir, f"locator-fail-step-{step.step_index}")
                last_error = ReplayFailure(
                    error_code=ErrorCode.LOCATOR_NOT_FOUND,
                    message=str(exc),
                    step_index=step.step_index,
                    step_description=self._step_label(step),
                    expected=f'Element [{exc.role}] "{exc.name}"',
                    observed="Element not found in page",
                    recoverable=True,
                )
                if attempt == 0:
                    await page.wait_for_timeout(500)
                    continue
            except Exception as exc:
                await _screenshot(page, self._evidence_dir, f"error-step-{step.step_index}")
                return ReplayFailure(
                    error_code=ErrorCode.LOCATOR_NOT_FOUND,
                    message=str(exc),
                    step_index=step.step_index,
                    step_description=self._step_label(step),
                )
        if last_error is not None:
            return last_error
        return None

    async def _dispatch_action(self, page: Page, step: ArtifactStep) -> ReplayResult | None:
        action = step.action
        if action.type == ActionType.NAVIGATE:
            assert isinstance(action, NavigateAction)
            await self._do_navigate(page, action)
        elif action.type == ActionType.CLICK:
            assert isinstance(action, ClickAction)
            return await self._do_click(page, action, step)
        elif action.type == ActionType.TYPE:
            assert isinstance(action, TypeAction)
            await self._do_type(page, action)
        elif action.type == ActionType.SELECT:
            assert isinstance(action, SelectAction)
            await self._do_select(page, action)
        elif action.type == ActionType.WAIT:
            assert isinstance(action, WaitAction)
            await self._do_wait(page, action)
        elif action.type == ActionType.READ:
            assert isinstance(action, ReadAction)
            await self._do_read(page, action)
        return None

    async def _do_navigate(self, page: Page, action: NavigateAction) -> None:
        url = self._interpolate(action.url_template or action.url)
        verdict = self._guard.check("navigate", {"url": url})
        if not verdict.allowed:
            raise RuntimeError(f"Safety blocked navigation: {verdict.reason}")
        await page.goto(url, wait_until="domcontentloaded", timeout=15_000)

    async def _do_click(self, page: Page, action: ClickAction, step: ArtifactStep) -> ReplayResult | None:
        locator = self._interpolate_locator(action.locator)
        verdict = self._guard.check("click", {"name": locator.name})
        if not verdict.allowed:
            return ReplayFailure(
                error_code=ErrorCode.SAFETY_BLOCK,
                message=verdict.reason or "Safety guard blocked click",
                step_index=step.step_index,
            )
        resolved = await resolve_locator(page, locator)
        await resolved.locator.click(timeout=8_000)
        await page.wait_for_timeout(500)
        await self._fill_decision_dependents(page, locator)
        return None

    def _should_skip_step(self, step: ArtifactStep) -> bool:
        action = step.action
        if action.type != ActionType.SELECT:
            return False
        html_name = (action.locator.html_name or "").lower()
        name = (action.locator.name or "").lower()
        decision = str(self._params.get("decision") or "")
        if html_name == "approved_limit" or "approved limit" in name:
            return decision != "Approve"
        if html_name == "decline_reason" or "decline reason" in name:
            return decision != "Decline"
        if html_name == "escalate_to" or "escalate to" in name:
            return decision != "Escalate to Senior Officer"
        return False

    async def _fill_decision_dependents(self, page: Page, locator: LocatorStrategy) -> None:
        if locator.role != "radio" and (locator.html_name or "").lower() != "decision":
            return
        label = locator.name
        if label not in DECISION_CHOICES and (locator.html_name or "").lower() != "decision":
            return
        if label == "Decline":
            await _select_if_visible(
                page,
                "select[name='decline_reason']",
                self._params.get("decline_reason"),
            )
        elif label == "Escalate to Senior Officer":
            await _select_if_visible(
                page,
                "select[name='escalate_to']",
                self._params.get("escalate_to"),
            )

    async def _do_type(self, page: Page, action: TypeAction) -> None:
        value = self._interpolate(action.value)
        resolved = await resolve_locator(page, self._interpolate_locator(action.locator))
        if not action.append:
            await resolved.locator.clear()
        await resolved.locator.fill(value)

    async def _do_select(self, page: Page, action: SelectAction) -> None:
        resolved = await resolve_locator(page, self._interpolate_locator(action.locator))
        raw = ""
        if action.option_label:
            raw = self._interpolate(action.option_label)
        elif action.option_value:
            raw = self._interpolate(action.option_value)
        if not raw.strip():
            raise ValueError("Select step has no option value after interpolation")
        try:
            await resolved.locator.select_option(label=raw)
            return
        except Exception:
            pass
        try:
            await resolved.locator.select_option(value=raw)
            return
        except Exception:
            pass
        options = await resolved.locator.locator("option").all_text_contents()
        match = next((opt.strip() for opt in options if raw.lower() in opt.lower()), "")
        if not match:
            raise ValueError(f"No select option matching {raw!r}")
        await resolved.locator.select_option(label=match)

    async def _do_wait(self, page: Page, action: WaitAction) -> None:
        if action.condition in ("element_visible", "element_hidden") and action.role and action.name:
            resolved = await resolve_locator(
                page,
                self._interpolate_locator(LocatorStrategy(role=action.role, name=action.name)),
            )
            state = "visible" if action.condition == "element_visible" else "hidden"
            await resolved.locator.wait_for(state=state, timeout=15_000)
        elif action.condition == "url_contains" and action.url_substring:
            await page.wait_for_url(
                f"**/*{self._interpolate(action.url_substring)}*", timeout=15_000
            )
        elif action.condition == "duration" and action.duration_ms:
            await page.wait_for_timeout(min(action.duration_ms, 5000))

    async def _do_read(self, page: Page, action: ReadAction) -> None:
        resolved = await resolve_locator(page, self._interpolate_locator(action.locator))
        try:
            value = (await resolved.locator.inner_text()).strip()
        except Exception:
            value = (await resolved.locator.input_value()).strip()
        self._outputs[action.output_key] = value

    async def _verify_checkpoint(self, page: Page) -> ReplayFailure | None:
        cp = self._artifact.checkpoint
        current_url = page.url
        page_text = await _get_page_text(page)

        url_contains = self._interpolate(cp.url_contains) if cp.url_contains else None
        text_visible = self._interpolate(cp.text_visible) if cp.text_visible else None

        if url_contains and url_contains not in current_url:
            return ReplayFailure(
                error_code=ErrorCode.CHECKPOINT_FAILED,
                message=f"Checkpoint failed: URL does not contain '{url_contains}'",
                step_index=len(self._artifact.steps),
                step_description="Final checkpoint",
                expected=f"URL contains '{url_contains}'",
                observed=f"URL is '{current_url}'",
            )

        if text_visible and text_visible.lower() not in page_text.lower():
            return ReplayFailure(
                error_code=ErrorCode.CHECKPOINT_FAILED,
                message=f"Checkpoint failed: text '{text_visible}' not visible on page",
                step_index=len(self._artifact.steps),
                step_description="Final checkpoint",
                expected=f"Page contains '{text_visible}'",
                observed="Text not found",
            )
        if cp.element_visible:
            try:
                await resolve_locator(page, self._interpolate_locator(cp.element_visible))
            except Exception:
                return ReplayFailure(
                    error_code=ErrorCode.CHECKPOINT_FAILED,
                    message=(
                        f"Checkpoint element not visible: "
                        f"[{cp.element_visible.role}] '{cp.element_visible.name}'"
                    ),
                    step_index=len(self._artifact.steps),
                    step_description="Final checkpoint",
                    expected=f"Element [{cp.element_visible.role}] '{cp.element_visible.name}' visible",
                    observed="Element not found or hidden",
                )
        print(f"Checkpoint passed: {self._interpolate(cp.description)}")
        return None

    def render(self, template: str) -> str:
        return self._interpolate(template)

    def _step_label(self, step: ArtifactStep) -> str:
        return self._interpolate(step.description)

    def _interpolate_locator(self, locator: LocatorStrategy) -> LocatorStrategy:
        data = locator.model_dump()
        for key, value in data.items():
            if isinstance(value, str) and "{" in value:
                data[key] = self._interpolate(value)
        return LocatorStrategy.model_validate(data)

    def _interpolate(self, template: str) -> str:
        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in self._params:
                raise ValueError(
                    f"Artifact requires parameter '{key}' but it was not supplied. "
                    f"Available: {list(self._params.keys())}"
                )
            return str(self._params[key])

        return re.sub(r"\{(\w+)\}", replace, template)


async def _select_if_visible(page: Page, css: str, preferred: object) -> None:
    loc = page.locator(css)
    try:
        if not await loc.is_visible():
            return
    except Exception:
        return
    if not preferred or not str(preferred).strip():
        return
    text = str(preferred)
    try:
        await loc.select_option(label=text)
        return
    except Exception:
        try:
            await loc.select_option(value=text)
        except Exception:
            pass


async def _accept_dialog(dialog: Dialog) -> None:
    await dialog.accept()


async def _get_page_text(page: Page) -> str:
    parts: list[str] = []
    try:
        parts.append(await page.inner_text("body"))
    except Exception:
        pass
    for frame in page.frames:
        if frame == page.main_frame or frame.url.startswith("about:"):
            continue
        try:
            parts.append(await frame.inner_text("body"))
        except Exception:
            pass
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _detect_business_outcome(page_text: str, step_index: int) -> BusinessOutcome | None:
    for pattern, code, description in _BUSINESS_OUTCOMES:
        if pattern.search(page_text):
            return BusinessOutcome(
                outcome_code=code,
                description=description,
                step_index=step_index,
            )
    return None


async def _screenshot(page: Page, directory: Path, name: str) -> None:
    try:
        await page.screenshot(path=str(directory / f"{name}.png"), full_page=True)
    except Exception:
        pass


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
