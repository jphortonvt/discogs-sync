from __future__ import annotations

from discogs_sync.models import Release, Track


def test_release_minimum_fields():
    r = Release(source="discogs", source_id="1", artist="Slint", title="Spiderland")
    assert r.year is None
    assert r.tracks == []
    assert r.track_count is None


def test_release_track_count():
    r = Release(
        source="discogs",
        source_id="1",
        artist="Wire",
        title="Pink Flag",
        tracks=[Track(title="Reuters"), Track(title="Field Day")],
    )
    assert r.track_count == 2


def test_release_round_trip_via_dict():
    r = Release(
        source="discogs",
        source_id="42",
        artist="Brian Eno",
        title="Music for Airports",
        edition_tags=["Album", "Reissue"],
    )
    payload = r.model_dump()
    restored = Release.model_validate(payload)
    assert restored == r
