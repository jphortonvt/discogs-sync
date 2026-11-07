from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    summary_json TEXT
);

CREATE TABLE IF NOT EXISTS release_status (
    discogs_release_id TEXT NOT NULL,
    target TEXT NOT NULL,
    target_kind TEXT,
    target_id TEXT,
    status TEXT NOT NULL,
    confidence REAL,
    last_seen_run INTEGER,
    notes TEXT,
    PRIMARY KEY (discogs_release_id, target)
);

CREATE TABLE IF NOT EXISTS review_selections (
    discogs_release_id TEXT PRIMARY KEY,
    chosen_target_kind TEXT,
    chosen_target_id TEXT,
    chosen_payload_json TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_session (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    folder TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class State:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False so FastAPI worker threads can share the connection;
        # SQLite serializes its own writes and we only have one process.
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def start_run(self, source: str, target: str) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO sync_runs (started_at, source, target) VALUES (?, ?, ?)",
                (now_iso(), source, target),
            )
            return cur.lastrowid

    def finish_run(self, run_id: int, summary: dict[str, Any]) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE sync_runs SET finished_at = ?, summary_json = ? WHERE id = ?",
                (now_iso(), json.dumps(summary), run_id),
            )

    def get_status(self, release_id: str, target: str) -> dict | None:
        cur = self.conn.execute(
            "SELECT * FROM release_status WHERE discogs_release_id=? AND target=?",
            (release_id, target),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def upsert_status(
        self,
        release_id: str,
        target: str,
        *,
        status: str,
        target_kind: str | None = None,
        target_id: str | None = None,
        confidence: float | None = None,
        run_id: int | None = None,
        notes: str | None = None,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO release_status
                    (discogs_release_id, target, target_kind, target_id, status,
                     confidence, last_seen_run, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(discogs_release_id, target) DO UPDATE SET
                    target_kind = excluded.target_kind,
                    target_id = excluded.target_id,
                    status = excluded.status,
                    confidence = excluded.confidence,
                    last_seen_run = excluded.last_seen_run,
                    notes = excluded.notes
                """,
                (release_id, target, target_kind, target_id, status, confidence, run_id, notes),
            )

    def is_already_added(self, release_id: str, target: str) -> bool:
        row = self.get_status(release_id, target)
        if not row:
            return False
        return row["status"] in {"added_album", "added_video", "already_present"}

    def get_processed_release_ids(self, target: str) -> set[str]:
        """Every release the user has already decided on (added, skipped, errored, no_match)."""
        cur = self.conn.execute(
            "SELECT discogs_release_id FROM release_status WHERE target=?", (target,)
        )
        return {row[0] for row in cur}

    def save_review_session(self, folder: str | None, payload: dict) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM review_session")
            conn.execute(
                "INSERT INTO review_session (id, folder, payload_json, created_at) "
                "VALUES (1, ?, ?, ?)",
                (folder, json.dumps(payload), now_iso()),
            )

    def load_review_session(self) -> dict | None:
        cur = self.conn.execute("SELECT * FROM review_session WHERE id=1")
        row = cur.fetchone()
        if not row:
            return None
        return {
            "folder": row["folder"],
            "payload": json.loads(row["payload_json"]),
            "created_at": row["created_at"],
        }

    def clear_review_session(self) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM review_session")
            conn.execute("DELETE FROM review_selections")

    def upsert_selection(
        self,
        release_id: str,
        chosen_target_kind: str | None,
        chosen_target_id: str | None,
        payload: dict | None = None,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO review_selections
                    (discogs_release_id, chosen_target_kind, chosen_target_id,
                     chosen_payload_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(discogs_release_id) DO UPDATE SET
                    chosen_target_kind = excluded.chosen_target_kind,
                    chosen_target_id = excluded.chosen_target_id,
                    chosen_payload_json = excluded.chosen_payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    release_id,
                    chosen_target_kind,
                    chosen_target_id,
                    json.dumps(payload) if payload else None,
                    now_iso(),
                ),
            )

    def remove_selection(self, release_id: str) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM review_selections WHERE discogs_release_id=?", (release_id,))

    def all_selections(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for row in self.conn.execute("SELECT * FROM review_selections"):
            out[row["discogs_release_id"]] = {
                "kind": row["chosen_target_kind"],
                "id": row["chosen_target_id"],
                "payload": (
                    json.loads(row["chosen_payload_json"])
                    if row["chosen_payload_json"]
                    else None
                ),
            }
        return out

    def latest_run_summary(self) -> dict | None:
        cur = self.conn.execute(
            "SELECT * FROM sync_runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "summary": json.loads(row["summary_json"]) if row["summary_json"] else {},
        }
