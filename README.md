# Spotify Playlist Outlier Analyzer

Identifies tracks that don't fit each playlist's theme using **Last.fm tag-based genre fingerprinting** and **4-signal anomaly detection**: tags (Isolation Forest), era, duration, and explicit ratio.

## Quick Start

### Setup (one time)

**1. Get credentials**

- [Spotify Developer Dashboard](https://developer.spotify.com/dashboard): Create an app, add redirect URI `http://127.0.0.1:8888/callback`, copy **Client ID**
- [last.fm/api/account/create](https://www.last.fm/api/account/create): Create API account (free), copy **API key**

**2. Configure**

```bash
cp .env.example .env
# Edit .env with your Client ID and Last.fm API key
pip install -r requirements.txt
```

First run opens your browser for Spotify OAuth. Token is cached locally; silently refreshed on subsequent runs.

### Analyze playlists

```bash
python main.py                          # Interactive playlist selection
python main.py --all                    # Analyze all playlists
python main.py --all --export-dir exports/  # Save ML training exports
```

Reports saved to `output/` as HTML (default). Add `--format md|txt` to change.

## Detection Signals

Each track is scored against four independent detectors:

| Signal | Source | Method |
|--------|--------|--------|
| **Tag outlier** | Last.fm artist+track tags | Isolation Forest on tag vectors, trained on oldest-added tracks |
| **Era outlier** | Spotify release date | Release year deviation from playlist median (disable by default) |
| **Duration outlier** | Spotify track length | Track duration deviation from playlist median |
| **Explicit outlier** | Spotify explicit flag | Contradicts playlist's explicit ratio (disable by default) |

A track is flagged if **any active detector** triggers. Tracks with no Last.fm tags are excluded from all scoring and listed separately.

## Correction Pipeline

### Step 1 — Generate exports

```bash
python main.py --all --export-dir exports/
```

Creates one JSON file per playlist (`exports/London.json`, `exports/Paris.json`, etc.) with:
- `suggested_outlier: true` for flagged tracks
- `confirmed_outlier: null` for manual review
- All track features and statistical signals

Re-running merges new results; existing labels are preserved.

### Step 2 — Curate false positives

**Batch session (all playlists):**
```bash
python curate.py exports/
```

**Single playlist:**
```bash
python curate.py exports/London.json
```

**Interactive controls:**
| Key | Action |
|-----|--------|
| `y` | Confirm outlier |
| `n` | Mark as false positive |
| `s` | Skip |
| `p` | Jump to next playlist |
| `q` | Quit |

Labels are saved atomically after each keypress. Session ends with precision stats (% of suggestions confirmed).

**Curation flags:**
- `--include-all`: Surface non-flagged tracks to catch false negatives
- `--re-review`: Revisit already-labeled tracks
- `--playlist ID`: Limit to one playlist (repeatable)

### Step 3 — Suppress and deploy

**Re-run analysis:**
```bash
python main.py --all --export-dir exports/
```

Tracks marked `confirmed_outlier: false` are automatically suppressed from reports.

**Deploy curated descriptions to Spotify:**
```bash
python deploy_descriptions.py
```

Reads descriptions from `output/DESCRIPTIONS.json` and updates Spotify playlists via API. Creates timestamped backup of current descriptions.

## Advanced Options

```bash
python main.py --all [FLAGS]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--output-dir DIR` | `output/` | Report output directory |
| `--format FMT` | `html` | Report format: `html`, `md`, or `txt` |
| `--export-dir DIR` | — | Save ML exports here (required for curation) |
| `--if-contamination N` | `0.05` | Isolation Forest contamination (0.0–1.0) |
| `--duration-threshold N` | `2.0` | Duration stddev multiplier |
| `--year` | disabled | Enable era detection |
| `--year-threshold N` | `2.0` | Era stddev multiplier |
| `--explicit` | disabled | Enable explicit ratio detection |
| `--no-tags` | enabled | Disable tag-based detection |
| `--no-duration` | enabled | Disable duration detection |
| `--lastfm-api-key KEY` | `.env` | Override Last.fm API key |
| `--client-id ID` | `.env` | Override Spotify Client ID |
| `--token-file PATH` | `~/.spotify_classifier_tokens.json` | Token storage |
| `--redirect-port N` | `8888` | OAuth callback port |

## Architecture

```
├── main.py                  # CLI, orchestration, analysis loop
├── curate.py                # Interactive false-positive labeler
├── deploy_descriptions.py   # Deploy curated descriptions to Spotify
├── classifier/
│   ├── auth.py              # PKCE OAuth, token refresh
│   ├── spotify_client.py    # Playlist/track fetching, pagination
│   ├── lastfm_client.py     # Tag fetching, disk cache
│   ├── profiler.py          # Profile computation, track data
│   ├── scorer.py            # Isolation Forest, stddev checks
│   ├── report.py            # HTML/Markdown/text rendering
│   └── export.py            # ML export format, label preservation
├── requirements.txt
├── .env.example
└── README.md
```

**Data flow:**
```
Spotify API → Last.fm API → Tag vectors + track data
                 ↓
          Isolation Forest training (oldest 20–35%)
                 ↓
Score all tracks (4 signals) → HTML/Markdown/text reports
                 ↓
Export JSON (with labels for curation)
                 ↓
curate.py (manual review, label preservation)
                 ↓
Re-run suppresses false positives → deploy_descriptions.py
```

## Performance & Caching

- **First run:** ~60 seconds for a 140-track playlist (2 API calls per track, 5 req/s throttled)
- **Subsequent runs:** Near-instant (Last.fm tags cached to `~/.spotify_classifier_tag_cache.json`, expires 90 days)
- **Large playlists:** Isolation Forest adapts — see `--if-contamination` to tune outlier sensitivity

## Known Limitations

**Spotify broke genres in Feb 2026.** The `GET /v1/artists/{id}` endpoint no longer returns `genres`, `popularity`, or `followers` for Development Mode apps. Replaced with Last.fm tag vectors + Isolation Forest.

**Era & explicit detection disabled by default.** Both create false positives on thematic playlists (a 1960s track fits "London vibe"; explicit tracks fit hip-hop). Enable with `--year` and `--explicit` only for playlists where those dimensions matter.

**Last.fm tag coverage varies.** Niche or very new artists may have sparse data. Tracks with no tags are excluded from scoring and listed as "No Tag Data" in reports.
