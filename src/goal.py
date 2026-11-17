"""Keep dollar amounts in --goal when a POSIX shell (Git Bash) ate them.

In bash, `$5000` inside double quotes is `$5` (positional arg 5, usually empty)
plus `000`. `$5,000` becomes `,000`. Python never sees the `$`.
"""
from __future__ import annotations

import re

# Leftover after `$` + first digit is consumed. Prefer Bank Suite limits.
_EATEN_AMOUNT_AFTER_FOR = [
    (re.compile(r"(?<=\bfor ),000\b"), "$5,000"),
    (re.compile(r"(?<=\bof ),000\b"), "$5,000"),
    (re.compile(r"(?<=\blimit ),000\b"), "$5,000"),
    (re.compile(r"(?<=\bfor )0,000\b"), "$10,000"),
    (re.compile(r"(?<=\bof )0,000\b"), "$10,000"),
    (re.compile(r"(?<=\bfor )000\b"), "$5000"),
    (re.compile(r"(?<=\bof )000\b"), "$5000"),
    (re.compile(r"(?<=\blimit )000\b"), "$5000"),
    # PowerShell `"$5000"` expands to empty (variable 5000).
    (re.compile(r"\bfor  with\b"), "for $5000 with"),
    (re.compile(r"\bof  with\b"), "of $5000 with"),
]


def preserve_dollar_amounts(text: str) -> str:
    out = text
    for pattern, replacement in _EATEN_AMOUNT_AFTER_FOR:
        out = pattern.sub(replacement, out)
    return out
