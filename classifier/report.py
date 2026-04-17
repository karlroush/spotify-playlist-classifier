from __future__ import annotations

import html as html_module
import json
import re
import shutil
import sys
import textwrap
from pathlib import Path
from typing import IO

from .profiler import PlaylistProfile, TrackData
from .scorer import ScoredTrack


# Load curated playlist descriptions from output/DESCRIPTIONS.json if available
_CURATED_DESCRIPTIONS: dict[str, str] = {}
_desc_path = Path(__file__).parent.parent / "output" / "DESCRIPTIONS.json"
if _desc_path.exists():
    try:
        with open(_desc_path, encoding="utf-8") as f:
            _CURATED_DESCRIPTIONS = json.load(f)
    except (json.JSONDecodeError, OSError):
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bar(fraction: float, max_width: int = 30) -> str:
    filled = round(max(0.0, min(1.0, fraction)) * max_width)
    return "\u2588" * filled + "\u2591" * (max_width - filled)


def _term_width(override: int | None) -> int:
    if override:
        return override
    return shutil.get_terminal_size(fallback=(80, 24)).columns


def _double_line(width: int) -> str:
    return "\u2550" * width


def _single_line(width: int) -> str:
    return "\u2500" * width


def _fmt_ms(ms: float) -> str:
    """Format milliseconds as m:ss."""
    total_s = int(ms / 1000)
    return f"{total_s // 60}:{total_s % 60:02d}"


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def render_header(
    name: str,
    total: int,
    classifiable: int,
    unclassifiable_count: int,
    width: int,
) -> str:
    lines = [
        _double_line(width),
        f" PLAYLIST: {name}  "
        f"({total} tracks \u00b7 {classifiable} classifiable \u00b7 {unclassifiable_count} no tag data)",
        _double_line(width),
    ]
    return "\n".join(lines)


def render_theme_summary(profile: PlaylistProfile, width: int) -> str:
    lines = ["\n THEME SUMMARY"]

    if profile.year_median is not None:
        lines.append(
            f"   Release era: median {int(profile.year_median)}, \u03c3 = {profile.year_stddev:.1f} yrs"
        )
    else:
        lines.append("   Release era: (no year data)")

    if profile.duration_median is not None:
        lines.append(
            f"   Duration:    median {_fmt_ms(profile.duration_median)}, "
            f"\u03c3 = {_fmt_ms(profile.duration_stddev or 0)}"
        )

    lines.append(f"   Explicit:    {profile.explicit_ratio:.0%} of tracks")

    if profile.tag_averages:
        top = sorted(profile.tag_averages.items(), key=lambda x: x[1], reverse=True)[:10]
        lines.append("   Top tags:    " + ", ".join(f"{t} ({w:.0%})" for t, w in top))

    return "\n".join(lines)


def render_outliers(outliers: list[ScoredTrack], width: int, suppressed_count: int = 0) -> str:
    if not outliers:
        if suppressed_count > 0:
            return f"\n OUTLIERS\n   (none — {suppressed_count} track(s) reviewed clean)"
        return "\n OUTLIERS\n   (none)"

    col_width = width - 4
    header_line = _single_line(col_width)
    col_header = f"  {'#':<4} {'Track':<28} {'Artist':<20} {'Flags'}"

    lines = [
        f"\n OUTLIERS  ({len(outliers)} tracks)",
        f"   {header_line}",
        col_header,
        f"   {header_line}",
    ]

    for i, st in enumerate(outliers, start=1):
        track = st.track
        name = track.name[:26] + ".." if len(track.name) > 28 else track.name
        artist = ", ".join(track.artist_names)
        artist = artist[:18] + ".." if len(artist) > 20 else artist

        flag_tokens = [
            "tags"     if st.is_tag_outlier       else "",
            "era"      if st.is_year_outlier       else "",
            "dur"      if st.is_duration_outlier   else "",
            "explicit" if st.is_explicit_outlier   else "",
        ]
        flags = " ".join(f for f in flag_tokens if f)

        lines.append(f"  {i:<4} {name!r:<28} {artist:<20} {flags}")

        for expl_line in st.explanation.split("\n"):
            for wl in textwrap.wrap(expl_line.strip(), width=col_width - 7):
                lines.append(f"       {wl}")

    lines.append(f"   {header_line}")
    return "\n".join(lines)


