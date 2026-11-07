from __future__ import annotations

import json
import sys
import webbrowser
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from discogs_sync import auth, config, secrets
from discogs_sync.adapters import discogs as ds
from discogs_sync.engine import MatchOptions, SyncEngine
from discogs_sync.state import State
from discogs_sync.targets import ytmusic as ytm

app = typer.Typer(help="Sync your Discogs collection to streaming services.", no_args_is_help=True)
auth_app = typer.Typer(help="Manage credentials for Discogs and streaming providers.", no_args_is_help=True)
app.add_typer(auth_app, name="auth")

console = Console()


_load_discogs_auth = auth.load_discogs_auth
_load_ytmusic_auth = auth.load_ytmusic_auth


@auth_app.command("login")
def auth_login(
    provider: str = typer.Argument(..., help="discogs | ytmusic | ytmusic-browser"),
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="(ytmusic-browser only) read headers from a file instead of stdin"),
):
    """Authorize a provider."""
    if provider == "ytmusic":
        _login_ytmusic()
        return
    if provider == "ytmusic-browser":
        _login_ytmusic_browser(file_path=file)
        return
    if provider != "discogs":
        console.print(f"[red]Unknown provider {provider!r}. Use 'discogs', 'ytmusic', or 'ytmusic-browser'.[/red]")
        raise typer.Exit(2)

    creds = config.discogs_consumer_credentials()
    if not creds:
        console.print(
            "[red]Missing Discogs consumer credentials.[/red]\n"
            "Register an app at https://www.discogs.com/settings/developers, then either:\n"
            "  • export DISCOGS_CONSUMER_KEY=... DISCOGS_CONSUMER_SECRET=...\n"
            f"  • or add them under [discogs] in {config.CONFIG_PATH}"
        )
        raise typer.Exit(2)
    consumer_key, consumer_secret = creds

    client, url = ds.begin_oauth(consumer_key, consumer_secret)
    console.print("\nOpening Discogs authorization page in your browser:")
    console.print(f"  [cyan]{url}[/cyan]\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass

    verifier = typer.prompt("Paste the verification code from Discogs").strip()
    access_token, access_token_secret = ds.complete_oauth(client, verifier)

    secrets.set_token("discogs", "access_token", access_token)
    secrets.set_token("discogs", "access_token_secret", access_token_secret)

    auth = ds.DiscogsAuth(consumer_key, consumer_secret, access_token, access_token_secret)
    adapter = ds.DiscogsAdapter(auth, username="")
    me = adapter.whoami()

    cfg = config.load()
    cfg["discogs"]["username"] = me
    config.save(cfg)

    console.print(f"\n[green]Logged in as[/green] [bold]{me}[/bold].")


def _login_ytmusic_browser(file_path: Path | None = None) -> None:
    if file_path:
        raw = file_path.read_text(encoding="utf-8")
        console.print(f"Read {len(raw)} chars from {file_path}")
    else:
        console.print(
            "[bold]Browser-auth setup[/bold]\n\n"
            "1. Open [cyan]https://music.youtube.com[/cyan] in your browser and log in.\n"
            "2. Open DevTools (F12) → [bold]Network[/bold] tab.\n"
            "3. Reload the page.\n"
            "4. In the filter box, type [cyan]/browse[/cyan]. Click any POST request.\n"
            "5. Right-click → Copy → [bold]Copy Request Headers[/bold] (Firefox)\n"
            "   or [bold]Copy → Copy as cURL[/bold] (Chrome).\n"
            "6. Paste below, then a blank line, then Ctrl-D.\n"
            "   (Tip: easier to save headers to a file and run\n"
            "    [cyan]discogs-sync auth login ytmusic-browser --file headers.txt[/cyan])\n\n"
        )
        lines: list[str] = []
        try:
            while True:
                lines.append(input())
        except EOFError:
            pass
        raw = "\n".join(lines)

    raw = raw.strip()
    console.print(f"[dim]Received {len(raw)} chars, {raw.count(chr(10)) + 1} lines.[/dim]")
    if not raw:
        console.print("[red]No headers received.[/red]")
        raise typer.Exit(2)

    try:
        header_json = ytm.setup_browser_auth(raw)
    except Exception as exc:
        console.print(f"[red]Failed to parse headers:[/red] {exc}")
        console.print("\n[dim]First 3 lines of what you pasted:[/dim]")
        for line in raw.splitlines()[:3]:
            console.print(f"  [dim]{line[:120]}[/dim]")
        raise typer.Exit(2)

    secrets.set_token("ytmusic", "browser_headers", header_json)
    console.print("\n[green]YouTube Music browser auth saved.[/green]")


def _login_ytmusic() -> None:
    creds = config.ytmusic_oauth_credentials()
    if not creds:
        console.print(
            "[red]Missing YouTube Music OAuth credentials.[/red]\n"
            "Create a 'TVs and Limited Input devices' OAuth client at\n"
            "  https://console.cloud.google.com/apis/credentials\n"
            "(enable the YouTube Data API v3 first), then either:\n"
            "  • export YTMUSIC_CLIENT_ID=... YTMUSIC_CLIENT_SECRET=...\n"
            f"  • or add them under [ytmusic] in {config.CONFIG_PATH}"
        )
        raise typer.Exit(2)
    cid, csec = creds
    console.print("Starting YouTube Music device-code flow. Follow the prompts in your terminal.\n")
    token = ytm.begin_oauth(cid, csec)
    secrets.set_token("ytmusic", "token", ytm.serialize_token(token))
    console.print("\n[green]YouTube Music authorized and token saved.[/green]")


@auth_app.command("status")
def auth_status():
    """Show which providers are authorized."""
    table = Table(show_header=True, header_style="bold")
    table.add_column("Provider")
    table.add_column("Status")
    table.add_column("Account")

    auth = _load_discogs_auth()
    if auth is None:
        table.add_row("discogs", "[yellow]not logged in[/yellow]", "—")
    else:
        cfg = config.load()
        table.add_row("discogs", "[green]ok[/green]", cfg["discogs"]["username"] or "?")

    yauth = _load_ytmusic_auth()
    if yauth is None:
        table.add_row("ytmusic", "[yellow]not logged in[/yellow]", "—")
    else:
        mode = "browser headers" if yauth.is_browser else "OAuth token"
        table.add_row("ytmusic", "[green]ok[/green]", mode)
    console.print(table)


@auth_app.command("logout")
def auth_logout(provider: str = typer.Argument(...)):
    """Remove stored credentials for a provider."""
    if provider == "discogs":
        secrets.delete_token("discogs", "access_token")
        secrets.delete_token("discogs", "access_token_secret")
        console.print("[green]Discogs credentials removed.[/green]")
    elif provider == "ytmusic":
        secrets.delete_token("ytmusic", "token")
        secrets.delete_token("ytmusic", "browser_headers")
        console.print("[green]YouTube Music credentials removed.[/green]")
    else:
        console.print(f"[red]Unknown provider {provider!r}.[/red]")
        raise typer.Exit(2)


@app.command()
def folders():
    """List Discogs collection folders."""
    auth = _load_discogs_auth()
    if auth is None:
        console.print("[red]Not logged in.[/red] Run: discogs-sync auth login discogs")
        raise typer.Exit(2)
    cfg = config.load()
    username = cfg["discogs"]["username"]
    if not username:
        console.print("[red]No Discogs username on file.[/red] Re-run: discogs-sync auth login discogs")
        raise typer.Exit(2)

    adapter = ds.DiscogsAdapter(auth, username=username)
    for name in adapter.list_folders():
        console.print(f"  • {name}")


@app.command()
def dump(
    folder: Optional[str] = typer.Option(None, "--folder", "-f", help="Discogs folder name"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write JSON to this path (default: stdout)"),
    full: bool = typer.Option(False, "--full", help="Fetch full release for each item (slow; needed for tracks)"),
    limit: Optional[int] = typer.Option(None, "--limit", help="Stop after N releases (for testing)"),
):
    """Dump a Discogs folder to JSON."""
    auth = _load_discogs_auth()
    if auth is None:
        console.print("[red]Not logged in.[/red] Run: discogs-sync auth login discogs")
        raise typer.Exit(2)
    cfg = config.load()
    username = cfg["discogs"]["username"]
    folder = folder or cfg["discogs"]["default_folder"]
    if not username:
        console.print("[red]No Discogs username on file.[/red] Re-run: discogs-sync auth login discogs")
        raise typer.Exit(2)

    adapter = ds.DiscogsAdapter(auth, username=username)
    out: list[dict] = []
    with console.status(f"[cyan]Fetching folder[/cyan] {folder!r}…") as status:
        for i, release in enumerate(adapter.iter_releases(folder, full=full), start=1):
            out.append(release.model_dump())
            status.update(f"[cyan]Fetched[/cyan] {i} releases…")
            if limit and i >= limit:
                break

    payload = json.dumps(out, indent=2, ensure_ascii=False)
    if output:
        output.write_text(payload, encoding="utf-8")
        console.print(f"[green]Wrote {len(out)} releases →[/green] {output}")
    else:
        sys.stdout.write(payload + "\n")


def _require_both_auths() -> tuple[ds.DiscogsAuth, ytm.YTMusicAuth, str]:
    dauth = _load_discogs_auth()
    if dauth is None:
        console.print("[red]Not logged in to Discogs.[/red] Run: discogs-sync auth login discogs")
        raise typer.Exit(2)
    yauth = _load_ytmusic_auth()
    if yauth is None:
        console.print("[red]Not logged in to YouTube Music.[/red] Run: discogs-sync auth login ytmusic")
        raise typer.Exit(2)
    cfg = config.load()
    username = cfg["discogs"]["username"]
    if not username:
        console.print("[red]No Discogs username on file.[/red] Re-run: discogs-sync auth login discogs")
        raise typer.Exit(2)
    return dauth, yauth, username


@app.command("sync")
def sync_cmd(
    target: str = typer.Argument("ytmusic"),
    folder: Optional[str] = typer.Option(None, "--folder", "-f"),
    full: bool = typer.Option(False, "--full", help="Fetch tracklists from Discogs (slower)"),
    apply: bool = typer.Option(False, "--apply", help="Auto-add albums ≥ autoselect threshold; mark others as skipped"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Don't prompt before applying"),
    limit: Optional[int] = typer.Option(None, "--limit", help="Stop after N releases (for testing)"),
    redo: bool = typer.Option(False, "--redo", help="Re-process releases already marked added/skipped in the state DB"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would happen but don't write to YTM"),
):
    """Headless: match a folder against the target library and (optionally) apply strong matches."""
    if target != "ytmusic":
        console.print(f"[red]Unknown target {target!r}.[/red]")
        raise typer.Exit(2)
    dauth, yauth, username = _require_both_auths()
    cfg = config.load()
    folder = folder or cfg["discogs"]["default_folder"]

    source = ds.DiscogsAdapter(dauth, username=username)
    library = ytm.YTMusicAdapter(yauth)
    state = State(cfg["state"]["db_path"])
    options = MatchOptions(
        strong_threshold=cfg["match"]["strong_threshold"],
        review_threshold=cfg["match"]["review_threshold"],
        video_threshold=cfg["match"]["video_threshold"],
    )
    engine = SyncEngine(source, library, state, options)

    run_id = state.start_run(source="discogs", target=target)

    def _progress(name: str, i: int, n: int) -> None:
        console.print(f"  [dim]{i}/{n}[/dim] {name[:70]}")

    autoselect = cfg["match"].get("autoselect_threshold", 0.75)
    video_thr = cfg["match"]["video_threshold"]

    console.print(f"[cyan]Fetching releases from Discogs folder[/cyan] {folder!r}…")
    results = engine.compute_matches(
        folder, full=full, progress=_progress, limit=limit, skip_processed=not redo
    )
    console.print("[cyan]Annotating against YTM library…[/cyan]")
    results = engine.annotate_with_library(results, target_name=target)

    counts = {"strong": 0, "review": 0, "no_album_match": 0, "with_video": 0}
    for r in results:
        counts[r.bucket] += 1
        if r.bucket == "no_album_match" and r.video_candidate:
            counts["with_video"] += 1

    if not results:
        console.print("[green]Nothing left to process[/green] — everything in this folder is already added or skipped.")
        console.print("Use [bold]--redo[/bold] to re-process previously skipped releases.")
        state.finish_run(run_id, {"counts": counts, "folder": folder, "applied": False})
        return

    console.print(
        f"\n[green]Buckets[/green]: strong={counts['strong']}  review={counts['review']}  "
        f"no_album={counts['no_album_match']} (videos found={counts['with_video']})"
    )

    payload = {
        "results": [_match_to_dict(r) for r in results],
        "options": options.__dict__,
    }
    state.save_review_session(folder, payload)

    if not apply:
        state.finish_run(run_id, {"counts": counts, "folder": folder, "applied": False})
        console.print("\nRun [bold]discogs-sync review[/bold] to open the UI, or re-run with --apply.")
        return

    # Build auto-selections: any album ≥ autoselect threshold, plus video fallbacks
    selections: dict[str, dict] = {}
    will_skip: list[str] = []
    for r in results:
        if r.best and r.best.score >= autoselect:
            selections[r.release.source_id] = {"kind": "album", "id": r.best.candidate.target_id}
        elif r.video_candidate and r.video_score and r.video_score >= video_thr:
            selections[r.release.source_id] = {"kind": "video", "id": r.video_candidate.target_id}
        else:
            will_skip.append(r.release.source_id)

    console.print(
        f"\n[bold]Plan[/bold]: add {sum(1 for s in selections.values() if s['kind'] == 'album')} albums, "
        f"add {sum(1 for s in selections.values() if s['kind'] == 'video')} videos, "
        f"skip {len(will_skip)} (will need manual review)."
    )

    if dry_run:
        console.print("\n[yellow]--dry-run set; nothing written.[/yellow]")
        state.finish_run(run_id, {"counts": counts, "folder": folder, "applied": False, "dry_run": True})
        return

    if not yes:
        ok = typer.confirm("Proceed?")
        if not ok:
            raise typer.Exit(1)

    results_by_id = {r.release.source_id: r for r in results}
    playlist_name = cfg["ytmusic"]["video_fallback_playlist"]
    console.print("[cyan]Applying…[/cyan]")
    tallies = engine.apply_selections(selections, run_id, playlist_name, results_by_id, progress=_progress)

    # Mark every unselected release as skipped so it doesn't surface next time
    for release_id in will_skip:
        state.upsert_status(
            release_id, target,
            status="skipped", target_kind=None, target_id=None, run_id=run_id,
        )
    tallies["skipped"] = tallies.get("skipped", 0) + len(will_skip)

    state.finish_run(run_id, {"counts": tallies | counts, "folder": folder, "applied": True})

    from discogs_sync.report import write_reports
    reports_dir = Path(cfg["state"]["db_path"]).parent / "reports"
    md_path, _ = write_reports(reports_dir, run_id, folder, tallies, results)
    console.print(f"\n[green]Done.[/green] {tallies}\n  Report: {md_path}")
    console.print("\nRe-run [bold]discogs-sync sync ytmusic --folder " + folder + " --apply --yes[/bold] to continue with the next batch.")


def _match_to_dict(r) -> dict:
    return r.model_dump(mode="json")


report_app = typer.Typer(help="Inspect past sync runs.", no_args_is_help=True)
app.add_typer(report_app, name="report")


@report_app.command("show")
def report_show():
    """Show the most recent sync run summary."""
    cfg = config.load()
    state = State(cfg["state"]["db_path"])
    latest = state.latest_run_summary()
    if not latest:
        console.print("[yellow]No completed runs yet.[/yellow]")
        raise typer.Exit(0)
    summary = latest["summary"]
    counts = summary.get("counts", {})
    console.print(f"[bold]Run #{latest['id']}[/bold]  ({latest['started_at']} → {latest['finished_at']})")
    console.print(f"  Folder: {summary.get('folder')!r}  Applied: {summary.get('applied')}")
    for k, v in counts.items():
        console.print(f"  {k:>16} : {v}")


@report_app.command("dir")
def report_dir():
    """Print the reports directory path."""
    cfg = config.load()
    console.print(str(Path(cfg["state"]["db_path"]).parent / "reports"))


@report_app.command("all")
def report_all_cmd():
    """Aggregate every per-run report + state DB into one comprehensive report.
    Output: report-all.md (grouped by status) and report-all.csv (one row per release)."""
    from discogs_sync.report_all import run
    cfg = config.load()
    reports_dir = Path(cfg["state"]["db_path"]).parent / "reports"
    db_path = Path(cfg["state"]["db_path"])
    md_path, csv_path, total = run(reports_dir, db_path)
    console.print(f"[green]Built[/green]: {total} releases")
    console.print(f"  Markdown: {md_path}")
    console.print(f"  CSV:      {csv_path}")


@app.command("review")
def review_cmd(
    folder: Optional[str] = typer.Option(None, "--folder", "-f"),
    full: bool = typer.Option(False, "--full"),
    port: int = typer.Option(8765, "--port"),
    rematch: bool = typer.Option(False, "--rematch", help="Re-run matching even if a saved session exists"),
    limit: Optional[int] = typer.Option(None, "--limit", help="Match only the first N releases"),
):
    """Launch the local review UI."""
    from discogs_sync.review.server import serve
    serve(folder=folder, full=full, port=port, rematch=rematch, limit=limit)


@app.command("ytm-search")
def ytm_search(
    artist: str = typer.Argument(...),
    title: str = typer.Argument(...),
    videos: bool = typer.Option(False, "--videos", help="Search videos instead of albums"),
    limit: int = typer.Option(5, "--limit"),
):
    """Probe the YouTube Music search for an artist + album."""
    auth = _load_ytmusic_auth()
    if auth is None:
        console.print("[red]Not logged in to YouTube Music.[/red] Run: discogs-sync auth login ytmusic")
        raise typer.Exit(2)
    target = ytm.YTMusicAdapter(auth)
    if videos:
        for v in target.search_album_video(artist, title, limit=limit):
            dur = f"{v.duration_seconds//60}:{v.duration_seconds%60:02d}" if v.duration_seconds else "?"
            tag = " [topic]" if v.is_topic_channel else ""
            console.print(f"  {v.title} — {v.channel}{tag} · {dur} · {v.target_id}")
    else:
        for a in target.search_album(artist, title, limit=limit):
            year = f" ({a.year})" if a.year else ""
            console.print(f"  {a.artist} — {a.title}{year} · {a.target_id}")


if __name__ == "__main__":
    app()
