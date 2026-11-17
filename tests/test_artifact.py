from pathlib import Path

from src.artifact.parameterizer import (
    expand_application_params,
    missing_review_params,
    normalize_decision,
    parameterize,
)
from src.artifact.writer import load_artifact, new_artifact_id, write_artifact
from src.discovery.recorder import RawStep, RawTrace
from src.artifact.builder import build_artifact
from src.schema.artifact import (
    Artifact,
    ArtifactStep,
    Checkpoint,
    ClickAction,
    LocatorStrategy,
    SelectAction,
    TypeAction,
)


def test_writer_filename_and_roundtrip(tmp_path: Path) -> None:
    artifact_id = "abcdef12-9999-0000-0000-ffffffffffff"
    artifact = Artifact(
        id=artifact_id,
        name="look-up-member",
        description="Look up a member",
        entry_url="http://localhost:3000/member-lookup.html",
        allowed_domain="localhost",
        checkpoint=Checkpoint(description="done", url_contains="member-lookup.html"),
    )
    path = write_artifact(artifact, directory=tmp_path)
    assert path.name == "look-up-member-v1-abcdef12.json"
    loaded = load_artifact(path)
    assert loaded.allowed_domain == "localhost"


def test_parameterizer_replaces_member_number() -> None:
    artifact = Artifact(
        id=new_artifact_id(),
        name="lookup",
        description="lookup",
        entry_url="http://localhost:3000/member-lookup.html",
        allowed_domain="localhost",
        steps=[
            ArtifactStep(
                step_index=0,
                description="type member",
                action=TypeAction(
                    locator=LocatorStrategy(name="Member Number"),
                    value="1042381",
                ),
            )
        ],
        checkpoint=Checkpoint(description="ok"),
        parameters=[],
    )
    out = parameterize(artifact)
    assert isinstance(out.steps[0].action, TypeAction)
    assert out.steps[0].action.value == "{member_no}"
    assert any(p.name == "member_no" for p in out.parameters)


def test_parameterizer_binds_review_link_to_app_id() -> None:
    artifact = Artifact(
        id=new_artifact_id(),
        name="review-application",
        description="Open APP-1001293 and approve it",
        entry_url="http://localhost:3000/queue.html",
        allowed_domain="localhost",
        steps=[
            ArtifactStep(
                step_index=0,
                description="Click Review",
                action=ClickAction(
                    locator=LocatorStrategy(role="link", name="Review", nth=0),
                ),
                expected_url_contains="http://localhost:3000/review.html?id=APP-1001293",
            )
        ],
        checkpoint=Checkpoint(description="done", text_visible="APP-1001293"),
        parameters=[],
        goal="Open application APP-1001293 from the queue",
    )
    out = parameterize(artifact)
    click = out.steps[0].action
    assert isinstance(click, ClickAction)
    assert click.locator.href_contains == "{app_id}"
    assert click.locator.nth is None
    assert out.steps[0].expected_url_contains and "{app_id}" in out.steps[0].expected_url_contains
    assert out.checkpoint.text_visible == "{app_id}"
    app_param = next(p for p in out.parameters if p.name == "app_id")
    assert app_param.default == "APP-1001293"


