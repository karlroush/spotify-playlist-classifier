# Spotify Playlist Outlier Analyzer

Analyzes your private Spotify playlists and identifies tracks that don't fit each playlist's theme. Output is a read-only terminal report showing a genre/energy summary and a ranked outlier list.

## How It Works

Since the Spotify audio features endpoint is unavailable for new developer apps (post Nov 27, 2024), theme detection is based on:

- **Artist genres** — pulled from the Spotify API and union'd across all artists on each track
- **Energy tier proxy** — genre keywords mapped to HIGH / MEDIUM / LOW (e.g. "edm" → HIGH, "ambient" → LOW)
- **Release year** — outliers flagged when a track's year deviates more than 1.5σ from the playlist median

A track is flagged as an outlier if it triggers *any* of: genre mismatch, vibe mismatch, or era mismatch.

## Setup

### 1. Get a Spotify Client ID

1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Create a new app
3. Under **Redirect URIs**, add: `http://127.0.0.1:8888/callback`
4. Copy your **Client ID**

### 2. Configure credentials

```
cp .env.example .env
```

Edit `.env` and fill in your Client ID:

```
SPOTIFY_CLIENT_ID=your_client_id_here
```

### 3. Install dependencies

```
pip install -r requirements.txt
```

## Usage

```
python main.py [OPTIONS]
```

Run without options for interactive playlist selection:

```
python main.py
```

### Options

| Flag | Description |
|------|-------------|
| `--all` | Analyze all playlists (skip interactive selection) |
| `--playlist ID` | Analyze a specific playlist by ID (repeatable) |
| `--threshold FLOAT` | Outlier percentile cutoff (default: `20.0`) |
| `--year-threshold FLOAT` | Stddev multiplier for era outliers (default: `1.5`) |
| `--no-year` | Disable era outlier detection |
| `--no-vibe` | Disable energy tier outlier detection |
| `--client-id ID` | Override the Client ID from `.env` |
| `--redirect-port INT` | Local OAuth callback port (default: `8888`) |
| `--token-file PATH` | Custom token storage location |

### First run

Your browser will open the Spotify login page. After authorizing, the token is saved to `~/.spotify_classifier_tokens.json` and reused on subsequent runs (silently refreshed when expired).

## Project Structure

```
spotify-playlist-classifier/
├── main.py              # Entry point, CLI args, orchestration
├── auth.py              # PKCE OAuth flow, token storage/refresh
├── spotify_client.py    # API calls, pagination, rate limiting, genre cache
├── profiler.py          # Genre fingerprint, energy tier, release year profile
├── scorer.py            # Fit scoring, outlier ranking, explanations
├── report.py            # Terminal report rendering
├── requirements.txt
├── .env.example
└── .env                 # Your credentials (never commit this)
```

## Sample Output

```
════════════════════════════════════════════════════════════
 PLAYLIST: Las Vegas  (247 tracks · 231 classifiable · 16 no genre data)
════════════════════════════════════════════════════════════

 THEME SUMMARY
   Top genres:  edm (62%) · electro house (40%) · big room edm (31%)
   Energy tier: HIGH  (genre-based proxy)
   Release era: median 2018, σ = 3.2 yrs

 OUTLIERS  (bottom percentile by genre fit — 46 tracks)
   ──────────────────────────────────────────────────────────
    #  Track                    Artist          Fit   Flags
   ──────────────────────────────────────────────────────────
    1  "Blinding Lights"        The Weeknd      0.00  genre vibe
       Genre: [canadian pop, pop] vs playlist: [edm, electro house, big room edm]
       Vibe: LOW-MEDIUM energy genres in a HIGH energy playlist
   ──────────────────────────────────────────────────────────
```

## Known Limitation

Energy/vibe detection is approximated from genre keyword matching — it won't catch a track that *sounds* wrong but carries correct genre tags (e.g. a slow atmospheric EDM track). This is a best-effort proxy due to the audio features restriction on new Spotify apps.
