"""
Parameterizer: walks a built Artifact and replaces hardcoded values
with {parameter_name} placeholders, then registers the corresponding
InputParameter on the artifact.
 
This is what makes an artifact reusable across invocations — instead of
"type '1042381' into Member Number", it becomes "type '{member_no}' into
Member Number" and the caller supplies member_no at replay time.
 
Strategy:
  - Known Bank Suite form fields (html name / label) → named placeholders
  - Member numbers (7 digits) → {member_no}
  - Application IDs (APP-XXXXXXX) in type/navigate/URLs/Review links → {app_id}
  - Dates (MM/DD/YYYY) → {dob} or {date}
  - Any TypeAction value that was marked sensitive → field map, else {<field_name>}
  - Caller can pass explicit_params to force-parameterize specific values
"""
from __future__ import annotations
 
import re
from copy import deepcopy
 
from ..schema.artifact import (
    Artifact, ArtifactStep, LocatorStrategy, TypeAction, NavigateAction, ClickAction, SelectAction,
    InputParameter, ParameterType, ActionType,
)

DECISION_CHOICES = (
    "Approve",
    "Decline",
    "Escalate to Senior Officer",
    "Request More Info",
)
_DECISION_STATUS = {
    "Approve": "APPROVED",
    "Decline": "DECLINED",
    "Escalate to Senior Officer": "ESCALATED",
    "Request More Info": "PENDING",
}
_DECISION_ALIASES = {
    "approve": "Approve",
    "approved": "Approve",
    "decline": "Decline",
    "declined": "Decline",
    "deny": "Decline",
    "escalate": "Escalate to Senior Officer",
    "escalated": "Escalate to Senior Officer",
    "escalate to senior officer": "Escalate to Senior Officer",
    "escalate to senior offier": "Escalate to Senior Officer",
    "request more info": "Request More Info",
    "more info": "Request More Info",
    "pending": "Request More Info",
}

# Pattern registry
_PATTERNS: list[tuple[re.Pattern[str], str, ParameterType, str]] = [
    # (pattern, param_name, type, description)
    (
        re.compile(r"^\d{7}$"),
        "member_no",
        ParameterType.STRING,
        "7-digit member number",
    ),
    (
        re.compile(r"^APP-\d+$"),
        "app_id",
        ParameterType.STRING,
        "Application ID (e.g. APP-1234567)",
    ),
    (
        re.compile(r"^\d{2}/\d{2}/\d{4}$"),
        "dob",
        ParameterType.STRING,
        "Date of birth (MM/DD/YYYY)",
    ),
]

# html_name → (param, description)
_FORM_HTML_FIELDS: dict[str, tuple[str, str]] = {
    "mem_no": ("member_no", "7-digit member number"),
    "app_mem_no": ("member_no", "7-digit member number"),
    "app_fname": ("first_name", "Applicant first name"),
    "app_lname": ("last_name", "Applicant last name"),
    "app_dob": ("dob", "Date of birth (MM/DD/YYYY)"),
    "ssn_pt1": ("ssn_pt1", "SSN first 3 digits"),
    "ssn_pt2": ("ssn_pt2", "SSN middle 2 digits"),
    "ssn_pt3": ("ssn_pt3", "SSN last 4 digits"),
    "app_addr1": ("address", "Street address"),
    "app_city": ("city", "City"),
    "app_state": ("state", "State abbreviation"),
    "app_zip": ("zip", "ZIP code"),
    "app_phone": ("phone", "Phone number"),
    "emp_status": ("employment_status", "Employment status"),
    "emp_name": ("employer", "Employer name"),
    "emp_years": ("years_employed", "Years employed"),
    "emp_income": ("gross_annual_income", "Gross annual income"),
    "emp_housing": ("monthly_rent", "Monthly rent/mortgage"),
    "card_type": ("card_type", "Card type"),
    "req_limit": ("requested_limit", "Requested credit limit"),
    "citizenship": ("citizenship", "Citizenship (e.g. US Citizen)"),
}

