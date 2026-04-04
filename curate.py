from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Cross-platform single-keypress input
# ---------------------------------------------------------------------------

_GETCH_FALLBACK = False


def _getch() -> str:
    """Read a single keypress without requiring Enter."""
    global _GETCH_FALLBACK
    if _GETCH_FALLBACK:
        return input("  > ").strip().lower()[:1] or " "

    if sys.platform == "win32":
        try:
            import msvcrt
            while True:
                ch = msvcrt.getch()
                if ch in (b"\x00", b"\xe0"):
                    msvcrt.getch()  # discard second byte of special keys
                    continue
                return ch.decode("utf-8", errors="replace").lower()
        except Exception:
            _GETCH_FALLBACK = True
            print("  (single-keypress mode unavailable — type a key and press Enter)")
            return input("  > ").strip().lower()[:1] or " "
    else:
        try:
            import termios
            import tty
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                return sys.stdin.read(1).lower()
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:
            _GETCH_FALLBACK = True
            print("  (single-keypress mode unavailable — type a key and press Enter)")
            return input("  > ").strip().lower()[:1] or " "


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------

def _load(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _save(data: list[dict], path: Path) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _width() -> int:
    return shutil.get_terminal_size(fallback=(80, 24)).columns


def _fmt_ms(ms: float) -> str:
    total_s = int(ms / 1000)
    return f"{total_s // 60}:{total_s % 60:02d}"


_FLAG_LABELS: list[tuple[str, str]] = [
    ("tag_outlier", "tags"),
    ("year_outlier", "era"),
    ("duration_outlier", "dur"),
    ("explicit_outlier", "explicit"),
]


def _render_track(track: dict, idx: int, total: int, profile: dict) -> None:
    w = _width()
    name = track["track_name"]
    artists = ", ".join(track["artist_names"])
    feat = track["features"]
    flags = track["statistical_flags"]

    active_flags = " ".join(lbl for key, lbl in _FLAG_LABELS if flags.get(key))

    print(f"\n[{idx}/{total}]  {name}  —  {artists}")
    print(f"  Flags: {active_flags or '(none)'}")

    # Contextual detail lines for each active flag
    if flags.get("tag_outlier"):
        tags = feat.get("tags", {})
        if tags:
            top_tags = sorted(tags.items(), key=lambda x: x[1], reverse=True)[:5]
            tag_str = ", ".join(f"{n} ({w:.0%})" for n, w in top_tags)
            print(f"  Tags:     [{tag_str}] — flagged by Isolation Forest")
        else:
            print(f"  Tags:     (no tags) — flagged by Isolation Forest")

    if flags.get("year_outlier") and feat.get("release_year") is not None:
        ym = profile.get("year_median")
        ys = profile.get("year_stddev")
        median_str = f"playlist median {int(ym)}" if ym is not None else ""
        stddev_str = f" (\u00b1{ys:.1f} yrs)" if ys is not None else ""
        print(f"  Era:      released {feat['release_year']}  —  {median_str}{stddev_str}")

    if flags.get("duration_outlier") and feat.get("duration_ms") is not None:
        dm = profile.get("duration_median_ms")
        median_str = f"playlist median {_fmt_ms(dm)}" if dm is not None else ""
        print(f"  Duration: {_fmt_ms(feat['duration_ms'])}  —  {median_str}")

    if flags.get("explicit_outlier"):
        status = "explicit" if feat.get("explicit") else "clean"
        ratio = profile.get("explicit_ratio", 0)
        lean = "mostly clean" if ratio < 0.15 else "mostly explicit"
        print(f"  Explicit: track is {status} in a {lean} playlist ({ratio:.0%} explicit)")

    confirmed = track.get("confirmed_outlier")
    if confirmed is not None:
        status = "outlier" if confirmed else "not an outlier"
        print(f"  (previously labeled: {status})")


# ---------------------------------------------------------------------------
# Curation loop
# ---------------------------------------------------------------------------

def _tracks_to_review(
    tracks: list[dict],
    include_all: bool,
    re_review: bool,
) -> list[dict]:
    """
    Return tracks to review, ordered: suggested outliers first (by flag count
    descending), then non-suggested if include_all.
    Skips already-labeled tracks unless re_review is set.
    """
    def _eligible(t: dict) -> bool:
        if re_review:
            return True
        return t.get("confirmed_outlier") is None

    def _flag_count(t: dict) -> int:
        flags = t.get("statistical_flags", {})
        return sum(1 for key, _ in _FLAG_LABELS if flags.get(key))

    suggested = sorted(
        [t for t in tracks if t.get("suggested_outlier") and _eligible(t)],
        key=_flag_count,
        reverse=True,
    )
    if not include_all:
        return suggested

    non_suggested = sorted(
        [t for t in tracks if not t.get("suggested_outlier") and _eligible(t)],
        key=_flag_count,
        reverse=True,
    )
    return suggested + non_suggested


def _curate_playlist(
    playlist: dict,
    include_all: bool,
    re_review: bool,
    export_path: Path,
    all_data: list[dict],
) -> tuple[int, int, int, bool]:
    """
    Interactive curation for one playlist.
    Returns (confirmed_outlier, confirmed_not, skipped, quit_requested).
    """
    w = _width()
    name = playlist["playlist_name"]
    profile = playlist["profile"]
    tracks = playlist["tracks"]

    queue = _tracks_to_review(tracks, include_all, re_review)
    if not queue:
        already = sum(1 for t in tracks if t.get("confirmed_outlier") is not None)
        print(f"\n  {name}: nothing to review "
              f"({'all labeled' if already else 'no suggested outliers'})")
        return 0, 0, 0, False

    total_reviewed_before = sum(1 for t in tracks if t.get("confirmed_outlier") is not None)
    total_tracks = len(tracks)

    print(f"\n{'\u2550' * w}")
    print(f" PLAYLIST: {name}  "
          f"({total_reviewed_before}/{total_tracks} already labeled, "
          f"{len(queue)} to review now)")
    print(f"{'\u2550' * w}")
    print("  Keys:  [y] outlier   [n] not an outlier   [s] skip   [p] next playlist   [q] quit")

    # Build a lookup so we can find and update tracks by track_id
    track_lookup: dict[str, dict] = {t["track_id"]: t for t in tracks}

    confirmed_yes = confirmed_no = skipped = 0

    for i, track in enumerate(queue, start=1):
        _render_track(track, i, len(queue), profile)
        print("\n  [y] outlier   [n] not outlier   [s] skip   [p] next playlist   [q] quit")
        print("  ", end="", flush=True)

        while True:
            key = _getch()
            if key == "y":
                track_lookup[track["track_id"]]["confirmed_outlier"] = True
                confirmed_yes += 1
                print("y  \u2192 outlier")
                _save(all_data, export_path)
                break
            elif key == "n":
                track_lookup[track["track_id"]]["confirmed_outlier"] = False
                confirmed_no += 1
                print("n  \u2192 not an outlier")
                _save(all_data, export_path)
                break
            elif key == "s":
                skipped += 1
                print("s  \u2192 skipped")
                break
            elif key == "p":
                print("p  \u2192 next playlist")
                return confirmed_yes, confirmed_no, skipped, False
            elif key == "q":
                print("q  \u2192 quit")
                return confirmed_yes, confirmed_no, skipped, True
            # any other key: re-prompt silently

    return confirmed_yes, confirmed_no, skipped, False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Interactively label tracks in a playlist export JSON."
    )
    parser.add_argument("path", metavar="PATH", help="Export JSON file to curate")
    parser.add_argument("--playlist", metavar="ID", action="append",
                        help="Only review this playlist ID (repeatable)")
    parser.add_argument("--include-all", action="store_true",
                        help="Review non-suggested tracks too (catch false negatives)")
    parser.add_argument("--re-review", action="store_true",
                        help="Revisit tracks that already have a confirmed_outlier label")
    args = parser.parse_args()

    export_path = Path(args.path)
    if not export_path.exists():
        print(f"ERROR: file not found: {export_path}")
        sys.exit(1)

    all_data = _load(export_path)

    # Filter to requested playlists
    if args.playlist:
        id_set = set(args.playlist)
        playlists = [p for p in all_data if p["playlist_id"] in id_set]
        if not playlists:
            print("ERROR: none of the specified playlist IDs found in the export.")
            sys.exit(1)
    else:
        playlists = all_data

    total_yes = total_no = total_skipped = 0

    for playlist in playlists:
        cy, cn, cs, quit_requested = _curate_playlist(
            playlist,
            include_all=args.include_all,
            re_review=args.re_review,
            export_path=export_path,
            all_data=all_data,
        )
        total_yes += cy
        total_no += cn
        total_skipped += cs
        if quit_requested:
            break

    # Summary
    w = _width()
    print(f"\n{'\u2500' * w}")
    print(f" Session summary")
    print(f"{'\u2500' * w}")
    print(f"  Confirmed outliers:     {total_yes}")
    print(f"  Confirmed not outliers: {total_no}")
    print(f"  Skipped:                {total_skipped}")

    # Across all reviewed playlists, show agreement with statistical suggestions
    all_tracks = [t for p in playlists for t in p["tracks"]]
    true_pos  = sum(1 for t in all_tracks if t.get("suggested_outlier") and t.get("confirmed_outlier") is True)
    false_pos = sum(1 for t in all_tracks if t.get("suggested_outlier") and t.get("confirmed_outlier") is False)
    false_neg = sum(1 for t in all_tracks if not t.get("suggested_outlier") and t.get("confirmed_outlier") is True)
    if true_pos + false_pos > 0:
        precision = true_pos / (true_pos + false_pos)
        print(f"\n  Statistical flag precision: {precision:.0%}  "
              f"({true_pos} correct, {false_pos} false positive{'s' if false_pos != 1 else ''})")
    if false_neg:
        print(f"  False negatives caught:     {false_neg}")
    print()


if __name__ == "__main__":
    main()
