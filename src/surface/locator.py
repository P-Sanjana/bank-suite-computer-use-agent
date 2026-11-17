"""
Shared control targeting for discovery and replay.

Fallback chain (first unique visible match wins):
  1. get_by_role(role, name, exact=True)
  2. get_by_role(role, name, exact=False)
  3. get_by_label(label)
  4. get_by_placeholder(name)
  5. [name="..."] HTML attribute
  6. [id="..."] HTML attribute
  7. [title="..."] attribute
  8. XPath proximity: //td[normalize-space()="Label"]/following-sibling::td[1]//input
  9. get_by_role(role).nth(nth)

The same chain runs on the main frame and then each iframe so replay does not
depend on a clean top-level DOM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from playwright.async_api import Frame, Locator, Page

from ..schema.artifact import LocatorStrategy

METHOD_ROLE_NAME_EXACT = "role_name_exact"
METHOD_ROLE_NAME_RELAXED = "role_name_relaxed"
METHOD_LABEL = "label"
METHOD_PLACEHOLDER = "placeholder"
METHOD_HTML_NAME = "html_name"
METHOD_HTML_ID = "html_id"
METHOD_TITLE = "title"
METHOD_XPATH_PROXIMITY = "xpath_proximity"
METHOD_XPATH_VALUE_CELL = "xpath_value_cell"
METHOD_ROLE_NTH = "role_nth"
METHOD_HREF_CONTAINS = "href_contains"
METHOD_INPUT_VALUE = "input_value"
METHOD_RADIO_ADJACENT = "radio_adjacent"

_RADIO_VALUE_BY_LABEL = {
    "approve": "APPROVED",
    "approved": "APPROVED",
    "decline": "DECLINED",
    "declined": "DECLINED",
    "escalate": "ESCALATED",
    "escalated": "ESCALATED",
    "escalate to senior officer": "ESCALATED",
    "request more info": "PENDING",
    "more info": "PENDING",
    "pending": "PENDING",
}

STRATEGY_ORDER: list[str] = [
    METHOD_HREF_CONTAINS,
    METHOD_INPUT_VALUE,
    METHOD_ROLE_NAME_EXACT,
    METHOD_ROLE_NAME_RELAXED,
    METHOD_LABEL,
    METHOD_PLACEHOLDER,
    METHOD_HTML_NAME,
    METHOD_HTML_ID,
    METHOD_TITLE,
    METHOD_XPATH_PROXIMITY,
    METHOD_XPATH_VALUE_CELL,
    METHOD_RADIO_ADJACENT,
    METHOD_ROLE_NTH,
]


class LocatorError(Exception):
    def __init__(self, msg: str, role: str = "", name: str = "") -> None:
        super().__init__(msg)
        self.role = role
        self.name = name


@dataclass
class ResolvedLocator:
    locator: Locator
    strategy: LocatorStrategy
    method: str
    frame_url: str


def strategy_from_tool_input(inp: dict[str, Any]) -> LocatorStrategy:
    nth_raw = inp.get("nth")
    nth = int(nth_raw) if nth_raw is not None and nth_raw != "" else None
    return LocatorStrategy(
        role=str(inp.get("role") or ""),
        name=str(inp.get("name") or ""),
        label=_opt_str(inp.get("label")),
        nth=nth,
        html_name=_opt_str(inp.get("html_name")),
        html_id=_opt_str(inp.get("html_id")),
        title=_opt_str(inp.get("title")),
        href_contains=_opt_str(inp.get("href_contains")),
        input_value=_opt_str(inp.get("input_value")),
        resolved_via=_opt_str(inp.get("resolved_via")) or "",
        frame_url=_opt_str(inp.get("frame_url")),
        rationale=str(inp.get("rationale") or inp.get("reason") or ""),
    )


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _xpath_literal(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ", \"'\", ".join(f"'{p}'" for p in parts) + ")"


def proximity_xpath(label: str) -> str:
    lit = _xpath_literal(label)
    return (
        f"//td[normalize-space()={lit}]/following-sibling::td[1]"
        f"//*[self::input or self::textarea or self::select]"
    )


def radio_adjacent_xpath(label: str) -> str:
    lit = _xpath_literal(label)
    return (
        f"//input[@type='radio']["
        f"contains(normalize-space(following-sibling::text()[1]), {lit})"
        f" or contains(normalize-space(following-sibling::*[1]), {lit})"
        f"]"
    )


def _radio_value(strategy: LocatorStrategy) -> str | None:
    if strategy.input_value:
        return strategy.input_value
    for raw in (strategy.name, strategy.label):
        if not raw:
            continue
        key = re.sub(r"\s+", " ", raw.strip().lower())
        if key in _RADIO_VALUE_BY_LABEL:
            return _RADIO_VALUE_BY_LABEL[key]
        upper = raw.strip().upper()
        if upper in {"APPROVED", "DECLINED", "ESCALATED", "PENDING"}:
            return upper
    return None


def proximity_value_xpath(label: str) -> str:
    lit = _xpath_literal(label)
    return f"//td[normalize-space()={lit}]/following-sibling::td[1]"


def _targets(page: Page) -> list[Page | Frame]:
    targets: list[Page | Frame] = [page]
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        if frame.url.startswith("about:"):
            continue
        targets.append(frame)
    return targets


def _attr_values(strategy: LocatorStrategy, attr: str) -> list[str]:
    ordered: list[str] = []
    if attr == "name" and strategy.html_name:
        ordered.append(strategy.html_name)
    if attr == "id" and strategy.html_id:
        ordered.append(strategy.html_id)
    if attr == "title" and strategy.title:
        ordered.append(strategy.title)
    for candidate in (strategy.name, strategy.label):
        if candidate and candidate not in ordered:
            ordered.append(candidate)
    return ordered


def _label_values(strategy: LocatorStrategy) -> list[str]:
    ordered: list[str] = []
    for candidate in (strategy.label, strategy.name):
        if candidate and candidate not in ordered:
            ordered.append(candidate)
    return ordered


def _candidate(target: Page | Frame, method: str, strategy: LocatorStrategy) -> Locator | None:
    role = strategy.role
    name = strategy.name

    if method == METHOD_HREF_CONTAINS:
        href = strategy.href_contains
        if not href:
            return None
        return target.locator(f'a[href*="{href}"]')

    if method == METHOD_INPUT_VALUE:
        value = _radio_value(strategy)
        if not value:
            return None
        if strategy.html_name:
            return target.locator(f'input[name="{strategy.html_name}"][value="{value}"]')
        return target.locator(f'input[type="radio"][value="{value}"]')

    if method == METHOD_RADIO_ADJACENT:
        labels = _label_values(strategy)
        if not labels:
            return None
        loc = target.locator(f"xpath={radio_adjacent_xpath(labels[0])}")
        for extra in labels[1:]:
            loc = loc.or_(target.locator(f"xpath={radio_adjacent_xpath(extra)}"))
        return loc

    if method == METHOD_ROLE_NAME_EXACT:
        if not (role and name):
            return None
        return target.get_by_role(role, name=name, exact=True)  # type: ignore[arg-type]

    if method == METHOD_ROLE_NAME_RELAXED:
        if not (role and name):
            return None
        return target.get_by_role(role, name=name, exact=False)  # type: ignore[arg-type]

    if method == METHOD_LABEL:
        labels = _label_values(strategy)
        if not labels:
            return None
        loc = target.get_by_label(labels[0], exact=False)
        for extra in labels[1:]:
            loc = loc.or_(target.get_by_label(extra, exact=False))
        return loc

    if method == METHOD_PLACEHOLDER:
        values = _label_values(strategy)
        if not values:
            return None
        loc = target.get_by_placeholder(values[0], exact=False)
        for extra in values[1:]:
            loc = loc.or_(target.get_by_placeholder(extra, exact=False))
        return loc

    if method == METHOD_HTML_NAME:
        values = _attr_values(strategy, "name")
        if not values:
            return None
        loc = target.locator(f'[name="{values[0]}"]')
        for extra in values[1:]:
            loc = loc.or_(target.locator(f'[name="{extra}"]'))
        return loc

    if method == METHOD_HTML_ID:
        values = _attr_values(strategy, "id")
        if not values:
            return None
        loc = target.locator(f'[id="{values[0]}"]')
        for extra in values[1:]:
            loc = loc.or_(target.locator(f'[id="{extra}"]'))
        return loc

    if method == METHOD_TITLE:
        values = _attr_values(strategy, "title")
        if not values:
            return None
        loc = target.locator(f'[title="{values[0]}"]')
        for extra in values[1:]:
            loc = loc.or_(target.locator(f'[title="{extra}"]'))
        return loc

    if method == METHOD_XPATH_PROXIMITY:
        labels = _label_values(strategy)
        if not labels:
            return None
        loc = target.locator(f"xpath={proximity_xpath(labels[0])}")
        for extra in labels[1:]:
            loc = loc.or_(target.locator(f"xpath={proximity_xpath(extra)}"))
        return loc

    if method == METHOD_XPATH_VALUE_CELL:
        labels = _label_values(strategy)
        if not labels:
            return None
        loc = target.locator(f"xpath={proximity_value_xpath(labels[0])}")
        for extra in labels[1:]:
            loc = loc.or_(target.locator(f"xpath={proximity_value_xpath(extra)}"))
        return loc

    if method == METHOD_ROLE_NTH:
        if not role or strategy.nth is None:
            return None
        return target.get_by_role(role)  # type: ignore[arg-type]

    return None


async def _usable(loc: Locator, strategy: LocatorStrategy, method: str) -> Locator | None:
    try:
        count = await loc.count()
    except Exception:
        return None
    if count == 0:
        return None

    if method == METHOD_ROLE_NTH:
        if strategy.nth is None:
            return None
        target = loc.nth(strategy.nth)
    elif count > 1:
        if strategy.nth is None:
            return None
        target = loc.nth(strategy.nth)
    else:
        target = loc.first if count == 1 else loc

    try:
        if await target.is_visible():
            return target
    except Exception:
        return None
    return None


def _methods_for(strategy: LocatorStrategy) -> list[str]:
    proven = strategy.resolved_via
    if proven and proven in STRATEGY_ORDER:
        return [proven] + [m for m in STRATEGY_ORDER if m != proven]
    return list(STRATEGY_ORDER)


async def resolve_locator(page: Page, strategy: LocatorStrategy) -> ResolvedLocator:
    """Return the first unique visible locator in the fallback chain."""
    last_reason = "no candidates matched"
    for target in _targets(page):
        frame_url = target.url if isinstance(target, Frame) else page.url
        for method in _methods_for(strategy):
            candidate = _candidate(target, method, strategy)
            if candidate is None:
                continue
            usable = await _usable(candidate, strategy, method)
            if usable is None:
                last_reason = f"{method} did not match a unique visible element"
                continue
            html_name = await _attr(usable, "name")
            html_id = await _attr(usable, "id")
            title = await _attr(usable, "title")
            filled = strategy.model_copy(
                update={
                    "resolved_via": method,
                    "html_name": html_name or strategy.html_name,
                    "html_id": html_id or strategy.html_id,
                    "title": title or strategy.title,
                    "frame_url": frame_url,
                    "rationale": strategy.rationale
                    or f"Resolved via {method} (role={strategy.role!r}, name={strategy.name!r})",
                }
            )
            return ResolvedLocator(
                locator=usable,
                strategy=filled,
                method=method,
                frame_url=frame_url,
            )
    raise LocatorError(
        f"Cannot resolve locator ({last_reason}). "
        f"role={strategy.role!r}, name={strategy.name!r}, label={strategy.label!r}",
        role=strategy.role,
        name=strategy.name,
    )


async def _attr(locator: Locator, name: str) -> str | None:
    try:
        value = await locator.get_attribute(name)
    except Exception:
        return None
    return value or None
