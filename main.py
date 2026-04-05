from __future__ import annotations

import argparse
import html as html_module
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from classifier.auth import AuthError, load_or_refresh_token
from classifier.export import build_playlist_export, write_export
from classifier.lastfm_client import LastFmClient
from classifier.profiler import build_playlist_profile, build_track_data
from classifier.report import format_html_report, format_markdown_report, print_report
from classifier.scorer import build_explanations, build_tag_vectors, run_isolation_forest, score_tracks
from classifier.spotify_client import SpotifyAPIError, SpotifyClient


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class Config:
    client_id: str
    redirect_port: int = 8888
    year_stddev_threshold: float = 2.0
    duration_threshold: float = 2.0
    if_contamination: float = 0.16
    token_file: Path = field(default_factory=lambda: Path.home() / ".spotify_classifier_tokens.json")
    analyze_all: bool = False
    playlist_ids: list[str] = field(default_factory=list)
    use_year_detection: bool = False
    use_tag_detection: bool = True
    use_duration_detection: bool = True
    use_explicit_detection: bool = False
    lastfm_api_key: str = ""
    output_dir: Path | None = None
    output_format: str = "html"         # "html" | "md" | "txt"
    export_dir: Path | None = None


def load_config(args: argparse.Namespace) -> Config:
    load_dotenv()

    client_id = args.client_id or os.environ.get("SPOTIFY_CLIENT_ID", "")
    if not client_id:
        print("ERROR: SPOTIFY_CLIENT_ID is required. Set it in .env or pass --client-id.")
        sys.exit(1)

    lastfm_api_key = args.lastfm_api_key or os.environ.get("LASTFM_API_KEY", "")
    if not lastfm_api_key:
        print("ERROR: LASTFM_API_KEY is required. Set it in .env or pass --lastfm-api-key.")
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
    output_dir = Path(args.output_dir) if args.output_dir else Path("output")
    export_dir = Path(args.export_dir) if args.export_dir else None

    # Default format: html when writing to files
    if args.format:
        output_format = args.format
    else:
        output_format = "html"

    return Config(
        client_id=client_id,
        redirect_port=redirect_port,
        year_stddev_threshold=args.year_threshold if args.year_threshold is not None else _env_float("YEAR_STDDEV_THRESHOLD", 2.0),
        duration_threshold=args.duration_threshold if args.duration_threshold is not None else _env_float("DURATION_THRESHOLD", 2.0),
        if_contamination=args.if_contamination if args.if_contamination is not None else 0.16,
        token_file=token_file,
        analyze_all=args.all,
        playlist_ids=args.playlist or [],
        use_year_detection=args.year,
        use_tag_detection=not args.no_tags,
        use_duration_detection=not args.no_duration,
        use_explicit_detection=args.explicit,
        lastfm_api_key=lastfm_api_key,
        output_dir=output_dir,
        output_format=output_format,
        export_dir=export_dir,
    )


# ---------------------------------------------------------------------------
# Interactive playlist selection
# ---------------------------------------------------------------------------

def select_playlists_interactive(playlists: list[dict]) -> list[dict]:
    print("\nYour playlists:\n")
    for i, pl in enumerate(playlists, start=1):
        name = pl.get("name", "(unnamed)")
        count = (pl.get("items") or {}).get("total", "?")
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
        if not config.use_tag_detection:
            st.is_tag_outlier = False
        if not config.use_year_detection:
            st.is_year_outlier = False
        if not config.use_duration_detection:
            st.is_duration_outlier = False
        if not config.use_explicit_detection:
            st.is_explicit_outlier = False


