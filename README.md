# Spotify Playlist Outlier Analyzer

Analyzes your Spotify playlists and identifies tracks that don't fit each playlist's theme, using Last.fm tag-based genre fingerprinting and Isolation Forest anomaly detection.

## How It Works

1. **Fetches playlists and tracks** from the Spotify API
2. **Fetches genre tags** for each artist and track from Last.fm (`artist.getTopTags` + `track.getTopTags`), merged by max weight and normalized to 0–1
3. **Builds tag vectors** for every classifiable track and trains an Isolation Forest on the oldest-added tracks (assumed to represent the playlist's core intent)
4. **Scores all tracks** — tags (Isolation Forest), duration, and optionally year/explicit
5. **Saves reports** to `output/` (HTML by default; use `--format md|txt` to change) and prints a text summary to the terminal

A track is flagged as an outlier if it triggers any active detector. Tracks with no Last.fm tag data are excluded from scoring and listed separately. Confirmed false positives from a prior curation session are suppressed automatically (see [Correction Pipeline](#correction-pipeline)).

## Setup

### 1. Get a Spotify Client ID

1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Create a new app
3. Under **Redirect URIs**, add: `http://127.0.0.1:8888/callback`
4. Copy your **Client ID**

### 2. Get a Last.fm API key

1. Go to [last.fm/api/account/create](https://www.last.fm/api/account/create)
2. Create an API account (free, personal use permitted)
3. Copy your **API key**

### 3. Configure credentials

```bash
cp .env.example .env
```

Edit `.env`:

```
SPOTIFY_CLIENT_ID=your_client_id_here
LASTFM_API_KEY=your_lastfm_api_key_here
```

### 4. Install dependencies

```bash
pip install -r requirements.txt
```

## Usage

```bash
python main.py
```

Run without options for interactive playlist selection. Reports are saved to `output/` as HTML and printed to the terminal.

### Options

| Flag | Description |
|------|-------------|
| `--all` | Analyze all playlists |
| `--playlist ID` | Analyze a specific playlist by ID (repeatable) |
| `--output-dir DIR` | Save reports here (default: `output/`) |
| `--format html\|md\|txt` | Saved report format (default: `html`) |
| `--export-dir DIR` | Save per-playlist ML training exports here; reads confirmed labels on re-run to suppress false positives |
| `--if-contamination FLOAT` | Isolation Forest contamination parameter (default: `0.05`) |
| `--duration-threshold FLOAT` | Stddev multiplier for duration outliers (default: `2.0`) |
| `--year` | Enable era outlier detection (off by default) |
| `--year-threshold FLOAT` | Stddev multiplier for era outliers when `--year` is set (default: `2.0`) |
| `--explicit` | Enable explicit flag outlier detection (off by default) |
| `--no-tags` | Disable tag-based Isolation Forest detection |
| `--no-duration` | Disable duration outlier detection |
| `--lastfm-api-key KEY` | Last.fm API key (overrides `.env`) |
| `--client-id ID` | Spotify Client ID (overrides `.env`) |
| `--token-file PATH` | Token storage location (default: `~/.spotify_classifier_tokens.json`) |
| `--redirect-port INT` | OAuth callback port (default: `8888`) |

### First run

Your browser will open for Spotify OAuth. After authorizing, the token is saved to `~/.spotify_classifier_tokens.json` and silently refreshed on subsequent runs.

Last.fm tags are cached to `~/.spotify_classifier_tag_cache.json`. A 140-track playlist takes ~60 seconds on the first run (~2 API calls per track at 5 req/s); subsequent runs on the same playlist are near-instant.

## Correction Pipeline

The tool includes an offline labeling loop for suppressing false positives over time.

### Step 1 — Run with export

```bash
python main.py --export-dir exports/
```

Each playlist gets its own file: `exports/London.json`, `exports/Chill.json`, etc. Each flagged track gets `suggested_outlier: true` and `confirmed_outlier: null`. Re-running merges new results with existing labels — confirmed labels are never overwritten.

### Step 2 — Label false positives

```bash
python curate.py exports/London.json
```

Walk through suggested outliers and press:

| Key | Action |
|-----|--------|
| `y` | Confirm outlier |
| `n` | Mark as false positive |
| `s` | Skip (review later) |
| `p` | Jump to next playlist |
| `q` | Quit |

Labels are saved atomically after each keypress.

| Flag | Effect |
|------|--------|
| `--include-all` | Also surface non-flagged tracks to catch false negatives |
| `--re-review` | Revisit tracks that already have a confirmed label |
| `--playlist ID` | Limit the session to a specific playlist (repeatable) |

At the end of each session, `curate.py` prints precision stats: how many of the model's suggestions you confirmed vs. rejected.

### Step 3 — Re-run with suppression

```bash
python main.py --export-dir exports/
```

Tracks you marked `confirmed_outlier: false` are automatically suppressed from the report. The export reflects the same set of flagged tracks as the HTML — only detectors you had enabled at run time contribute to `suggested_outlier`.

## Project Structure

```
spotify-playlist-classifier/
├── main.py                # Entry point, CLI args, orchestration
├── curate.py              # Interactive false-positive labeler
├── requirements.txt
├── .env.example
├── classifier/
│   ├── auth.py            # PKCE OAuth flow, token storage/refresh
│   ├── spotify_client.py  # Spotify API: playlists, tracks, pagination
│   ├── lastfm_client.py   # Last.fm API: artist/track tags, disk cache
│   ├── profiler.py        # TrackData, PlaylistProfile, tag averages
│   ├── scorer.py          # Isolation Forest, stddev checks, explanations
│   ├── report.py          # Text/HTML/Markdown report rendering
│   └── export.py          # ML training JSON export and merge
└── docs/
    └── spotify_AI-assistant_prompt.txt
```

## Known Limitations

**Spotify stripped genres in February 2026.** `GET /v1/artists/{id}` no longer returns `genres`, `popularity`, or `followers` for Development Mode apps (requires 250,000 MAUs + registered business entity for Extended Quota Mode). Genre-based detection has been replaced by Last.fm tag vectors + Isolation Forest.

**Era and explicit detection are off by default.** Both signals generate false positives on thematic playlists (a 1960s track can fit a London vibe; an explicit track can fit a hip-hop playlist). Enable them with `--year` and `--explicit` for playlists where those dimensions are meaningful.

**Last.fm tag coverage.** Niche or very new artists may have sparse or no tag data. Tracks with no tags are excluded from scoring and listed in the report under "No Tag Data."