_FORM_LABEL_FIELDS: list[tuple[str, str, str]] = [
    ("member number", "member_no", "7-digit member number"),
    ("first name", "first_name", "Applicant first name"),
    ("last name", "last_name", "Applicant last name"),
    ("date of birth", "dob", "Date of birth (MM/DD/YYYY)"),
    ("address line 1", "address", "Street address"),
    ("city", "city", "City"),
    ("phone", "phone", "Phone number"),
    ("employment status", "employment_status", "Employment status"),
    ("employer", "employer", "Employer name"),
    ("years employed", "years_employed", "Years employed"),
    ("gross annual", "gross_annual_income", "Gross annual income"),
    ("rent/mortgage", "monthly_rent", "Monthly rent/mortgage"),
    ("card type", "card_type", "Card type"),
    ("requested credit limit", "requested_limit", "Requested credit limit"),
]

_SSN_PARTS = ("ssn_pt1", "ssn_pt2", "ssn_pt3")

_PARAM_ALIASES = {
    "memberno": "member_no",
    "member_number": "member_no",
    "firstname": "first_name",
    "lastname": "last_name",
    "date_of_birth": "dob",
    "date": "dob",
    "employer_status": "employment_status",
    "emp_status": "employment_status",
    "income": "gross_annual_income",
    "housing": "monthly_rent",
    "monthly_rent_mortgage": "monthly_rent",
    "requested_credit_limit": "requested_limit",
    "limit": "requested_limit",
}

_INJECT_APPLICATION_FIELDS: tuple[tuple[str, str, str, str], ...] = (
    ("first_name", "app_fname", "First Name *", "Applicant first name"),
    ("last_name", "app_lname", "Last Name *", "Applicant last name"),
    ("dob", "app_dob", "Date of Birth *", "Date of birth (MM/DD/YYYY)"),
    ("phone", "app_phone", "Phone *", "Phone number"),
)

def parameterize(
        artifact: Artifact,
        explicit_params: dict[str, str] | None = None,
) -> Artifact:
    """
    Return a new Artifact with hardcoded values replaced by {placeholders}. The artifact's 
    parameters list is updated to include the discovered params.
    explicit_params: {literal_value: param_name} - caller-forced substitutions.
    """
    result = deepcopy(artifact)
    discovered: dict[str, InputParameter] = {
        p.name: p for p in result.parameters
    }

    recorded_notes: str | None = None
    for step in result.steps:
        action = step.action
        if action.type == ActionType.TYPE:
            assert isinstance(action, TypeAction)
            if (
                _is_officer_notes(action)
                and action.value
                and not (action.value.startswith("{") and action.value.endswith("}"))
            ):
                recorded_notes = action.value
            action, discovered = _parameterize_type(action, discovered, explicit_params or {})
            step.action = action

        if action.type == ActionType.SELECT:
            assert isinstance(action, SelectAction)
            action, discovered = _parameterize_select(action, discovered)
            step.action = action

        if action.type == ActionType.CLICK:
            assert isinstance(action, ClickAction)
            action, discovered = _parameterize_click(action, discovered)
            step.action = action

        if action.type == ActionType.NAVIGATE:
            assert isinstance(action, NavigateAction)
            action, discovered = _parameterize_navigate(action, discovered, explicit_params or {})
            step.action = action
    result = _ensure_application_identity_steps(result, discovered)
    if _app_id_is_input(result):
        result = _parameterize_recorded_app_id(result, discovered)
    else:
        discovered.pop("app_id", None)
        for stale in ("xxx", "xx", "xxxx"):
            if "ssn_pt1" in discovered:
                discovered.pop(stale, None)
        result = _parameterize_submit_checkpoint(result)
    result = _parameterize_review_fields(result, discovered, recorded_notes)
    result.parameters = list(discovered.values())
    return result


def expand_application_params(params: dict[str, str]) -> dict[str, str]:
    """Normalize CLI aliases and split `ssn` into the three SSN boxes."""
    out = dict(params)
    for alias, canonical in _PARAM_ALIASES.items():
        if alias in out and canonical not in out:
            out[canonical] = out[alias]
    raw_ssn = str(out.get("ssn") or "").strip()
    chunks = [part for part in re.split(r"\D+", raw_ssn) if part]
    if len(chunks) == 3:
        out.setdefault("ssn_pt1", chunks[0])
        out.setdefault("ssn_pt2", chunks[1])
        out.setdefault("ssn_pt3", chunks[2])
    else:
        digits = re.sub(r"\D", "", raw_ssn)
        if len(digits) == 9:
            out.setdefault("ssn_pt1", digits[:3])
            out.setdefault("ssn_pt2", digits[3:5])
            out.setdefault("ssn_pt3", digits[5:])
    limit = str(out.get("requested_limit") or "").strip()
    if limit:
        amount = re.sub(r"[^\d]", "", limit)
        if amount:
            out["requested_limit"] = f"${int(amount):,}"
    citizenship = str(out.get("citizenship") or "").strip().lower()
    _citizenship = {
        "us": "US Citizen",
        "us citizen": "US Citizen",
        "pr": "Permanent Resident",
        "permanent resident": "Permanent Resident",
        "ot": "Other (attach docs)",
        "other": "Other (attach docs)",
        "other (attach docs)": "Other (attach docs)",
    }
    if citizenship in _citizenship:
        out["citizenship"] = _citizenship[citizenship]
    card = str(out.get("card_type") or "").strip()
    if card.lower() == "gold rewards":
        out["card_type"] = "Gold Rewards ($95/yr)"
    return out