def test_parameterizer_binds_decision_and_officer_notes() -> None:
    artifact = Artifact(
        id=new_artifact_id(),
        name="review-application",
        entry_url="http://localhost:3000/queue.html",
        allowed_domain="localhost",
        steps=[
            ArtifactStep(
                step_index=0,
                description="type notes",
                action=TypeAction(
                    locator=LocatorStrategy(
                        role="textbox",
                        name="Required: Document your decision rationale...",
                        html_name="officer_notes",
                    ),
                    value="Score and DTI within policy",
                ),
            ),
            ArtifactStep(
                step_index=1,
                description="Click Approve",
                action=ClickAction(
                    locator=LocatorStrategy(
                        role="radio",
                        name="Approve",
                        html_name="decision",
                        nth=0,
                        resolved_via="role_nth",
                    ),
                ),
            ),
        ],
        checkpoint=Checkpoint(
            description="Decision Recorded: APPROVED Application APP-1001293",
            text_visible="APPROVED",
        ),
        parameters=[],
        goal=(
            "Open application APP-1001293 from the queue, approve it "
            "with officer notes 'Score and DTI within policy'"
        ),
        description=(
            "Open application APP-1001293 from the queue, approve it "
            "with officer notes 'Score and DTI within policy'"
        ),
    )
    out = parameterize(artifact)
    notes = out.steps[0].action
    decision = out.steps[1].action
    assert isinstance(notes, TypeAction)
    assert notes.value == "{officer_notes}"
    assert isinstance(decision, ClickAction)
    assert decision.locator.name == "{decision}"
    assert decision.locator.input_value == "{decision_status}"
    assert decision.locator.nth is None
    assert decision.locator.resolved_via == "input_value"
    assert "{decision_status}" in out.checkpoint.description
    names = {p.name: p for p in out.parameters}
    assert names["decision"].default is None
    assert names["decision"].required is True
    assert names["officer_notes"].default is None
    assert names["officer_notes"].required is True
    assert "{decision}" in out.goal
    assert "{officer_notes}" in out.goal
    assert "approve it" not in out.goal.lower()
    assert "Score and DTI within policy" not in out.goal
    assert "{decision}" in out.description
    assert "{officer_notes}" in out.description


def test_parameterizer_binds_decline_reason_select() -> None:
    artifact = Artifact(
        id=new_artifact_id(),
        name="decline-application",
        description="Decline APP-1001187",
        entry_url="http://localhost:3000/queue.html",
        allowed_domain="localhost",
        steps=[
            ArtifactStep(
                step_index=0,
                description="select reason",
                action=SelectAction(
                    locator=LocatorStrategy(
                        role="combobox",
                        name="Decline Reason *",
                        html_name="decline_reason",
                    ),
                    option_label="Does not meet product policy",
                ),
            ),
            ArtifactStep(
                step_index=1,
                description="notes",
                action=TypeAction(
                    locator=LocatorStrategy(html_name="officer_notes", name="Officer Notes"),
                    value="policy miss",
                ),
            ),
            ArtifactStep(
                step_index=2,
                description="Click Decline",
                action=ClickAction(
                    locator=LocatorStrategy(role="radio", name="Decline", html_name="decision"),
                ),
            ),
        ],
        checkpoint=Checkpoint(description="DECLINED APP-1001187"),
        goal="Decline application APP-1001187",
    )
    out = parameterize(artifact)
    select = out.steps[0].action
    assert isinstance(select, SelectAction)
    assert select.option_label == "{decline_reason}"
    names = {p.name for p in out.parameters}
    assert "decline_reason" in names
    assert "officer_notes" in names


def test_missing_review_params_by_decision() -> None:
    artifact = Artifact(
        id=new_artifact_id(),
        name="review",
        description="review",
        entry_url="http://localhost:3000/queue.html",
        allowed_domain="localhost",
        checkpoint=Checkpoint(description="ok"),
        parameters=[],
        goal="review APP-1001187",
        steps=[
            ArtifactStep(
                step_index=0,
                description="Click Decline",
                action=ClickAction(
                    locator=LocatorStrategy(role="radio", name="Decline", html_name="decision"),
                ),
            ),
            ArtifactStep(
                step_index=1,
                description="notes",
                action=TypeAction(
                    locator=LocatorStrategy(html_name="officer_notes"),
                    value="notes",
                ),
            ),
        ],
    )
    out = parameterize(artifact)
    base = {"app_id": "APP-1001187", "decision": "Decline"}
    assert "decline_reason" in missing_review_params(base, out)
    assert "officer_notes" in missing_review_params(base, out)
    assert not missing_review_params(
        {**base, "decline_reason": "HIGH_DTI", "officer_notes": "DTI too high"},
        out,
    )
    escalate = {"app_id": "APP-1001187", "decision": "escalate", "officer_notes": "need SO"}
    assert missing_review_params(escalate, out) == ["escalate_to"]
    more_info = {
        "app_id": "APP-1001187",
        "decision": "Request More Info",
        "officer_notes": "need docs",
    }
    assert missing_review_params(more_info, out) == []
    assert "officer_notes" in missing_review_params(
        {"app_id": "APP-1001187", "decision": "Request More Info"},
        out,
    )


