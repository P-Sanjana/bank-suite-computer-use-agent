from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from src.escalation.handler import EscalationHandler, EscalationReason, EscalationRequest
from src.replay.runner import _detect_business_outcome
from src.schema.result import OutcomeCode


def test_mock_operator_response(tmp_path: Path) -> None:
    path = EscalationHandler.write_mock_response(
        "abc123",
        actions_taken="Filled member number",
        intervention_dir=tmp_path,
    )
    assert path.exists()
    text = path.read_text()
    assert "abc123" in text
    assert "Filled member number" in text


async def _expand_dock(page) -> None:
    await page.wait_for_selector("#cui-operator-host")
    toggle = page.locator("#cui-operator-toggle")
    box = await toggle.bounding_box()
    assert box is not None and box["height"] >= 20 and box["width"] >= 80
    await toggle.click()
    await page.locator("#cui-operator-notes").wait_for()


@pytest.mark.asyncio
async def test_operator_banner_writes_response_and_resume(tmp_path: Path) -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content("<html><body><p>Bank Suite</p></body></html>")
        handler = EscalationHandler(
            page,
            goal="Look up SWIFT code",
            entry_url="http://localhost:3000/member-lookup.html",
            intervention_dir=tmp_path / "interventions",
            poll_timeout_s=15,
            poll_interval_s=0.15,
        )
        request = EscalationRequest(
            reason=EscalationReason.STUCK,
            description="SWIFT code is not on this page",
            current_step="read SWIFT",
            suggested_action="Check another screen",
        )

        async def click_resume() -> None:
            await _expand_dock(page)
            await page.fill("#cui-operator-notes", "Confirmed SWIFT is not available")
            await page.click("#cui-operator-resume")

        task = asyncio.create_task(handler.handle(request, "op1"))
        await click_resume()
        record = await task
        await browser.close()

    assert record is not None
    assert record.resume is True
    assert "Confirmed SWIFT" in record.actions_taken
    response_path = tmp_path / "interventions" / "op1-response.json"
    assert response_path.exists()
    assert (tmp_path / "handoff.json").exists()


@pytest.mark.asyncio
async def test_operator_stop_does_not_resume(tmp_path: Path) -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content("<html><body><p>Bank Suite</p></body></html>")
        handler = EscalationHandler(
            page,
            goal="goal",
            entry_url="http://localhost",
            intervention_dir=tmp_path / "interventions",
            poll_timeout_s=15,
            poll_interval_s=0.15,
        )
        request = EscalationRequest(
            reason=EscalationReason.STUCK,
            description="blocked",
            current_step="step",
        )

        async def click_stop() -> None:
            await _expand_dock(page)
            await page.fill("#cui-operator-notes", "closed the browser")
            await page.click("#cui-operator-stop")

        task = asyncio.create_task(handler.handle(request, "op2"))
        await click_stop()
        record = await task
        await browser.close()

    assert record is not None
    assert record.resume is False
    assert "closed the browser" in record.actions_taken


@pytest.mark.asyncio
async def test_resume_disabled_without_description(tmp_path: Path) -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content("<html><body><p>Bank Suite</p></body></html>")
        handler = EscalationHandler(
            page,
            goal="goal",
            entry_url="http://localhost",
            intervention_dir=tmp_path / "interventions",
            poll_timeout_s=8,
            poll_interval_s=0.15,
        )
        request = EscalationRequest(
            reason=EscalationReason.STUCK,
            description="blocked",
            current_step="step",
        )
        task = asyncio.create_task(handler.handle(request, "op3"))
        await _expand_dock(page)
        assert await page.locator("#cui-operator-resume").is_disabled()
        assert await page.locator("#cui-operator-stop").is_disabled()
        await page.fill("#cui-operator-notes", "   ")
        assert await page.locator("#cui-operator-resume").is_disabled()
        await page.fill("#cui-operator-notes", "Opened member detail")
        assert await page.locator("#cui-operator-resume").is_enabled()
        await page.click("#cui-operator-resume")
        record = await task
        await browser.close()
    assert record is not None
    assert record.actions_taken == "Opened member detail"


def test_business_outcome_not_found() -> None:
    outcome = _detect_business_outcome(
        "No members found matching the search criteria. Verify input and try again.",
        step_index=2,
    )
    assert outcome is not None
    assert outcome.outcome_code == OutcomeCode.MEMBER_NOT_FOUND
    assert outcome.status == "business_outcome"
