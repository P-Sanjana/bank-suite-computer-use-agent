"""
Artifact schema - the versioned, serializable capability record produced after a successful
discovery run.

Design decisions:
    - Every step carries a full LocatorStrategy so the replay runner has everything it needs
    without re-parsing free text.
    - InputParameter/OutputField give the artifact a typed contract - both a human reviewer and 
    a calling agent know exactly what to supply and what they get back.
    - ResultContract uses a discriminated union (status field) so callers can pattern-match without 
    inspecting free text.
    - The schema is versioed (schema_version) so the replay runner can refuse to execute artifacts 
    it doesn't understand.   
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime, timezone

CURRENT_SCHEMA_VERSION = "1.0"

class LocatorStrategy(BaseModel):
    """
    How the replay runner finds a UI element. Mirrors the executor's fallback
    chain so the recorded strategy is the one that was proven to work during discovery.
    """
    role: str = ""
    name: str = ""
    label: str | None = None
    nth: int | None = None
    html_name: str | None = None
    html_id: str | None = None
    title: str | None = None
    href_contains: str | None = None
    input_value: str | None = None
    resolved_via: str = ""
    frame_url: str | None = None
    rationale: str = ""

class ActionType(str, Enum):
    NAVIGATE = "navigate"
    CLICK = "click"
    TYPE = "type"
    SELECT = "select"
    WAIT = "wait"
    READ = "read"

class NavigateAction(BaseModel):
    type: Literal[ActionType.NAVIGATE] = ActionType.NAVIGATE
    url: str
    url_template: str | None = None

class ClickAction(BaseModel):
    type: Literal[ActionType.CLICK] = ActionType.CLICK
    locator: LocatorStrategy
    irreversible: bool = False

class TypeAction(BaseModel):
    type: Literal[ActionType.TYPE] = ActionType.TYPE
    locator: LocatorStrategy
    value: str
    sensitive: bool = False
    append: bool = False

class SelectAction(BaseModel):
    type: Literal[ActionType.SELECT] = ActionType.SELECT
    locator: LocatorStrategy
    option_label: str | None = None
    option_value: str | None = None

class WaitAction(BaseModel):
    type: Literal[ActionType.WAIT] = ActionType.WAIT
    condition: Literal["element_visible", "element_hidden", "url_contains", "duration"]
    role: str | None = None
    name: str | None = None
    url_substring: str | None = None
    duration_ms: int | None = None

class ReadAction(BaseModel):
    type: Literal[ActionType.READ] = ActionType.READ
    output_key: str
    locator: LocatorStrategy

ArtifactAction = NavigateAction | ClickAction | TypeAction | SelectAction | WaitAction | ReadAction

class ArtifactStep(BaseModel):
    step_index: int
    description: str
    action: ArtifactAction
    expected_url_contains: str | None = None
    expected_element: LocatorStrategy | None = None
    agent_reasoning: str = ""

class ParameterType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    ENUM = "enum"

class InputParameter(BaseModel):
    name: str
    type: ParameterType
    description: str
    required: bool = True
    default: Any = None
    allowed_values: list[str] = Field(default_factory=list)

class OutputField(BaseModel):
    name: str
    type: ParameterType
    description: str
    source_locator: LocatorStrategy | None = None

class Checkpoint(BaseModel):
    """
    The success condition the replay runner verifies after the final step. At least one of 
    url_contains / element_visible / text_visible must be set.
    """
    description: str
    url_contains: str | None = None
    element_visible: LocatorStrategy | None = None
    text_visible: str | None = None

class Artifact(BaseModel):
    """
    A versioned, reusable capability record.
    Produced by: artifact/builder.py after a successful discovery run.
    Consumer by: replay/runner.py to deterministically re-execute the flow.

    The artifact is the contract between discovery and reply. Everything the runner needs
    must be here. Nothing outside should be repquired.
    """
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
    )

    schema_version: str = CURRENT_SCHEMA_VERSION
    id: str
    name: str
    description: str
    version: int = 1
    entry_url: str
    allowed_domain: str
    parameters: list[InputParameter] = Field(default_factory=list)
    outputs: list[OutputField] = Field(default_factory=list)
    steps: list[ArtifactStep] = Field(default_factory=list)
    checkpoint: Checkpoint
    discovered_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    goal: str = ""
    total_discovery_steps: int = 0