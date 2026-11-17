"""
Result contract - what the replay runner returns to the caller

Three categories:
- ReplaySuccess:   flow completed successfully, outputs extracted and checkpoint passed
- BusinessOutcome: a legitimate application state the caller must handle
                   (e.g. "member not found", "application already decided"). This is NOT a 
                   failure, it is information.
- ReplayFailure:   the runner could not complete the flow and includes enough details to debug
                   the issue (step, expected, observed).
"""

from __future__ import annotations
from typing import Literal, Any
from pydantic import BaseModel, Field

class ReplaySuccess(BaseModel):
    status: Literal["success"] = "success"
    outputs: dict[str, Any] = Field(default_factory=dict)
    checkpoint_passed: bool = True
    steps_executed: int = 0
    duration_ms: int = 0

class BusinessOutcome(BaseModel):
    """
    A known, expected non-success state that the caller needs to handle.
    Examples: member not found, application already decided, account frozen, etc.
    The caller should treat this as information, not an error.
    """
    status: Literal["business_outcome"] = "business_outcome"
    outcome_code: str
    description: str
    step_index: int
    outputs: dict[str, Any] = Field(default_factory=dict)


class ReplayFailure(BaseModel):
    """
    The runner could not complete the flow. Includes enough context to debug without re-running.
    """
    status: Literal["failure"] = "failure"
    error_code: str
    message: str
    step_index: int
    step_description: str = ""
    expected: str = ""
    observed: str = ""
    recoverable: bool = False


ReplayResult = ReplaySuccess | BusinessOutcome | ReplayFailure


class OutcomeCode:
    MEMBER_NOT_FOUND = "MEMBER_NOT_FOUND"
    ALREADY_DECIDED = "ALREADY_DECIDED"
    ACCOUNT_FROZEN = "ACCOUNT_FROZEN"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    RECORD_NOT_FOUND = "RECORD_NOT_FOUND"
    SESSION_EXPIRED = "SESSION_EXPIRED"

class ErrorCode:
    LOCATOR_NOT_FOUND = "LOCATOR_NOT_FOUND"
    CHECKPOINT_FAILED = "CHECKPOINT_FAILED"
    TIMEOUT = "TIMEOUT"
    NAVIGATION_BLOCKED = "NAVIGATION_BLOCKED"
    UNEXPECTED_DIALOG = "UNEXPECTED_DIALOG"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
    SAFETY_BLOCK = "SAFETY_BLOCK"
    MAX_RETRIES = "MAX_RETRIES"