def normalize_decision(raw: str) -> tuple[str, str]:
    """Map a CLI decision value to (radio label, confirmation status)."""
    trimmed = raw.strip()
    key = re.sub(r"\s+", " ", trimmed.lower())
    if trimmed in DECISION_CHOICES:
        label = trimmed
    elif key in _DECISION_ALIASES:
        label = _DECISION_ALIASES[key]
    else:
        label = next((choice for choice in DECISION_CHOICES if choice.lower() == key), "")
        if not label:
            raise ValueError(
                f"Unknown decision {raw!r}. Use one of: {', '.join(DECISION_CHOICES)}"
            )
    return label, _DECISION_STATUS[label]


def extras_required_for_decision(label: str) -> list[str]:
    """Params required in addition to officer_notes for a review decision."""
    if label == "Decline":
        return ["decline_reason"]
    if label == "Escalate to Senior Officer":
        return ["escalate_to"]
    return []


def missing_review_params(params: dict[str, str], artifact: Artifact) -> list[str]:
    """Return required CLI params that are absent or blank for this replay."""
    supplied = expand_application_params(params)
    names = {p.name for p in artifact.parameters}
    missing: list[str] = []
    ssn_ok = all(str(supplied.get(part) or "").strip() for part in _SSN_PARTS)
    for param in artifact.parameters:
        if param.name in ("decline_reason", "escalate_to", "approved_limit", "app_id"):
            continue
        if param.name in _SSN_PARTS and ssn_ok:
            continue
        if param.required and param.default is None and not str(supplied.get(param.name) or "").strip():
            missing.append(param.name)
    if "decision" not in names:
        return missing
    raw = str(supplied.get("decision") or "").strip()
    if not raw:
        if "decision" not in missing:
            missing.append("decision")
        return missing
    try:
        label, _ = normalize_decision(raw)
    except ValueError:
        return missing + ["decision"]
    needed = ["officer_notes", *extras_required_for_decision(label)]
    for name in needed:
        if not str(supplied.get(name) or "").strip() and name not in missing:
            missing.append(name)
    return missing

def _parameterize_type(
        action: TypeAction,
        discovered: dict[str, InputParameter],
        explicit: dict[str, str],
) -> tuple[TypeAction, dict[str, InputParameter]]:
    value = action.value

    # Already a placeholder — remap XXX/SSN fragments onto html_name fields
    if value.startswith("{") and value.endswith("}"):
        mapped = _field_for_locator(action.locator)
        if mapped:
            return _bind_type(action, discovered, mapped[0], mapped[1])
        param_name = value[1:-1]
        if param_name not in discovered:
            discovered[param_name] = InputParameter(
                name=param_name,
                type=ParameterType.STRING,
                description=f'Value for field "{action.locator.name}"',
                required=True,
            )
        return action, discovered
    if not value.strip():
        return action, discovered

    mapped = _field_for_locator(action.locator)
    if mapped:
        param_name, description = mapped
        return _bind_type(action, discovered, param_name, description)

    # Sensitive values always become {<field_name>}
    if action.sensitive:
        param_name = _safe_name(action.locator.name) or "sensitive_value"
        return _bind_type(
            action,
            discovered,
            param_name,
            f'Sensitive value for field "{action.locator.name}"',
        )

    # Explicit caller override
    if value in explicit:
        param_name = explicit[value]
        placeholder = f"{{{param_name}}}"
        if param_name not in discovered:
            discovered[param_name] = InputParameter(
                name=param_name,
                type=ParameterType.STRING,
                description=f"Value for field \"{action.locator.name}\"",
                required=True,
            )
        return TypeAction(**{**action.model_dump(), "value": placeholder}), discovered

    if _is_officer_notes(action):
        if "officer_notes" not in discovered:
            discovered["officer_notes"] = InputParameter(
                name="officer_notes",
                type=ParameterType.STRING,
                description="Officer notes / decision rationale",
                required=True,
            )
        return TypeAction(**{**action.model_dump(), "value": "{officer_notes}"}), discovered

    # Pattern matching
    for pattern, param_name, ptype, description in _PATTERNS:
        if pattern.match(value):
            if param_name not in discovered:
                discovered[param_name] = InputParameter(
                    name=param_name,
                    type=ptype,
                    description=description,
                    required=True,
                )
            placeholder = f"{{{param_name}}}"
            return TypeAction(**{**action.model_dump(), "value": placeholder}), discovered

    return action, discovered


