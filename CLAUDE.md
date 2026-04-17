# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Public Repo Policy

This is a public repository. Before editing any file, check that your changes contain no personally identifiable or sensitive information:

- No real Spotify IDs (user IDs, playlist IDs, track IDs, client IDs)
- No real names, email addresses, or usernames
- No hardcoded file paths that reveal a local machine username
- No tokens, secrets, or credentials of any kind
- Example/sample output in docs should use only well-known public artists/tracks — never real personal playlist data

The `.env` file (containing `SPOTIFY_CLIENT_ID` and `LASTFM_API_KEY`) is gitignored and must never be committed. Token files (`~/.spotify_classifier_tokens.json`) live outside the repo entirely.

## Running the tool

```bash
python main.py                                    # interactive playlist selection
python main.py --all --output-dir reports/        # save HTML reports for all playlists
python main.py --all --export-dir exports/        # also write per-playlist ML training exports
python main.py --if-contamination 0.02            # stricter Isolation Forest threshold
python main.py --no-tags                          # disable tag-based outlier detection
python curate.py exports/                         # curate all playlists in directory (batch session)
python curate.py exports/London.json              # curate single playlist
python curate.py exports/London.json --include-all  # also hunt for false negatives
python deploy_descriptions.py                     # deploy curated descriptions from output/DESCRIPTIONS.json to Spotify
```

No build step. No test suite. Dependencies: `pip install -r requirements.txt` (`python-dotenv`, `scikit-learn`). Requires `SPOTIFY_CLIENT_ID` and `LASTFM_API_KEY` in `.env` or via CLI flags.

First run opens a browser for Spotify PKCE OAuth. Token saved to `~/.spotify_classifier_tokens.json`, silently refreshed on subsequent runs.

## Architecture

### Data flow

```
classifier/auth.py ──► SpotifyClient ──► LastFmClient ──► classifier/profiler.py ──► classifier/scorer.py ──► classifier/report.py ────┐
                                                                                                           └──► classifier/export.py ──► curate.py
                                                                                                                                           ↓
                                                                                                     (curated descriptions) ← ← ← ─ output/DESCRIPTIONS.json
                                                                                                                                           ↓
                                                                                                                                    deploy_descriptions.py
```

1. `load_or_refresh_token()` — PKCE OAuth, token cached to disk
2. `SpotifyClient` — fetches playlists and tracks (with `added_at` timestamps)
3. `LastFmClient` — fetches artist+track tags from Last.fm, merges by max weight, normalizes to 0-1 (artist + track tag caches persist across playlists; both written to `~/.spotify_classifier_tag_cache.json` between runs)
4. `build_track_data()` — merges raw Spotify dicts + Last.fm tags into `TrackData` objects
5. `build_playlist_profile()` — computes year/duration medians, stddevs, explicit ratio → `PlaylistProfile`
6. `build_tag_vectors()` — builds full-vocabulary tag vectors from all classifiable tracks
7. `run_isolation_forest()` — trains on oldest max(20%, 35) tracks, predicts outliers for all → `{track_id: bool}`
8. `score_tracks()` — combines IF result with year/duration/explicit checks → `ScoredTrack` list
9. `format_html_report()` / `format_markdown_report()` / `print_report()` — three output formats
10. `build_playlist_export()` + `write_export()` — ML training JSON, merges with existing labels on re-run

### Core types (defined in `classifier/profiler.py` and `classifier/scorer.py`, imported everywhere)

- **`TrackData`** — per-track: tags (Last.fm, dict[str, float]), added_at, release_year, duration_ms, explicit, artist_ids, artist_names, unclassifiable (True when no tags)
- **`PlaylistProfile`** — playlist-level: year_median, year_stddev, duration_median, duration_stddev, explicit_ratio, total_tracks, classifiable_count, unclassifiable_tracks
- **`ScoredTrack`** — wraps TrackData with four `is_*_outlier` booleans (tag, year, duration, explicit), explanation string

### Detector set (4 signals)

