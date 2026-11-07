from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from discogs_sync.adapters.base import CollectionSource
from discogs_sync.matcher import MatchResult, match_release
from discogs_sync.models import Release
from discogs_sync.normalize import normalize_artist, split_title
from discogs_sync.state import State
from discogs_sync.targets.base import LibraryTarget

ProgressFn = Callable[[str, int, int], None]


@dataclass
class MatchOptions:
    strong_threshold: float = 0.90
    review_threshold: float = 0.70
    video_threshold: float = 0.60
    fetch_videos: bool = True
    candidate_limit: int = 5


class SyncEngine:
    def __init__(
        self,
        source: CollectionSource,
        target: LibraryTarget,
        state: State,
        options: MatchOptions,
    ):
        self.source = source
        self.target = target
        self.state = state
        self.options = options

    def compute_matches(
        self,
        folder: str,
        *,
        full: bool = False,
        progress: ProgressFn | None = None,
        limit: int | None = None,
        skip_processed: bool = False,
    ) -> list[MatchResult]:
        already_done: set[str] = set()
        if skip_processed:
            already_done = self.state.get_processed_release_ids(self.target.name)

        releases: list[Release] = []
        seen_keys: set[tuple[str, str]] = set()
        skipped_dups = 0
        skipped_processed = 0
        for r in self._iter_releases(folder, full=full):
            if r.source_id in already_done:
                skipped_processed += 1
                continue
            key = (normalize_artist(r.artist), split_title(r.title)[0])
            if key in seen_keys:
                skipped_dups += 1
                continue
            seen_keys.add(key)
            releases.append(r)
            if limit and len(releases) >= limit:
                break
            if progress and len(releases) % 25 == 0:
                progress(f"…fetched {len(releases)} from Discogs so far", len(releases), 0)
        total = len(releases)
        if progress:
            extras = []
            if skipped_dups:
                extras.append(f"{skipped_dups} duplicate pressings")
            if skipped_processed:
                extras.append(f"{skipped_processed} already processed")
            suffix = f" ({', '.join(extras)} skipped)" if extras else ""
            progress(
                f"Discogs fetch complete: {total} releases{suffix}. Searching YTM next.",
                total,
                total,
            )
        results: list[MatchResult] = []
        for i, release in enumerate(releases, start=1):
            albums = self.target.search_album(
                release.artist, release.title, limit=self.options.candidate_limit
            )
            initial = match_release(
                release,
                albums,
                video_candidates=None,
                strong_threshold=self.options.strong_threshold,
                review_threshold=self.options.review_threshold,
                video_threshold=self.options.video_threshold,
            )
            videos = []
            if initial.bucket == "no_album_match" and self.options.fetch_videos:
                videos = self.target.search_album_video(
                    release.artist, release.title, limit=self.options.candidate_limit
                )
            final = match_release(
                release,
                albums,
                videos,
                strong_threshold=self.options.strong_threshold,
                review_threshold=self.options.review_threshold,
                video_threshold=self.options.video_threshold,
            )
            results.append(final)
            if progress:
                progress(release.title, i, total)
        return results

    def annotate_with_library(
        self,
        results: Iterable[MatchResult],
        target_name: str,
        progress: ProgressFn | None = None,
    ) -> list[MatchResult]:
        """Mark releases as already_present if their best match's target_id is in the library.
        Sets MatchResult.already_in_library so the UI can surface them separately."""
        if progress:
            progress("Fetching your YTM library…", 0, 0)
        try:
            library = {a.target_id for a in self.target.list_library_albums()}
        except Exception:
            library = set()

        results_list = list(results)
        total = len(results_list)
        annotated: list[MatchResult] = []
        for i, r in enumerate(results_list, start=1):
            if r.best and r.best.candidate.target_id in library:
                r.already_in_library = True
                self.state.upsert_status(
                    r.release.source_id,
                    target_name,
                    status="already_present",
                    target_kind="album",
                    target_id=r.best.candidate.target_id,
                    confidence=r.best.score,
                )
            annotated.append(r)
            if progress and i % 100 == 0:
                progress(f"Annotating against library… {i}/{total}", i, total)
        return annotated

    def apply_selections(
        self,
        selections: dict[str, dict],
        run_id: int,
        playlist_name: str,
        results_by_id: dict[str, MatchResult],
        *,
        progress: ProgressFn | None = None,
    ) -> dict[str, int]:
        target_name = self.target.name
        counts = {"added_album": 0, "added_video": 0, "error": 0, "skipped": 0}
        playlist_id: str | None = None
        items = list(selections.items())
        total = len(items)

        for i, (release_id, sel) in enumerate(items, start=1):
            kind = sel["kind"]
            target_id = sel["id"]
            if not kind or not target_id:
                counts["skipped"] += 1
                continue

            if self.state.is_already_added(release_id, target_name):
                counts["skipped"] += 1
                continue

            try:
                if kind == "album":
                    result = results_by_id.get(release_id)
                    candidate = None
                    if result:
                        candidate = next(
                            (
                                sc.candidate
                                for sc in result.candidates
                                if sc.candidate.target_id == target_id
                            ),
                            None,
                        )
                    if candidate is None:
                        from discogs_sync.targets.base import AlbumCandidate
                        candidate = AlbumCandidate(
                            target=target_name, target_id=target_id, title="", artist=""
                        )
                    self.target.add_album_to_library(candidate)
                    self.state.upsert_status(
                        release_id, target_name,
                        status="added_album",
                        target_kind="album",
                        target_id=target_id,
                        confidence=result.best.score if result and result.best else None,
                        run_id=run_id,
                    )
                    counts["added_album"] += 1

                elif kind == "video":
                    if playlist_id is None:
                        playlist_id = self.target.ensure_playlist(playlist_name)
                    result = results_by_id.get(release_id)
                    video = result.video_candidate if result else None
                    if video is None:
                        from discogs_sync.targets.base import VideoCandidate
                        video = VideoCandidate(
                            target=target_name, target_id=target_id, title="", channel=""
                        )
                    self.target.add_video_to_playlist(playlist_id, video)
                    self.state.upsert_status(
                        release_id, target_name,
                        status="added_video",
                        target_kind="video",
                        target_id=target_id,
                        confidence=result.video_score if result else None,
                        run_id=run_id,
                        notes=f"playlist={playlist_name}",
                    )
                    counts["added_video"] += 1
                else:
                    counts["skipped"] += 1
            except Exception as exc:
                counts["error"] += 1
                self.state.upsert_status(
                    release_id, target_name,
                    status="error",
                    target_kind=kind,
                    target_id=target_id,
                    run_id=run_id,
                    notes=str(exc)[:300],
                )

            if progress:
                progress(release_id, i, total)

        return counts

    def _iter_releases(self, folder: str, *, full: bool):
        from discogs_sync.adapters import discogs as ds
        if isinstance(self.source, ds.DiscogsAdapter):
            return self.source.iter_releases(folder, full=full)
        return self.source.iter_releases(folder)