def render_unclassifiable(tracks: list[TrackData]) -> str:
    if not tracks:
        return ""
    lines = [f"\n NO TAG DATA  (excluded \u2014 {len(tracks)} tracks)"]
    for t in tracks:
        artist = ", ".join(t.artist_names) or "Unknown"
        lines.append(f'   - "{t.name}" by {artist}')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _all_outliers(scored_tracks: list[ScoredTrack]) -> list[ScoredTrack]:
    return [
        st for st in scored_tracks
        if any([
            st.is_tag_outlier, st.is_year_outlier,
            st.is_duration_outlier, st.is_explicit_outlier,
        ])
    ]


def format_report(
    profile: PlaylistProfile,
    scored_tracks: list[ScoredTrack],
    term_width: int | None = None,
    suppressed_count: int = 0,
) -> str:
    """Render a full playlist report as a string."""
    width = _term_width(term_width)
    outliers = _all_outliers(scored_tracks)
    # Sort by flag count descending
    outliers.sort(key=lambda st: sum([
        st.is_tag_outlier, st.is_year_outlier,
        st.is_duration_outlier, st.is_explicit_outlier,
    ]), reverse=True)

    sections = [
        render_header(
            name=profile.playlist_name,
            total=profile.total_tracks,
            classifiable=profile.classifiable_count,
            unclassifiable_count=len(profile.unclassifiable_tracks),
            width=width,
        ),
        render_theme_summary(profile, width),
        render_outliers(outliers, width, suppressed_count),
    ]
    unclassifiable_section = render_unclassifiable(profile.unclassifiable_tracks)
    if unclassifiable_section:
        sections.append(unclassifiable_section)
    sections.append("")
    return "\n".join(sections)


def print_report(
    profile: PlaylistProfile,
    scored_tracks: list[ScoredTrack],
    term_width: int | None = None,
    file: IO[str] | None = None,
    suppressed_count: int = 0,
) -> None:
    """Render a full playlist report to stdout or a file."""
    print(format_report(profile, scored_tracks, term_width, suppressed_count), file=file or sys.stdout)


# ---------------------------------------------------------------------------
# HTML renderer
# ---------------------------------------------------------------------------

_HTML_CSS = """
body { font-family: monospace; font-size: 15px; background: #1a1a2e; color: #e0e0e0; padding: 2rem; max-width: 1100px; margin: auto; }
h1 { color: #a29bfe; border-bottom: 2px solid #6c5ce7; padding-bottom: .4rem; }
h2 { color: #74b9ff; margin-top: 2rem; border-bottom: 1px solid #2d3436; padding-bottom: .2rem; }
.meta { color: #b2bec3; font-size: .85em; margin-bottom: 1rem; }
.stat-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: .5rem; margin: 1rem 0; }
.stat { background: #16213e; border-left: 3px solid #6c5ce7; padding: .4rem .8rem; border-radius: 3px; }
.stat-label { color: #b2bec3; font-size: .8em; }
.bar { display: inline-block; background: #6c5ce7; height: .7em; vertical-align: middle; border-radius: 2px; }
.bar-bg { display: inline-block; background: #2d3436; height: .7em; vertical-align: middle; border-radius: 2px; }
table { width: 100%; border-collapse: collapse; margin: 1rem 0; font-size: .88em; }
th { background: #16213e; color: #a29bfe; text-align: left; padding: .5rem .7rem; }
td { padding: .45rem .7rem; border-bottom: 1px solid #2d3436; vertical-align: top; }
tr:hover td { background: #16213e; }
.flag { display: inline-block; background: #2d3436; color: #a29bfe; padding: .1rem .35rem; border-radius: 3px; font-size: .78em; margin: .1rem; }
.expl { color: #b2bec3; font-size: .82em; margin-top: .2rem; }
.unclassifiable { color: #636e72; font-size: .88em; }
.desc-block { background: #16213e; border-left: 3px solid #74b9ff; padding: .5rem .8rem; border-radius: 3px; margin: .6rem 0; font-size: .9em; }
.desc-label { color: #b2bec3; font-size: .78em; margin-bottom: .15rem; }
.desc-suggested { color: #a29bfe; }
""".strip()


