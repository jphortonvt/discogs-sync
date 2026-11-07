"""Build a comprehensive report across every release ever processed.

Reads all per-run JSON reports, dedupes by Discogs release ID (keeping the most
recent match data), cross-references the state DB for the final status, and emits:
- a CSV with one row per release
- a markdown summary grouped by status
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from discogs_sync.state import State


def collect_all(
    reports_dir: Path, state: State, target: str = "ytmusic"
) -> dict[str, dict[str, Any]]:
    """Return {discogs_release_id: row_dict}, merged across all runs + state DB."""
    by_id: dict[str, dict[str, Any]] = {}

    # Pass 1: walk every report JSON in chronological filename order so later runs
    # overwrite earlier match data for the same release.
    report_files = sorted(reports_dir.glob("report-*.json"))
    for path in report_files:
        try:
            payload = json.loads(path.read_text())
        except Exception:
            continue
        run_id = payload.get("run_id")
        folder = payload.get("folder", "")
        for r in payload.get("results", []):
            rel = r.get("release") or {}
            rid = str(rel.get("source_id", ""))
            if not rid:
                continue
            best = r.get("best") or {}
            best_cand = best.get("candidate") or {}
            video = r.get("video_candidate") or {}
            by_id[rid] = {
                "discogs_id": rid,
                "discogs_artist": rel.get("artist", ""),
                "discogs_title": rel.get("title", ""),
                "discogs_year": rel.get("year"),
                "discogs_label": rel.get("label", ""),
                "discogs_format": rel.get("format", ""),
                "discogs_folder": folder,
                "bucket": r.get("bucket", ""),
                "best_score": best.get("score"),
                "best_ytm_id": best_cand.get("target_id", ""),
                "best_ytm_artist": best_cand.get("artist", ""),
                "best_ytm_title": best_cand.get("title", ""),
                "video_id": video.get("target_id", ""),
                "video_title": video.get("title", ""),
                "video_channel": video.get("channel", ""),
                "video_score": r.get("video_score"),
                "already_in_library": r.get("already_in_library", False),
                "last_run_id": run_id,
            }

    # Pass 2: overlay final status from state DB (source of truth for what's in YTM).
    cur = state.conn.execute(
        "SELECT discogs_release_id, status, target_kind, target_id, confidence "
        "FROM release_status WHERE target=?",
        (target,),
    )
    for row in cur:
        rid = str(row["discogs_release_id"])
        entry = by_id.setdefault(rid, {
            "discogs_id": rid,
            "discogs_artist": "(unknown — never seen in a report)",
            "discogs_title": "",
            "discogs_year": None,
            "discogs_label": "", "discogs_format": "", "discogs_folder": "",
            "bucket": "", "best_score": None, "best_ytm_id": "",
            "best_ytm_artist": "", "best_ytm_title": "",
            "video_id": "", "video_title": "", "video_channel": "",
            "video_score": None, "already_in_library": False, "last_run_id": None,
        })
        entry["final_status"] = row["status"]
        entry["applied_target_kind"] = row["target_kind"]
        entry["applied_target_id"] = row["target_id"]
        entry["applied_confidence"] = row["confidence"]
        if row["status"] == "already_present":
            entry["already_in_library"] = True

    for entry in by_id.values():
        entry.setdefault("final_status", "(not in state DB)")
        entry.setdefault("applied_target_kind", "")
        entry.setdefault("applied_target_id", "")
        entry.setdefault("applied_confidence", None)

    return by_id


def write_csv(rows: list[dict], out_path: Path) -> None:
    if not rows:
        out_path.write_text("")
        return
    cols = [
        "discogs_id", "discogs_artist", "discogs_title", "discogs_year",
        "discogs_label", "discogs_format", "discogs_folder",
        "final_status", "applied_target_kind", "applied_target_id", "applied_confidence",
        "best_score", "best_ytm_id", "best_ytm_artist", "best_ytm_title",
        "video_id", "video_title", "video_channel", "video_score",
        "bucket", "already_in_library", "last_run_id",
    ]
    with out_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})


def write_markdown(rows: list[dict], out_path: Path) -> None:
    by_status: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_status[r.get("final_status", "(unknown)")].append(r)

    counts = {k: len(v) for k, v in by_status.items()}
    total = sum(counts.values())
    has_ytm_match = sum(
        1 for r in rows
        if r.get("final_status") in {"added_album", "added_video", "already_present"}
        or (r.get("best_ytm_id") and r.get("best_score") and r["best_score"] >= 0.70)
        or r.get("video_id")
    )
    no_ytm_match = total - has_ytm_match

    lines = [
        "# discogs-sync — complete sync report",
        "",
        f"**Total Discogs releases processed:** {total}",
        f"**Had a plausible YouTube Music match:** {has_ytm_match}  "
        f"({100*has_ytm_match//max(total,1)}%)",
        f"**No YouTube Music match:** {no_ytm_match}  ({100*no_ytm_match//max(total,1)}%)",
        "",
        "## Final status breakdown",
        "",
        "| Status | Count | What it means |",
        "|---|---:|---|",
        f"| added_album | {counts.get('added_album', 0)} | added to YTM Library → Albums |",
        f"| added_video | {counts.get('added_video', 0)} | "
        "YouTube video added to fallback playlist |",
        f"| already_present | {counts.get('already_present', 0)} | "
        "matched a YTM album already in your library |",
        f"| skipped | {counts.get('skipped', 0)} | low confidence; you chose not to add |",
        f"| error | {counts.get('error', 0)} | YTM API rejected the write |",
        f"| (not in state DB) | {counts.get('(not in state DB)', 0)} | matched but never applied |",
        "",
    ]

    section_order = [
        ("added_album", "Added to YTM Albums library"),
        ("added_video", "Added as YouTube video to fallback playlist"),
        ("already_present", "Already in YTM library"),
        ("skipped", "No YTM match (skipped)"),
        ("error", "Failed to add (errors)"),
        ("(not in state DB)", "Matched but never written"),
    ]
    for status, heading in section_order:
        items = by_status.get(status, [])
        if not items:
            continue
        lines.append(f"## {heading}  ({len(items)})")
        lines.append("")
        items.sort(key=lambda r: (r.get("discogs_artist") or "", r.get("discogs_title") or ""))
        for r in items:
            year = f" ({r['discogs_year']})" if r.get("discogs_year") else ""
            score = ""
            if r.get("best_score") is not None:
                score = f"  · score {r['best_score']:.2f}"
            tid = ""
            if r.get("applied_target_id"):
                if r.get("applied_target_kind") == "album":
                    tid = f"  · `{r['applied_target_id']}`"
                elif r.get("applied_target_kind") == "video":
                    tid = f"  · video [`{r['applied_target_id']}`](https://music.youtube.com/watch?v={r['applied_target_id']})"
            elif r.get("best_ytm_id") and status in {"already_present", "skipped"}:
                tid = f"  · `{r['best_ytm_id']}`"
            ytm_match = ""
            if r.get("best_ytm_artist") or r.get("best_ytm_title"):
                ytm_match = (
                    f"  →  *{r.get('best_ytm_artist','?')}* — "
                    f"*{r.get('best_ytm_title','?')}*"
                )
            lines.append(
                f"- **{r.get('discogs_artist','?')}** — "
                f"{r.get('discogs_title','?')}{year}{ytm_match}{score}{tid}"
            )
        lines.append("")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(reports_dir: Path, db_path: Path, out_dir: Path | None = None) -> tuple[Path, Path, int]:
    state = State(db_path)
    rows = list(collect_all(reports_dir, state).values())
    rows.sort(key=lambda r: (r.get("discogs_artist") or "", r.get("discogs_title") or ""))
    out_dir = out_dir or reports_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "report-all.csv"
    md_path = out_dir / "report-all.md"
    write_csv(rows, csv_path)
    write_markdown(rows, md_path)
    return md_path, csv_path, len(rows)
