from __future__ import annotations

from discogs_sync import config, secrets
from discogs_sync.adapters import discogs as ds
from discogs_sync.targets import ytmusic as ytm


def load_discogs_auth() -> ds.DiscogsAuth | None:
    creds = config.discogs_consumer_credentials()
    if not creds:
        return None
    ck, cs = creds
    at = secrets.get_token("discogs", "access_token")
    ats = secrets.get_token("discogs", "access_token_secret")
    if not at or not ats:
        return None
    return ds.DiscogsAuth(ck, cs, at, ats)


def load_ytmusic_auth() -> ytm.YTMusicAuth | None:
    browser = secrets.get_token("ytmusic", "browser_headers")
    if browser:
        return ytm.YTMusicAuth(header_json=browser)
    creds = config.ytmusic_oauth_credentials()
    if not creds:
        return None
    cid, csec = creds
    raw = secrets.get_token("ytmusic", "token")
    if not raw:
        return None
    return ytm.YTMusicAuth(client_id=cid, client_secret=csec, token=ytm.deserialize_token(raw))
