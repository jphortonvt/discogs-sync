from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from discogs_sync.matcher import MatchResult


def write_reports(
    reports_dir: Path,
    run_id: int,
    folder: str,
    tallies: dict,
    results: list[MatchResult],
) -> tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    md_path = reports_dir / f"report-{ts}-run{run_id}.md"
    json_path = reports_dir / f"report-{ts}-run{run_id}.json"

    md_path.write_text(_markdown(run_id, folder, tallies, results), encoding="utf-8")
    json_path.write_text(
        json.dumps(_json_payload(run_id, folder, tallies, results), indent=2),
        encoding="utf-8",
    )
    return md_path, json_path


def _markdown(run_id: int, folder: str, tallies: dict, results: list[MatchResult]) -> str:
    by_bucket = {
        b: [r for r in results if r.bucket == b]
        for b in ("strong", "review", "no_album_match")
    }

    lines = [
        f"# discogs-sync run {run_id}",
        f"Folder: `{folder}`",
        "",
        "| Outcome | Count |",
        "|---|---|",
        f"| Added to Albums | {tallies.get('added_album', 0)} |",
        f"| Added video to playlist | {tallies.get('added_video', 0)} |",
        f"| Skipped | {tallies.get('skipped', 0)} |",
        f"| Errors | {tallies.get('error', 0)} |",
        "",
        f"## Strong ({len(by_bucket['strong'])})",
    ]
    for r in by_bucket["strong"]:
        if r.best:
            lines.append(
                f"- {r.release.artist} — {r.release.title}  →  "
                f"`{r.best.candidate.target_id}` ({r.best.score:.2f})"
            )
    lines += ["", f"## Reviewed ({len(by_bucket['review'])})"]
    for r in by_bucket["review"]:
        if r.best:
            lines.append(
                f"- {r.release.artist} — {r.release.title}  →  "
                f"best `{r.best.candidate.target_id}` ({r.best.score:.2f})"
            )
    lines += ["", f"## No album match ({len(by_bucket['no_album_match'])})"]
    for r in by_bucket["no_album_match"]:
        if r.video_candidate:
            lines.append(
                f"- {r.release.artist} — {r.release.title}  ·  "
                f"video `{r.video_candidate.target_id}` ({r.video_score:.2f})"
            )
        else:
            lines.append(f"- {r.release.artist} — {r.release.title}  ·  no video")
    return "\n".join(lines) + "\n"


def _json_payload(run_id: int, folder: str, tallies: dict, results: list[MatchResult]) -> dict:
    return {
        "run_id": run_id,
        "folder": folder,
        "tallies": tallies,
        "results": [r.model_dump(mode="json") for r in results],
    }
