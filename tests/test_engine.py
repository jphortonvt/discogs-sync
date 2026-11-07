from __future__ import annotations

from collections.abc import Iterator

from discogs_sync.adapters.base import CollectionSource
from discogs_sync.engine import MatchOptions, SyncEngine
from discogs_sync.models import Release
from discogs_sync.state import State
from discogs_sync.targets.base import AlbumCandidate, LibraryTarget, VideoCandidate


class FakeSource(CollectionSource):
    name = "fake"

    def __init__(self, releases: list[Release]):
        self._releases = releases

    def list_folders(self) -> list[str]:
        return ["F"]

    def iter_releases(self, folder: str) -> Iterator[Release]:
        yield from self._releases


class FakeTarget(LibraryTarget):
    name = "fake_target"

    def __init__(self, album_map: dict[str, list[AlbumCandidate]] | None = None,
                 video_map: dict[str, list[VideoCandidate]] | None = None,
                 library: list[AlbumCandidate] | None = None):
        self._albums = album_map or {}
        self._videos = video_map or {}
        self._library = library or []
        self.added_albums: list[AlbumCandidate] = []
        self.added_videos: list[tuple[str, VideoCandidate]] = []
        self.ensured_playlist: str | None = None

    def search_album(self, artist, title, *, limit=5):
        return self._albums.get(f"{artist}|{title}", [])

    def search_album_video(self, artist, title, *, limit=5):
        return self._videos.get(f"{artist}|{title}", [])

    def list_library_albums(self):
        return self._library

    def add_album_to_library(self, candidate):
        self.added_albums.append(candidate)

    def ensure_playlist(self, name):
        self.ensured_playlist = name
        return f"PL_{name}"

    def add_video_to_playlist(self, playlist_id, video):
        self.added_videos.append((playlist_id, video))


def _rel(rid: str, artist: str, title: str) -> Release:
    return Release(source="discogs", source_id=rid, artist=artist, title=title, year=2000)


def test_compute_matches_buckets(tmp_path):
    releases = [
        _rel("1", "Wire", "Pink Flag"),
        _rel("2", "Mystery Artist", "Mystery Album"),
    ]
    albums = {
        "Wire|Pink Flag": [
            AlbumCandidate(
                target="t", target_id="A", artist="Wire", title="Pink Flag", year=2000
            )
        ],
    }
    source = FakeSource(releases)
    target = FakeTarget(album_map=albums, video_map={})
    state = State(tmp_path / "s.db")
    engine = SyncEngine(source, target, state, MatchOptions(fetch_videos=False))

    results = engine.compute_matches("F")
    assert len(results) == 2
    by_id = {r.release.source_id: r for r in results}
    assert by_id["1"].bucket == "strong"
    assert by_id["2"].bucket == "no_album_match"


def test_annotate_with_library_marks_already_present(tmp_path):
    rel = _rel("1", "Wire", "Pink Flag")
    cand = AlbumCandidate(target="t", target_id="A", artist="Wire", title="Pink Flag", year=2000)
    source = FakeSource([rel])
    target = FakeTarget(album_map={"Wire|Pink Flag": [cand]}, library=[cand])
    state = State(tmp_path / "s.db")
    engine = SyncEngine(source, target, state, MatchOptions(fetch_videos=False))

    results = engine.compute_matches("F")
    engine.annotate_with_library(results, target_name="fake_target")

    row = state.get_status("1", "fake_target")
    assert row is not None
    assert row["status"] == "already_present"


def test_apply_selections_album_and_video(tmp_path):
    rel = _rel("1", "Wire", "Pink Flag")
    rel2 = _rel("2", "Obscure", "Tape Only")
    cand = AlbumCandidate(target="t", target_id="A", artist="Wire", title="Pink Flag", year=2000)
    vid = VideoCandidate(
        target="t",
        target_id="V",
        title="Obscure - Tape Only (Full Album)",
        channel="Obscure - Topic",
        duration_seconds=2400,
        is_topic_channel=True,
    )

    source = FakeSource([rel, rel2])
    target = FakeTarget(
        album_map={"Wire|Pink Flag": [cand]},
        video_map={"Obscure|Tape Only": [vid]},
    )
    state = State(tmp_path / "s.db")
    engine = SyncEngine(source, target, state, MatchOptions(video_threshold=0.5))

    results = engine.compute_matches("F")
    by_id = {r.release.source_id: r for r in results}

    selections = {
        "1": {"kind": "album", "id": "A"},
        "2": {"kind": "video", "id": "V"},
    }
    run_id = state.start_run("discogs", "t")
    counts = engine.apply_selections(selections, run_id, "MyPlaylist", by_id)

    assert counts["added_album"] == 1
    assert counts["added_video"] == 1
    assert target.added_albums[0].target_id == "A"
    assert target.added_videos[0][0] == "PL_MyPlaylist"
    assert target.ensured_playlist == "MyPlaylist"

    assert state.is_already_added("1", "fake_target")
    assert state.is_already_added("2", "fake_target")


def test_apply_skips_already_added(tmp_path):
    rel = _rel("1", "Wire", "Pink Flag")
    cand = AlbumCandidate(target="t", target_id="A", artist="Wire", title="Pink Flag", year=2000)
    source = FakeSource([rel])
    target = FakeTarget(album_map={"Wire|Pink Flag": [cand]})
    state = State(tmp_path / "s.db")
    state.upsert_status(
        "1", "fake_target", status="added_album", target_kind="album", target_id="A"
    )
    engine = SyncEngine(source, target, state, MatchOptions(fetch_videos=False))

    results = engine.compute_matches("F")
    by_id = {r.release.source_id: r for r in results}
    counts = engine.apply_selections({"1": {"kind": "album", "id": "A"}}, 0, "P", by_id)

    assert counts["skipped"] == 1
    assert target.added_albums == []
