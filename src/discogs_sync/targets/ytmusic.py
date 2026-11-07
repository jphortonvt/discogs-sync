from __future__ import annotations

import json
import socket
import time as _time
from dataclasses import dataclass

import urllib3.util.connection as _uc
from ytmusicapi import OAuthCredentials, YTMusic

from discogs_sync.targets.base import AlbumCandidate, LibraryTarget, VideoCandidate


def _prefer_ipv4() -> None:
    """Workaround: requests/urllib3 stalls 60s on IPv6 to www.youtube.com on some macOS setups.
    Curl handles this via Happy Eyeballs; requests doesn't. Force IPv4 for this process."""
    _uc.allowed_gai_family = lambda: socket.AF_INET


def _install_ytmusic_retry() -> None:
    """YTM occasionally returns empty bodies under load. ytmusicapi blows up on json.loads.
    Wrap _send_request to retry with backoff."""
    if getattr(YTMusic, "_dsync_patched", False):
        return
    original = YTMusic._send_request

    def _patched(self, endpoint, body, additionalParams=""):
        last_exc = None
        for attempt in range(5):
            try:
                return original(self, endpoint, body, additionalParams)
            except json.JSONDecodeError as exc:
                last_exc = exc
                _time.sleep(min(2 ** attempt, 16))
        raise last_exc

    YTMusic._send_request = _patched
    YTMusic._dsync_patched = True


_prefer_ipv4()
_install_ytmusic_retry()


@dataclass
class YTMusicAuth:
    """Either OAuth (token + client_id/secret) or browser-headers (header_json)."""
    client_id: str | None = None
    client_secret: str | None = None
    token: dict | None = None
    header_json: str | None = None

    @property
    def is_browser(self) -> bool:
        return self.header_json is not None


_TOKEN_FIELDS = {"scope", "token_type", "access_token", "refresh_token", "expires_at", "expires_in"}


def begin_oauth(client_id: str, client_secret: str) -> dict:
    """Run the device flow interactively. Returns a token dict safe to persist.

    Bypasses ytmusicapi's setup_oauth because newer Google OAuth responses
    include 'refresh_token_expires_in' which ytmusicapi 1.12.0's RefreshingToken
    constructor rejects."""
    import time

    from ytmusicapi.auth.oauth.credentials import OAuthCredentials as _OAuth

    creds = _OAuth(client_id, client_secret)
    code = creds.get_code()
    url = f"{code['verification_url']}?user_code={code['user_code']}"
    print(f"\nVisit: {url}")
    print(f"Enter code: {code['user_code']}")
    input("Press Enter once you've approved (Ctrl-C to abort)…")

    raw = creds.token_from_code(code["device_code"])
    token = {k: v for k, v in raw.items() if k in _TOKEN_FIELDS}
    if "expires_at" not in token and "expires_in" in token:
        token["expires_at"] = int(time.time()) + int(token["expires_in"])
    return token


class YTMusicAdapter(LibraryTarget):
    name = "ytmusic"

    def __init__(self, auth: YTMusicAuth):
        self._auth = auth
        if auth.is_browser:
            self._client = YTMusic(auth=auth.header_json)
        else:
            self._client = YTMusic(
                auth=auth.token,
                oauth_credentials=OAuthCredentials(
                    client_id=auth.client_id, client_secret=auth.client_secret
                ),
            )

    def list_library_albums(self) -> list[AlbumCandidate]:
        raw = self._client.get_library_albums(limit=5000)
        return [_album_from_library(a) for a in raw]

    def search_album(self, artist: str, title: str, *, limit: int = 5) -> list[AlbumCandidate]:
        query = f"{artist} {title}".strip()
        import os
        if os.environ.get("DISCOGS_SYNC_DEBUG"):
            print(f"[search_album] query={query!r} limit={limit}", flush=True)
        try:
            results = self._client.search(query, filter="albums", limit=limit) or []
        except Exception as exc:
            if os.environ.get("DISCOGS_SYNC_DEBUG"):
                print(f"[search_album] FAILED for query={query!r}: {exc}", flush=True)
            raise
        return [_album_from_search(a) for a in results[:limit]]

    def search_album_video(
        self, artist: str, title: str, *, limit: int = 5
    ) -> list[VideoCandidate]:
        query = f"{artist} {title} full album".strip()
        results = self._client.search(query, filter="videos", limit=limit) or []
        return [_video_from_search(v) for v in results[:limit]]

    def get_album_audio_playlist_id(self, browse_id: str) -> str:
        details = self._client.get_album(browse_id)
        return details["audioPlaylistId"]

    def add_album_to_library(self, candidate: AlbumCandidate) -> None:
        playlist_id = self.get_album_audio_playlist_id(candidate.target_id)
        self._client.rate_playlist(playlist_id, "LIKE")

    def ensure_playlist(self, name: str) -> str:
        playlists = self._client.get_library_playlists(limit=200) or []
        for p in playlists:
            if p.get("title") == name:
                return p["playlistId"]
        return self._client.create_playlist(
            title=name,
            description=(
                "Created by discogs-sync. Holds full-album YouTube videos "
                "for releases not on YouTube Music."
            ),
            privacy_status="PRIVATE",
        )

    def add_video_to_playlist(self, playlist_id: str, video: VideoCandidate) -> None:
        self._client.add_playlist_items(playlist_id, [video.target_id])


