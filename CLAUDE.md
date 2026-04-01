# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the tool

```bash
python main.py                                    # interactive playlist selection
python main.py --all --output-dir reports/        # save HTML reports for all playlists
python main.py --all --export training.json       # also write ML training export
python curate.py training.json                    # interactively label tracks
python curate.py training.json --include-all      # also hunt for false negatives
```

No build step. No test suite. Dependencies: `pip install -r requirements.txt` (`requests`, `python-dotenv`). Requires `SPOTIFY_CLIENT_ID` in `.env` or via `--client-id`.

First run opens a browser for Spotify PKCE OAuth. Token saved to `~/.spotify_classifier_tokens.json`, silently refreshed on subsequent runs.

## Architecture

### Data flow

```
auth.py ──► SpotifyClient ──► profiler.py ──► scorer.py ──► report.py
                                                         └──► export.py ──► curate.py
```

1. `load_or_refresh_token()` — PKCE OAuth, token cached to disk
2. `SpotifyClient` — fetches playlists, tracks, artist data (session-cached), optional audio analysis
3. `build_track_data()` — merges raw API dicts into `TrackData` objects
4. `build_playlist_profile()` — computes genre weights, medians, stddevs → `PlaylistProfile`
5. `score_tracks()` — produces `ScoredTrack` list with 8 outlier flags + human-readable explanations
6. `format_html_report()` / `format_markdown_report()` / `print_report()` — three output formats
7. `build_playlist_export()` + `write_export()` — ML training JSON, merges with existing labels on re-run

### Core types (defined in `profiler.py` and `scorer.py`, imported everywhere)

- **`TrackData`** — per-track: genres, energy_tier, release_year, popularity, duration_ms, explicit, artist_popularity, artist_followers, tempo
- **`PlaylistProfile`** — playlist-level: genre_weights, top_genres, dominant_energy_tier, median+stddev for year/popularity/duration/artist_popularity/tempo, explicit_ratio, unclassifiable_tracks
- **`ScoredTrack`** — wraps TrackData with fit_score, eight `is_*_outlier` booleans, explanation string

### Key design decisions

**Audio features unavailable for new Spotify apps** (post Nov 27, 2024). Energy/vibe detection uses a genre-keyword proxy (`ENERGY_TIER_MAP` in `profiler.py`). Audio analysis (BPM) is opt-in via `--audio-analysis`; `SpotifyClient._audio_analysis_available` is set False on first 403/404, skipping all subsequent calls rather than hammering a restricted endpoint.

**Unclassifiable tracks** (no artist genre data) are excluded from scoring and collected in `PlaylistProfile.unclassifiable_tracks` for a separate report section. They don't affect genre weights or outlier detection.

**Flag disabling after scoring.** `_disable_flags()` in `main.py` zeroes out `ScoredTrack` flag fields after `score_tracks()` returns. Detection toggles (`--no-year`, `--no-vibe`, etc.) are applied post-hoc rather than threading state through the scorer.

**Fit score formula.** `sum(genre_weights[g] for g in track.genres) / max(len(genres), 1)`. Genre outlier = fit score in bottom N-th percentile of classifiable tracks.

**Artist cache.** `SpotifyClient._artist_cache` persists across all playlists in one run (genres, popularity, followers). Only uncached IDs are fetched per playlist.

**Spotify refresh token retention.** Spotify doesn't always return a new refresh token on refresh. `_save_tokens()` uses `token_data.get("refresh_token") or existing_refresh` to avoid losing the old one.

**Atomic writes.** Token storage (`auth.py`), export JSON (`export.py`), and curate saves (`curate.py`) all write to a `.tmp` file then `rename()` to the final path.

**Export label preservation.** `write_export()` merges by `playlist_id`; `confirmed_outlier` is only overwritten when the stored value is non-null. Re-running `main.py --export` never erases manual labels.

### ML pipeline

