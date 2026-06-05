"""relationships.py - read the epic <-> ticket <-> issue graph from body links.

The work breakdown is flat posts; the structure lives as markdown links in each
body (see ``skills/specseed/references/remote-posts.md`` and the entity
templates):

    Ticket body:  ``Epic: #12``   ``Issues: #61, #62``   ``Depends on: #40``
    Issue body:   ``Ticket: #41``  ``Depends on: #61``
    Epic body:    ``## Tickets`` then ``#41, #42`` lines

This module parses those links so the scheduler can roll a parent up to ``done``
once all its children finish, without inventing a parent/child label scheme.

Only Python stdlib is used.
"""

from __future__ import annotations

import re
from typing import Optional

# ``Epic: #12`` / ``Ticket: #41`` -> the single parent id (first match wins).
_PARENT_RE = {
    "epic": re.compile(r"\bEpic:\s*#?(\d+)", re.IGNORECASE),
    "ticket": re.compile(r"\bTicket:\s*#?(\d+)", re.IGNORECASE),
}
# ``Issues: #61, #62`` / ``Tickets: #41 #42`` -> the listed child ids.
_CHILDREN_RE = {
    "issues": re.compile(r"\bIssues?:\s*([#\d,\s]+)", re.IGNORECASE),
    "tickets": re.compile(r"\bTickets?:\s*([#\d,\s]+)", re.IGNORECASE),
}
_ID_RE = re.compile(r"#?(\d+)")

# A post body section header for children, e.g. ``## Tickets`` then id lines.
_SECTION_RE = {
    "tickets": re.compile(r"^#+\s*Tickets?\s*$", re.IGNORECASE),
    "issues": re.compile(r"^#+\s*Issues?\s*$", re.IGNORECASE),
}


def parent_id(body: Optional[str], parent_tier: str) -> Optional[str]:
    """Return the id of the named parent (``epic``/``ticket``) linked in ``body``."""
    if not body:
        return None
    pattern = _PARENT_RE.get(parent_tier)
    if pattern is None:
        return None
    match = pattern.search(body)
    return match.group(1) if match else None


def child_ids(body: Optional[str], child_kind: str) -> list[str]:
    """Return the listed child ids for ``child_kind`` (``issues``/``tickets``).

    Reads both the inline ``Issues: #a, #b`` link form and a ``## Issues``
    section that lists ids on following lines, de-duplicated in order.
    """
    if not body:
        return []
    ids: list[str] = []

    pattern = _CHILDREN_RE.get(child_kind)
    if pattern is not None:
        for match in pattern.finditer(body):
            ids.extend(_ID_RE.findall(match.group(1)))

    section = _SECTION_RE.get(child_kind)
    if section is not None:
        ids.extend(_ids_under_section(body, section))

    seen: set[str] = set()
    ordered: list[str] = []
    for cid in ids:
        if cid not in seen:
            seen.add(cid)
            ordered.append(cid)
    return ordered


def _ids_under_section(body: str, header: re.Pattern[str]) -> list[str]:
    """Collect ``#nn`` ids on the lines following a matching section header."""
    ids: list[str] = []
    in_section = False
    for line in body.splitlines():
        if header.match(line.strip()):
            in_section = True
            continue
        if in_section:
            if line.strip().startswith("#") and re.match(r"^#+\s", line):
                break  # a new markdown header ends the section
            found = _ID_RE.findall(line)
            if found:
                ids.extend(found)
    return ids
