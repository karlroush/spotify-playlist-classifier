from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from auth import AuthError, load_or_refresh_token
from export import build_playlist_export, write_export
from profiler import build_playlist_profile, build_track_data
from report import format_html_report, format_markdown_report, format_report, print_report
from scorer import score_tracks
from spotify_client import SpotifyAPIError, SpotifyClient


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class Config:
    client_id: str
    redirect_port: int = 8888
    outlier_percentile: float = 20.0
    year_stddev_threshold: float = 1.5
    popularity_threshold: float = 1.5
    duration_threshold: float = 1.5
    artist_popularity_threshold: float = 1.5
    tempo_threshold: float = 1.5
    token_file: Path = field(default_factory=lambda: Path.home() / ".spotify_classifier_tokens.json")
    analyze_all: bool = False
    playlist_ids: list[str] = field(default_factory=list)
    use_year_detection: bool = True
    use_vibe_detection: bool = True
    use_popularity_detection: bool = True
    use_duration_detection: bool = True
    use_explicit_detection: bool = True
    use_artist_popularity_detection: bool = True
    use_audio_analysis: bool = False    # opt-in: one API call per track
    output_dir: Path | None = None
    output_format: str = "html"         # "html" | "md" | "txt"
    export_path: Path | None = None


def load_config(args: argparse.Namespace) -> Config:
    load_dotenv()

    client_id = args.client_id or os.environ.get("SPOTIFY_CLIENT_ID", "")
    if not client_id:
        print("ERROR: SPOTIFY_CLIENT_ID is required. Set it in .env or pass --client-id.")
        sys.exit(1)

    def _env_float(name: str, default: float) -> float:
        return float(os.environ.get(name, default))

    redirect_port = (
        args.redirect_port if args.redirect_port is not None
        else int(os.environ.get("SPOTIFY_REDIRECT_PORT", 8888))
    )
    token_file = (
        Path(args.token_file) if args.token_file
        else Path.home() / ".spotify_classifier_tokens.json"
    )
    output_dir = Path(args.output_dir) if args.output_dir else None
    export_path = Path(args.export) if args.export else None

    # Default format: html when writing to files, txt when printing to stdout
    if args.format:
        output_format = args.format
    elif output_dir:
        output_format = "html"
    else:
        output_format = "txt"

    return Config(
        client_id=client_id,
        redirect_port=redirect_port,
        outlier_percentile=args.threshold if args.threshold is not None else _env_float("OUTLIER_PERCENTILE", 20.0),
        year_stddev_threshold=args.year_threshold if args.year_threshold is not None else _env_float("YEAR_STDDEV_THRESHOLD", 1.5),
        popularity_threshold=args.popularity_threshold if args.popularity_threshold is not None else 1.5,
        duration_threshold=args.duration_threshold if args.duration_threshold is not None else 1.5,
        artist_popularity_threshold=args.artist_popularity_threshold if args.artist_popularity_threshold is not None else 1.5,
        tempo_threshold=args.tempo_threshold if args.tempo_threshold is not None else 1.5,
        token_file=token_file,
        analyze_all=args.all,
        playlist_ids=args.playlist or [],
        use_year_detection=not args.no_year,
        use_vibe_detection=not args.no_vibe,
        use_popularity_detection=not args.no_popularity,
        use_duration_detection=not args.no_duration,
        use_explicit_detection=not args.no_explicit,
        use_artist_popularity_detection=not args.no_artist_popularity,
        use_audio_analysis=args.audio_analysis,
        output_dir=output_dir,
        output_format=output_format,
        export_path=export_path,
    )


# ---------------------------------------------------------------------------
# Interactive playlist selection
# ---------------------------------------------------------------------------

