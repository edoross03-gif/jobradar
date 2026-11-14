"""Stage-1 deterministic filter: seniority × function × location, plus recruiting-window tags.

Cheap, explainable, and aggressive on recall for early-career roles. Stage 2 (LLM scoring) refines.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .models import Posting


def _rx(words: list[str]) -> re.Pattern | None:
    words = [w for w in words if w]
    if not words:
        return None
    # word-ish boundaries that also work for accented chars and multi-word phrases
    return re.compile(r"(?<![\w])(?:" + "|".join(w if "\\" in w else re.escape(w) for w in words) + r")(?![\w])", re.I)


_NON_EU = re.compile(r"\b(US|USA|United States|Canada|India|Singapore|Brazil|Mexico|Japan|Australia|Hong Kong|"
                     r"New York|San Francisco|Seattle|Austin|Chicago|Toronto|Bangalore|Bengaluru|Dubai|Tokyo|Sydney)\b")


@dataclass
class FilterResult:
    passed: bool
    level: str = ""                  # strong | weak
    location: str = ""               # matched label (city / country / Remote-EU)
    windows: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


class RulesFilter:
    def __init__(self, cfg: dict):
        loc = cfg["locations"]
        self.cities = {label: _rx(p) for label, p in loc["cities"].items()}
        self.country_names, self.country_codes = {}, {}
        for label, pats in loc["countries"].items():
            codes = [p for p in pats if p.isupper() and len(p) <= 3]
            names = [p for p in pats if p not in codes]
            self.country_names[label] = _rx(names)
            for c in codes:
                self.country_codes[c] = label
        self.remote_words = _rx(loc["remote_words"])
        self.remote_scopes = _rx(loc["remote_scopes"])
        self.remote_blockers = _rx(loc["remote_blockers"])

        sen = cfg["seniority"]
        self.strong, self.weak = _rx(sen["strong"]), _rx(sen["weak"])
        self.hard_ex, self.soft_ex = _rx(sen["hard_exclude"]), _rx(sen["soft_exclude"])
        self.max_yoe = int(sen.get("max_yoe", 3))
        self.func_ex = _rx(cfg["functions"]["exclude"])
        self.windows = {k: _rx(v) for k, v in cfg["windows"].items()}
        self.search_terms = cfg.get("search_terms", [])

    @classmethod
    def from_file(cls, path: str | Path) -> "RulesFilter":
        return cls(yaml.safe_load(Path(path).read_text()))

    # ---------- pieces ----------
    def match_location(self, p: Posting) -> str | None:
        hay = p.haystack_location()
        for label, rx in self.cities.items():
            if rx and rx.search(hay):
                return label
        is_remote = p.remote or bool(self.remote_words and self.remote_words.search(hay))
        if is_remote and not (self.remote_blockers and self.remote_blockers.search(hay)):
            if self.remote_scopes and self.remote_scopes.search(hay):
                return "Remote-EU"
            if not p.country and not _NON_EU.search(hay):
                return "Remote (unscoped)"
        return self._country(p, hay)

    def _country(self, p: Posting, hay: str) -> str | None:
        if p.country and p.country.strip().upper() in self.country_codes:
            return self.country_codes[p.country.strip().upper()]
        tokens = {t.strip() for t in re.split(r"[,;|/()\-]", hay)}
        for t in tokens:
            if t in self.country_codes:
                return self.country_codes[t]
        for label, rx in self.country_names.items():
            if rx and rx.search(hay):
                return label
        return None

    def _min_yoe(self, text: str) -> int | None:
        best = None
        for m in re.finditer(r"(\d{1,2})\s*\+?\s*(?:-|to|–)?\s*\d{0,2}\s*\+?\s*years?[^.]{0,40}?experience", text, re.I):
            n = int(m.group(1))
            if n <= 20:
                best = n if best is None else min(best, n)
        return best

    def tag_windows(self, p: Posting, level: str) -> list[str]:
        text = f"{p.title} {p.employment_type} {p.description[:3000]}"
        tags = [k for k, rx in self.windows.items() if rx and rx.search(text)]
        if not tags and level == "strong" and re.search(
                r"\b(?:intern(?:s|ship|ships)?\b|stage\b|estág|estag|tirocin|praktik|placement)", p.title, re.I):
            tags = ["intern_unspecified"]
        return tags

    # ---------- main ----------
    def evaluate(self, p: Posting) -> FilterResult:
        title = f"{p.title} {p.employment_type}".strip()
        r = FilterResult(passed=False)
        if self.hard_ex and (m := self.hard_ex.search(title)):
            r.reasons.append(f"hard_exclude:{m.group(0)}")
            return r
        if self.func_ex and (m := self.func_ex.search(p.title)):
            r.reasons.append(f"function_exclude:{m.group(0)}")
            return r
        if self.strong and self.strong.search(title):
            r.level = "strong"
        elif self.weak and self.weak.search(title):
            if self.soft_ex and (m := self.soft_ex.search(title)):
                r.reasons.append(f"soft_exclude:{m.group(0)}")
                return r
            yoe = self._min_yoe(p.description)
            if yoe is not None and yoe >= self.max_yoe:
                r.reasons.append(f"yoe:{yoe}")
                return r
            r.level = "weak"
        else:
            r.reasons.append("not_early_career")
            return r
        loc = self.match_location(p)
        if not loc:
            r.reasons.append("location")
            return r
        r.location = loc
        r.windows = self.tag_windows(p, r.level)
        r.passed = True
        return r
