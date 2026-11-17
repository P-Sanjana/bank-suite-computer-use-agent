"""
Tool definitions sent to Claude on every discovery turn.
Each tool has:
- A TypedDict for the input the Claude API returns
- An Anthropic-format tool dict for the API call
"""

from typing import Any, Literal, TypedDict

class ClickInput(TypedDict, total=False):
    role: str
    name: str
    reason: str
    label: str
    nth: int

class TypeInput(TypedDict, total=False):
    role: str
    name: str
    value: str
    reason: str
    label: str
    nth: int
    append: bool
    sensitive: bool

class SelectInput(TypedDict, total=False):
    role: str
    name: str
    reason: str
    label: str
    nth: int
    option_label: str
    option_value: str

class NavigateInput(TypedDict):
    url: str
    reason: str

class WaitInput(TypedDict, total=False):
    condition: str
    reason: str
    role: str
    name: str
    url_substring: str
    duration_ms: int

class ReadInput(TypedDict, total=False):
    role: str
    name: str
    label: str
    nth: int
    output_key: str
    reason: str


class ReadStateInput(TypedDict):
    reason: str

class DeclareGoalCompleteInput(TypedDict, total=False):
    summary: str
    checkpoint_evidence: str
    outputs: dict[str, Any]

class EscalateInput(TypedDict, total=False):
    reason: str
    description: str
    current_step: str
    suggested_action: str

# Tool name constants
NAVIGATE = "navigate"
CLICK = "click"
TYPE = "type"
SELECT = "select"
WAIT = "wait"
READ = "read"
READ_STATE = "read_state"
DECLARE_GOAL_COMPLETE = "declare_goal_complete"
ESCALATE = "escalate"

ToolName = Literal[
    "navigate", "click", "type", "select", "wait", "read", "read_state",
    "declare_goal_complete", "escalate",
]

MUTATING_TOOLS: set[str] = {NAVIGATE, CLICK, TYPE, SELECT}
IRREVERSIBLE_HINT_NAMES = {"submit", "confirm", "approve", "decline", "delete", "remove"}

# Anthropic tool dicts
NAVIGATE_TOOL: dict[str, Any] = {
    "name": NAVIGATE,
    "description": (
        "Navigate the browser to a URL. Use to reach the entry point of a flow or recover from a dead-end"
        " by returning to a known page."
        "Only navigate to URLs within the permitted domain."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The full URL to navigate to."},
            "reason": {"type": "string", "description": "Why you are navigating here."},
        },
        "required": ["url", "reason"],
    },
}

CLICK_TOOL: dict[str, Any] = {
    "name": CLICK,
    "description": (
        "Click an interactive element. Prefer identifying by accessible role + name."
        " use label as fallback for unassociated inputs. Use nth (0-indexed) only as last resort."
    ),
    "input_schema":{
        "type": "object",
        "properties": {
            "role": {"type": "string", "description": "ARIA role from the accessibility tree."},
            "name": {"type": "string", "description": "Accessible name and must match the a11y tree exactly."},
            "label": {"type": "string", "description": "Fallback: visible label text near the element."},
            "nth": {"type": "integer", "description": "Fallback: 0-indexed position among matched elements."},
            "reason": {"type": "string", "description": "What you expect this click to do."},
        },
        "required": ["role", "name", "reason"],
    },
}

TYPE_TOOL: dict[str, Any] = {
    "name": TYPE,
    "description": (
        "Type text into an input field or textarea. Clears existing content first unless append=true."
        " Set sensitive=true to mask input (e.g. SSN, passwords or regulated PII). Value will be redacted"
        " in the logs."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "role": {"type": "string", "description": "ARIA role from the accessibility tree (typically "
            "textbox, searchbox)."},
            "name": {"type": "string", "description": "Accessible name of the field."},
            "value": {"type": "string", "description": "Text to type."},
            "label": {"type": "string", "description": "Fallback: visible label text."},
            "nth": {"type": "integer", "description": "Fallback: 0-indexed position."},
            "append": {"type": "boolean", "description": "Append instead of clearing first."},
            "sensitive": {"type": "boolean", "description": "True for SSN, passwords or PII - redacted "
            "in the logs."},
            "reason": {"type": "string", "description": "Why you are typing this value."},
        },
        "required": ["role", "name", "value", "reason"],
    },
}