def _join_artists(artists: list[dict] | None) -> str:
    if not artists:
        return ""
    return ", ".join(a.get("name", "") for a in artists if a.get("name"))


def _best_thumb(thumbs: list[dict] | None) -> str | None:
    if not thumbs:
        return None
    return thumbs[-1].get("url")


def _album_from_search(a: dict) -> AlbumCandidate:
    year = a.get("year")
    try:
        year_int = int(year) if year else None
    except (TypeError, ValueError):
        year_int = None
    return AlbumCandidate(
        target="ytmusic",
        target_id=a["browseId"],
        title=(a.get("title") or "").strip(),
        artist=_join_artists(a.get("artists")),
        year=year_int,
        cover_url=_best_thumb(a.get("thumbnails")),
        raw=a,
    )


def _album_from_library(a: dict) -> AlbumCandidate:
    year = a.get("year")
    try:
        year_int = int(year) if year else None
    except (TypeError, ValueError):
        year_int = None
    return AlbumCandidate(
        target="ytmusic",
        target_id=a["browseId"],
        title=(a.get("title") or "").strip(),
        artist=_join_artists(a.get("artists")),
        year=year_int,
        cover_url=_best_thumb(a.get("thumbnails")),
        raw=a,
    )


def _video_from_search(v: dict) -> VideoCandidate:
    artists = v.get("artists") or []
    channel = artists[0].get("name", "") if artists else ""
    is_topic = channel.endswith(" - Topic")
    duration_seconds = v.get("duration_seconds")
    if duration_seconds is None:
        duration_seconds = _parse_duration(v.get("duration"))

    return VideoCandidate(
        target="ytmusic",
        target_id=v["videoId"],
        title=(v.get("title") or "").strip(),
        channel=channel,
        channel_kind="topic" if is_topic else None,
        duration_seconds=duration_seconds,
        is_topic_channel=is_topic,
        cover_url=_best_thumb(v.get("thumbnails")),
        raw=v,
    )


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


def serialize_token(token: dict) -> str:
    return json.dumps(token)


def deserialize_token(s: str) -> dict:
    return json.loads(s)


def setup_browser_auth(raw_headers: str) -> str:
    """Convert raw HTTP headers OR a "Copy as cURL" blob into the JSON auth blob
    that ytmusicapi expects."""
    from ytmusicapi.setup import setup
    text = _normalize_input(raw_headers)
    return setup(filepath=None, headers_raw=text)


def _normalize_input(text: str) -> str:
    """If the input looks like a curl command, extract -H / -b flags into raw header lines."""
    import re
    stripped = text.lstrip()
    if not stripped.startswith("curl "):
        return text

    lines: list[str] = []
    # -H 'header: value'  (matches either single or double quotes around the arg)
    for m in re.finditer(r"-H\s+(['\"])(.*?)\1", text, flags=re.DOTALL):
        lines.append(m.group(2))
    # -b 'cookie1=v1; cookie2=v2'  → cookie header
    cookie = re.search(r"-b\s+(['\"])(.*?)\1", text, flags=re.DOTALL)
    if cookie:
        lines.append(f"cookie: {cookie.group(2)}")
    return "\n".join(lines)