def _h(text: str) -> str:
    """HTML-escape a string."""
    return html_module.escape(str(text))


def _bar_html(fraction: float, width_px: int = 180) -> str:
    filled = max(0, min(width_px, round(fraction * width_px)))
    empty = width_px - filled
    return (
        f'<span class="bar" style="width:{filled}px"></span>'
        f'<span class="bar-bg" style="width:{empty}px"></span>'
    )


# Tags that describe geographic origin rather than genre — excluded from suggestions.
_GEO_TAGS = {
    "usa", "american", "japanese", "chinese", "china", "taiwanese", "korean",
    "british", "uk", "english", "french", "german", "australian", "netherlands",
    "dutch", "swedish", "norwegian", "danish", "finnish", "canadian",
    "brazilian", "mexican", "spanish", "italian", "russian", "thai",
    "vietnamese", "indian", "portuguese", "polish",
}

# Abbreviations resolved to their canonical form for deduplication only.
_TAG_ALIASES = {
    "dnb": "drum and bass",
    "d&b": "drum and bass",
}


def _normalize_for_dedup(tag: str) -> str:
    canonical = _TAG_ALIASES.get(tag.lower(), tag.lower())
    return re.sub(r"[-\s&]", "", canonical)


def _title_tag(tag: str) -> str:
    """Title-case a tag but keep letters that follow digits lowercase (e.g. '80s' not '80S')."""
    titled = tag.title()
    return re.sub(r"(?<=\d)([A-Z])", lambda m: m.group(1).lower(), titled)


def _suggest_description(profile: PlaylistProfile) -> str:
    """
    Generate a suggested playlist description from profile data.
    Returns a curated description if available in DESCRIPTIONS.json, otherwise
    auto-generates one from the top tags (by average weight) and year era.
    Filters geographic origin tags and deduplicates near-synonyms (e.g. hip-hop/hip hop,
    k-pop/kpop, drum and bass/dnb). Tags with >= 4 space-separated words are skipped
    (noise like 'better than selena gomez').
    """
    # Return curated description if available
    if profile.playlist_name in _CURATED_DESCRIPTIONS:
        return _CURATED_DESCRIPTIONS[profile.playlist_name]

    # Auto-generate fallback description
    if not profile.tag_averages:
        return ""
    top = sorted(profile.tag_averages.items(), key=lambda x: x[1], reverse=True)
    seen_norm: set[str] = set()
    parts: list[str] = []
    for tag, _weight in top:
        if tag.lower() in _GEO_TAGS:
            continue
        if len(tag.split()) >= 4:
            continue
        norm = _normalize_for_dedup(tag)
        if norm in seen_norm:
            continue
        seen_norm.add(norm)
        parts.append(_title_tag(tag))
        if len(parts) == 5:
            break
    if profile.year_median is not None:
        decade = int(profile.year_median) // 10 * 10
        parts.append(f"{decade}s")
    return " \u00b7 ".join(parts)


