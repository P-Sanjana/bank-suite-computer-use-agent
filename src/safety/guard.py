"""
Enforces the allowlist policy before every action dispatch. Called by both the discovery agent 
loop and the replay runner. Returns a SafetyVerdict - callers decide what to do with it.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from pydantic import BaseModel
from pydantic_settings import BaseSettings

class SafetyConfig(BaseModel):
    """
    Configurable allowlist loaded from a JSON file or constructed in code. Defaults are 
    permissive enough to run against any application locally. 
    """
    allowed_domains: list[str] = ["localhost", "127.0.0.1"]
    allowed_action_types: list[str] = [
        "navigate", "click", "type", "select", "wait", "read", "read_state",
    ]
    irreversible_name_hints: list[str] = ["submit", "confirm", "approve", "decline", "delete", 
                                          "remove", "override", "finalize"]

    # if true, irreversible actions are blocked entrirely (replay mode)
    # if false, they are flagged but allowed (discovery mode)
    block_irreversible: bool = False
    sensitive_field_patterns: list[str] = ["ssn", "password", "passwd", "secret", "token"]

def load_config(path: Path | None = None) -> SafetyConfig:
    """Load config from JSON file or return defaults."""
    if path and path.exists():
        import json
        return SafetyConfig.model_validate(json.loads(path.read_text()))
    return SafetyConfig()

@dataclass
class SafetyVerdict:
    allowed: bool
    reason: str | None = None
    requires_confirmation: bool = False
    is_irreversible: bool = False

class SafetyGuard:
    """Stateless policy enforcer. Instantiate once per run with a config."""
    def __init__(self, config: SafetyConfig | None = None) -> None:
        self._cfg = config or SafetyConfig()

    """
    check(tool, input) -> SafetyVerdict
    The caller must respect the verdict:
        - allowed=False -> stop, do not dispatch
        - requires_confirmation=True + block_irreversible=True -> stop
        - requires_confirmation=True + block_irreversible=Flase -> log and proceed
    """
    def check(self, tool: str, inp: dict[str, Any]) -> SafetyVerdict:
        if tool not in self._cfg.allowed_action_types:
            return SafetyVerdict(
                allowed=False,
                reason=f"Tool '{tool}' is not in the permitted action types: "
                f"{self._cfg.allowed_action_types}",
            )
        if tool == "navigate":
            url = str(inp.get("url", ""))
            verdict = self._check_domain(url)
            if not verdict.allowed:
                return verdict
        if tool == "click":
            name = str(inp.get("name", "")).lower()
            if any(hint in name for hint in self._cfg.irreversible_name_hints):
                if self._cfg.block_irreversible:
                    return SafetyVerdict(
                        allowed=False,
                        reason=f"Irreversible action blocked: clicking '{inp.get('name')}'. "
                                "Requires supervisor override.",
                        is_irreversible=True,
                    )
                return SafetyVerdict(
                    allowed=True,
                    requires_confirmation=True,
                    is_irreversible=True,
                )
        if tool == "type":
            field_name = str(inp.get("name", "")).lower()
            if(any(p in field_name for p in self._cfg.sensitive_field_patterns) and not inp.get("sensitive", False)):
                return SafetyVerdict(
                    allowed=False,
                    reason=f"Field '{inp.get('name')}' looks sensitive but sensitive=true was not "
                            "set. Mark the field sensitive or confirm it is safe.",
                )
        return SafetyVerdict(allowed=True)

    def _check_domain(self, url: str) -> SafetyVerdict:
        try:
            hostname = urlparse(url).hostname or ""
        except Exception:
            return SafetyVerdict(allowed=False, reason=f"Unparseable URL: {url}")

        for allowed in self._cfg.allowed_domains:
            clean = allowed.replace("http://", "").replace("https://", "").split("/")[0]
            if clean in hostname:
                return SafetyVerdict(allowed=True)

        return SafetyVerdict(
            allowed=False,
            reason=f"Navigation to '{url}' is outside permitted domains: "
            f"{self._cfg.allowed_domains}",
        )