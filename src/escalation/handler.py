"""
Human-in-the-loop handoff on the live Playwright session.

When automation cannot proceed:
  1. Pause (browser stays open)
  2. Write an intervention request + screenshot under evidence/
  3. Show a collapsed dock on the same page
  4. Human works in the app, expands the dock, enters a required description, Resume or Stop
  5. The handler writes the response JSON itself
  6. Discovery/replay continues only if the human chose Resume
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from playwright.async_api import Page

from ..observability.dump import dump_json

class EscalationReason(str, Enum):
    STUCK = "STUCK"
    IRREVERSIBLE_ACTION = "IRREVERSIBLE_ACTION"
    ERROR_STATE = "ERROR_STATE"
    POLICY_BLOCK = "POLICY_BLOCK"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass
class EscalationRequest:
    reason: EscalationReason
    description: str
    current_step: str
    suggested_action: str = ""


@dataclass
class InterventionRequest:
    id: str
    reason: str
    description: str
    current_step: str
    suggested_action: str
    goal: str
    entry_url: str
    current_url: str
    screenshot_path: str | None
    created_at: str
    status: str = "pending"


@dataclass
class InterventionResponse:
    intervention_id: str
    resolved_by: str
    actions_taken: str
    resume: bool
    resolved_at: str


@dataclass
class HandoffRecord:
    intervention_id: str
    reason: str
    operator: str
    actions_taken: str
    duration_seconds: float
    timestamp: str
    resume: bool = True


INTERVENTION_DIR = Path("evidence") / "interventions"
POLL_INTERVAL_S = 0.4
POLL_TIMEOUT_S = 300.0
_TITLE_PREFIX = "[ESCALATED] "

_DOCK_JS = """
(payload) => {
  const { reason, description, goal, currentStep, suggested } = payload;
  window.__CUI_OPERATOR_DECISION__ = null;
  const stale = document.getElementById("cui-operator-host");
  if (stale) stale.remove();

  const host = document.createElement("div");
  host.id = "cui-operator-host";
  host.setAttribute("data-cui-operator", "1");
  host.style.cssText = [
    "position:fixed",
    "left:50%",
    "bottom:20px",
    "transform:translateX(-50%)",
    "z-index:2147483647",
    "display:flex",
    "flex-direction:column",
    "align-items:center",
    "gap:8px",
    "font:14px/1.3 Segoe UI,Arial,sans-serif",
    "color:#fff",
    "pointer-events:auto",
  ].join(";");

  const panel = document.createElement("div");
  panel.id = "cui-operator-panel";
  panel.style.cssText = [
    "display:none",
    "width:min(420px,92vw)",
    "background:#1a1a1a",
    "border:1px solid #555",
    "border-radius:12px",
    "padding:12px",
    "box-shadow:0 12px 32px rgba(0,0,0,.45)",
    "color:#fff",
    "font:13px/1.35 Segoe UI,Arial,sans-serif",
  ].join(";");

  const meta = document.createElement("div");
  meta.style.cssText = "color:#ccc;font-size:12px;max-height:72px;overflow:auto;white-space:pre-wrap;";
  meta.textContent = [
    "Reason: " + reason,
    "Goal: " + goal,
    "Step: " + currentStep,
    description ? ("Why: " + description) : "",
    suggested ? ("Suggested: " + suggested) : "",
  ].filter(Boolean).join("\\n");

  const label = document.createElement("label");
  label.setAttribute("for", "cui-operator-notes");
  label.style.cssText = "display:block;font-weight:700;margin:8px 0 4px;color:#fff;";
  label.innerHTML = 'Description <span style="color:#ffb4b4">*</span>';

  const notes = document.createElement("textarea");
  notes.id = "cui-operator-notes";
  notes.placeholder = "Required: what you did on this screen";
  notes.style.cssText = [
    "width:100%",
    "box-sizing:border-box",
    "min-height:64px",
    "resize:vertical",
    "border:1px solid #666",
    "border-radius:6px",
    "background:#111",
    "color:#fff",
    "padding:8px",
    "font:13px Segoe UI,Arial,sans-serif",
  ].join(";");

  const err = document.createElement("div");
  err.id = "cui-operator-error";
  err.style.cssText = "color:#ff8a8a;font-size:12px;min-height:16px;margin-top:4px;";

  const row = document.createElement("div");
  row.style.cssText = "display:flex;gap:8px;margin-top:8px;";

  const resume = document.createElement("button");
  resume.id = "cui-operator-resume";
  resume.type = "button";
  resume.textContent = "Resume";
  resume.disabled = true;
  resume.style.cssText = "flex:1;padding:8px;border:0;border-radius:6px;font-weight:700;background:#0a7a32;color:#fff;cursor:not-allowed;opacity:.45;";

  const stop = document.createElement("button");
  stop.id = "cui-operator-stop";
  stop.type = "button";
  stop.textContent = "Stop";
  stop.disabled = true;
  stop.style.cssText = "flex:1;padding:8px;border:0;border-radius:6px;font-weight:700;background:#7a0019;color:#fff;cursor:not-allowed;opacity:.45;";

  row.appendChild(resume);
  row.appendChild(stop);
  panel.appendChild(meta);
  panel.appendChild(label);
  panel.appendChild(notes);
  panel.appendChild(err);
  panel.appendChild(row);

  const pill = document.createElement("button");
  pill.id = "cui-operator-toggle";
  pill.type = "button";
  pill.title = "Expand operator dock";
  pill.style.cssText = [
    "display:flex",
    "align-items:center",
    "gap:10px",
    "background:#111",
    "color:#fff",
    "border:2px solid #f2c94c",
    "border-radius:999px",
    "padding:10px 18px",
    "box-shadow:0 10px 28px rgba(0,0,0,.45)",
    "cursor:pointer",
    "user-select:none",
    "white-space:nowrap",
    "font:600 14px/1 Segoe UI,Arial,sans-serif",
  ].join(";");
  pill.innerHTML = '<span style="width:10px;height:10px;border-radius:50%;background:#f2c94c;box-shadow:0 0 0 5px rgba(242,201,76,.28);flex:none"></span><b>ESCALATED</b><span>Take control</span><span id="cui-operator-chevron">▴</span>';

  host.appendChild(panel);
  host.appendChild(pill);

  function descriptionOk() {
    return (notes.value || "").trim().length > 0;
  }
  function sync() {
    const ok = descriptionOk();
    resume.disabled = !ok;
    stop.disabled = !ok;
    resume.style.opacity = ok ? "1" : ".45";
    stop.style.opacity = ok ? "1" : ".45";
    resume.style.cursor = ok ? "pointer" : "not-allowed";
    stop.style.cursor = ok ? "pointer" : "not-allowed";
    notes.style.borderColor = (!ok && notes.value.length > 0) ? "#e05" : "#666";
    if (ok) err.textContent = "";
  }
  function decide(shouldResume) {
    if (!descriptionOk()) {
      err.textContent = "Description is required.";
      notes.style.borderColor = "#e05";
      notes.focus();
      panel.style.display = "block";
      return;
    }
    window.__CUI_OPERATOR_DECISION__ = {
      resume: shouldResume,
      actions_taken: notes.value.trim(),
      resolved_by: "human-operator",
    };
    resume.disabled = true;
    stop.disabled = true;
  }

  pill.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    const open = panel.style.display !== "block";
    panel.style.display = open ? "block" : "none";
    const chevron = host.querySelector("#cui-operator-chevron");
    if (chevron) chevron.textContent = open ? "▾" : "▴";
  });
  notes.addEventListener("input", sync);
  resume.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); decide(true); });
  stop.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); decide(false); });
  sync();

  const root = document.body || document.documentElement;
  root.appendChild(host);
}
"""


class EscalationHandler:
    def __init__(
        self,
        page: Page,
        goal: str,
        entry_url: str,
        intervention_dir: Path | None = None,
        mock_operator: bool = False,
        poll_timeout_s: float = POLL_TIMEOUT_S,
        poll_interval_s: float = POLL_INTERVAL_S,
    ) -> None:
        self._page = page
        self._goal = goal
        self._entry_url = entry_url
        self._dir = intervention_dir or INTERVENTION_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._mock_operator = mock_operator
        self._poll_timeout_s = poll_timeout_s
        self._poll_interval_s = poll_interval_s
        self._original_title: str | None = None
        self._active_request: EscalationRequest | None = None

    async def handle(
        self,
        request: EscalationRequest,
        intervention_id: str,
    ) -> HandoffRecord | None:
        started = datetime.now(timezone.utc)
        screenshot_path = await self._screenshot(intervention_id)

        iv_request = InterventionRequest(
            id=intervention_id,
            reason=request.reason.value,
            description=request.description,
            current_step=request.current_step,
            suggested_action=request.suggested_action,
            goal=self._goal,
            entry_url=self._entry_url,
            current_url=self._page.url,
            screenshot_path=str(screenshot_path) if screenshot_path else None,
            created_at=started.isoformat(),
        )
        request_path = self._dir / f"{intervention_id}-request.json"
        request_path.write_text(json.dumps(asdict(iv_request), indent=2), encoding="utf-8")
        self._write_handoff_summary(iv_request, response=None, status="awaiting_operator")

        print("\n" + "=" * 60)
        print(f"ESCALATION [{request.reason.value}] — HUMAN MUST TAKE CONTROL")
        print(f"Goal: {self._goal}")
        print(f"Why: {request.description}")
        print("A collapsed ESCALATED dock is at the bottom of this window.")
        print("Use the page, expand the dock, enter a description, then Resume or Stop.")
        print("The response JSON is written for you.")
        print(f"Request: {request_path}")
        print("=" * 60 + "\n")

        if self._mock_operator:
            self.write_response(
                intervention_id,
                actions_taken="Mock operator completed the blocking step on the live session.",
                resume=True,
                operator="mock-operator",
                intervention_dir=self._dir,
            )
        else:
            self._active_request = request
            await self._show_dock(request)

        response = await self._wait_for_decision(intervention_id)
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()

        if response is None:
            response = self.write_response(
                intervention_id,
                actions_taken=f"Timed out after {self._poll_timeout_s}s waiting for operator.",
                resume=False,
                operator="timeout",
                intervention_dir=self._dir,
            )
            print(f"Escalation timed out after {self._poll_timeout_s}s. Aborting run.")

        await self._remove_dock()
        self._active_request = None
        iv_request.status = "resolved"
        self._write_handoff_summary(iv_request, response=response, status="resolved")

        record = HandoffRecord(
            intervention_id=intervention_id,
            reason=request.reason.value,
            operator=response.resolved_by,
            actions_taken=response.actions_taken,
            duration_seconds=elapsed,
            timestamp=started.isoformat(),
            resume=response.resume,
        )
        if response.resume:
            print("Operator chose Resume. Continuing automation on the same session.")
        else:
            print("Operator chose Stop. Run will not resume.")
        return record

    def _write_handoff_summary(
        self,
        request: InterventionRequest,
        response: InterventionResponse | None,
        status: str,
    ) -> None:
        evidence_root = self._dir.parent if self._dir.name == "interventions" else self._dir
        dump_json(
            evidence_root,
            "handoff.json",
            {
                "status": status,
                "request": asdict(request),
                "response": asdict(response) if response else None,
            },
        )

    async def _show_dock(self, request: EscalationRequest) -> None:
        try:
            if self._page.is_closed():
                return
            await self._page.bring_to_front()
            self._original_title = await self._page.title()
            await self._page.evaluate(
                """(prefix) => {
                  if (!document.title.startsWith(prefix)) {
                    document.title = prefix + document.title;
                  }
                }""",
                _TITLE_PREFIX,
            )
            payload = {
                "reason": request.reason.value,
                "description": request.description,
                "goal": self._goal,
                "currentStep": request.current_step,
                "suggested": request.suggested_action,
            }
            await self._page.evaluate(_DOCK_JS, payload)
            for frame in self._page.frames:
                if frame == self._page.main_frame:
                    continue
                try:
                    await frame.evaluate(_DOCK_JS, payload)
                except Exception:
                    pass
        except Exception as exc:
            print(f"Could not show operator dock (use --headful): {exc}")

    async def _remove_dock(self) -> None:
        try:
            await self._page.evaluate(
                """() => {
                  const el = document.getElementById("cui-operator-host");
                  if (el) el.remove();
                  window.__CUI_OPERATOR_DECISION__ = null;
                }"""
            )
        except Exception:
            pass
        if self._original_title is not None:
            try:
                await self._page.evaluate("(t) => { document.title = t; }", self._original_title)
            except Exception:
                pass
            self._original_title = None

    async def _wait_for_decision(self, intervention_id: str) -> InterventionResponse | None:
        response_path = self._dir / f"{intervention_id}-response.json"
        elapsed = 0.0
        while elapsed < self._poll_timeout_s:
            if self._page.is_closed():
                return self.write_response(
                    intervention_id,
                    actions_taken="Browser closed during handoff.",
                    resume=False,
                    operator="browser-closed",
                    intervention_dir=self._dir,
                )
            if response_path.exists():
                try:
                    data = json.loads(response_path.read_text(encoding="utf-8"))
                    return InterventionResponse(**data)
                except Exception as exc:
                    print(f"Could not parse response file: {exc}")

            if not self._mock_operator:
                if self._active_request is not None:
                    await self._ensure_dock(self._active_request)
                decision = await self._read_operator_decision()
                if decision is not None:
                    notes = str(decision.get("actions_taken") or "").strip()
                    if not notes:
                        await asyncio.sleep(self._poll_interval_s)
                        elapsed += self._poll_interval_s
                        continue
                    return self.write_response(
                        intervention_id,
                        actions_taken=notes,
                        resume=bool(decision.get("resume")),
                        operator=str(decision.get("resolved_by") or "human-operator"),
                        intervention_dir=self._dir,
                    )

            await asyncio.sleep(self._poll_interval_s)
            elapsed += self._poll_interval_s
        return None

    async def _ensure_dock(self, request: EscalationRequest) -> None:
        try:
            present = await self._page.evaluate(
                "() => !!document.getElementById('cui-operator-host')"
            )
        except Exception:
            return
        if not present:
            await self._show_dock(request)

    async def _read_operator_decision(self) -> dict | None:
        try:
            return await self._page.evaluate("() => window.__CUI_OPERATOR_DECISION__")
        except Exception:
            return None

    async def _screenshot(self, intervention_id: str) -> Path | None:
        try:
            path = self._dir / f"{intervention_id}-screenshot.png"
            await self._page.screenshot(path=str(path), full_page=True)
            return path
        except Exception:
            return None

    @staticmethod
    def write_response(
        intervention_id: str,
        actions_taken: str,
        resume: bool,
        operator: str = "human-operator",
        intervention_dir: Path | None = None,
    ) -> InterventionResponse:
        directory = intervention_dir or INTERVENTION_DIR
        directory.mkdir(parents=True, exist_ok=True)
        response = InterventionResponse(
            intervention_id=intervention_id,
            resolved_by=operator,
            actions_taken=actions_taken,
            resume=resume,
            resolved_at=datetime.now(timezone.utc).isoformat(),
        )
        path = directory / f"{intervention_id}-response.json"
        path.write_text(json.dumps(asdict(response), indent=2), encoding="utf-8")
        print(f"Operator response written: {path}")
        return response

    @staticmethod
    def write_mock_response(
        intervention_id: str,
        actions_taken: str = "Manual steps performed by operator.",
        resume: bool = True,
        operator: str = "mock-operator",
        intervention_dir: Path | None = None,
    ) -> Path:
        directory = intervention_dir or INTERVENTION_DIR
        EscalationHandler.write_response(
            intervention_id,
            actions_taken=actions_taken,
            resume=resume,
            operator=operator,
            intervention_dir=directory,
        )
        return directory / f"{intervention_id}-response.json"
