from __future__ import annotations

from discogs_sync.targets.ytmusic import _normalize_input


def test_normalize_input_passthrough_when_not_curl():
    raw = "cookie: abc=1\nx-goog-authuser: 0"
    assert _normalize_input(raw) == raw


def test_normalize_input_extracts_curl_headers():
    curl = """curl 'https://music.youtube.com/youtubei/v1/browse' \\
  -H 'accept: */*' \\
  -H 'x-goog-authuser: 0' \\
  -b 'SID=abc; SAPISID=xyz'
"""
    out = _normalize_input(curl)
    lines = out.split("\n")
    assert "accept: */*" in lines
    assert "x-goog-authuser: 0" in lines
    assert "cookie: SID=abc; SAPISID=xyz" in lines


def test_normalize_input_handles_double_quotes():
    curl = 'curl "https://x" -H "x-goog-authuser: 0" -b "SID=abc"'
    out = _normalize_input(curl)
    assert "x-goog-authuser: 0" in out
    assert "cookie: SID=abc" in out
