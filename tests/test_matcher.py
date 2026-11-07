from __future__ import annotations

from discogs_sync.matcher import bucketize, match_release, score_album, score_video
from discogs_sync.models import Release, Track
from discogs_sync.targets.base import AlbumCandidate, VideoCandidate


def _rel(artist="Slint", title="Spiderland", year=1991, tracks=None) -> Release:
    return Release(
        source="discogs",
        source_id="1",
        artist=artist,
        title=title,
        year=year,
        tracks=tracks or [],
    )


def _cand(artist="Slint", title="Spiderland", year=1991, tid="MPRE_x", **kw) -> AlbumCandidate:
    return AlbumCandidate(target="ytmusic", target_id=tid, artist=artist, title=title, year=year, **kw)


def test_score_album_exact_match():
    r = _rel()
    c = _cand()
    s = score_album(r, c)
    assert s.score >= 0.85
    assert s.breakdown["artist"] == 1.0
    assert s.breakdown["title"] == 1.0


def test_score_album_tolerant_of_remaster_suffix():
    r = _rel("Wire", "Pink Flag", 1977)
    c = _cand("Wire", "Pink Flag (Remastered 2018)", 2018, tid="MPRE_2")
    s = score_album(r, c)
    assert s.score >= 0.65


def test_score_album_year_off_by_one_full_credit():
    r = _rel(year=1991)
    c = _cand(year=1992)
    s = score_album(r, c)
    assert s.breakdown["year"] == 1.0


def test_score_album_year_far_apart_penalized():
    r = _rel(year=1991)
    c = _cand(year=2018)
    s = score_album(r, c)
    assert s.breakdown["year"] == 0.0


def test_bucketize_strong_when_clear_winner():
    scored = sorted(
        [score_album(_rel(), _cand()), score_album(_rel(), _cand(title="Unrelated Album", year=1970, tid="MPRE_2"))],
        key=lambda s: s.score,
        reverse=True,
    )
    b = bucketize(scored, 0.90, 0.70)
    assert b == "strong"


def test_bucketize_review_when_two_remasters_tie():
    """Two near-identical edition variants should fall into review, not strong."""
    a = score_album(_rel(), _cand())
    b = score_album(_rel(), _cand(title="Spiderland (Remastered)", tid="MPRE_2"))
    bucket = bucketize(sorted([a, b], key=lambda s: s.score, reverse=True), 0.90, 0.70)
    assert bucket == "review"


def test_bucketize_review_when_runner_up_close():
    a = score_album(_rel(), _cand())
    b = score_album(_rel(), _cand(tid="MPRE_2"))
    bucket = bucketize([a, b], 0.90, 0.70)
    assert bucket == "review"


def test_bucketize_no_match_when_all_low():
    a = score_album(_rel("Wire", "Pink Flag"), _cand("Beyonce", "Lemonade", year=2016, tid="X"))
    bucket = bucketize([a], 0.90, 0.70)
    assert bucket == "no_album_match"


def test_bucketize_empty():
    assert bucketize([], 0.90, 0.70) == "no_album_match"


def test_match_release_strong_with_video_skipped():
    r = _rel()
    albums = [_cand()]
    res = match_release(r, albums, video_candidates=None)
    assert res.bucket == "strong"
    assert res.video_candidate is None


def test_match_release_no_match_picks_plausible_video():
    r = _rel("Obscure Band", "Lost Album", year=1973, tracks=[Track(title="Song", duration_seconds=600)])
    video = VideoCandidate(
        target="ytmusic",
        target_id="vid1",
        title="Obscure Band — Lost Album (Full Album)",
        channel="Obscure Band - Topic",
        duration_seconds=2400,
        is_topic_channel=True,
    )
    res = match_release(r, [], [video], video_threshold=0.5)
    assert res.bucket == "no_album_match"
    assert res.video_candidate is not None
    assert res.video_candidate.target_id == "vid1"


def test_score_video_low_for_short_video():
    r = _rel()
    v = VideoCandidate(target="ytmusic", target_id="x", title="Slint Spiderland", channel="x", duration_seconds=120)
    assert score_video(r, v) < 0.7
