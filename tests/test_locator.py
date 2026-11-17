from __future__ import annotations

import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from src.schema.artifact import LocatorStrategy
from src.surface.locator import (
    METHOD_HREF_CONTAINS,
    METHOD_INPUT_VALUE,
    METHOD_XPATH_PROXIMITY,
    resolve_locator,
)

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "target-app"


class _Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(APP), **kwargs)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        pass


@pytest.fixture
def app_origin() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    yield f"http://{host}:{port}"
    server.shutdown()
    server.server_close()


@pytest.mark.asyncio
async def test_member_number_resolved_via_proximity(app_origin: str) -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"{app_origin}/member-lookup.html", wait_until="domcontentloaded")
        resolved = await resolve_locator(
            page, LocatorStrategy(role="textbox", name="Member Number")
        )
        assert resolved.method == METHOD_XPATH_PROXIMITY
        await resolved.locator.fill("1042381")
        assert await page.locator('[name="mem_no"]').input_value() == "1042381"
        await browser.close()


@pytest.mark.asyncio
async def test_search_button_by_role_and_read_score(app_origin: str) -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"{app_origin}/member-lookup.html", wait_until="domcontentloaded")
        member = await resolve_locator(page, LocatorStrategy(name="Member Number"))
        await member.locator.fill("1042381")
        search = await resolve_locator(
            page, LocatorStrategy(role="button", name="Search Member")
        )
        await search.locator.click()
        await page.wait_for_timeout(400)
        view = await resolve_locator(page, LocatorStrategy(role="link", name="View"))
        await view.locator.click()
        for _ in range(25):
            if any("member-detail" in (f.url or "") for f in page.frames):
                break
            await page.wait_for_timeout(100)
        score = await resolve_locator(page, LocatorStrategy(name="Credit Score"))
        text = (await score.locator.inner_text()).strip()
        assert text == "734"
        await browser.close()


@pytest.mark.asyncio
async def test_review_link_resolved_by_app_id_href(app_origin: str) -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"{app_origin}/queue.html", wait_until="domcontentloaded")
        await page.wait_for_timeout(200)
        resolved = await resolve_locator(
            page,
            LocatorStrategy(role="link", name="Review", href_contains="APP-1000901"),
        )
        assert resolved.method == METHOD_HREF_CONTAINS
        await resolved.locator.click()
        await page.wait_for_timeout(300)
        assert "APP-1000901" in page.url
        await browser.close()


@pytest.mark.asyncio
async def test_decision_radio_resolved_by_input_value(app_origin: str) -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"{app_origin}/review.html?id=APP-1001187", wait_until="domcontentloaded")
        await page.wait_for_timeout(200)
        resolved = await resolve_locator(
            page,
            LocatorStrategy(
                role="radio",
                name="Decline",
                html_name="decision",
                input_value="DECLINED",
            ),
        )
        assert resolved.method == METHOD_INPUT_VALUE
        await resolved.locator.click()
        assert await page.locator('input[name="decision"][value="DECLINED"]').is_checked()
        await browser.close()


@pytest.mark.asyncio
async def test_unlabeled_approve_radio_by_visible_name(app_origin: str) -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"{app_origin}/review.html?id=APP-1001187", wait_until="domcontentloaded")
        await page.wait_for_timeout(200)
        resolved = await resolve_locator(
            page, LocatorStrategy(role="radio", name="Approve")
        )
        assert resolved.method == METHOD_INPUT_VALUE
        await resolved.locator.click()
        assert await page.locator('input[name="decision"][value="APPROVED"]').is_checked()
        await browser.close()