_SELECT_FIELDS: dict[str, tuple[str, str]] = {
    "decline_reason": ("decline_reason", "Decline reason (option label or value)"),
    "escalate_to": ("escalate_to", "Escalate-to officer (option label or value)"),
    "approved_limit": ("approved_limit", "Approved credit limit"),
    **_FORM_HTML_FIELDS,
}


def _parameterize_select(
    action: SelectAction,
    discovered: dict[str, InputParameter],
) -> tuple[SelectAction, dict[str, InputParameter]]:
    html_name = (action.locator.html_name or "").lower()
    visible = (action.locator.name or "").lower()
    param_name = ""
    description = ""
    if html_name in _SELECT_FIELDS:
        param_name, description = _SELECT_FIELDS[html_name]
    elif "decline reason" in visible:
        param_name, description = _SELECT_FIELDS["decline_reason"]
    elif "escalate to" in visible:
        param_name, description = _SELECT_FIELDS["escalate_to"]
    elif "approved limit" in visible:
        param_name, description = _SELECT_FIELDS["approved_limit"]
    else:
        mapped = _field_for_locator(action.locator)
        if mapped:
            param_name, description = mapped
    if not param_name:
        return action, discovered
    required = param_name not in ("decline_reason", "escalate_to", "approved_limit")
    if param_name not in discovered:
        discovered[param_name] = InputParameter(
            name=param_name,
            type=ParameterType.STRING,
            description=description,
            required=required,
        )
    placeholder = f"{{{param_name}}}"
    return (
        SelectAction(
            **{
                **action.model_dump(),
                "option_label": placeholder,
                "option_value": placeholder,
            }
        ),
        discovered,
    )

        
def _parameterize_navigate(
        action: NavigateAction,
        discovered: dict[str, InputParameter],
        explicit: dict[str, str],
    ) -> tuple[NavigateAction, dict[str, InputParameter]]:
    url = action.url

    #Replace APP-XXXXXXX in query strings to {app_id}
    app_id_match = re.search(r"APP-\d+", url)

    if app_id_match:
        param_name = "app_id"
        if param_name not in discovered:
            discovered[param_name] = InputParameter(
                name=param_name,
                type=ParameterType.STRING,
                description="Application ID (e.g. APP-1234567)",
                required=True,
            )
        template = url.replace(app_id_match.group(), f"{{{param_name}}}")
        return NavigateAction(url=url, url_template=template), discovered

    # Replace member numbers in query strings to {member_no}
    mem_match = re.search(r"(?<=id=)\d{7}", url)
    if mem_match:
        param_name = "member_no"
        if param_name not in discovered:
            discovered[param_name] = InputParameter(
                name=param_name,
                type=ParameterType.STRING,
                description="7-digit member number",
                required=True,
            )
        template = url[:mem_match.start()] + f"{{{param_name}}}" + url[mem_match.end():]
        return NavigateAction(url=url, url_template=template), discovered

    return action, discovered

def _parameterize_click(
    action: ClickAction,
    discovered: dict[str, InputParameter],
) -> tuple[ClickAction, dict[str, InputParameter]]:
    mapped = _field_for_locator(action.locator)
    html_name = (action.locator.html_name or "").lower()
    if html_name == "citizenship" or (mapped and mapped[0] == "citizenship"):
        param_name, description = mapped or _FORM_HTML_FIELDS["citizenship"]
        if param_name not in discovered:
            discovered[param_name] = InputParameter(
                name=param_name,
                type=ParameterType.STRING,
                description=description,
                required=True,
            )
        action.locator.name = "{citizenship}"
        action.locator.html_name = "citizenship"
        return action, discovered
    return action, discovered


