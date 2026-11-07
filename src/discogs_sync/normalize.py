from __future__ import annotations

import re
import unicodedata

EDITION_PATTERN = re.compile(r"[\(\[]([^)\]]+)[\)\]]")
SEPARATOR_NORMALIZE = [
    (re.compile(r"\b(featuring|feat\.|ft\.|with)(?=\s|$)", re.IGNORECASE), "&"),
    (re.compile(r"\band\b", re.IGNORECASE), "&"),
]


def strip_diacritics(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def collapse_whitespace(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def normalize_text(s: str) -> str:
    if not s:
        return ""
    s = strip_diacritics(s).lower()
    s = re.sub(r"[^\w\s&]", " ", s)
    return collapse_whitespace(s)


def normalize_artist(s: str) -> str:
    s = strip_diacritics(s)
    for pattern, repl in SEPARATOR_NORMALIZE:
        s = pattern.sub(repl, s)
    s = re.sub(r"^the\s+", "", s, flags=re.IGNORECASE)
    return normalize_text(s)


def split_title(title: str) -> tuple[str, list[str]]:
    """Return (base_title_normalized, edition_tags_lowercased)."""
    if not title:
        return "", []
    edition_tags: list[str] = []

    def _capture(m: re.Match) -> str:
        edition_tags.append(m.group(1).strip().lower())
        return " "

    base = EDITION_PATTERN.sub(_capture, title)
    return normalize_text(base), edition_tags