def test_normalize_decision_aliases() -> None:
    assert normalize_decision("decline") == ("Decline", "DECLINED")
    assert normalize_decision("escalate") == ("Escalate to Senior Officer", "ESCALATED")
    assert normalize_decision("request more info") == ("Request More Info", "PENDING")


def test_builder_uses_resolved_locator() -> None:
    trace = RawTrace(
        goal="lookup",
        entry_url="http://localhost:3000/member-lookup.html",
        started_at="t0",
        outcome_type="success",
        total_steps=1,
        declared_outputs={"credit_score": "734"},
        checkpoint_evidence='Saw "734" in Credit Score',
        steps=[
            RawStep(
                step_index=0,
                tool="type",
                input={"role": "textbox", "name": "Member Number", "value": "1042381"},
                observation="typed",
                success=True,
                timestamp="t1",
                resolved_locator={
                    "role": "textbox",
                    "name": "Member Number",
                    "html_name": "mem_no",
                    "resolved_via": "xpath_proximity",
                    "rationale": "proximity",
                },
            ),
            RawStep(
                step_index=1,
                tool="read",
                input={"name": "Credit Score", "output_key": "credit_score"},
                observation="read",
                success=True,
                timestamp="t2",
                extracted={"credit_score": "734"},
                resolved_locator={
                    "name": "Credit Score",
                    "resolved_via": "xpath_value_cell",
                    "rationale": "cell",
                },
            ),
        ],
    )
    artifact = build_artifact(
        trace,
        artifact_id="id",
        name="lookup",
        description="lookup",
        allowed_domain="localhost",
    )
    type_step = artifact.steps[0]
    assert isinstance(type_step.action, TypeAction)
    assert type_step.action.locator.html_name == "mem_no"
    assert type_step.action.locator.resolved_via == "xpath_proximity"
    assert artifact.outputs[0].name == "credit_score"
    assert artifact.outputs[0].source_locator is not None