def format_html_report(
    profile: PlaylistProfile,
    scored_tracks: list[ScoredTrack],
    suppressed_count: int = 0,
) -> str:
    """Render a full playlist report as a self-contained HTML string."""
    outliers = _all_outliers(scored_tracks)
    outliers.sort(key=lambda st: sum([
        st.is_tag_outlier, st.is_year_outlier,
        st.is_duration_outlier, st.is_explicit_outlier,
    ]), reverse=True)
    unclassifiable = profile.unclassifiable_tracks

    # --- Header ---
    sections: list[str] = [
        "<!DOCTYPE html><html lang='en'><head>",
        "<meta charset='UTF-8'>",
        f"<title>{_h(profile.playlist_name)} \u2014 Outlier Report</title>",
        f"<style>{_HTML_CSS}</style>",
        "</head><body>",
        f"<h1>{_h(profile.playlist_name)}</h1>",
        f"<p class='meta'>{profile.total_tracks} tracks \u00b7 "
        f"{profile.classifiable_count} classifiable \u00b7 "
        f"{len(unclassifiable)} no tag data</p>",
    ]

    # --- Description block ---
    suggested = _suggest_description(profile)
    if profile.description or suggested:
        sections.append("<div>")
        if profile.description:
            sections.append(
                f"<div class='desc-block'>"
                f"<div class='desc-label'>Current description</div>"
                f"{_h(profile.description)}</div>"
            )
        if suggested:
            sections.append(
                f"<div class='desc-block'>"
                f"<div class='desc-label'>Suggested description</div>"
                f"<span class='desc-suggested'>{_h(suggested)}</span></div>"
            )
        sections.append("</div>")

    # --- Theme summary ---
    sections.append("<h2>Theme Summary</h2><div class='stat-grid'>")

    if profile.year_median is not None:
        sections.append(
            f"<div class='stat'><div class='stat-label'>Release era</div>"
            f"median {int(profile.year_median)}, \u03c3 = {profile.year_stddev:.1f} yrs</div>"
        )

    if profile.duration_median is not None:
        sections.append(
            f"<div class='stat'><div class='stat-label'>Duration</div>"
            f"median {_fmt_ms(profile.duration_median)}, "
            f"\u03c3 = {_fmt_ms(profile.duration_stddev or 0)}</div>"
        )

    sections.append(
        f"<div class='stat'><div class='stat-label'>Explicit</div>"
        f"{profile.explicit_ratio:.0%} of tracks</div>"
    )

    if profile.tag_averages:
        top = sorted(profile.tag_averages.items(), key=lambda x: x[1], reverse=True)[:10]
        tag_html = " &middot; ".join(
            f"{_h(t)} <small style='color:#b2bec3'>({w:.0%})</small>" for t, w in top
        )
        sections.append(
            f"<div class='stat' style='grid-column:1/-1'>"
            f"<div class='stat-label'>Top tags</div>{tag_html}</div>"
        )

    sections.append("</div>")  # end stat-grid

    # --- Outliers table ---
    if outliers:
        sections.append(f"<h2>Outliers ({len(outliers)} tracks)</h2>")
        sections.append(
            "<table><thead><tr>"
            "<th>#</th><th>Track</th><th>Artist</th><th>Flags</th>"
            "</tr></thead><tbody>"
        )
        for i, st in enumerate(outliers, start=1):
            t = st.track
            flag_tokens = [
                ("tags", st.is_tag_outlier),
                ("era", st.is_year_outlier),
                ("dur", st.is_duration_outlier),
                ("explicit", st.is_explicit_outlier),
            ]
            flags_html = "".join(
                f"<span class='flag'>{label}</span>"
                for label, active in flag_tokens if active
            )
            expl_html = ""
            if st.explanation and st.explanation != "No specific flags":
                expl_html = (
                    f"<div class='expl'>{_h(st.explanation.replace(chr(10) + '       ', ' \u00b7 '))}</div>"
                )
            artist = _h(", ".join(t.artist_names))
            sections.append(
                f"<tr><td>{i}</td>"
                f"<td>{_h(t.name)}{expl_html}</td>"
                f"<td>{artist}</td>"
                f"<td>{flags_html}</td></tr>"
            )
        sections.append("</tbody></table>")
    elif suppressed_count > 0:
        sections.append(
            f"<h2>Outliers</h2><p><em>None unresolved — "
            f"{suppressed_count} track(s) reviewed clean.</em></p>"
        )
    else:
        sections.append("<h2>Outliers</h2><p><em>None detected.</em></p>")

    # --- Unclassifiable ---
    if unclassifiable:
        sections.append(
            f"<h2>No Tag Data ({len(unclassifiable)} tracks excluded)</h2>"
            "<ul class='unclassifiable'>"
        )
        for t in unclassifiable:
            artist = _h(", ".join(t.artist_names) or "Unknown")
            sections.append(f"<li>{_h(t.name)} \u2014 {artist}</li>")
        sections.append("</ul>")

    sections.append("</body></html>")
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------