def select_playlists_interactive(playlists: list[dict]) -> list[dict]:
    print("\nYour playlists:\n")
    for i, pl in enumerate(playlists, start=1):
        name = pl.get("name", "(unnamed)")
        count = (pl.get("tracks") or {}).get("total", "?")
        print(f"  {i:>3}.  {name}  ({count} tracks)")

    print("\nEnter playlist numbers separated by commas, or 'all': ", end="")
    raw = input().strip().lower()

    if raw == "all":
        return playlists

    selected: list[dict] = []
    for token in raw.split(","):
        token = token.strip()
        if not token.isdigit():
            print(f"  Skipping invalid entry: '{token}'")
            continue
        idx = int(token)
        if 1 <= idx <= len(playlists):
            selected.append(playlists[idx - 1])
        else:
            print(f"  Skipping out-of-range number: {idx}")

    if not selected:
        print("No valid playlists selected. Exiting.")
        sys.exit(0)

    return selected


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_filename(name: str) -> str:
    """Convert playlist name to a safe filename."""
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip() or "playlist"


def _disable_flags(scored: list, config: Config) -> None:
    """Zero out flags for detection types the user has disabled."""
    for st in scored:
        if not config.use_year_detection:
            st.is_year_outlier = False
        if not config.use_vibe_detection:
            st.is_vibe_outlier = False
        if not config.use_popularity_detection:
            st.is_popularity_outlier = False
        if not config.use_duration_detection:
            st.is_duration_outlier = False
        if not config.use_explicit_detection:
            st.is_explicit_outlier = False
        if not config.use_artist_popularity_detection:
            st.is_artist_popularity_outlier = False
        if not config.use_audio_analysis:
            st.is_tempo_outlier = False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Spotify Playlist Outlier Analyzer"
    )
    # Playlist selection
    parser.add_argument("--all", action="store_true", help="Analyze all playlists")
    parser.add_argument("--playlist", metavar="ID", action="append", help="Specific playlist ID (repeatable)")

    # Thresholds
    parser.add_argument("--threshold", type=float, metavar="FLOAT", help="Genre outlier percentile cutoff (default: 20.0)")
    parser.add_argument("--year-threshold", type=float, metavar="FLOAT", help="Stddev multiplier for era outliers (default: 1.5)")
    parser.add_argument("--popularity-threshold", type=float, metavar="FLOAT", help="Stddev multiplier for popularity outliers (default: 1.5)")
    parser.add_argument("--duration-threshold", type=float, metavar="FLOAT", help="Stddev multiplier for duration outliers (default: 1.5)")
    parser.add_argument("--artist-popularity-threshold", type=float, metavar="FLOAT", help="Stddev multiplier for artist popularity outliers (default: 1.5)")
    parser.add_argument("--tempo-threshold", type=float, metavar="FLOAT", help="Stddev multiplier for tempo outliers (default: 1.5)")

    # Feature toggles
    parser.add_argument("--no-year", action="store_true", help="Disable era outlier detection")
    parser.add_argument("--no-vibe", action="store_true", help="Disable energy tier detection")
    parser.add_argument("--no-popularity", action="store_true", help="Disable popularity outlier detection")
    parser.add_argument("--no-duration", action="store_true", help="Disable duration outlier detection")
    parser.add_argument("--no-explicit", action="store_true", help="Disable explicit flag outlier detection")
    parser.add_argument("--no-artist-popularity", action="store_true", help="Disable artist popularity outlier detection")
    parser.add_argument("--audio-analysis", action="store_true", help="Enable tempo detection via audio analysis (one API call per track; may be unavailable for new apps)")

    # Auth / output
    parser.add_argument("--client-id", metavar="ID", help="Spotify Client ID (overrides .env)")
    parser.add_argument("--redirect-port", type=int, metavar="INT", help="Local OAuth callback port (default: 8888)")
    parser.add_argument("--token-file", metavar="PATH", help="Token storage location")
    parser.add_argument("--output-dir", metavar="DIR", help="Write each playlist report to a file in this directory instead of stdout")
    parser.add_argument("--format", choices=["html", "md", "txt"], metavar="FMT",
                        help="Report format when using --output-dir: html (default), md, txt")
    parser.add_argument("--export", metavar="PATH", help="Append ML training export to this JSON file (preserves confirmed_outlier labels)")

    args = parser.parse_args()
    config = load_config(args)

    # --- Auth ---
    try:
        token = load_or_refresh_token(config.client_id, config.token_file, config.redirect_port)
    except AuthError as exc:
        print(f"Authentication failed: {exc}")
        sys.exit(1)

    client = SpotifyClient(token)

    # --- Playlist selection ---
    try:
        all_playlists = client.get_user_playlists()
    except SpotifyAPIError as exc:
        print(f"Failed to fetch playlists: {exc}")
        sys.exit(1)

    if not all_playlists:
        print("No playlists found in your account.")
        sys.exit(0)

    if config.playlist_ids:
        id_set = set(config.playlist_ids)
        chosen = [pl for pl in all_playlists if pl.get("id") in id_set]
        if not chosen:
            print("None of the provided playlist IDs were found in your account.")
            sys.exit(1)
    elif config.analyze_all:
        chosen = all_playlists
    else:
        chosen = select_playlists_interactive(all_playlists)

    if config.output_dir:
        config.output_dir.mkdir(parents=True, exist_ok=True)
        print(f"Reports will be written to: {config.output_dir}")

    print(f"\nAnalyzing {len(chosen)} playlist(s)...\n")

    _EXT = {"html": ".html", "md": ".md", "txt": ".txt"}

    # --- Per-playlist analysis ---
    playlist_exports: list[dict] = []
    for playlist in chosen:
        pl_id = playlist.get("id", "")
        pl_name = playlist.get("name", "(unnamed)")

        try:
            print(f"  Fetching tracks for: {pl_name}")
            raw_tracks = client.get_playlist_tracks(pl_id)

            artist_ids = list({
                aid
                for t in raw_tracks
                for artist in (t.get("artists") or [])
                if (aid := artist.get("id"))
            })

            print(f"  Fetching artist data for {len(artist_ids)} unique artist(s)...")
            artist_data = client.get_artist_data(artist_ids)

            # Audio analysis — opt-in, one call per track
            audio_analyses: dict[str, dict | None] = {}
            if config.use_audio_analysis:
                print(f"  Fetching audio analysis for {len(raw_tracks)} track(s)...")
                for i, t in enumerate(raw_tracks, start=1):
                    tid = t.get("id", "")
                    if tid:
                        if i % 25 == 0:
                            print(f"    {i}/{len(raw_tracks)}")
                        audio_analyses[tid] = client.get_audio_analysis(tid)
                    if not client._audio_analysis_available:
                        break  # endpoint unavailable — stop early

            tracks = build_track_data(raw_tracks, artist_data, audio_analyses)
            profile = build_playlist_profile(pl_id, pl_name, tracks)

            scored = score_tracks(
                tracks,
                profile,
                outlier_percentile=config.outlier_percentile,
                year_threshold=config.year_stddev_threshold,
                popularity_threshold=config.popularity_threshold,
                duration_threshold=config.duration_threshold,
                artist_popularity_threshold=config.artist_popularity_threshold,
                tempo_threshold=config.tempo_threshold,
            )

            _disable_flags(scored, config)

            outlier_count = sum(
                1 for st in scored
                if any([
                    st.is_genre_outlier, st.is_vibe_outlier, st.is_year_outlier,
                    st.is_popularity_outlier, st.is_duration_outlier,
                    st.is_explicit_outlier, st.is_artist_popularity_outlier,
                    st.is_tempo_outlier,
                ])
            )

            if config.export_path:
                playlist_exports.append(build_playlist_export(profile, scored))

            if config.output_dir:
                ext = _EXT[config.output_format]
                filename = _safe_filename(pl_name) + ext
                out_path = config.output_dir / filename
                with open(out_path, "w", encoding="utf-8") as f:
                    if config.output_format == "html":
                        f.write(format_html_report(profile, scored))
                    elif config.output_format == "md":
                        f.write(format_markdown_report(profile, scored))
                    else:
                        print_report(profile, scored, outlier_count, file=f)
                print(f"  Saved: {out_path}")
            else:
                print_report(profile, scored, outlier_count)

        except SpotifyAPIError as exc:
            print(f"  ERROR processing '{pl_name}': {exc}\n")
            continue

    if playlist_exports and config.export_path:
        write_export(playlist_exports, config.export_path)
        print(f"\nExport written to: {config.export_path}")


if __name__ == "__main__":
    main()