| Signal | Source | Method |
|---|---|---|
| `is_tag_outlier` | Last.fm artist+track tags | Isolation Forest on tag vectors |
| `is_year_outlier` | Spotify release date | Stddev from median |
| `is_duration_outlier` | Spotify track duration | Stddev from median |
| `is_explicit_outlier` | Spotify explicit flag | Ratio threshold |

Tracks with no Last.fm tags are excluded from scoring (not flagged as outliers).

### Key design decisions

**Last.fm tag-based detection.** Spotify stripped genres, popularity, and followers from artist/track endpoints in February 2026. Tags come from `artist.getTopTags` + `track.getTopTags` via Last.fm API, merged by taking max weight per tag, normalized to 0-1. Artist tag cache (`LastFmClient._artist_tag_cache`) persists across playlists within a run.

**Isolation Forest for tag outliers.** `scikit-learn IsolationForest` trains on the oldest `max(20% of playlist, 35)` tracks (by `added_at` timestamp). Full tag vocabulary is built from ALL tracks (not just training set) so contaminated training data remains distinguishable. Contamination defaults to 0.16 (~1σ one-tailed; upper bound, not guarantee), configurable via `--if-contamination`. An **adaptive score gate** suppresses borderline predictions: the effective gate is `max(score_gate, score_gate_fraction * most_extreme_outlier_score)`, where `score_gate=-0.05` and `score_gate_fraction=0.5`. On large, genre-coherent playlists (e.g. 442-track EDM playlist) IF scores are compressed near 0 — a fixed gate would suppress all predictions, so the fraction-based component scales the gate relative to the run's actual score range. The net effect: only the most-anomalous half of IF-predicted outliers are flagged; coherent playlists surface fewer flags without silencing the detector entirely.

**Unclassifiable tracks** (no Last.fm tag data) are excluded from scoring and collected in `PlaylistProfile.unclassifiable_tracks` for a separate report section. They don't affect tag vectors or outlier detection.

**Flag disabling after scoring.** `_disable_flags()` in `main.py` zeroes out `ScoredTrack` flag fields after `score_tracks()` returns. Detection toggles (`--year`, `--no-tags`, etc.) are applied post-hoc rather than threading state through the scorer.

**False positive suppression.** `_apply_confirmed_labels()` in `main.py` reads `confirmed_outlier: false` labels written by `curate.py` and clears all flags for those tracks before report generation. Runs only when `--export-dir` is set. The `suggested_outlier` value in the export is never touched, preserving the model's original prediction as training signal.

**Spotify refresh token retention.** Spotify doesn't always return a new refresh token on refresh. `_save_tokens()` uses `token_data.get("refresh_token") or existing_refresh` to avoid losing the old one.

**Atomic writes.** Token storage (`classifier/auth.py`), export JSON (`classifier/export.py`), and curate saves (`curate.py`) all write to a `.tmp` file then `rename()` to the final path.

**Export label preservation.** `write_export()` merges by `playlist_id`; `confirmed_outlier` is only overwritten when the stored value is non-null. Re-running `main.py --export` never erases manual labels.

**Curated descriptions.** `classifier/report.py` loads playlist descriptions from `output/DESCRIPTIONS.json` if present and renders them in terminal/HTML/Markdown reports. `deploy_descriptions.py` is a utility that reads from the same JSON and deploys descriptions back to Spotify via `PUT /playlists/{id}`. First run creates a timestamped backup of current descriptions.

### ML pipeline

`--export training.json` produces JSON with per-track features (tags, duration, year, explicit) + `suggested_outlier` (statistical) + `confirmed_outlier: null` (to be filled). `curate.py` walks through suggested outliers sorted by flag count descending, writing y/n labels back after each keypress. Training target is `confirmed_outlier`; input features are track features + the `profile` block (exported per playlist) so a cross-playlist model has playlist context without retraining per playlist.

## Line Maps

