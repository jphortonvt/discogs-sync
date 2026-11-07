from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

import tomli_w

CONFIG_DIR = Path(
    os.environ.get("DISCOGS_SYNC_CONFIG_DIR", Path.home() / ".config" / "discogs-sync")
)
CONFIG_PATH = CONFIG_DIR / "config.toml"

DEFAULTS: dict[str, Any] = {
    "discogs": {
        "username": None,
        "default_folder": "Uncategorized",
        "consumer_key": None,
        "consumer_secret": None,
    },
    "match": {
        "strong_threshold": 0.90,
        "review_threshold": 0.70,
        "video_threshold": 0.60,
        "autoselect_threshold": 0.75,
    },
    "ytmusic": {
        "client_id": None,
        "client_secret": None,
        "video_fallback_playlist": "Discogs — Video Only",
    },
    "state": {
        "db_path": str(Path.home() / ".local" / "share" / "discogs-sync" / "state.db"),
    },
}


def load() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return _deep_copy(DEFAULTS)
    with CONFIG_PATH.open("rb") as f:
        data = tomllib.load(f)
    return _merge(_deep_copy(DEFAULTS), data)


def save(data: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with CONFIG_PATH.open("wb") as f:
        tomli_w.dump(_strip_nones(data), f)


def _strip_nones(d: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        if v is None:
            continue
        if isinstance(v, dict):
            cleaned = _strip_nones(v)
            out[k] = cleaned
        else:
            out[k] = v
    return out


def discogs_consumer_credentials() -> tuple[str, str] | None:
    """Resolve Discogs app credentials from env vars first, then config."""
    key = os.environ.get("DISCOGS_CONSUMER_KEY")
    secret = os.environ.get("DISCOGS_CONSUMER_SECRET")
    if key and secret:
        return key, secret
    cfg = load()["discogs"]
    if cfg["consumer_key"] and cfg["consumer_secret"]:
        return cfg["consumer_key"], cfg["consumer_secret"]
    return None


def ytmusic_oauth_credentials() -> tuple[str, str] | None:
    """Resolve YouTube Music OAuth client credentials from env vars first, then config."""
    cid = os.environ.get("YTMUSIC_CLIENT_ID")
    csec = os.environ.get("YTMUSIC_CLIENT_SECRET")
    if cid and csec:
        return cid, csec
    cfg = load()["ytmusic"]
    if cfg.get("client_id") and cfg.get("client_secret"):
        return cfg["client_id"], cfg["client_secret"]
    return None


def _deep_copy(d: dict[str, Any]) -> dict[str, Any]:
    return {k: (_deep_copy(v) if isinstance(v, dict) else v) for k, v in d.items()}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = _merge(base[k], v)
        else:
            base[k] = v
    return base
