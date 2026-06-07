"""quota.py - detect provider usage-limit / rate-limit signals.

A provider quota ("You've hit your usage limit... try again at 8:27 PM") is a
GLOBAL condition, not a task-local failure. Treating it as retryable spammed 280
doomed spawns, 70 ``platform_error`` posts, and 70 dead resolver runs (the
resolver shares the same quota) in 15 minutes. So we sniff it out of the agent
output and let the scheduler open a circuit breaker instead: park, wait for the
reset, requeue - no error post, no resolver.

Detection is string-based and may drift if a provider changes wording. Accepted
for now; both ``claude`` and ``codex`` phrasings are covered.

Only Python stdlib is used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

# How long to park when no reset time can be parsed out of the message.
DEFAULT_PARK_S = 15 * 60
# Never park less than this even if a parsed reset is in the past / very near.
MIN_PARK_S = 60

_SIGNAL_RES = (
    re.compile(r"usage limit", re.IGNORECASE),
    re.compile(r"rate limit(?:ed)?", re.IGNORECASE),
    re.compile(r"\bquota\b", re.IGNORECASE),
    re.compile(r"too many requests", re.IGNORECASE),
    re.compile(r"resource[_ ]exhausted", re.IGNORECASE),
    re.compile(r"(?<!\d)429(?!\d)"),
)

# "try again in 5 minutes" / "retry after 30s"
_IN_RE = re.compile(
    r"(?:try again|retry|again)\s+(?:in|after)\s+(\d+)\s*"
    r"(seconds?|minutes?|hours?|sec|min|hr|s|m|h)\b",
    re.IGNORECASE,
)
# "try again at 8:27 PM" (clock time, optional am/pm)
_AT_RE = re.compile(
    r"(?:try again|reset[s]?|available)\s+at\s+(\d{1,2}):(\d{2})\s*([ap]\.?m\.?)?",
    re.IGNORECASE,
)

# Unit -> seconds, keyed by first letter (s/m/h) - covers sec/secs/second(s),
# min/minute(s)/m, hr/hour(s)/h unambiguously.
_UNIT_FIRST_S = {"s": 1, "m": 60, "h": 3600}


@dataclass
class QuotaInfo:
    """A detected quota signal and how long to park for it."""

    matched: str
    park_seconds: int = DEFAULT_PARK_S
    reset_at: Optional[datetime] = None


def quota_signal(text: Optional[str], *, now: Optional[datetime] = None) -> Optional[QuotaInfo]:
    """Return a :class:`QuotaInfo` if ``text`` looks like a provider quota error.

    ``None`` means it is an ordinary failure (handle as today). ``now`` is
    injectable for tests.
    """
    if not text:
        return None
    hit = None
    for rx in _SIGNAL_RES:
        m = rx.search(text)
        if m:
            hit = m.group(0)
            break
    if hit is None:
        return None
    now = now or datetime.now(timezone.utc)
    reset_at, park = _parse_reset(text, now)
    return QuotaInfo(matched=hit, park_seconds=park, reset_at=reset_at)


def _parse_reset(text: str, now: datetime) -> tuple[Optional[datetime], int]:
    m = _IN_RE.search(text)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        secs = n * _UNIT_FIRST_S.get(unit[0], 60)
        secs = max(MIN_PARK_S, secs)
        return now + timedelta(seconds=secs), secs
    m = _AT_RE.search(text)
    if m:
        reset_at = _clock_to_dt(int(m.group(1)), int(m.group(2)), m.group(3), now)
        if reset_at is not None:
            secs = max(MIN_PARK_S, int((reset_at - now).total_seconds()))
            return reset_at, secs
    return None, DEFAULT_PARK_S


def _clock_to_dt(hour: int, minute: int, ampm: Optional[str], now: datetime) -> Optional[datetime]:
    """Resolve a wall-clock "H:MM [am/pm]" to the next future datetime (UTC-naive math).

    The message carries no timezone, so we interpret the clock in ``now``'s tz and
    roll to tomorrow if the time already passed today.
    """
    if ampm:
        ap = ampm.replace(".", "").lower()
        if ap == "pm" and hour < 12:
            hour += 12
        elif ap == "am" and hour == 12:
            hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate
