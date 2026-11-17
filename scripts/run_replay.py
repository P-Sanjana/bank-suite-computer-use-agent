#!/usr/bin/env python3
"""
Deterministically replay a saved artifact.
 
Usage:
    # Basic replay
    python scripts/run_replay.py --artifact artifacts/look-up-member-v1-abc123.json \
        --param member_no=1042381
 
    # With multiple params
    python scripts/run_replay.py --artifact artifacts/review-application-v1-abc123.json \
        --param app_id=APP-1001187 --param decision=Decline \
        --param decline_reason="Does not meet product policy" \
        --param officer_notes="DTI above policy"
 
    # Show browser
    python scripts/run_replay.py --artifact artifacts/... --param ... --headful
 
Exit codes:
    0 — success
    1 — hard failure (locator not found, checkpoint failed, etc.)
    2 — business outcome (member not found, already decided, etc.)
    3 — safety block
"""
from __future__ import annotations
 
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.artifact.parameterizer import extras_required_for_decision, expand_application_params, missing_review_params, normalize_decision, parameterize
from src.artifact.writer import load_artifact
from src.replay.runner import ReplayRunner
from src.schema.result import ReplaySuccess, BusinessOutcome, ReplayFailure
from src.safety.guard import SafetyConfig

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Replay a saved artifact deterministically.")
    p.add_argument("--artifact", required=True, help="Path to artifact JSON file")
    p.add_argument("--param", action="append", default=[], metavar="KEY=VALUE",
                   help="Input parameter (repeat for multiple)")
    p.add_argument("--headful", action="store_true", help="Show browser UI")
    p.add_argument("--block-irreversible", action="store_true", help="Block irreversible actions (safer for production replay)")
    p.add_argument("--handoff", action="store_true", help="On recoverable failure, pause the live session for an operator")
    p.add_argument("--mock-operator", action="store_true", help="Auto-resume handoff with a mock operator")
    return p.parse_args()

def parse_params(raw: list[str]) -> dict[str, str]:
    params: dict[str, str] = {}
    for item in raw:
        if "=" not in item:
            print(f"Ignoring malformed param (expected KEY+VALUE): {item!r}")
            continue
        key, _, value = item.partition("=")
        params[key.strip()] = value.strip()
    return params

async def main() -> None:
    args = parse_args()
    artifact_path = Path(args.artifact)

    if not artifact_path.exists():
        print(f"Artifact not found: {artifact_path}")
        sys.exit(1)

    try:
        artifact = parameterize(load_artifact(artifact_path))
    except Exception as exc:
        print(f"Failed to load artifact: {exc}")
        sys.exit(1)

    params = expand_application_params(parse_params(args.param))

    missing = missing_review_params(params, artifact)
    if missing:
        print(f"Missing required parameters: {missing}")
        print("    Artifact parameters:")
        for p in artifact.parameters:
            req = " (required)" if p.required else f" (default: {p.default})"
            print(f"    {p.name}: {p.description}{req}")
        decision = params.get("decision")
        if decision:
            try:
                label, _ = normalize_decision(decision)
                extras = extras_required_for_decision(label)
                print(f"    For {label}: officer_notes" + (f", {', '.join(extras)}" if extras else ""))
            except ValueError as exc:
                print(f"    {exc}")
        sys.exit(1)

    safety_config = SafetyConfig(
        allowed_domains=[artifact.allowed_domain],
        block_irreversible=args.block_irreversible,
    )

    runner = ReplayRunner(
        artifact=artifact,
        params=params,
        safety_config=safety_config,
        headless=not args.headful,
        handoff=args.handoff,
        mock_operator=args.mock_operator,
    )

    print(f"\n▶ Replaying: {artifact.name} v{artifact.version}")
    print(f"    Goal: {runner.render(artifact.goal)}")
    print(f"    Steps: {len(artifact.steps)}")
    print(f"    Params: {params}\n")

    result = await runner.run()

    print(f"\n{'='*60}")

    if isinstance(result, ReplaySuccess):
        print(f"    REPLAY SUCCESS")
        print(f"    Steps executed: {result.steps_executed}")
        print(f"    Duration: {result.duration_ms}ms")
        if result.outputs:
            print(f"    Outputs: {json.dumps(result.outputs, indent=2)}")
        sys.exit(0)

    elif isinstance(result, BusinessOutcome):
        print(f"    BUSINESS OUTCOME: {result.outcome_code}")
        print(f"    {result.description}")
        print(f"    At step: {result.step_index}")
        print("    (This is a legitimate application state, not a failure)")
        sys.exit(2)

    elif isinstance(result, ReplayFailure):
        print(f"    REPLAY FAILURE: {result.error_code}")
        print(f"    Message: {result.message}")
        print(f"    At step: {result.step_index} - {result.step_description}")
        if result.expected:
            print(f"    Expected: {result.expected}")
        if result.observed:
            print(f"    Observed: {result.observed}")
        if result.recoverable:
            print(f"    (Recoverable - retry may succeed)")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