def test_parameterizer_binds_new_application_fields() -> None:
    artifact = Artifact(
        id=new_artifact_id(),
        name="new-cc-application",
        description="Submit a new credit card application",
        entry_url="http://localhost:3000/application.html",
        allowed_domain="localhost",
        steps=[
            ArtifactStep(
                step_index=0,
                description="type member",
                action=TypeAction(
                    locator=LocatorStrategy(name="Member Number *", html_name="app_mem_no"),
                    value="1042381",
                ),
            ),
            ArtifactStep(
                step_index=1,
                description="Click Lookup Member",
                action=ClickAction(locator=LocatorStrategy(role="button", name="Lookup Member")),
            ),
            ArtifactStep(
                step_index=2,
                description="ssn1",
                action=TypeAction(
                    locator=LocatorStrategy(name="XXX", html_name="ssn_pt1"),
                    value="342",
                    sensitive=True,
                ),
            ),
            ArtifactStep(
                step_index=3,
                description="ssn2",
                action=TypeAction(
                    locator=LocatorStrategy(name="XX", html_name="ssn_pt2"),
                    value="980",
                    sensitive=True,
                ),
            ),
            ArtifactStep(
                step_index=4,
                description="ssn3",
                action=TypeAction(
                    locator=LocatorStrategy(name="XXXX", html_name="ssn_pt3"),
                    value="9876",
                    sensitive=True,
                ),
            ),
            ArtifactStep(
                step_index=5,
                description="Click US Citizen",
                action=ClickAction(
                    locator=LocatorStrategy(role="radio", name="US Citizen", html_name="citizenship"),
                ),
            ),
            ArtifactStep(
                step_index=6,
                description="address",
                action=TypeAction(
                    locator=LocatorStrategy(name="Address Line 1 *", html_name="app_addr1"),
                    value="2240 Birchwood Ave",
                ),
            ),
            ArtifactStep(
                step_index=7,
                description="city",
                action=TypeAction(
                    locator=LocatorStrategy(name="City *", html_name="app_city"),
                    value="Springfield",
                ),
            ),
            ArtifactStep(
                step_index=8,
                description="state",
                action=SelectAction(
                    locator=LocatorStrategy(name="", html_name="app_state"),
                    option_label="IL",
                ),
            ),
            ArtifactStep(
                step_index=9,
                description="employer",
                action=TypeAction(
                    locator=LocatorStrategy(name="Employer Name *", html_name="emp_name"),
                    value="ABC Consultants",
                ),
            ),
            ArtifactStep(
                step_index=10,
                description="status",
                action=SelectAction(
                    locator=LocatorStrategy(name="Employment Status *", html_name="emp_status"),
                    option_label="Full-Time Employed",
                ),
            ),
            ArtifactStep(
                step_index=11,
                description="years",
                action=TypeAction(
                    locator=LocatorStrategy(name="Years Employed *", html_name="emp_years"),
                    value="3",
                ),
            ),
            ArtifactStep(
                step_index=12,
                description="income",
                action=TypeAction(
                    locator=LocatorStrategy(name="Gross Annual Income *", html_name="emp_income"),
                    value="82000",
                ),
            ),
            ArtifactStep(
                step_index=13,
                description="rent",
                action=TypeAction(
                    locator=LocatorStrategy(name="Monthly Rent/Mortgage *", html_name="emp_housing"),
                    value="1200",
                ),
            ),
            ArtifactStep(
                step_index=14,
                description="card",
                action=SelectAction(
                    locator=LocatorStrategy(name="Card Type *", html_name="card_type"),
                    option_label="Gold Rewards ($95/yr)",
                ),
            ),
            ArtifactStep(
                step_index=15,
                description="limit",
                action=SelectAction(
                    locator=LocatorStrategy(name="Requested Credit Limit *", html_name="req_limit"),
                    option_label="$5,000",
                ),
            ),
            ArtifactStep(
                step_index=16,
                description="Submit Application",
                action=ClickAction(locator=LocatorStrategy(role="button", name="Submit Application")),
            ),
        ],
        checkpoint=Checkpoint(
            description="Application ID: APP-8856600",
            text_visible="APP-8856600",
        ),
        parameters=[],
        goal="Submit a new credit card application for member 1042381",
    )
    out = parameterize(artifact)
    names = {p.name for p in out.parameters}
    assert "app_id" not in names
    assert out.checkpoint.text_visible == "Application Submitted Successfully"
    assert {p.name: p for p in out.parameters}["ssn_pt1"].required
    for required in (
        "member_no",
        "first_name",
        "last_name",
        "dob",
        "ssn_pt1",
        "ssn_pt2",
        "ssn_pt3",
        "citizenship",
        "address",
        "city",
        "state",
        "phone",
        "employment_status",
        "employer",
        "years_employed",
        "gross_annual_income",
        "monthly_rent",
        "card_type",
        "requested_limit",
    ):
        assert required in names, required
    typed = {
        step.action.value
        for step in out.steps
        if isinstance(step.action, TypeAction)
    }
    assert "{first_name}" in typed
    assert "{address}" in typed
    citizen = next(
        step.action
        for step in out.steps
        if isinstance(step.action, ClickAction) and step.action.locator.html_name == "citizenship"
    )
    assert citizen.locator.name == "{citizenship}"
    limit = next(
        step.action
        for step in out.steps
        if isinstance(step.action, SelectAction) and step.action.locator.html_name == "req_limit"
    )
    assert limit.option_label == "{requested_limit}"
    filled = expand_application_params(
        {
            "member_no": "1042381",
            "first_name": "Marcus",
            "last_name": "Delacroix",
            "dob": "04/17/1979",
            "ssn": "342-980-9876",
            "citizenship": "US",
            "address": "2240 Birchwood Ave",
            "city": "Springfield",
            "state": "IL",
            "phone": "(217) 555-0143",
            "employment_status": "Full-Time Employed",
            "employer": "ABC Consultants",
            "years_employed": "3",
            "gross_annual_income": "82000",
            "monthly_rent": "1200",
            "card_type": "Gold Rewards",
            "requested_limit": "5000",
        }
    )
    assert not missing_review_params(filled, out)
    assert missing_review_params({"member_no": "1042381"}, out)
    assert filled["ssn_pt1"] == "342"
    assert filled["requested_limit"] == "$5,000"
    assert filled["card_type"] == "Gold Rewards ($95/yr)"
    assert filled["citizenship"] == "US Citizen"
