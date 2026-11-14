"""Telegram + healthchecks.io."""
from __future__ import annotations

import html
import logging

import httpx

log = logging.getLogger(__name__)
TG_LIMIT = 3900  # Telegram hard limit is 4096 chars


def _e(s) -> str:
    return html.escape(str(s or ""), quote=False)


def format_posting(m: dict) -> str:
    windows = ", ".join(m.get("windows") or []) or "—"
    tier = m.get("tier", "?")
    flag = "🔥 " if tier == "A" and m.get("level") == "strong" else ""
    llm = m.get("llm_json") or {}
    score = ""
    if m.get("llm_score") is not None:
        route = {"apply_cold": "apply", "referral_needed": "referral", "skip": "skip"}.get(llm.get("route"), "")
        raw = llm.get("score")
        score = f"⭐ {m['llm_score']}" + (f" (raw {raw})" if raw is not None and raw != m["llm_score"] else "") + (f" · {route}" if route else "") + (f" — {_e(llm['reason'])}" if llm.get("reason") else "") + "\n"
    return (f"{flag}<b>{_e(m['company'])}</b> [{_e(tier)}] — {_e(m['title'])}\n"
            f"📍 {_e(m.get('location'))} · {_e(windows)} · {_e(m.get('level'))}\n"
            f"{score}<a href=\"{_e(m['url'])}\">Open posting</a>")


def chunk_messages(header: str, blocks: list[str]) -> list[str]:
    msgs, cur = [], header
    for b in blocks:
        if len(cur) + len(b) + 2 > TG_LIMIT:
            msgs.append(cur)
            cur = ""
        cur += ("\n\n" if cur else "") + b
    if cur:
        msgs.append(cur)
    return msgs


class Telegram:
    def __init__(self, token: str | None, chat_id: str | None):
        self.token, self.chat_id = token, chat_id

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> bool:
        if not self.enabled:
            log.info("[telegram disabled] %s", text[:300])
            return True
        try:
            r = httpx.post(f"https://api.telegram.org/bot{self.token}/sendMessage", timeout=20, json={
                "chat_id": self.chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})
            if r.status_code != 200:
                log.error("telegram %s: %s", r.status_code, r.text[:300])
            return r.status_code == 200
        except httpx.HTTPError as e:
            log.error("telegram error: %s", e)
            return False

    def send_many(self, header: str, blocks: list[str]) -> bool:
        return all([self.send(m) for m in chunk_messages(header, blocks)])


def hc_ping(url: str | None, suffix: str = "", body: str = "") -> None:
    if not url:
        return
    try:
        httpx.post(url.rstrip("/") + suffix, content=body.encode()[:10000], timeout=10)
    except httpx.HTTPError as e:
        log.warning("healthchecks ping failed: %s", e)