`--export training.json` produces JSON with per-track features + `suggested_outlier` (statistical) + `confirmed_outlier: null` (to be filled). `curate.py` walks through suggested outliers, writing y/n labels back after each keypress. Training target is `confirmed_outlier`; input features are track features + the `profile` block (exported per playlist) so a cross-playlist model has playlist context without retraining per playlist.

## Line Maps

### export.py (~124 lines)
| Lines | Section | Key contents |
|-------|---------|--------------|
| 1–8 | Imports | json, datetime, PlaylistProfile, ScoredTrack |
| 11–85 | Playlist export builder | `build_playlist_export` |
| 88–124 | Export file writer | `write_export` |

### main.py (~336 lines)
| Lines | Section | Key contents |
|-------|---------|--------------|
| 1–17 | Imports | auth, export, profiler, report, scorer, spotify_client |
| 20–47 | Config dataclass | `Config` — all CLI-settable fields with defaults |
| 49–101 | Config loader | `load_config` — env + arg resolution, format defaulting |
| 104–137 | Interactive playlist selector | `select_playlists_interactive` |
| 140–165 | Helpers | `_safe_filename`, `_disable_flags` |
| 168–207 | CLI argument definitions | `main` — parser setup, all `--flags` |
| 208–335 | Analysis loop & output | auth, playlist fetch, per-playlist scoring, export write |

### report.py (~484 lines)
| Lines | Section | Key contents |
|-------|---------|--------------|
| 1–10 | Imports | html, shutil, textwrap, PlaylistProfile, ScoredTrack |
| 13–39 | Shared helpers | `_bar`, `_term_width`, `_double_line`, `_single_line`, `_fmt_ms` |
| 42–160 | Terminal section renderers | `render_header`, `render_theme_summary`, `render_outliers`, `render_unclassifiable` |
| 163–213 | Terminal public API | `_all_outliers`, `format_report`, `print_report` |
| 216–263 | HTML constants & helpers | `_HTML_CSS`, `_h`, `_fit_class`, `_bar_html` |
| 265–394 | HTML renderer | `format_html_report` |
| 397–484 | Markdown renderer | `format_markdown_report` |

### profiler.py (~255 lines)
| Lines | Section | Key contents |
|-------|---------|--------------|
| 1–7 | Imports | statistics, dataclasses |
| 10–24 | Energy tier config | `ENERGY_TIER_MAP`, `_TIER_ORDER` |
| 27–48 | TrackData dataclass | all track-level fields inc. tempo, artist_popularity |
| 51–72 | PlaylistProfile dataclass | all playlist-level stats inc. medians, stddevs |
| 75–110 | Helpers | `extract_year`, `assign_energy_tier`, `_median_stddev` |
| 113–182 | Track builder | `build_track_data` |
| 185–255 | Profile builder | `build_playlist_profile` |

### scorer.py (~197 lines)
| Lines | Section | Key contents |
|-------|---------|--------------|
| 1–10 | Imports | PlaylistProfile, TrackData |
| 12–24 | ScoredTrack dataclass | 8 `is_*_outlier` flags + explanation |
| 27–59 | Outlier helpers | `_stddev_outlier`, `_compute_fit_score`, `_compute_explicit_outlier` |
| 62–121 | Explanation builder | `_build_explanation` |
| 124–197 | Public API | `score_tracks` |

### curate.py (~339 lines)
| Lines | Section | Key contents |
|-------|---------|--------------|
| 1–8 | Imports | argparse, json, shutil |
| 11–50 | Keypress input | `_getch` — Win32 msvcrt + Unix tty + line-input fallback |
| 53–64 | Load / save | `_load`, `_save` (atomic write) |
| 67–89 | Display helpers | `_width`, `_fmt_ms`, `_FLAG_LABELS` |
| 92–153 | Track renderer | `_render_track` — per-flag contextual detail with profile medians |
| 156–186 | Queue builder | `_tracks_to_review` — ordering + eligibility filter |
| 189–261 | Playlist curation loop | `_curate_playlist` — y/n/s/p/q, per-annotation atomic save |
| 264–339 | Entry point | `main` — arg parsing, playlist filter, session summary |
