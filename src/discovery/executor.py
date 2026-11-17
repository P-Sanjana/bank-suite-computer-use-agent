"""
Dispatches parsed tool-call inputs to Playwright. Emits ActionEvent after every dispatch.
Does not enforce safety policy; that is done by the agent loop.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from playwright.async_api import Page

from ..schema.artifact import LocatorStrategy
from ..safety.redactor import redact_dict
from ..surface.locator import LocatorError, ResolvedLocator, resolve_locator, strategy_from_tool_input
from .observer import PageState, capture_page_state_with_iframes
from .tools.definitions import (
    CLICK,
    NAVIGATE,
    READ,
    READ_STATE,
    SELECT,
    TYPE,
    WAIT,
    ClickInput,
    NavigateInput,
    SelectInput,
    ToolName,
    TypeInput,
    WaitInput,
)


@dataclass
class ActionResult:
    success: bool
    observation: str
    page_state: PageState | None = None
    error: str | None = None
    extracted: dict[str, Any] | None = None
    resolved_locator: LocatorStrategy | None = None


@dataclass
class ActionEvent:
    tool: ToolName
    input: dict[str, Any]
    result: ActionResult
    step_index: int
    timestamp: str


ActionListener = Callable[[ActionEvent], Awaitable[None]]


class Executor:
    """Dispatches tool calls to Playwright and notifies listeners after each action."""

    def __init__(self, page: Page) -> None:
        self._page = page
        self._step_index = 0
        self._listeners: list[ActionListener] = []

    def on_action(self, listener: ActionListener) -> None:
        self._listeners.append(listener)

    async def _emit(self, event: ActionEvent) -> None:
        for listener in self._listeners:
            await listener(event)

    async def dispatch(self, tool: ToolName, raw_input: dict[str, Any]) -> ActionResult:
        redacted_input = redact_dict(raw_input)
        try:
            result = await self._dispatch_inner(tool, raw_input)
        except LocatorError as exc:
            result = ActionResult(
                success=False,
                observation=f"Action failed: {exc}",
                error=str(exc),
            )
        except Exception as exc:
            msg = str(exc)
            result = ActionResult(
                success=False,
                observation=f"Action failed: {msg}",
                error=msg,
            )

        if result.resolved_locator:
            redacted_input["resolved_via"] = result.resolved_locator.resolved_via
            redacted_input["html_name"] = result.resolved_locator.html_name
            redacted_input["html_id"] = result.resolved_locator.html_id
            redacted_input["frame_url"] = result.resolved_locator.frame_url

        event = ActionEvent(
            tool=tool,
            input=redacted_input,
            result=result,
            step_index=self._step_index,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._step_index += 1
        await self._emit(event)
        return result

    async def _dispatch_inner(self, tool: ToolName, inp: dict[str, Any]) -> ActionResult:
        if tool == NAVIGATE:
            return await self._navigate(inp)
        if tool == CLICK:
            return await self._click(inp)
        if tool == TYPE:
            return await self._type(inp)
        if tool == SELECT:
            return await self._select(inp)
        if tool == WAIT:
            return await self._wait(inp)
        if tool == READ:
            return await self._read(inp)
        if tool == READ_STATE:
            return await self._read_state(inp)
        raise ValueError(f"Unrecognized tool: {tool}")

    async def _resolve(self, inp: dict[str, Any]) -> ResolvedLocator:
        return await resolve_locator(self._page, strategy_from_tool_input(inp))

    async def _navigate(self, inp: NavigateInput) -> ActionResult:
        await self._page.goto(inp["url"], wait_until="domcontentloaded", timeout=15_000)
        state = await capture_page_state_with_iframes(self._page)
        return ActionResult(
            success=True,
            observation=f"Navigated to {inp['url']}, title: '{state.title}'.",
            page_state=state,
        )

    async def _click(self, inp: ClickInput) -> ActionResult:
        resolved = await self._resolve(inp)
        await resolved.locator.click(timeout=8_000)
        await self._page.wait_for_timeout(500)
        state = await capture_page_state_with_iframes(self._page)
        return ActionResult(
            success=True,
            observation=f'Clicked "{inp.get("name")}". Page updated ({state.url}).',
            page_state=state,
            resolved_locator=resolved.strategy,
        )

    async def _type(self, inp: TypeInput) -> ActionResult:
        resolved = await self._resolve(inp)
        if not inp.get("append", False):
            await resolved.locator.clear()
        await resolved.locator.fill(inp["value"])
        display = "[REDACTED]" if inp.get("sensitive") else f'"{inp["value"]}"'
        return ActionResult(
            success=True,
            observation=f'Typed {display} into "{inp.get("name") or inp.get("label")}".',
            resolved_locator=resolved.strategy,
        )

    async def _select(self, inp: SelectInput) -> ActionResult:
        resolved = await self._resolve(inp)
        option_label = inp.get("option_label")
        option_value = inp.get("option_value")
        if option_label:
            await resolved.locator.select_option(label=option_label)
            chosen = option_label
        elif option_value:
            await resolved.locator.select_option(value=option_value)
            chosen = option_value
        else:
            raise ValueError("select requires option_label or option_value")
        return ActionResult(
            success=True,
            observation=f'Selected "{chosen}" in "{inp.get("name")}".',
            resolved_locator=resolved.strategy,
        )

    async def _wait(self, inp: WaitInput) -> ActionResult:
        condition = inp["condition"]
        if condition in ("element_visible", "element_hidden"):
            resolved = await self._resolve(inp)
            state = "visible" if condition == "element_visible" else "hidden"
            await resolved.locator.wait_for(state=state, timeout=15_000)
            return ActionResult(
                success=True,
                observation=f'Element [{inp.get("role")}] "{inp.get("name")}" is {state}.',
                resolved_locator=resolved.strategy,
            )
        if condition == "url_contains":
            sub = inp.get("url_substring", "")
            await self._page.wait_for_url(f"**/*{sub}*", timeout=15_000)
            return ActionResult(success=True, observation=f'URL contains "{sub}".')
        if condition == "duration":
            ms = min(int(inp.get("duration_ms") or 1000), 5000)
            await self._page.wait_for_timeout(ms)
            return ActionResult(success=True, observation=f"Waited {ms}ms.")
        raise ValueError(f"Unknown wait condition: {condition}")

    async def _read(self, inp: dict[str, Any]) -> ActionResult:
        resolved = await self._resolve(inp)
        try:
            value = (await resolved.locator.inner_text()).strip()
        except Exception:
            value = (await resolved.locator.input_value()).strip()
        key = str(inp.get("output_key") or "value")
        return ActionResult(
            success=True,
            observation=f'Read {key}="{value}" from "{inp.get("name") or inp.get("label")}".',
            extracted={key: value},
            resolved_locator=resolved.strategy,
        )

    async def _read_state(self, inp: dict[str, Any] | None = None) -> ActionResult:
        state = await capture_page_state_with_iframes(self._page)
        return ActionResult(
            success=True,
            observation=f'URL: {state.url}\nTitle: "{state.title}"',
            page_state=state,
        )