SELECT_TOOL: dict[str, Any] = {
    "name": SELECT,
    "description": (
        "Select an option from a dropdown. Prefer option_label (visible text)."
        " Use option_value only when label is not stable."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "role": {"type": "string", "description": "ARIA role from the accessibility tree "
            "(combobox, listbox)."},
            "name": {"type": "string", "description": "Accessible name of the select element."},
            "option_label": {"type": "string", "description": "Visible text of the option to select (preferred)."},
            "option_value": {"type": "string", "description": "Fallback: value attribute of the option."},
            "label": {"type": "string", "description": "Fallback: visible label near the select."},
            "nth": {"type": "integer", "description": "Fallback: 0-indexed position."},
            "reason": {"type": "string", "description": "Why you are selecting this option."},
        },
        "required": ["role", "name", "reason"],
    },
}

WAIT_TOOL: dict[str, Any] = {
    "name": WAIT,
    "description": (
        "Wait for a condition before proceeding. Prefer element_visible/hidden over duration."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "condition": {
                "type": "string",
                "description": "What to wait for.",
                "enum": ["element_visible", "element_hidden", "url_contains", "duration"],
            },
            "role": {"type": "string", "description": "For element conditions: ARIA role."},
            "name": {"type": "string", "description": "For element contitions: Accessible name"},
            "url_substring": {"type": "string", "description": "For url_contains: substring the URL "
            "must contain."},
            "duration_ms": {"type": "integer", "description": "Duration in ms to wait (max 5000)."},
            "reason": {"type": "string", "description": "Why you are waiting."},
        },
        "required": ["condition", "reason"],
    },
}

READ_TOOL: dict[str, Any] = {
    "name": READ,
    "description": (
        "Read visible text from a control or labeled cell and store it as a named output. "
        "Use this to extract values the goal asked for (credit score, status, application id). "
        "Prefer the accessible name; unlabeled table fields can be targeted by the nearby label."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "role": {"type": "string", "description": "ARIA role if known (e.g. cell, textbox)."},
            "name": {"type": "string", "description": "Accessible name or nearby label text."},
            "label": {"type": "string", "description": "Visible label in an adjacent table cell."},
            "nth": {"type": "integer", "description": "Fallback: 0-indexed position."},
            "output_key": {"type": "string", "description": "Key to store the extracted value under."},
            "reason": {"type": "string", "description": "Why you are reading this value."},
        },
        "required": ["name", "output_key", "reason"],
    },
}

READ_STATE_TOOL: dict[str, Any] = {
    "name": READ_STATE,
    "description": (
        "Read the current page state: URL, title and full accessibility tree."
        " Use when the page may have changed and you need to re-observe before deciding."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {"type": "string", "description": "Why you need a fresh snapshot."},
        },
        "required": ["reason"],
    },
}

DECLARE_GOAL_COMPLETE_TOOL: dict[str, Any] = {
    "name": DECLARE_GOAL_COMPLETE,
    "description": (
        "Signal the goal is fully achieved. Only call with positive confirmation with a visible"
        " confirmation message, exptected data on screen or unambiguous state change."
        " Include all data you were asked to extract in outputs."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "One-sentence description of what was "
            "accomplished."},
            "checkpoint_evidence": {"type": "string", "desctiption": "What you observed that confirms"
            "success."},
            "outputs": {
                "type": "object",
                "description": "Key/value map of extracted data (e.g. member_status, credit_score).",
                "additionalProperties": True,
            },
        },
        "required": ["summary", "checkpoint_evidence"],
    },
}

ESCALATE_TOOL: dict[str, Any] = {
    "name": ESCALATE,
    "description": (
        "Signal you cannot safely continue. A human operator must take over."
        " Use when stuck, the next step is irreversible, an expected error occurred, or safety"
        " policy blocks a required action."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "enum": ["STUCK", "IRREVERSIBLE_ACTION", "ERROR_STATE", "POLICY_BLOCK", "AMBIGUOUS"],
            },
            "description": {"type": "string", "description": "What you tried, what you observed, what"
            " is blocking you."},
            "current_step": {"type": "string", "description": "The step you were attempting."},
            "suggested_action": {"type": "string", "description": "Optional: what the human should do."},
        },
        "required": ["reason", "description", "current_step"],
    },
}

ALL_TOOLS: list[dict[str, Any]] = [
    NAVIGATE_TOOL,
    CLICK_TOOL,
    TYPE_TOOL,
    SELECT_TOOL,
    WAIT_TOOL,
    READ_TOOL,
    READ_STATE_TOOL,
    DECLARE_GOAL_COMPLETE_TOOL,
    ESCALATE_TOOL
]