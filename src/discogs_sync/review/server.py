from __future__ import annotations

import threading
import time
import webbrowser
from pathlib import Path
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from discogs_sync import auth, config
from discogs_sync.adapters import discogs as ds
from discogs_sync.engine import MatchOptions, SyncEngine
from discogs_sync.matcher import MatchResult
from discogs_sync.state import State
from discogs_sync.targets import ytmusic as ytm
from discogs_sync.targets.base import AlbumCandidate, VideoCandidate  # noqa: F401

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


class ReviewContext:
    def __init__(self, folder: str | None, full: bool, rematch: bool, limit: int | None = None):
        cfg = config.load()
        self.cfg = cfg
        self.folder = folder or cfg["discogs"]["default_folder"]
        self.full = full
        self.rematch = rematch
        self.limit = limit
        # Apply-job state shared between the background thread and the polling endpoint.
        self.apply_state: dict[str, Any] = {"status": "idle", "applied": 0, "total": 0, "tallies": {}}
        self._apply_lock = threading.Lock()
        # Match-job state, populated by the background matching thread.
        self.match_state: dict[str, Any] = {
            "status": "idle", "phase": "starting",
            "fetched": 0, "i": 0, "total": 0, "current": "", "fetch_msg": "",
        }
        self._match_lock = threading.Lock()

        dauth = auth.load_discogs_auth()
        yauth = auth.load_ytmusic_auth()
        if not dauth or not yauth:
            raise RuntimeError("Both Discogs and YouTube Music must be authorized before running review.")

        username = cfg["discogs"]["username"]
        if not username:
            raise RuntimeError("No Discogs username on file.")

        self.source = ds.DiscogsAdapter(dauth, username=username)
        self.target = ytm.YTMusicAdapter(yauth)
        self.state = State(cfg["state"]["db_path"])
        self.options = MatchOptions(
            strong_threshold=cfg["match"]["strong_threshold"],
            review_threshold=cfg["match"]["review_threshold"],
            video_threshold=cfg["match"]["video_threshold"],
        )
        self.engine = SyncEngine(self.source, self.target, self.state, self.options)

        self.results: list[MatchResult] = []
        self.results_by_id: dict[str, MatchResult] = {}
        self._load_or_compute()

    def trigger_next_batch(self) -> None:
        """Mark unprocessed items in the current batch as skipped, then kick off fresh match."""
        target_name = self.target.name
        for r in self.results:
            row = self.state.get_status(r.release.source_id, target_name)
            if row and row["status"] in {"added_album", "added_video", "already_present"}:
                continue
            self.state.upsert_status(
                r.release.source_id, target_name,
                status="skipped", target_kind=None, target_id=None,
            )
        self.state.clear_review_session()
        self.results = []
        self.results_by_id = {}
        self._start_compute_thread()

    def _load_or_compute(self) -> None:
        existing = self.state.load_review_session()
        if existing and not self.rematch and existing.get("folder") == self.folder:
            try:
                self.results = [MatchResult.model_validate(r) for r in existing["payload"]["results"]]
                self.results_by_id = {r.release.source_id: r for r in self.results}
                self.match_state["status"] = "done"
                self.match_state["total"] = len(self.results)
                self.match_state["i"] = len(self.results)
                self.match_state["phase"] = "done"
                return
            except Exception as exc:
                print(f"[review] Cached session invalid ({exc}); recomputing.")
        self._start_compute_thread()

    def _start_compute_thread(self) -> None:
        with self._match_lock:
            if self.match_state.get("status") == "running":
                return
            self.match_state = {
                "status": "running", "phase": "starting",
                "fetched": 0, "i": 0, "total": 0, "current": "", "fetch_msg": "",
            }
        threading.Thread(target=self._bg_compute, daemon=True).start()

    def _bg_compute(self) -> None:
        try:
            def progress(name: str, i: int, n: int) -> None:
                # Parse the message to derive phase. compute_matches uses these patterns:
                #   "…fetched N from Discogs so far"          → discogs_fetch
                #   "Discogs fetch complete: N releases..."   → fetch done, ytm_search begins
                #   "<release.title>" with i, n               → ytm_search
                # annotate_with_library uses:
                #   "Fetching your YTM library…"              → annotate (initial)
                #   "Annotating against library… i/total"     → annotate
                if "from Discogs so far" in name:
                    self.match_state["phase"] = "discogs_fetch"
                    self.match_state["fetched"] = i
                elif name.startswith("Discogs fetch complete"):
                    self.match_state["phase"] = "ytm_search"
                    self.match_state["total"] = n
                    self.match_state["i"] = 0
                    self.match_state["fetch_msg"] = name
                elif name.startswith("Fetching your YTM library") or name.startswith("Annotating against library"):
                    self.match_state["phase"] = "annotate"
                    self.match_state["i"] = i
                    self.match_state["total"] = n
                    self.match_state["current"] = name
                else:
                    self.match_state["phase"] = "ytm_search"
                    self.match_state["i"] = i
                    self.match_state["total"] = n
                    self.match_state["current"] = name

            results = self.engine.compute_matches(
                self.folder, full=self.full, limit=self.limit, skip_processed=True, progress=progress
            )
            results = self.engine.annotate_with_library(results, target_name="ytmusic", progress=progress)

            self.state.save_review_session(
                self.folder,
                {"results": [r.model_dump(mode="json") for r in results], "options": self.options.__dict__},
            )
            self.results = results
            self.results_by_id = {r.release.source_id: r for r in results}
            self.match_state["status"] = "done"
            self.match_state["phase"] = "done"
            self.match_state["total"] = len(results)
            self.match_state["i"] = len(results)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.match_state["status"] = "error"
            self.match_state["error"] = str(exc)[:500]


