"""
Converts the RawTrace produced by the Recorder into
a validated Artifact that the replay runner can execute.
 
The builder does three things:
  1. Maps each RawStep → ArtifactStep, inferring locator strategy and
     detecting irreversible actions.
  2. Calls the parameterizer to replace hardcoded values with {placeholders}.
  3. Infers the checkpoint from the final declared outputs / page state.
"""
from __future__ import annotations
 
import re
from typing import Any
 
from ..discovery.recorder import RawStep, RawTrace
from ..discovery.tools.definitions import (
    NAVIGATE, CLICK, TYPE, SELECT, WAIT, READ, READ_STATE,
    IRREVERSIBLE_HINT_NAMES,
)
from ..goal import preserve_dollar_amounts
from ..schema.artifact import (
    Artifact, ArtifactStep, ArtifactAction,
    NavigateAction, ClickAction, TypeAction, SelectAction, WaitAction, ReadAction,
    LocatorStrategy, Checkpoint, InputParameter, OutputField,
    ParameterType, ActionType,
)

def build_artifact(
    trace: RawTrace,
    *,
    artifact_id: str,
    name: str,
    description: str,
    allowed_domain: str,
    parameters: list[InputParameter] | None = None,
    outputs: list[OutputField] | None = None,
) -> Artifact:
    """
    Convert a successful RawTrace into a validated Artifact.
    Only call this on traces with outcome_type == "success".
    """
    if trace.outcome_type != "success":
        raise ValueError(
            f"Cannot build artifact from trace with outcome '{trace.outcome_type}'. Only successful "
            "runs produce artifacts."
        )
    steps = _build_steps(trace.steps)
    checkpoint = _infer_checkpoint(trace)

    return Artifact(
        id=artifact_id,
        name=name,
        description=preserve_dollar_amounts(description),
        entry_url=trace.entry_url,
        allowed_domain=allowed_domain,
        parameters=parameters or [],
        outputs=outputs or _infer_outputs(trace),
        steps=steps,
        checkpoint=checkpoint,
        goal=preserve_dollar_amounts(trace.goal),
        total_discovery_steps=trace.total_steps,
    )

def _build_steps(raw_steps: list[RawStep]) -> list[ArtifactStep]:
    artifact_steps: list[ArtifactStep] = []
    for raw in raw_steps:

        # Skip read_state - it's an observation tool, not a replayable action
        if raw.tool == READ_STATE:
            continue

        # Skip failed steps - the artifact only records what worked
        if not raw.success:
            continue

        action = _map_action(raw)
        if action is None:
            continue

        step = ArtifactStep(
            step_index=len(artifact_steps),
            description=_describe(raw),
            action=action,
            expected_url_contains=raw.page_url,
            agent_reasoning=raw.agent_reasoning or "",
        )
        artifact_steps.append(step)
    return artifact_steps

def _map_action(raw: RawStep) -> ArtifactAction | None:
    inp = raw.input

    if raw.tool == NAVIGATE:
        return NavigateAction(url=str(inp.get("url", "")))

    if raw.tool == CLICK:
        locator = _locator(raw)
        name_lower = str(inp.get("name", "")).lower()
        irreversible = any(hint in name_lower for hint in IRREVERSIBLE_HINT_NAMES)
        return ClickAction(locator=locator, irreversible=irreversible)

    if raw.tool == TYPE:
        locator = _locator(raw)
        value = str(inp.get("value", ""))
        sensitive = bool(inp.get("sensitive", False))
        return TypeAction(
            locator=locator,
            value=value,
            sensitive=sensitive,
            append=bool(inp.get("append", False)),
        )

    if raw.tool == SELECT:
        locator = _locator(raw)
        return SelectAction(
            locator=locator,
            option_label=inp.get("option_label") and str(inp["option_label"]) or None,
            option_value=inp.get("option_value") and str(inp["option_value"]) or None,
        )

    if raw.tool == READ:
        key = str(inp.get("output_key") or "value")
        return ReadAction(output_key=key, locator=_locator(raw))

    if raw.tool == WAIT:
        condition = str(inp.get("condition", "duration"))
        return WaitAction(
            condition=condition,
            role=inp.get("role") and str(inp["role"]) or None,
            name=inp.get("name") and str(inp["name"]) or None,
            url_substring=inp.get("url_substring") and str(inp["url_substring"]) or None,
            duration_ms=inp.get("duration_ms") and int(inp["duration_ms"]) or None,
        )
    return None

