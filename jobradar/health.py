"""Board health: alert once per problem, then stay quiet until something changes.

Each run produces one event per fetched company (ok / failing / anomaly). `decide()` compares it with the stored
`health_state` row and says whether it belongs in this run's single health message. Pure logic, no I/O.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

import httpx

from .http import NotFound
from .notify import _e

ANOMALY_FP = "anomaly:empty_board"
REASON_MAX = 80
_URL = re.compile(r"https?://\S+")
_MORE = re.compile(r"\s*For more information check:.*$", re.S)


def shorten(text: str, limit: int = REASON_MAX) -> str:
    """Collapse whitespace, drop URLs, cut at a word boundary (never mid-word) to at most `limit` chars."""
    text = _MORE.sub("", text or "")
    text = _URL.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" :,;-–")
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip(" :,;-–") + "…"


def http_status(exc: BaseException) -> int | None:
    if isinstance(exc, NotFound):
        return exc.status
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code
    return None


def error_class(exc: BaseException) -> str:
    # All timeout flavours (read/connect/pool/asyncio) are the same problem for alerting purposes.
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return "Timeout"
    return type(exc).__name__


def reason(exc: BaseException) -> str:
    st = http_status(exc)
    if isinstance(exc, NotFound):
        return f"{st} – board not found"
    if st is not None:
        known = {401: "unauthorized", 403: "blocked by site", 429: "rate limited", 400: "bad request"}
        if st in known:
            return f"{st} – {known[st]}"
        if st >= 500:
            return f"{st} – server error"
        return f"{st} – HTTP error"
    if isinstance(exc, json.JSONDecodeError):
        return "invalid JSON (likely HTML/anti-bot page)"
    if error_class(exc) == "Timeout":
        return "timeout"
    if isinstance(exc, httpx.ConnectError):
        return "connection failed"
    msg = str(exc)
    return shorten(f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__)


def fingerprint(company_key: str, ats: str | None, exc: BaseException) -> str:
    """Stable identity of a problem: no URLs, timestamps or IDs, so the same breakage always maps to the same string."""
    st = http_status(exc)
    return f"{company_key}:{ats or '-'}:{error_class(exc)}" + (f":{st}" if st is not None else "")


@dataclass
class State:
    company_key: str
    status: str = "ok"
    fingerprint: str | None = None
    reason: str | None = None
    first_failed_at: datetime | None = None
    last_failed_at: datetime | None = None
    consecutive_failures: int = 0
    notified_at: datetime | None = None
    muted: bool = False


@dataclass
class Event:
    company_key: str
    label: str                    # "Name (ats)"
    ok: bool
    fingerprint: str | None = None
    reason: str | None = None


@dataclass
class Decision:
    state: State                  # row to persist (notified_at is set by the caller only if the send succeeded)
    kind: str | None              # None | "new" | "changed" | "reminder" | "recovered"


def decide(prev: State | None, ev: Event, now: datetime, reminder_days: int = 0) -> Decision:
    prev = prev or State(ev.company_key)
    if ev.ok:
        if prev.status != "failing":
            return Decision(prev, None)
        reset = State(ev.company_key, muted=prev.muted)
        # Only announce a recovery for a problem that was announced in the first place.
        return Decision(reset, "recovered" if prev.notified_at and not prev.muted else None)

    if prev.status == "failing" and prev.fingerprint == ev.fingerprint:
        st = replace(prev, reason=ev.reason, last_failed_at=now, consecutive_failures=prev.consecutive_failures + 1)
        if prev.muted:
            return Decision(st, None)
        if prev.notified_at is None:            # never got through (Telegram down, or just unmuted)
            return Decision(st, "new")
        if reminder_days > 0 and now - prev.notified_at >= timedelta(days=reminder_days):
            return Decision(st, "reminder")
        return Decision(st, None)

    kind = "changed" if prev.status == "failing" else "new"
    st = State(ev.company_key, "failing", ev.fingerprint, ev.reason, first_failed_at=now, last_failed_at=now,
               consecutive_failures=1, notified_at=None, muted=prev.muted)
    return Decision(st, None if prev.muted else kind)


def build_message(items: list[tuple[Event, Decision]], limit: int = 30) -> list[str]:
    """Blocks for one "Job Radar health" message; empty list = nothing new to say."""
    icon = {"new": "❌", "changed": "🔀", "reminder": "🔁"}
    probs = [(e, d) for e, d in items if d.kind in icon]
    probs.sort(key=lambda x: ("new", "changed", "reminder").index(x[1].kind))
    blocks = []
    for e, d in probs[:limit]:
        mark = "⚠️" if d.kind == "new" and d.state.fingerprint == ANOMALY_FP else icon[d.kind]
        line = f"{mark} {_e(e.label)}: {_e(e.reason)}"
        if d.kind == "changed":
            line += " (problem changed)"
        elif d.kind == "reminder":
            line += f" — still failing since {d.state.first_failed_at:%d %b}, {d.state.consecutive_failures} runs"
        blocks.append(line)
    if len(probs) > limit:
        blocks.append(f"…and {len(probs) - limit} more. Run <code>jobradar health</code>.")
    rec = [e.label.split(" (")[0] for e, d in items if d.kind == "recovered"]
    if rec:
        blocks.append("✅ Recovered: " + _e(", ".join(sorted(rec))))
    return blocks