### classifier/auth.py (234 lines)
| Lines | Section | Key contents |
|-------|---------|--------------|
| 1–19 | Imports + constants | base64, hashlib, secrets, threading, urllib, SPOTIFY_AUTH_URL, SPOTIFY_TOKEN_URL, SCOPE |
| 22–27 | Exceptions | `AuthError` |
| 30–38 | PKCE helpers | `generate_pkce_pair()` — verifier + base64url(sha256(verifier)) challenge |
| 41–56 | Auth URL builder | `build_auth_url()` — PKCE S256 authorization endpoint |
| 63–89 | Callback handler | `CallbackHandler` — HTTP GET handler for OAuth redirect, sends completion response |
| 95–129 | PKCE flow | `run_pkce_flow()` — opens browser, waits for callback on local server, handles state/error |
| 132–154 | Code exchange | `exchange_code()` — POST to token endpoint with PKCE verifier |
| 161–176 | Token refresh | `refresh_access_token()` — refresh grant flow |
| 183–225 | Token storage | `_save_tokens()` — atomic write to `~/.spotify_classifier_tokens.json`, retains old refresh if new not provided |
| 197–225 | Main entry | `load_or_refresh_token()` — disk load → silent refresh → full PKCE flow |
| 228–235 | Helpers | `_default_port()` — extract port from token file or default 8888 |

### classifier/lastfm_client.py (218 lines)
| Lines | Section | Key contents |
|-------|---------|--------------|
| 1–14 | Imports + constants | urllib, json, time, datetime, LASTFM_API_BASE, MAX_RETRIES, CACHE_TTL_DAYS |
| 19–28 | Constructor | `LastFmClient.__init__` — api_key, cache_path, artist+track tag caches, loads disk cache |
| 34–44 | Cache expiry | `_is_expired` — TTL check (90d for tagged, 7d for empty entries) |
| 46–79 | Disk cache | `_load_disk_cache`, `save_cache` — atomic JSON read/write to `~/.spotify_classifier_tag_cache.json` |
| 85–125 | Internal HTTP | `_get` — rate-limited GET with retry |
| 131–155 | Artist tags | `get_artist_tags` — cached by lowered name |
| 157–182 | Track tags | `get_track_tags` — cached by lowered `artist\x00track` key |
| 184–218 | Tag merger | `get_merged_tags` — accepts `list[str]` artists; tries joined form first for track lookup, falls back to primary |

### classifier/profiler.py (147 lines)
| Lines | Section | Key contents |
|-------|---------|--------------|
| 1–4 | Imports | statistics, dataclasses |
| 11–22 | TrackData dataclass | tags, added_at, release_year, duration_ms, explicit, artist_ids, artist_names, unclassifiable |
| 25–37 | PlaylistProfile dataclass | year/duration medians+stddevs, explicit_ratio, tag_averages, unclassifiable_tracks |
| 44–51 | extract_year | YYYY/YYYY-MM/YYYY-MM-DD parser |
| 54–60 | _median_stddev | (median, pstdev) helper |
| 67–100 | build_track_data | raw Spotify dicts + Last.fm tags → TrackData list |
| 103–147 | build_playlist_profile | year, duration, explicit stats + tag_averages from classifiable tracks |

### classifier/scorer.py (253 lines)
| Lines | Section | Key contents |
|-------|---------|--------------|
| 1–5 | Imports | PlaylistProfile, TrackData |
| 12–19 | ScoredTrack dataclass | 4 `is_*_outlier` flags + explanation |
| 26–48 | build_tag_vectors | full-vocabulary vectors from all classifiable tracks |
| 51–105 | run_isolation_forest | train on oldest max(20%, 35), predict all; adaptive score gate |
| 108–116 | _stddev_outlier | threshold-based numeric outlier check |
| 119–128 | _compute_explicit_outlier | ratio-based explicit flag check |
| 131–180 | _build_explanation | distinctive/missing tag analysis + era/duration/explicit detail |
| 187–224 | score_tracks | combines IF + stddev + explicit checks; leaves explanation="" |
| 227–253 | build_explanations | fills explanation strings; call after _disable_flags() |