def _apply_confirmed_labels(scored: list, playlist_id: str, export_path: Path) -> int:
    """
    Suppress outlier flags for tracks manually confirmed as non-outliers in a
    prior curate.py session. Reads confirmed_outlier labels from the export JSON.
    Returns the count of suppressed tracks.
    """
    if not export_path.exists():
        return 0
    try:
        data = json.loads(export_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0

    confirmed_normal: set[str] = set()
    for pl in data:
        if pl.get("playlist_id") == playlist_id:
            for t in pl.get("tracks", []):
                if t.get("confirmed_outlier") is False:
                    confirmed_normal.add(t["track_id"])
            break

    if not confirmed_normal:
        return 0

    suppressed = 0
    for st in scored:
        if st.track.id in confirmed_normal:
            st.is_tag_outlier = False
            st.is_year_outlier = False
            st.is_duration_outlier = False
            st.is_explicit_outlier = False
            suppressed += 1
    return suppressed


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="Spotify Playlist Outlier Analyzer"
    )
    # Playlist selection
    parser.add_argument("--all", action="store_true", help="Analyze all playlists")
    parser.add_argument("--playlist", metavar="ID", action="append", help="Specific playlist ID (repeatable)")

    # Thresholds
    parser.add_argument("--year-threshold", type=float, metavar="FLOAT", help="Stddev multiplier for era outliers (default: 2.0)")
    parser.add_argument("--duration-threshold", type=float, metavar="FLOAT", help="Stddev multiplier for duration outliers (default: 2.0)")
    parser.add_argument("--if-contamination", type=float, metavar="FLOAT", help="Isolation Forest contamination parameter (default: 0.16)")

    # Feature toggles
    parser.add_argument("--year", action="store_true", help="Enable era outlier detection (off by default)")
    parser.add_argument("--no-tags", action="store_true", help="Disable tag-based outlier detection")
    parser.add_argument("--no-duration", action="store_true", help="Disable duration outlier detection")
    parser.add_argument("--explicit", action="store_true", help="Enable explicit flag outlier detection (off by default)")

    # Auth / output
    parser.add_argument("--client-id", metavar="ID", help="Spotify Client ID (overrides .env)")
    parser.add_argument("--lastfm-api-key", metavar="KEY", help="Last.fm API key (overrides .env)")
    parser.add_argument("--redirect-port", type=int, metavar="INT", help="Local OAuth callback port (default: 8888)")
    parser.add_argument("--token-file", metavar="PATH", help="Token storage location")
    parser.add_argument("--output-dir", metavar="DIR", help="Write each playlist report to a file in this directory instead of stdout")
    parser.add_argument("--format", choices=["html", "md", "txt"], metavar="FMT",
                        help="Report format when using --output-dir: html (default), md, txt")
    parser.add_argument("--export-dir", metavar="DIR", help="Save per-playlist ML training exports to this directory (default: exports/)")

    args = parser.parse_args()
    config = load_config(args)

    # --- Auth ---
    try:
        token = load_or_refresh_token(config.client_id, config.token_file, config.redirect_port)
    except AuthError as exc:
        print(f"Authentication failed: {exc}")
        sys.exit(1)

    client = SpotifyClient(token)
    lastfm = LastFmClient(
        config.lastfm_api_key,
        cache_path=Path.home() / ".spotify_classifier_tag_cache.json",
    )

    # --- Playlist selection ---
    try:
        all_playlists = client.get_user_playlists()
    except SpotifyAPIError as exc:
        print(f"Failed to fetch playlists: {exc}")
        sys.exit(1)

    if not all_playlists:
        print("No playlists found in your account.")
        sys.exit(0)

    all_playlists.sort(key=lambda pl: (pl.get("name") or "").casefold())

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

    config.output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Reports will be written to: {config.output_dir}")

    if config.export_dir:
        config.export_dir.mkdir(parents=True, exist_ok=True)
        print(f"Exports will be written to: {config.export_dir}")

    print(f"\nAnalyzing {len(chosen)} playlist(s)...\n")

    _EXT = {"html": ".html", "md": ".md", "txt": ".txt"}

    # --- Per-playlist analysis ---
    for playlist in chosen:
        pl_id = playlist.get("id", "")
        pl_name = playlist.get("name", "(unnamed)")

        try:
            print(f"  Fetching tracks for: {pl_name}")
            raw_tracks = client.get_playlist_tracks(pl_id)

            # Fetch Last.fm tags for each track
            print(f"  Fetching Last.fm tags for {len(raw_tracks)} track(s)...")
            track_tags: dict[str, dict[str, float]] = {}
            for i, t in enumerate(raw_tracks, start=1):
                tid = t.get("id", "")
                if not tid:
                    continue
                artists = t.get("artists") or []
                artist_names = [a.get("name", "") for a in artists if a.get("name")]
                track_name = t.get("name", "")
                if artist_names:
                    track_tags[tid] = lastfm.get_merged_tags(artist_names, track_name)
                else:
                    track_tags[tid] = {}
                if i % 25 == 0:
                    print(f"    {i}/{len(raw_tracks)}")

            lastfm.save_cache()
            tracks = build_track_data(raw_tracks, track_tags)
            pl_description = html_module.unescape(playlist.get("description") or "")
            profile = build_playlist_profile(pl_id, pl_name, tracks, description=pl_description)

            # Build tag vectors and run Isolation Forest
            vocabulary, vectors = build_tag_vectors(tracks)
            tag_outliers = run_isolation_forest(
                tracks, vocabulary, vectors,
                contamination=config.if_contamination,
            )

            scored = score_tracks(
                tracks,
                profile,
                tag_outliers,
                year_threshold=config.year_stddev_threshold,
                duration_threshold=config.duration_threshold,
            )

            _disable_flags(scored, config)
            build_explanations(scored, profile)

            # Export after flag disabling so suggested_outlier matches what the
            # HTML report shows — tracks flagged only by disabled detectors are
            # excluded from both.
            suppressed = 0
            if config.export_dir:
                pl_export_path = config.export_dir / (_safe_filename(pl_name) + ".json")
                write_export([build_playlist_export(profile, scored)], pl_export_path)
                suppressed = _apply_confirmed_labels(scored, pl_id, pl_export_path)
                if suppressed:
                    print(f"  Suppressed {suppressed} confirmed non-outlier(s)")

            ext = _EXT[config.output_format]
            filename = _safe_filename(pl_name) + ext
            out_path = config.output_dir / filename
            with open(out_path, "w", encoding="utf-8") as f:
                if config.output_format == "html":
                    f.write(format_html_report(profile, scored, suppressed))
                elif config.output_format == "md":
                    f.write(format_markdown_report(profile, scored, suppressed))
                else:
                    print_report(profile, scored, file=f, suppressed_count=suppressed)
            print(f"  Saved: {out_path}")
            print_report(profile, scored, suppressed_count=suppressed)

        except SpotifyAPIError as exc:
            print(f"  ERROR processing '{pl_name}': {exc}\n")
            continue



if __name__ == "__main__":
    main()