def create_app(ctx: ReviewContext) -> FastAPI:
    app = FastAPI(title="discogs-sync review")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    def _selections_summary() -> dict[str, int]:
        sels = ctx.state.all_selections()
        albums = sum(1 for s in sels.values() if s["kind"] == "album")
        videos = sum(1 for s in sels.values() if s["kind"] == "video")
        return {"album": albums, "video": videos, "total": albums + videos}

    def _bucketed():
        already = [r for r in ctx.results if r.already_in_library]
        strong = [r for r in ctx.results if not r.already_in_library and r.bucket == "strong"]
        review = [r for r in ctx.results if not r.already_in_library and r.bucket == "review"]
        no_match = [r for r in ctx.results if not r.already_in_library and r.bucket == "no_album_match"]
        return strong, review, no_match, already

    def _initialize_default_selections():
        """Pre-check strong matches AND review-bucket items whose best score >= autoselect_threshold.
        Skip already-in-library releases — they don't need re-adding."""
        existing = ctx.state.all_selections()
        autoselect = ctx.cfg["match"].get("autoselect_threshold", 0.75)
        for r in ctx.results:
            if r.release.source_id in existing:
                continue
            if r.already_in_library:
                continue
            if not r.best:
                continue
            should_select = r.bucket == "strong" or r.best.score >= autoselect
            if should_select:
                ctx.state.upsert_selection(
                    r.release.source_id,
                    "album",
                    r.best.candidate.target_id,
                    payload={"title": r.best.candidate.title, "artist": r.best.candidate.artist},
                )

    if ctx.match_state.get("status") == "done":
        _initialize_default_selections()

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        if ctx.match_state.get("status") != "done":
            return templates.TemplateResponse(
                request,
                "loading.html",
                {
                    "folder": ctx.folder,
                    "limit": ctx.limit,
                    "error": ctx.match_state.get("error"),
                    "is_error": ctx.match_state.get("status") == "error",
                },
            )
        # Match just finished — initialize default selections if not done yet
        _initialize_default_selections()
        strong, review, no_match, already = _bucketed()
        selections = ctx.state.all_selections()
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "folder": ctx.folder,
                "strong": strong,
                "review": review,
                "no_match": no_match,
                "already": already,
                "selections": selections,
                "summary": _selections_summary(),
                "video_playlist": ctx.cfg["ytmusic"]["video_fallback_playlist"],
            },
        )

    @app.get("/match-progress")
    def match_progress():
        return JSONResponse(dict(ctx.match_state))

    @app.post("/select", response_class=PlainTextResponse)
    def select(
        release_id: str = Form(...),
        kind: str = Form(...),
        target_id: str = Form(...),
        title: str = Form(""),
        artist: str = Form(""),
    ):
        if kind == "none":
            ctx.state.remove_selection(release_id)
        else:
            ctx.state.upsert_selection(
                release_id, kind, target_id, payload={"title": title, "artist": artist}
            )
        return "ok"

    @app.post("/toggle", response_class=PlainTextResponse)
    def toggle(
        release_id: str = Form(...),
        kind: str = Form(...),
        target_id: str = Form(...),
        title: str = Form(""),
        artist: str = Form(""),
        on: bool = Form(False),
    ):
        if on:
            ctx.state.upsert_selection(
                release_id, kind, target_id, payload={"title": title, "artist": artist}
            )
        else:
            ctx.state.remove_selection(release_id)
        return "ok"

    @app.get("/summary", response_class=HTMLResponse)
    def summary(request: Request):
        return templates.TemplateResponse(
            request, "_summary.html", {"summary": _selections_summary()}
        )

    @app.post("/next-batch")
    def next_batch():
        ctx.trigger_next_batch()
        return RedirectResponse("/", status_code=303)

    @app.post("/apply")
    def apply():
        with ctx._apply_lock:
            if ctx.apply_state.get("status") == "running":
                return JSONResponse({"status": "already_running"}, status_code=409)
            selections = ctx.state.all_selections()
            ctx.apply_state = {
                "status": "running",
                "applied": 0,
                "total": len(selections),
                "tallies": {},
                "current": "",
                "started_at": time.time(),
            }

        def _bg_apply(snapshot_selections: dict):
            target_name = ctx.target.name
            run_id = ctx.state.start_run(source="discogs", target=target_name)
            playlist_name = ctx.cfg["ytmusic"]["video_fallback_playlist"]

            def _progress(name: str, i: int, n: int) -> None:
                ctx.apply_state["applied"] = i
                ctx.apply_state["current"] = name

            try:
                tallies = ctx.engine.apply_selections(
                    snapshot_selections, run_id, playlist_name, ctx.results_by_id, progress=_progress
                )
                counts = {b: sum(1 for r in ctx.results if r.bucket == b) for b in ("strong", "review", "no_album_match")}
                ctx.state.finish_run(run_id, {"counts": tallies | counts, "folder": ctx.folder, "applied": True})
                ctx.state.clear_review_session()
                reports_dir = Path(ctx.cfg["state"]["db_path"]).parent / "reports"
                from discogs_sync.report import write_reports
                md_path, json_path = write_reports(reports_dir, run_id, ctx.folder, tallies, ctx.results)
                ctx.apply_state.update({
                    "status": "done",
                    "tallies": tallies,
                    "report_md": str(md_path),
                    "report_json": str(json_path),
                    "finished_at": time.time(),
                })
            except Exception as exc:
                ctx.apply_state.update({
                    "status": "error",
                    "error": str(exc)[:500],
                    "finished_at": time.time(),
                })

        t = threading.Thread(target=_bg_apply, args=(selections,), daemon=True)
        t.start()
        return JSONResponse({"status": "started", "total": ctx.apply_state["total"]})

    @app.get("/apply-progress")
    def apply_progress():
        s = dict(ctx.apply_state)
        # truncate the "current" so the JSON stays small even if titles are long
        if "current" in s:
            s["current"] = s["current"][:120]
        return JSONResponse(s)

    return app


def serve(*, folder: Optional[str], full: bool, port: int, rematch: bool, limit: int | None = None) -> None:
    try:
        ctx = ReviewContext(folder=folder, full=full, rematch=rematch, limit=limit)
    except RuntimeError as exc:
        print(f"Error: {exc}")
        raise SystemExit(2)

    app = create_app(ctx)
    url = f"http://localhost:{port}"
    threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    print(f"Opening {url}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