def _bind_type(
    action: TypeAction,
    discovered: dict[str, InputParameter],
    param_name: str,
    description: str,
) -> tuple[TypeAction, dict[str, InputParameter]]:
    if param_name not in discovered:
        discovered[param_name] = InputParameter(
            name=param_name,
            type=ParameterType.STRING,
            description=description,
            required=True,
        )
    return TypeAction(**{**action.model_dump(), "value": f"{{{param_name}}}"}), discovered


def _field_for_locator(locator: LocatorStrategy) -> tuple[str, str] | None:
    html_name = (locator.html_name or "").lower()
    if html_name in _FORM_HTML_FIELDS:
        return _FORM_HTML_FIELDS[html_name]
    visible = f"{locator.name} {locator.label or ''}".lower()
    for needle, param_name, description in _FORM_LABEL_FIELDS:
        if needle in visible:
            return param_name, description
    return None


def _ensure_application_identity_steps(
    artifact: Artifact,
    discovered: dict[str, InputParameter],
) -> Artifact:
    if "application.html" not in (artifact.entry_url or "").lower():
        return artifact
    present: set[str] = set()
    for step in artifact.steps:
        action = step.action
        html_name = ""
        if isinstance(action, TypeAction):
            html_name = (action.locator.html_name or "").lower()
            if action.value.startswith("{") and action.value.endswith("}"):
                present.add(action.value[1:-1])
        elif isinstance(action, (SelectAction, ClickAction)):
            html_name = (action.locator.html_name or "").lower()
        mapped = _FORM_HTML_FIELDS.get(html_name)
        if mapped:
            present.add(mapped[0])
    missing = [row for row in _INJECT_APPLICATION_FIELDS if row[0] not in present]
    if not missing:
        return artifact
    insert_at = _identity_insert_index(artifact)
    extra: list[ArtifactStep] = []
    for param_name, html_name, visible, description in missing:
        discovered[param_name] = InputParameter(
            name=param_name,
            type=ParameterType.STRING,
            description=description,
            required=True,
        )
        extra.append(
            ArtifactStep(
                step_index=0,
                description=f'Type "{{{param_name}}}" into "{visible}"',
                action=TypeAction(
                    locator=LocatorStrategy(
                        role="textbox",
                        name=visible,
                        html_name=html_name,
                        resolved_via="xpath_proximity",
                    ),
                    value=f"{{{param_name}}}",
                ),
            )
        )
    steps = artifact.steps[:insert_at] + extra + artifact.steps[insert_at:]
    for index, step in enumerate(steps):
        step.step_index = index
    artifact.steps = steps
    return artifact


def _identity_insert_index(artifact: Artifact) -> int:
    insert_at = 0
    for index, step in enumerate(artifact.steps):
        action = step.action
        mapped = _field_for_locator(action.locator) if isinstance(action, TypeAction) else None
        if mapped and mapped[0] == "member_no":
            insert_at = index + 1
        if isinstance(action, ClickAction) and "lookup member" in (action.locator.name or "").lower():
            return index + 1
    return insert_at


def _app_id_is_input(artifact: Artifact) -> bool:
    for step in artifact.steps:
        action = step.action
        if isinstance(action, ClickAction) and (action.locator.name or "").strip() == "Review":
            return True
        blob = " ".join(
            part
            for part in (
                getattr(action, "url", None),
                getattr(action, "url_template", None),
                step.expected_url_contains,
            )
            if part
        )
        if "review.html" in blob.lower() and "APP-" in blob:
            return True
    return False


def _parameterize_submit_checkpoint(artifact: Artifact) -> Artifact:
    cp = artifact.checkpoint
    visible = (cp.text_visible or "").strip()
    if visible == "{app_id}" or (visible and _APP_ID_RE.fullmatch(visible)):
        cp.text_visible = "Application Submitted Successfully"
    if cp.description:
        cp.description = _APP_ID_RE.sub("a new application ID", cp.description)
        cp.description = cp.description.replace("{app_id}", "a new application ID")
    return artifact


_APP_ID_RE = re.compile(r"APP-\d+")