def _locator(raw: RawStep) -> LocatorStrategy:
    if raw.resolved_locator:
        return LocatorStrategy.model_validate(raw.resolved_locator)
    inp = raw.input
    nth_raw = inp.get("nth")
    nth = int(nth_raw) if nth_raw is not None and nth_raw != "" else None
    return LocatorStrategy(
        role=str(inp.get("role") or ""),
        name=str(inp.get("name") or ""),
        label=str(inp["label"]) if inp.get("label") else None,
        nth=nth,
        html_name=str(inp["html_name"]) if inp.get("html_name") else None,
        html_id=str(inp["html_id"]) if inp.get("html_id") else None,
        resolved_via=str(inp.get("resolved_via") or ""),
        rationale=(
            f"Recorded from discovery: role={inp.get('role')!r}, name={inp.get('name')!r}"
        ),
    )

def _describe(raw: RawStep) -> str:
    inp = raw.input
    if raw.tool == NAVIGATE:
        return f"Navigate to {inp.get('url')}"
    if raw.tool == CLICK:
        return f"Click \"{inp.get('name')}\""
    if raw.tool == TYPE:
        val = "[REDACTED]" if inp.get("sensitive") else f"\"{inp.get('value')}\""
        return f"Type {val} into \"{inp.get('name')}\""
    if raw.tool == SELECT:
        chosen = inp.get("option_label") or inp.get("option_value")
        return f"Select \"{chosen}\" in \"{inp.get('name')}\""
    if raw.tool == WAIT:
        return f"Wait: {inp.get('condition')} - {inp.get('reason', '')}"
    if raw.tool == READ:
        return f"Read \"{inp.get('output_key')}\" from \"{inp.get('name') or inp.get('label')}\""
    return f"{raw.tool}: {inp}"

def _infer_checkpoint(trace: RawTrace) -> Checkpoint:
    """
    Build the success checkpoint from what the agent declared as evidence. Falls back to URL-based 
    check if evidence is thin.
    """
    evidence = trace.checkpoint_evidence or ""
    text_visible: str | None = None
    if trace.declared_outputs:
        for value in trace.declared_outputs.values():
            if isinstance(value, str) and value.strip() and len(value.strip()) <= 40:
                text_visible = value.strip()
                break
    if text_visible is None:
        text_match = re.search(r'"([^"]{1,80})"', evidence)
        if text_match:
            text_visible = text_match.group(1)

    # Use the final steps' URL as a URL check if available
    url_contains: str | None = None
    if trace.steps:
        last_url = trace.steps[-1].page_url
        if last_url:
            # Extract the path component as the check substring
            path = last_url.split("?")[0].split("/")[-1]
            if path:
                url_contains = path
    return Checkpoint(
        description=evidence or "Goal declared complete by agent",
        url_contains=url_contains,
        text_visible=text_visible,
    )

def _infer_outputs(trace: RawTrace) -> list[OutputField]:
    fields: dict[str, OutputField] = {}
    for raw in trace.steps:
        if not raw.extracted:
            continue
        loc = LocatorStrategy.model_validate(raw.resolved_locator) if raw.resolved_locator else None
        for key, value in raw.extracted.items():
            fields[key] = OutputField(
                name=key,
                type=_ptype(value),
                description=f"Extracted during discovery: {key}",
                source_locator=loc,
            )
    if trace.declared_outputs:
        for key, value in trace.declared_outputs.items():
            if key not in fields:
                fields[key] = OutputField(
                    name=key,
                    type=_ptype(value),
                    description=f"Extracted during discovery: {key}",
                )
    return list(fields.values())


def _ptype(value: Any) -> ParameterType:
    if isinstance(value, bool):
        return ParameterType.BOOLEAN
    if isinstance(value, int) and not isinstance(value, bool):
        return ParameterType.INTEGER
    return ParameterType.STRING