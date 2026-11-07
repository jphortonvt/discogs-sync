from __future__ import annotations

from types import SimpleNamespace

from discogs_sync.adapters.discogs import (
    _join_artists,
    _normalize,
    _parse_duration,
    _RateLimiter,
    _strip_disambiguator,
)


def make_item(data: dict, release_data: dict | None = None):
    item = SimpleNamespace(data={"basic_information": data}, id=data.get("id", 1))
    if release_data is not None:
        item.release = SimpleNamespace(tracklist=release_data.get("tracklist", []))
    return item


def test_strip_disambiguator():
    assert _strip_disambiguator("Wire") == "Wire"
    assert _strip_disambiguator("Wire (2)") == "Wire"
    assert _strip_disambiguator("Slint") == "Slint"
    assert _strip_disambiguator("The The (1)") == "The The"


def test_join_artists_simple():
    artists = [{"name": "Wire", "join": ""}]
    assert _join_artists(artists) == "Wire"


def test_join_artists_with_joiner():
    artists = [
        {"name": "Brian Eno", "join": "&"},
        {"name": "David Byrne", "join": ""},
    ]
    assert _join_artists(artists) == "Brian Eno & David Byrne"


def test_join_artists_strips_disambiguator():
    artists = [{"name": "Wire (2)", "join": ""}]
    assert _join_artists(artists) == "Wire"


def test_parse_duration():
    assert _parse_duration(None) is None
    assert _parse_duration("") is None
    assert _parse_duration("3:45") == 225
    assert _parse_duration("1:02:30") == 3750
    assert _parse_duration("not a duration") is None


def test_normalize_basic():
    rate = _RateLimiter(1000, 60.0)
    item = make_item({
        "id": 12345,
        "title": "Spiderland",
        "year": 1991,
        "artists": [{"name": "Slint", "join": ""}],
        "labels": [{"name": "Touch and Go", "catno": "TG83"}],
        "formats": [{"name": "Vinyl", "descriptions": ["LP", "Album"]}],
        "cover_image": "https://example.com/cover.jpg",
    })
    rel = _normalize(item, folder="Uncategorized", full=False)
    assert rel.source == "discogs"
    assert rel.source_id == "12345"
    assert rel.artist == "Slint"
    assert rel.title == "Spiderland"
    assert rel.year == 1991
    assert rel.label == "Touch and Go"
    assert rel.catalog_number == "TG83"
    assert rel.format == "Vinyl"
    assert rel.edition_tags == ["Album", "LP"]
    assert rel.folder == "Uncategorized"
    assert rel.cover_url == "https://example.com/cover.jpg"
    assert rel.tracks == []


def test_normalize_drops_none_catno():
    rate = _RateLimiter(1000, 60.0)
    item = make_item({
        "id": 1,
        "title": "x",
        "artists": [{"name": "y", "join": ""}],
        "labels": [{"name": "Self-Released", "catno": "none"}],
        "formats": [],
    })
    rel = _normalize(item, folder="f", full=False)
    assert rel.catalog_number is None


def test_normalize_full_pulls_tracks():
    rate = _RateLimiter(1000, 60.0)
    item = make_item(
        {
            "id": 99,
            "title": "Pink Flag",
            "artists": [{"name": "Wire", "join": ""}],
            "labels": [{"name": "Harvest", "catno": "SHSP 4076"}],
            "formats": [{"name": "Vinyl", "descriptions": ["LP"]}],
        },
        release_data={
            "tracklist": [
                SimpleNamespace(data={"position": "A1", "title": "Reuters", "duration": "3:09"}),
                SimpleNamespace(data={"position": "A2", "title": "Field Day for the Sundays", "duration": "0:28"}),
            ]
        },
    )
    rel = _normalize(item, folder="Punk", full=True)
    assert rel.track_count == 2
    assert rel.tracks[0].title == "Reuters"
    assert rel.tracks[0].duration_seconds == 189
    assert rel.tracks[1].duration_seconds == 28


def test_rate_limiter_does_not_block_under_limit():
    import time

    limiter = _RateLimiter(max_calls=10, per_seconds=60.0)
    start = time.monotonic()
    for _ in range(5):
        limiter.acquire()
    assert time.monotonic() - start < 0.1
