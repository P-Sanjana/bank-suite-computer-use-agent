"""
Reads the accessibility tree from a Playwright page and returns a structured PageState for the
agent loop and planner.
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from playwright.async_api import Page, Frame

_NOISE_ROLES: frozenset[str] = frozenset({
    "none", "presentation", "generic", "LineBreak", "separator", "IFramePresentational"
})
_FORM_ROLES: frozenset[str] = frozenset({
    "radio", "checkbox", "textbox", "combobox", "searchbox", "spinbutton", "slider",
})

@dataclass
class A11yNode:
    role: str
    name: str
    value: str | None = None
    description: str | None = None
    disabled: bool = False
    checked: bool | None = None
    expanded: bool | None = None
    level: int | None = None # for headings
    children: list["A11yNode"] = field(default_factory=list)

    def is_useful(self) -> bool:
        if self.role in _NOISE_ROLES:
            return False
        role_key = self.role.split()[0].lower() if self.role else ""
        if role_key in _FORM_ROLES:
            return True
        if not self.name.strip() and not self.children:
            return False
        return True

@dataclass
class PageState:
    url: str
    title: str
    tree: list[A11yNode]
    tree_summary: str
    captured_at: str

def _normalize(raw: dict[str, Any]) -> A11yNode:
    node = A11yNode(
        role = str(raw.get("role", "unknown")),
        name = str(raw.get("name","")),
    )
    if raw.get("value") not in (None, ""):
        node.value = str(raw["value"])
    if raw.get("description") not in (None, ""):
        node.description = str(raw["description"])
    if raw.get("disabled"):
        node.disabled = True
    if "checked" in raw:
        node.checked = bool(raw["checked"])
    if "expanded" in raw:
        node.expanded = bool(raw["expanded"])
    if isinstance(raw.get("level"), int):
        node.level = raw["level"]

    for child_raw in raw.get("children", []):
        child = _normalize(child_raw)
        if child.is_useful():
            node.children.append(child)
    return node

def _render(nodes: list[A11yNode], depth: int = 0, max_depth: int = 8) -> str:
    """Render the tree as an indented text outline for the prompt."""
    if depth > max_depth:
        return ""
    lines: list[str] = []
    indent = " " * depth
    for node in nodes:
        parts = [f"{indent}[{node.role}]"]
        if node.name:
            parts.append(f'"{node.name}"')
        if node.value is not None:
            parts.append(f'="{node.value}"')
        if node.disabled:
            parts.append("(disabled)")
        if node.checked is not None:
            parts.append(f"({'checked' if node.checked else 'unchecked'})")
        if node.expanded is not None:
            parts.append(f"({'expanded' if node.expanded else 'collapsed'})")
        lines.append(" ".join(parts))
        if node.children:
            lines.append(_render(node.children, depth + 1, max_depth))
    return "\n".join(l for l in lines if l)

async def _extract_tree(source: Page | Frame) -> list[A11yNode]:
    try:
        raw_snapshot = await source.locator("body").aria_snapshot()
    except Exception:
        raw_snapshot = ""
    
    if not raw_snapshot:
        return []
    return _parse_aria_snapshot(raw_snapshot)

def _parse_aria_snapshot(snapshot: str) -> list[A11yNode]:
    """
    Parse Playwright's aria_snapshot() YAML-like output into A11yNode list.
    Format example:
      - heading "Page Title"
      - button "Submit"
      - textbox "Member Number"
    """
    nodes: list[A11yNode] = []
    for line in snapshot.splitlines():
        stripped = line.strip()
        if not stripped or not stripped.startswith("- "):
            continue
        content = stripped[2:] # remove "- "

        if '"' in content:
            role, _, rest = content.partition('"')
            name = rest.rstrip('"').strip()
            role = role.strip()
        else:
            role = content.strip()
            name = ""

        node = A11yNode(role=role, name=name)
        if node.is_useful():
            nodes.append(node)
    return nodes

async def capture_page_state(page: Page) -> PageState:
    """Capture current page state - called at the start of each agent turn."""
    url, title, tree = await asyncio.gather(
        _get_url(page),
        page.title(),
        _extract_tree(page),
    )
    return PageState(
        url=url,
        title=title,
        tree=tree,
        tree_summary=_render(tree),
        captured_at=datetime.now(timezone.utc).isoformat(),
    )

async def _get_url(page: Page) -> str:
    return page.url

async def capture_page_state_with_iframes(page: Page) -> PageState:
    """Capture state and merge in any non-blank iframes."""
    state = await capture_page_state(page)
    for frame in page.frames:
        if frame == page.main_frame or frame.url.startswith("about:"):
            continue
        try:
            frame_tree = await _extract_tree(frame)
            iframe_wrapper = A11yNode(
                role="region",
                name=f"iframe[{frame.url}]",
                children=frame_tree,
            )
            state.tree.append(iframe_wrapper)
            state.tree_summary += (
                f"\n\n--- IFRAME: {frame.url} ---\n {_render(frame_tree)}"
            )
        except Exception:
            # Cross-origin or not yet loaded - skip silently
            pass
    return state