"""Keyword / regular-expression content filters (privacy-safe: never logs text)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class FilterMatch:
    rule_id: int
    rule_type: str

    reason_code = "content_filtered"

    @property
    def reason(self) -> str:
        return f"Skipped by {self.rule_type} filter rule #{self.rule_id}"


@dataclass(frozen=True)
class Rule:
    id: int
    rule_type: str
    pattern: str


def validate_rule(rule_type: str, pattern: str) -> None:
    if rule_type not in {"keyword", "regex"}:
        raise ValueError("Filter type must be keyword or regex")
    if not pattern:
        raise ValueError("Filter pattern cannot be empty")
    if rule_type == "regex":
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"Invalid regular expression: {exc}") from exc


class ContentFilter:
    def __init__(self, rules: Sequence[Rule], *, enabled: bool, case_sensitive: bool) -> None:
        self.enabled = enabled and bool(rules)
        self.case_sensitive = case_sensitive
        flags = 0 if case_sensitive else re.IGNORECASE
        self._keywords = [(rule.id, rule.pattern if case_sensitive else rule.pattern.casefold()) for rule in rules if rule.rule_type == "keyword"]
        self._regex = []
        for rule in rules:
            if rule.rule_type != "regex":
                continue
            try:
                self._regex.append((rule.id, re.compile(rule.pattern, flags)))
            except re.error:
                continue

    def match_text(self, text: str | None) -> FilterMatch | None:
        if not self.enabled or not text:
            return None
        haystack = text if self.case_sensitive else text.casefold()
        for rule_id, needle in self._keywords:
            if needle and needle in haystack:
                return FilterMatch(rule_id, "keyword")
        for rule_id, pattern in self._regex:
            if pattern.search(text):
                return FilterMatch(rule_id, "regex")
        return None

    def match_texts(self, texts: Iterable[str | None]) -> FilterMatch | None:
        for text in texts:
            match = self.match_text(text)
            if match:
                return match
        return None
