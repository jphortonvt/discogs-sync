from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from discogs_sync.models import Release
from discogs_sync.normalize import normalize_artist, split_title
from discogs_sync.targets.base import AlbumCandidate, VideoCandidate

Bucket = Literal["strong", "review", "no_album_match"]

WEIGHT_ARTIST = 0.35
WEIGHT_TITLE = 0.35
WEIGHT_YEAR = 0.10
WEIGHT_TRACK_COUNT = 0.10
WEIGHT_FIRST_TRACK = 0.10


class ScoredCandidate(BaseModel):
    candidate: AlbumCandidate
    score: float
    breakdown: dict[str, float] = Field(default_factory=dict)


class MatchResult(BaseModel):
    release: Release
    candidates: list[ScoredCandidate]
    best: ScoredCandidate | None
    bucket: Bucket
    video_candidate: VideoCandidate | None = None
    video_score: float | None = None
    already_in_library: bool = False


def score_album(release: Release, candidate: AlbumCandidate) -> ScoredCandidate:
    rel_artist = normalize_artist(release.artist)
    cand_artist = normalize_artist(candidate.artist)
    rel_title, _ = split_title(release.title)
    cand_title, _ = split_title(candidate.title)

    artist_score = fuzz.token_set_ratio(rel_artist, cand_artist) / 100.0
    title_score = fuzz.token_set_ratio(rel_title, cand_title) / 100.0

    if release.year and candidate.year:
        diff = abs(release.year - candidate.year)
        year_score = 1.0 if diff <= 1 else max(0.0, 1.0 - 0.1 * diff)
    else:
        year_score = 0.5

    if release.track_count and candidate.track_count:
        diff = abs(release.track_count - candidate.track_count)
        track_count_score = 1.0 if diff == 0 else max(0.0, 1.0 - 0.2 * diff)
    else:
        track_count_score = 0.5

    if release.tracks and candidate.raw.get("first_track"):
        first_score = (
            fuzz.token_set_ratio(
                release.tracks[0].title.lower(),
                candidate.raw["first_track"].lower(),
            )
            / 100.0
        )
    else:
        first_score = 0.5

    total = (
        artist_score * WEIGHT_ARTIST
        + title_score * WEIGHT_TITLE
        + year_score * WEIGHT_YEAR
        + track_count_score * WEIGHT_TRACK_COUNT
        + first_score * WEIGHT_FIRST_TRACK
    )

    return ScoredCandidate(
        candidate=candidate,
        score=round(total, 4),
        breakdown={
            "artist": round(artist_score, 3),
            "title": round(title_score, 3),
            "year": round(year_score, 3),
            "track_count": round(track_count_score, 3),
            "first_track": round(first_score, 3),
        },
    )


def bucketize(scored: list[ScoredCandidate], strong_threshold: float, review_threshold: float) -> Bucket:
    if not scored:
        return "no_album_match"
    best = scored[0].score
    runner_up = scored[1].score if len(scored) > 1 else 0.0
    if best >= strong_threshold and (best - runner_up) >= 0.05:
        return "strong"
    if best >= review_threshold:
        return "review"
    if best >= strong_threshold and (best - runner_up) < 0.05:
        return "review"
    return "no_album_match"


def score_video(release: Release, video: VideoCandidate) -> float:
    rel_artist = normalize_artist(release.artist)
    rel_title, _ = split_title(release.title)
    title_norm = normalize_artist(video.title)

    artist_in = fuzz.partial_ratio(rel_artist, title_norm) / 100.0
    title_in = fuzz.partial_ratio(rel_title, title_norm) / 100.0
    text_score = 0.5 * artist_in + 0.5 * title_in

    expected = sum(t.duration_seconds or 0 for t in release.tracks) if release.tracks else 0
    duration_score = 0.5
    if video.duration_seconds:
        if expected and video.duration_seconds >= expected * 0.7:
            duration_score = 1.0
        elif video.duration_seconds >= 600:
            duration_score = 0.85
        elif video.duration_seconds >= 300:
            duration_score = 0.6
        else:
            duration_score = 0.2

    channel_bonus = 0.0
    if video.is_topic_channel or video.is_official_artist:
        channel_bonus = 0.1

    return round(min(1.0, 0.55 * text_score + 0.35 * duration_score + channel_bonus), 4)


def match_release(
    release: Release,
    album_candidates: list[AlbumCandidate],
    video_candidates: list[VideoCandidate] | None,
    *,
    strong_threshold: float = 0.90,
    review_threshold: float = 0.70,
    video_threshold: float = 0.60,
) -> MatchResult:
    scored = sorted(
        (score_album(release, c) for c in album_candidates),
        key=lambda s: s.score,
        reverse=True,
    )
    bucket = bucketize(scored, strong_threshold, review_threshold)
    best = scored[0] if scored else None

    video_pick: VideoCandidate | None = None
    video_score: float | None = None
    if bucket == "no_album_match" and video_candidates:
        scored_videos = sorted(
            ((v, score_video(release, v)) for v in video_candidates),
            key=lambda x: x[1],
            reverse=True,
        )
        if scored_videos and scored_videos[0][1] >= video_threshold:
            video_pick, video_score = scored_videos[0]

    return MatchResult(
        release=release,
        candidates=scored,
        best=best,
        bucket=bucket,
        video_candidate=video_pick,
        video_score=video_score,
    )