### classifier/report.py (526 lines)
| Lines | Section | Key contents |
|-------|---------|--------------|
| 1–10 | Imports | html, shutil, sys, textwrap, PlaylistProfile, ScoredTrack |
| 16–25 | Curated descriptions | load from `output/DESCRIPTIONS.json` if available |
| 30–50 | Shared helpers | `_bar`, `_term_width`, `_double_line`, `_single_line`, `_fmt_ms` |
| 60–80 | render_header | playlist name + track counts |
| 82–105 | render_theme_summary | year, duration, explicit stats + curated description if present |
| 107–165 | render_outliers | 4-flag table with explanations |
| 167–175 | render_unclassifiable | "no tag data" section |
| 182–190 | _all_outliers | filter to tracks with any flag set |
| 200–238 | format_report + print_report | terminal text output |
| 240–280 | HTML constants + helpers | `_HTML_CSS`, `_h`, `_bar_html` |
| 290–450 | format_html_report | self-contained HTML with 4-flag table + curated description |
| 460–526 | format_markdown_report | pipe tables with 4 flags + curated description |

### classifier/export.py (104 lines)
| Lines | Section | Key contents |
|-------|---------|--------------|
| 1–8 | Imports | json, datetime, PlaylistProfile, ScoredTrack |
| 11–70 | build_playlist_export | profile dict + track features (tags, duration, year, explicit) + 4 flags |
| 73–104 | write_export | merge by playlist_id, preserve confirmed_outlier labels |

### classifier/spotify_client.py (177 lines)
| Lines | Section | Key contents |
|-------|---------|--------------|
| 1–12 | Imports + constants | urllib, SPOTIFY_API_BASE, ARTIST_BATCH_SIZE |
| 19–22 | SpotifyAPIError | exception class |
| 29–35 | SpotifyClient.__init__ | token, artist_cache, audio_analysis_available flag |
| 41–72 | _get | HTTP GET with retry + rate-limit handling |
| 78–82 | get_current_user_id | GET /me → current user's ID |
| 84–100 | get_user_playlists | paginated playlist fetch, filtered to owner-only |
| 102–131 | get_playlist_tracks | paginated track fetch with added_at |
| 133–157 | get_artist_data + _fetch_artists_batch | individual artist fetches with cache |
| 159–177 | get_audio_analysis | optional audio analysis (auto-disable on 403) |

### main.py (359 lines)
| Lines | Section | Key contents |
|-------|---------|--------------|
| 1–19 | Imports | classifier.auth/export/lastfm_client/profiler/report/scorer/spotify_client |
| 26–43 | Config dataclass | if_contamination, lastfm_api_key, detection toggles |
| 46–97 | load_config | env + arg resolution, LASTFM_API_KEY required |
| 103–132 | select_playlists_interactive | numbered list + comma input |
| 139–189 | Helpers | `_safe_filename`, `_disable_flags`, `_apply_confirmed_labels` |
| 196–225 | CLI argument definitions | --if-contamination, --no-tags, --lastfm-api-key |
| 226–359 | Analysis loop | auth, Last.fm tag fetch, IF train/predict, _disable_flags → build_explanations, reports, export |

### deploy_descriptions.py (94 lines)
| Lines | Section | Key contents |
|-------|---------|--------------|
| 1–19 | Imports + constants | json, urllib, datetime, Path, classifier.auth, SpotifyClient, DESCRIPTIONS_FILE, BACKUP_DIR |
| 20–40 | PUT handler | `put_playlist_description()` — HTTP PUT to `/playlists/{id}` with description JSON |
| 41–94 | main | load token, fetch playlists, backup current descriptions, load new descriptions from JSON, deploy with error handling |

### curate.py (319 lines)
| Lines | Section | Key contents |
|-------|---------|--------------|
| 1–8 | Imports | argparse, json, shutil |
| 14–53 | _getch | Win32 msvcrt + Unix tty + line-input fallback |
| 59–66 | Load / save | `_load`, `_save` (atomic write) |
| 72–82 | Display helpers | `_width`, `_fmt_ms`, `_FLAG_LABELS` (4 entries) |
| 85–133 | _render_track | per-flag contextual detail (tags, era, duration, explicit) |
| 139–166 | _tracks_to_review | ordering by flag count descending |
| 169–238 | _curate_playlist | y/n/s/p/q, per-annotation atomic save |
| 244–319 | main | arg parsing, playlist filter, session summary |
