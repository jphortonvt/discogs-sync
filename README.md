# discogs-sync

Sync your [Discogs](https://www.discogs.com) record collection to a streaming service, with a review step so nothing gets added behind your back.

**v1: Discogs → YouTube Music.**

It pulls your collection, fuzzy-matches each release against YouTube Music's catalogue, scores the candidates, and shows you the results in a local web UI. You tick what's right, click **Apply**, and it writes those albums to your YTM library. Releases with no album match can fall back to a full-album YouTube video saved into a playlist.

```
┌─────────────┐    ┌──────────────┐    ┌─────────────────┐
│  Discogs    │───▶│   Matcher    │───▶│   Review UI     │
│  Adapter    │    │ + Normalizer │    │  (FastAPI/HTMX) │
└─────────────┘    └──────────────┘    └────────┬────────┘
                          │                     │ you confirm
                          ▼                     ▼
                   ┌──────────────┐    ┌─────────────────┐
                   │  Sync State  │◀───│  YTM Adapter    │
                   │  (SQLite)    │    │  (add + video)  │
                   └──────────────┘    └─────────────────┘
```

---

## Requirements

- Python 3.11+
- A Discogs account with a collection
- A Google account signed in to YouTube Music
- macOS, Linux, or WSL (uses your OS keyring for token storage)

## Install

```bash
git clone https://github.com/<your-username>/discogs-sync.git
cd discogs-sync
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

That puts a `discogs-sync` command on your PATH while the venv is active.

Verify:

```bash
discogs-sync --help
```

---

## Setup

There are two things to authorize: Discogs (step 1) and YouTube Music (step 2).

### 1. Discogs

Register a developer app at <https://www.discogs.com/settings/developers> → **Create an Application**.

- **Application URL** / **Callback URL**: any valid URL (e.g. `http://localhost`). This CLI uses PIN-based out-of-band OAuth, so Discogs ignores the callback and shows you a verification code on its own page instead.

Give the CLI your app credentials, either as environment variables:

```bash
export DISCOGS_CONSUMER_KEY=your_key_here
export DISCOGS_CONSUMER_SECRET=your_secret_here
```

…or in `~/.config/discogs-sync/config.toml` (copy [`config.example.toml`](config.example.toml) as a starting point):

```toml
[discogs]
consumer_key = "your_key_here"
consumer_secret = "your_secret_here"
```

Then authorize:

```bash
discogs-sync auth login discogs
```

A browser opens, you click **Authorize** on Discogs, and paste the verification code back into the terminal. Your access tokens go into your **OS keyring**, never into a file in this repo.

### 2. YouTube Music (browser headers — recommended)

YouTube Music has no official public API. The OAuth path through `ytmusicapi` is currently broken upstream — YouTube rejects ytmusicapi-style OAuth bearer tokens against its internal `youtubei/v1` endpoints. Browser headers work reliably, because the requests then look identical to your real browser traffic.

1. Open <https://music.youtube.com> in Chrome or Firefox, signed in.
2. Open DevTools (F12) → **Network** tab → reload the page.
3. In the filter box type `browse`. Click any POST request to `music.youtube.com/youtubei/v1/browse?…`.
4. Right-click the request → **Copy → Copy as cURL (bash)** (Chrome), or **Copy → Copy Request Headers** (Firefox).
5. Save it to a temporary file:

   ```bash
   pbpaste > ~/yt-headers.txt     # macOS
   # xclip -o > ~/yt-headers.txt  # Linux
   ```

6. Load it in, then delete the file:

   ```bash
   discogs-sync auth login ytmusic-browser --file ~/yt-headers.txt
   rm ~/yt-headers.txt
   ```

   The headers are moved into your OS keyring, so the temp file is no longer needed.

7. Check both services are connected:

   ```bash
   discogs-sync auth status
   discogs-sync ytm-search "Slint" "Spiderland"
   ```

> **Note:** these headers contain a cookie tied to your Google session. Treat that file like a password while it exists, and delete it once loaded. If you sign out of YouTube Music, or the session expires (usually months), repeat steps 1–6.

<details>
<summary>Optional: OAuth instead of browser headers (currently broken upstream)</summary>

If `ytmusicapi` fixes the OAuth path, you can use `auth login ytmusic`. It needs Google Cloud setup: enable the YouTube Data API v3, create a **TVs and Limited Input devices** OAuth client, set `YTMUSIC_CLIENT_ID` and `YTMUSIC_CLIENT_SECRET`, and add yourself as a test user on the OAuth consent screen.

</details>

---

## Usage

### The normal workflow: review, then apply

```bash
discogs-sync review --folder Uncategorized --limit 50
```

This opens <http://localhost:8765> with the next batch of releases. Nothing is written to YouTube Music until you click **Apply selected**.

The UI has four sections:

| Section | Score | Default |
|---|---|---|
| **Strong matches** | ≥ 0.90, clear of runner-up | pre-checked |
| **Needs review** | 0.70 – 0.89 | pre-checked at ≥ 0.75 |
| **No album match** | — | opt-in YouTube video fallback |
| **Already in your YTM library** | — | collapsed, informational |

For **Needs review** rows, each shows up to 3 candidates as radio buttons — pick one and the row arms itself.

Apply writes albums one at a time with a live progress bar. Progress is tracked in SQLite, so re-running `review` picks up the next batch automatically, and closing the tab preserves your picks. The **"Next batch →"** button skips the current set.

### Other commands

```bash
# Inspect your Discogs collection
discogs-sync folders
discogs-sync dump --folder Uncategorized --limit 5 -o sample.json

# Probe YTM search directly
discogs-sync ytm-search "Slint" "Spiderland"
discogs-sync ytm-search "Some Obscure Band" "Lost Album" --videos

# Match a folder and record results, without writing anything
discogs-sync sync ytmusic --folder Uncategorized

# Run summaries
discogs-sync report show
discogs-sync report dir
```

### Headless bulk apply

For when you trust the matcher and want to power through a large folder:

```bash
# Preview first
discogs-sync sync ytmusic --folder Uncategorized --apply --dry-run --limit 1000

# Then for real
discogs-sync sync ytmusic --folder Uncategorized --apply --yes --limit 1000
```

This auto-applies anything ≥ `autoselect_threshold` (default 0.75), adds video fallbacks ≥ `video_threshold` (default 0.60), and marks the rest skipped. `--redo` re-processes items already recorded in state.

This path writes to your library without showing you the matches first — `--dry-run` is worth doing.

### Reports

Every apply (CLI or UI) writes a markdown + JSON report to:

```
~/.local/share/discogs-sync/reports/report-<timestamp>-run<N>.{md,json}
```

---

## Configuration

Config lives at `~/.config/discogs-sync/config.toml`. See [`config.example.toml`](config.example.toml) for a documented template.

```toml
[discogs]
username = ""              # auto-filled on first login
default_folder = "Uncategorized"
consumer_key = ""
consumer_secret = ""

[match]
strong_threshold = 0.90    # >= this AND clear of runner-up by 0.05 -> "strong"
review_threshold = 0.70    # >= this otherwise -> "needs review"
video_threshold = 0.60     # >= this for a video fallback to surface
autoselect_threshold = 0.75

[ytmusic]
video_fallback_playlist = "Discogs — Video Only"

[state]
db_path = "~/.local/share/discogs-sync/state.db"
```

Environment variables override config when set: `DISCOGS_CONSUMER_KEY`, `DISCOGS_CONSUMER_SECRET`, `YTMUSIC_CLIENT_ID`, `YTMUSIC_CLIENT_SECRET`. `DISCOGS_SYNC_CONFIG_DIR` relocates the config directory.

## Where your credentials live

| What | Where |
|---|---|
| Discogs & YTM access tokens | OS keyring (Keychain / Secret Service), via `keyring` |
| App credentials (consumer key/secret) | env vars, or `~/.config/discogs-sync/config.toml` |
| Sync state | `~/.local/share/discogs-sync/state.db` |

All of it sits outside this repository, and `.gitignore` additionally blocks `config.toml`, `.env`, header dumps, and `*.db` so none of it can be committed by accident.

---

## Development

```bash
source .venv/bin/activate
pytest          # 48 tests
ruff check .
```

If `pytest` reports `ModuleNotFoundError: No module named 'discogs_sync'`, the editable install is stale — re-run `pip install -e ".[dev]"`, or run `PYTHONPATH=src pytest`.

### Layout

```
src/discogs_sync/
  adapters/      CollectionSource implementations (Discogs)
  targets/       LibraryTarget implementations (YouTube Music)
  review/        FastAPI + HTMX review UI
  cli/           Typer CLI entry point
  matcher.py     scoring and bucketing
  normalize.py   artist/title normalization
  engine.py      SyncEngine orchestration
  state.py       SQLite state
  config.py      config file + env resolution
  secrets.py     OS keyring wrapper
```

Core types: `Release` (normalized model), `MatchResult` (`{release, candidates, best, bucket, video_candidate?}`), `SyncEngine` (orchestration), `State` (SQLite tables `sync_runs`, `release_status`, `review_session`, `review_selections`).

### Adding another streaming service

1. Implement `LibraryTarget` in `src/discogs_sync/targets/<service>.py`. See `ytmusic.py` for the contract: `search_album`, `search_album_video`, `list_library_albums`, `add_album_to_library`, `ensure_playlist`, `add_video_to_playlist`.
2. Add credential plumbing in `config.py` and `secrets.py`.
3. Wire up `auth login <service>` in `cli/main.py`.
4. Add a case in `cli/main.py:sync_cmd` (and `review/server.py` to get it in the UI).

The matcher, state, and UI all work against the abstract interface, so they need no changes.

## Status

| Milestone | Scope | Done |
|---|---|---|
| **M1** | Discogs OAuth + adapter + `Release` model + `dump` | ✓ |
| **M2** | YouTube Music adapter (read-only): search albums/videos, list library | ✓ |
| **M3** | Matcher + scoring + buckets + SQLite state + `SyncEngine` + `sync` CLI | ✓ |
| **M4** | FastAPI + HTMX review UI | ✓ |
| **M5** | Write path: add albums, video fallback playlist, apply flow + reports | ✓ |
| **M6** | Polish: packaging, walkthrough, config UI | in progress |

## Caveats

- YouTube Music has no official public API. This leans on `ytmusicapi` and browser-session headers, which can break when YouTube changes its internal endpoints.
- Matching is fuzzy. Reissues, box sets, compilations, and classical releases are the usual troublemakers — which is exactly why the review UI exists.
- Discogs rate-limits authenticated requests; large collections take a while to pull.

## License

MIT — see [LICENSE](LICENSE).
