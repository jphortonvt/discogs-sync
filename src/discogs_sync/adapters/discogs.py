from __future__ import annotations

import json as _json
import time
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass

import discogs_client
from discogs_client.client import Client as _DiscogsClient

from discogs_sync import __version__
from discogs_sync.adapters.base import CollectionSource
from discogs_sync.models import Release, Track

USER_AGENT = f"discogs-sync/{__version__} +https://github.com/local/discogs-sync"


_DISCOGS_HTTP_RATE = None  # set on first install


def _install_discogs_retry() -> None:
    """Apply HTTP-level rate limiting AND empty-response retry to python3-discogs-client.

    Without this:
    - Empty pagination bodies blow up json.loads with JSONDecodeError.
    - Per-item rate limiting (what we used to do) over-throttled by ~25× because
      each HTTP page fetch returns 25 items.
    """
    global _DISCOGS_HTTP_RATE
    if getattr(_DiscogsClient, "_dsync_patched", False):
        return
    _DISCOGS_HTTP_RATE = _RateLimiter(max_calls=55, per_seconds=60.0)
    original_request = _DiscogsClient._request

    def _patched_request(self, method, url, data=None):
        _DISCOGS_HTTP_RATE.acquire()
        last_exc = None
        for attempt in range(5):
            try:
                return original_request(self, method, url, data)
            except _json.JSONDecodeError as exc:
                last_exc = exc
                time.sleep(min(2 ** attempt, 16))
        raise last_exc

    _DiscogsClient._request = _patched_request
    _DiscogsClient._dsync_patched = True


class _RateLimiter:
    def __init__(self, max_calls: int, per_seconds: float):
        self.max_calls = max_calls
        self.per_seconds = per_seconds
        self.calls: deque[float] = deque()

    def acquire(self) -> None:
        now = time.monotonic()
        while self.calls and now - self.calls[0] > self.per_seconds:
            self.calls.popleft()
        if len(self.calls) >= self.max_calls:
            sleep_for = self.per_seconds - (now - self.calls[0])
            if sleep_for > 0:
                time.sleep(sleep_for)
        self.calls.append(time.monotonic())


@dataclass
class DiscogsAuth:
    consumer_key: str
    consumer_secret: str
    access_token: str
    access_token_secret: str


def build_client(auth: DiscogsAuth) -> discogs_client.Client:
    client = discogs_client.Client(USER_AGENT)
    client.set_consumer_key(auth.consumer_key, auth.consumer_secret)
    client.set_token(auth.access_token, auth.access_token_secret)
    return client


def begin_oauth(consumer_key: str, consumer_secret: str) -> tuple[discogs_client.Client, str]:
    client = discogs_client.Client(USER_AGENT)
    client.set_consumer_key(consumer_key, consumer_secret)
    _, _, url = client.get_authorize_url()
    return client, url


def complete_oauth(client: discogs_client.Client, verifier: str) -> tuple[str, str]:
    access_token, access_token_secret = client.get_access_token(verifier)
    return access_token, access_token_secret


_install_discogs_retry()


class DiscogsAdapter(CollectionSource):
    name = "discogs"

    def __init__(self, auth: DiscogsAuth, username: str):
        self.client = build_client(auth)
        self.username = username

    def whoami(self) -> str:
        return self.client.identity().username

    def list_folders(self) -> list[str]:
        return [f.name for f in self.client.user(self.username).collection_folders]

    def iter_releases(self, folder: str, *, full: bool = False) -> Iterator[Release]:
        folders = self.client.user(self.username).collection_folders
        target = next((f for f in folders if f.name == folder), None)
        if target is None:
            raise ValueError(f"Folder not found: {folder!r}")

        for item in target.releases:
            yield _normalize(item, folder, full=full)


def _normalize(item, folder: str, *, full: bool) -> Release:
    bi = item.data.get("basic_information") or item.data

    artists_raw = [a["name"] for a in bi.get("artists", [])]
    artist_display = _join_artists(bi.get("artists", []))

    formats = bi.get("formats", [])
    fmt = ", ".join(f["name"] for f in formats) if formats else None
    edition_tags = sorted({d for f in formats for d in f.get("descriptions", [])})

    labels = bi.get("labels", [])
    label = labels[0]["name"] if labels else None
    catno = labels[0].get("catno") if labels else None
    if catno in ("none", "", None):
        catno = None

    cover = bi.get("cover_image") or bi.get("thumb")

    tracks: list[Track] = []
    if full:
        try:
            full_release = item.release
            for t in full_release.tracklist or []:
                td = t.data if hasattr(t, "data") else t
                tracks.append(
                    Track(
                        position=td.get("position") or None,
                        title=td.get("title", ""),
                        duration_seconds=_parse_duration(td.get("duration")),
                    )
                )
        except Exception:
            tracks = []

    return Release(
        source="discogs",
        source_id=str(bi.get("id", item.id)),
        artist=artist_display,
        title=bi.get("title", "").strip(),
        year=bi.get("year") or None,
        label=label,
        catalog_number=catno,
        format=fmt,
        artists_raw=artists_raw,
        tracks=tracks,
        edition_tags=edition_tags,
        folder=folder,
        cover_url=cover,
    )


def _join_artists(artists: list[dict]) -> str:
    parts: list[str] = []
    for i, a in enumerate(artists):
        name = _strip_disambiguator(a.get("name", ""))
        joiner = a.get("join", "").strip()
        parts.append(name)
        if joiner and i < len(artists) - 1:
            parts.append(joiner)
    return " ".join(p for p in parts if p).strip()


def _strip_disambiguator(name: str) -> str:
    """Discogs appends "(2)" etc. to disambiguate artists with the same name."""
    if name.endswith(")") and "(" in name:
        head, _, tail = name.rpartition("(")
        if tail.rstrip(")").strip().isdigit():
            return head.strip()
    return name


def _parse_duration(s: str | None) -> int | None:
    if not s:
        return None
    parts = s.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    return None