def format_markdown_report(
    profile: PlaylistProfile,
    scored_tracks: list[ScoredTrack],
    suppressed_count: int = 0,
) -> str:
    """Render a full playlist report as a Markdown string."""
    outliers = _all_outliers(scored_tracks)
    outliers.sort(key=lambda st: sum([
        st.is_tag_outlier, st.is_year_outlier,
        st.is_duration_outlier, st.is_explicit_outlier,
    ]), reverse=True)
    lines: list[str] = []

    # Header
    lines.append(f"# {profile.playlist_name}")
    lines.append(
        f"_{profile.total_tracks} tracks \u00b7 {profile.classifiable_count} classifiable \u00b7 "
        f"{len(profile.unclassifiable_tracks)} no tag data_\n"
    )

    # Theme summary
    lines.append("## Theme Summary\n")
    if profile.year_median is not None:
        lines.append(
            f"- **Release era:** median {int(profile.year_median)}, \u03c3 = {profile.year_stddev:.1f} yrs"
        )
    if profile.duration_median is not None:
        lines.append(
            f"- **Duration:** median {_fmt_ms(profile.duration_median)}, "
            f"\u03c3 = {_fmt_ms(profile.duration_stddev or 0)}"
        )
    lines.append(f"- **Explicit:** {profile.explicit_ratio:.0%} of tracks")
    if profile.tag_averages:
        top = sorted(profile.tag_averages.items(), key=lambda x: x[1], reverse=True)[:10]
        lines.append("- **Top tags:** " + ", ".join(f"{t} ({w:.0%})" for t, w in top))
    lines.append("")

    # Outliers table
    if outliers:
        lines.append(f"## Outliers ({len(outliers)} tracks)\n")
        lines.append("| # | Track | Artist | Flags |")
        lines.append("|---|-------|--------|-------|")
        for i, st in enumerate(outliers, start=1):
            t = st.track
            flag_tokens = [
                ("tags", st.is_tag_outlier),
                ("era", st.is_year_outlier),
                ("dur", st.is_duration_outlier),
                ("explicit", st.is_explicit_outlier),
            ]
            flags = " ".join(label for label, active in flag_tokens if active)
            name = t.name.replace("|", "\\|")
            artist = ", ".join(t.artist_names).replace("|", "\\|")
            lines.append(f"| {i} | {name} | {artist} | {flags} |")

            if st.explanation and st.explanation != "No specific flags":
                expl = st.explanation.replace("\n       ", " \u00b7 ").replace("|", "\\|")
                lines.append(f"| | _{expl}_ | | |")
        lines.append("")
    elif suppressed_count > 0:
        lines.append(
            f"## Outliers\n\n_None unresolved — {suppressed_count} track(s) reviewed clean._\n"
        )
    else:
        lines.append("## Outliers\n\n_None detected._\n")

    # Unclassifiable
    if profile.unclassifiable_tracks:
        lines.append(f"## No Tag Data ({len(profile.unclassifiable_tracks)} tracks excluded)\n")
        for t in profile.unclassifiable_tracks:
            artist = ", ".join(t.artist_names) or "Unknown"
            lines.append(f'- "{t.name}" by {artist}')
        lines.append("")

    return "\n".join(lines)
