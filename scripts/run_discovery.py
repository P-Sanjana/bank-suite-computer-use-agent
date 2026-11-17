#!/usr/bin/env python3
"""
Run the LLM-driven discovery agent against Bank Suite.
 
Usage:
    python scripts/run_discovery.py \
        --goal "Look up member 1042381 and read their credit score" \
        --entry "http://localhost:3000/member-lookup.html" \
        --headful   # optional: show the browser
 
After a successful run, the artifact is written to artifacts/ and the
evidence is written to evidence/.
 
Requires: ANTHROPIC_API_KEY in environment.
"""
from __future__ import annotations
 
import argparse
import asyncio
import json
import sys
from pathlib import Path

# Allow running from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.discovery.agent import DiscoveryConfig, run_discovery
from src.artifact.builder import build_artifact
from src.artifact.parameterizer import parameterize
from src.artifact.writer import write_artifact, new_artifact_id
from src.goal import preserve_dollar_amounts

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run LLM-driven discovery against Bank Suite.")
    p.add_argument("--goal", required=True, help="Natural language goal")
    p.add_argument("--entry", default="http://localhost:3000/member-lookup.html", help="Entry URL")
    p.add_argument("--domain", default="localhost", help="Permitted domain")
    p.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="Max discovery turns (default 30, or 80 for the new-application wizard)",
    )
    p.add_argument("--headful", action="store_true", help="Show browser UI")
    p.add_argument("--artifact-name", default=None, help="Name for the artifact file")
    p.add_argument(
        "--mock-operator",
        action="store_true",
        help="On escalation, write a mock operator resume so the live session can continue",
    )
    return p.parse_args()

async def main() -> None:
    args = parse_args()
    goal = preserve_dollar_amounts(args.goal)
    config = DiscoveryConfig(
        goal=goal,
        entry_url=args.entry,
        allowed_domain=args.domain,
        max_steps=args.max_steps,
        headless=not args.headful,
        mock_operator=args.mock_operator,
    )

    print(f"\n Starting discovery run")
    print(f"    Goal: {goal}")
    print(f"    Entry: {args.entry}")
    print(f"    Max steps: {config.max_steps}\n")

    outcome = await run_discovery(config)

    print(f"\n{'='*60}")
    print(f"Outcome: {outcome.status.upper()}")

    if outcome.status == "success":
        print(f"Outputs: {json.dumps(outcome.outputs, indent=2)}")

        # Build and write artifact
        name = args.artifact_name or _slugify(goal)
        artifact = build_artifact(
            outcome.trace,
            artifact_id=new_artifact_id(),
            name=name,
            description=goal,
            allowed_domain=args.domain,
        )
        artifact = parameterize(artifact)
        path = write_artifact(artifact)
        print(f"\n Artifact saved: {path}")
        print(f"    Parameter: {[p.name for p in artifact.parameters]}")
        print(f"    Steps: {len(artifact.steps)}")

    elif outcome.status == "escalated":
        print(f"Escalation reason: {outcome.reason}")
        print("Run did not complete - human intervention required.")
        sys.exit(2)

    elif outcome.status == "failed":
        print(f"Error: {outcome.error}")
        sys.exit(1)

    elif outcome.status == "max_steps":
        print("Max steps reached without completion.")
        sys.exit(3)

def _slugify(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", s.lower())[:50].strip("-")

if __name__ == "__main__":
    asyncio.run(main())