def _parameterize_recorded_app_id(
    artifact: Artifact,
    discovered: dict[str, InputParameter],
) -> Artifact:
    recorded = _recorded_app_id(artifact)
    if not recorded:
        return artifact
    if "app_id" not in discovered:
        discovered["app_id"] = InputParameter(
            name="app_id",
            type=ParameterType.STRING,
            description="Application ID (e.g. APP-1234567)",
            required=True,
            default=recorded,
        )
    data = _replace_strings(artifact.model_dump(mode="json"), recorded, "{app_id}")
    updated = Artifact.model_validate(data)
    for step in updated.steps:
        action = step.action
        if not isinstance(action, ClickAction):
            continue
        if action.locator.name != "Review":
            continue
        action.locator.href_contains = "{app_id}"
        action.locator.nth = None
        action.locator.resolved_via = "href_contains"
    return updated


def _parameterize_review_fields(
    artifact: Artifact,
    discovered: dict[str, InputParameter],
    recorded_notes: str | None = None,
) -> Artifact:
    recorded_status: str | None = None
    recorded_label: str | None = None
    for step in artifact.steps:
        action = step.action
        if isinstance(action, ClickAction) and _is_decision_radio(action):
            try:
                recorded_label, recorded_status = normalize_decision(action.locator.name)
            except ValueError:
                recorded_status = None
            action.locator.name = "{decision}"
            action.locator.html_name = "decision"
            action.locator.input_value = "{decision_status}"
            action.locator.nth = None
            action.locator.resolved_via = "input_value"
            if "decision" not in discovered:
                discovered["decision"] = InputParameter(
                    name="decision",
                    type=ParameterType.ENUM,
                    description=(
                        "Decision radio: Approve, Decline, "
                        "Escalate to Senior Officer, Request More Info"
                    ),
                    required=True,
                    allowed_values=list(DECISION_CHOICES),
                )
            if recorded_status:
                step.description = f'Click "{{decision}}"'
    if recorded_status and artifact.checkpoint.description:
        artifact.checkpoint.description = artifact.checkpoint.description.replace(
            recorded_status, "{decision_status}"
        )
    if recorded_status and artifact.checkpoint.text_visible == recorded_status:
        artifact.checkpoint.text_visible = "{decision_status}"
    artifact.goal = _rewrite_goal_text(artifact.goal, recorded_notes, recorded_label)
    artifact.description = _rewrite_goal_text(
        artifact.description, recorded_notes, recorded_label
    )
    return artifact


def _rewrite_goal_text(
    text: str,
    recorded_notes: str | None,
    recorded_label: str | None,
) -> str:
    out = text
    if recorded_notes:
        quoted = False
        for wrap_l, wrap_r in (("'", "'"), ('"', '"')):
            needle = f"{wrap_l}{recorded_notes}{wrap_r}"
            if needle in out:
                out = out.replace(needle, "'{officer_notes}'")
                quoted = True
        if not quoted and recorded_notes in out:
            out = out.replace(recorded_notes, "{officer_notes}")
    if recorded_label:
        out = re.sub(
            rf"\b{re.escape(recorded_label)}\b",
            "{decision}",
            out,
            flags=re.IGNORECASE,
        )
    return out


def _is_decision_radio(action: ClickAction) -> bool:
    loc = action.locator
    if (loc.html_name or "").lower() == "decision":
        return True
    if loc.role != "radio":
        return False
    name = loc.name.strip()
    if name in DECISION_CHOICES:
        return True
    try:
        normalize_decision(name)
        return True
    except ValueError:
        return False


def _is_officer_notes(action: TypeAction) -> bool:
    loc = action.locator
    if (loc.html_name or "").lower() == "officer_notes":
        return True
    blob = f"{loc.name} {loc.label or ''}".lower()
    return "officer notes" in blob or "decision rationale" in blob


def _recorded_app_id(artifact: Artifact) -> str | None:
    blob = str(artifact.model_dump(mode="json"))
    found = _APP_ID_RE.findall(blob)
    return found[0] if found else None


def _replace_strings(value: object, recorded: str, placeholder: str) -> object:
    if isinstance(value, str):
        return value.replace(recorded, placeholder)
    if isinstance(value, dict):
        return {k: _replace_strings(v, recorded, placeholder) for k, v in value.items()}
    if isinstance(value, list):
        return [_replace_strings(v, recorded, placeholder) for v in value]
    return value


def _safe_name(s: str) -> str:
    """
    Convert a field label to a safe Python identifier.
    """
    return re.sub(r"[^a-z0-9_]", "_", s.lower().strip()).strip("_")[:40]
