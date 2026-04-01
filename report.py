from __future__ import annotations

import html as html_module
import shutil
import sys
import textwrap
from typing import IO

from profiler import PlaylistProfile, TrackData
from scorer import ScoredTrack


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bar(fraction: float, max_width: int = 30) -> str:
    filled = round(max(0.0, min(1.0, fraction)) * max_width)
    return "█" * filled + "░" * (max_width - filled)


def _term_width(override: int | None) -> int:
    if override:
        return override
    return shutil.get_terminal_size(fallback=(80, 24)).columns


def _double_line(width: int) -> str:
    return "═" * width


def _single_line(width: int) -> str:
    return "─" * width


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
        f"({total} tracks · {classifiable} classifiable · {unclassifiable_count} no genre data)",
        _double_line(width),
    ]
    return "\n".join(lines)


def render_theme_summary(profile: PlaylistProfile, width: int) -> str:
    lines = ["\n THEME SUMMARY"]

    if profile.top_genres:
        top_display = " · ".join(f"{g} ({v:.0%})" for g, v in profile.top_genres[:5])
        lines.append(f"   Top genres:  {top_display}")
        lines.append(f"   Dominant:    {_bar(profile.top_genres[0][1])}")
    else:
        lines.append("   Top genres:  (none)")

    tier = profile.dominant_energy_tier or "unknown"
    lines.append(f"   Energy tier: {tier}  (genre-based proxy)")

    if profile.year_median is not None:
        lines.append(
            f"   Release era: median {int(profile.year_median)}, σ = {profile.year_stddev:.1f} yrs"
        )
    else:
        lines.append("   Release era: (no year data)")

    if profile.popularity_median is not None:
        lines.append(
            f"   Popularity:  median {profile.popularity_median:.0f}/100, "
            f"σ = {profile.popularity_stddev:.1f}"
        )

    if profile.duration_median is not None:
        lines.append(
            f"   Duration:    median {_fmt_ms(profile.duration_median)}, "
            f"σ = {_fmt_ms(profile.duration_stddev or 0)}"
        )

    lines.append(f"   Explicit:    {profile.explicit_ratio:.0%} of tracks")

    if profile.artist_popularity_median is not None:
        lines.append(
            f"   Artist pop:  median {profile.artist_popularity_median:.0f}/100"
        )

    if profile.tempo_median is not None:
        lines.append(
            f"   Tempo:       median {profile.tempo_median:.0f} BPM, "
            f"σ = {profile.tempo_stddev:.1f}"
        )

    return "\n".join(lines)


def render_outliers(outliers: list[ScoredTrack], width: int) -> str:
    if not outliers:
        return "\n OUTLIERS\n   (none)"

    col_width = width - 4
    header_line = _single_line(col_width)
    col_header = f"  {'#':<4} {'Track':<28} {'Artist':<20} {'Fit':>5}  {'Flags'}"

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
            "genre"   if st.is_genre_outlier            else "",
            "vibe"    if st.is_vibe_outlier              else "",
            "era"     if st.is_year_outlier              else "",
            "pop"     if st.is_popularity_outlier        else "",
            "dur"     if st.is_duration_outlier          else "",
            "explicit" if st.is_explicit_outlier         else "",
            "artpop"  if st.is_artist_popularity_outlier else "",
            "tempo"   if st.is_tempo_outlier             else "",
        ]
        flags = " ".join(f for f in flag_tokens if f)

        lines.append(f"  {i:<4} {name!r:<28} {artist:<20} {st.fit_score:.2f}  {flags}")

        for expl_line in st.explanation.split("\n"):
            for wl in textwrap.wrap(expl_line.strip(), width=col_width - 7):
                lines.append(f"       {wl}")

    lines.append(f"   {header_line}")
    return "\n".join(lines)


def render_unclassifiable(tracks: list[TrackData]) -> str:
    if not tracks:
        return ""
    lines = [f"\n NO GENRE DATA  (excluded — {len(tracks)} tracks)"]
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
            st.is_genre_outlier, st.is_vibe_outlier, st.is_year_outlier,
            st.is_popularity_outlier, st.is_duration_outlier, st.is_explicit_outlier,
            st.is_artist_popularity_outlier, st.is_tempo_outlier,
        ])
    ]


def format_report(
    profile: PlaylistProfile,
    scored_tracks: list[ScoredTrack],
    term_width: int | None = None,
) -> str:
    """Render a full playlist report as a string."""
    width = _term_width(term_width)
    outliers = sorted(_all_outliers(scored_tracks), key=lambda st: st.fit_score)

    sections = [
        render_header(
            name=profile.playlist_name,
            total=profile.total_tracks,
            classifiable=profile.classifiable_count,
            unclassifiable_count=len(profile.unclassifiable_tracks),
            width=width,
        ),
        render_theme_summary(profile, width),
        render_outliers(outliers, width),
    ]
    unclassifiable_section = render_unclassifiable(profile.unclassifiable_tracks)
    if unclassifiable_section:
        sections.append(unclassifiable_section)
    sections.append("")
    return "\n".join(sections)


def print_report(
    profile: PlaylistProfile,
    scored_tracks: list[ScoredTrack],
    outlier_count: int,
    term_width: int | None = None,
    file: IO[str] | None = None,
) -> None:
    """Render a full playlist report to stdout or a file."""
    print(format_report(profile, scored_tracks, term_width), file=file or sys.stdout)


# ---------------------------------------------------------------------------
# HTML renderer
# ---------------------------------------------------------------------------

_HTML_CSS = """
body { font-family: monospace; background: #1a1a2e; color: #e0e0e0; padding: 2rem; max-width: 1100px; margin: auto; }
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
.fit-low { color: #ff7675; }
.fit-mid { color: #fdcb6e; }
.fit-ok { color: #55efc4; }
.flag { display: inline-block; background: #2d3436; color: #a29bfe; padding: .1rem .35rem; border-radius: 3px; font-size: .78em; margin: .1rem; }
.expl { color: #b2bec3; font-size: .82em; margin-top: .2rem; }
.unclassifiable { color: #636e72; font-size: .88em; }
""".strip()


def _h(text: str) -> str:
    """HTML-escape a string."""
    return html_module.escape(str(text))


def _fit_class(score: float) -> str:
    if score < 0.15:
        return "fit-low"
    if score < 0.35:
        return "fit-mid"
    return "fit-ok"


def _bar_html(fraction: float, width_px: int = 180) -> str:
    filled = max(0, min(width_px, round(fraction * width_px)))
    empty = width_px - filled
    return (
        f'<span class="bar" style="width:{filled}px"></span>'
        f'<span class="bar-bg" style="width:{empty}px"></span>'
    )


def format_html_report(
    profile: PlaylistProfile,
    scored_tracks: list[ScoredTrack],
) -> str:
    """Render a full playlist report as a self-contained HTML string."""
    outliers = sorted(_all_outliers(scored_tracks), key=lambda st: st.fit_score)
    unclassifiable = profile.unclassifiable_tracks

    # --- Header ---
    sections: list[str] = [
        "<!DOCTYPE html><html lang='en'><head>",
        "<meta charset='UTF-8'>",
        f"<title>{_h(profile.playlist_name)} — Outlier Report</title>",
        f"<style>{_HTML_CSS}</style>",
        "</head><body>",
        f"<h1>{_h(profile.playlist_name)}</h1>",
        f"<p class='meta'>{profile.total_tracks} tracks · "
        f"{profile.classifiable_count} classifiable · "
        f"{len(unclassifiable)} no genre data</p>",
    ]

    # --- Theme summary ---
    sections.append("<h2>Theme Summary</h2><div class='stat-grid'>")

    if profile.top_genres:
        top5 = " · ".join(f"{g} ({v:.0%})" for g, v in profile.top_genres[:5])
        bar_frac = profile.top_genres[0][1]
        sections.append(
            f"<div class='stat'><div class='stat-label'>Top genres</div>"
            f"{_h(top5)}<br>{_bar_html(bar_frac)}</div>"
        )

    tier = _h(profile.dominant_energy_tier or "unknown")
    sections.append(
        f"<div class='stat'><div class='stat-label'>Energy tier</div>{tier}</div>"
    )

    if profile.year_median is not None:
        sections.append(
            f"<div class='stat'><div class='stat-label'>Release era</div>"
            f"median {int(profile.year_median)}, σ = {profile.year_stddev:.1f} yrs</div>"
        )

    if profile.popularity_median is not None:
        sections.append(
            f"<div class='stat'><div class='stat-label'>Popularity</div>"
            f"median {profile.popularity_median:.0f}/100, σ = {profile.popularity_stddev:.1f}</div>"
        )

    if profile.duration_median is not None:
        sections.append(
            f"<div class='stat'><div class='stat-label'>Duration</div>"
            f"median {_fmt_ms(profile.duration_median)}, "
            f"σ = {_fmt_ms(profile.duration_stddev or 0)}</div>"
        )

    sections.append(
        f"<div class='stat'><div class='stat-label'>Explicit</div>"
        f"{profile.explicit_ratio:.0%} of tracks</div>"
    )

    if profile.artist_popularity_median is not None:
        sections.append(
            f"<div class='stat'><div class='stat-label'>Artist popularity</div>"
            f"median {profile.artist_popularity_median:.0f}/100</div>"
        )

    if profile.tempo_median is not None:
        sections.append(
            f"<div class='stat'><div class='stat-label'>Tempo</div>"
            f"median {profile.tempo_median:.0f} BPM, σ = {profile.tempo_stddev:.1f}</div>"
        )

    sections.append("</div>")  # end stat-grid

    # --- Outliers table ---
    if outliers:
        sections.append(f"<h2>Outliers ({len(outliers)} tracks)</h2>")
        sections.append(
            "<table><thead><tr>"
            "<th>#</th><th>Track</th><th>Artist</th><th>Fit</th><th>Flags</th>"
            "</tr></thead><tbody>"
        )
        for i, st in enumerate(outliers, start=1):
            t = st.track
            flag_tokens = [
                ("genre", st.is_genre_outlier),
                ("vibe", st.is_vibe_outlier),
                ("era", st.is_year_outlier),
                ("pop", st.is_popularity_outlier),
                ("dur", st.is_duration_outlier),
                ("explicit", st.is_explicit_outlier),
                ("artpop", st.is_artist_popularity_outlier),
                ("tempo", st.is_tempo_outlier),
            ]
            flags_html = "".join(
                f"<span class='flag'>{label}</span>"
                for label, active in flag_tokens if active
            )
            expl_html = ""
            if st.explanation and st.explanation != "No specific flags":
                expl_html = (
                    f"<div class='expl'>{_h(st.explanation.replace(chr(10) + '       ', ' · '))}</div>"
                )
            artist = _h(", ".join(t.artist_names))
            fit_cls = _fit_class(st.fit_score)
            sections.append(
                f"<tr><td>{i}</td>"
                f"<td>{_h(t.name)}{expl_html}</td>"
                f"<td>{artist}</td>"
                f"<td class='{fit_cls}'>{st.fit_score:.2f}</td>"
                f"<td>{flags_html}</td></tr>"
            )
        sections.append("</tbody></table>")
    else:
        sections.append("<h2>Outliers</h2><p><em>None detected.</em></p>")

    # --- Unclassifiable ---
    if unclassifiable:
        sections.append(
            f"<h2>No Genre Data ({len(unclassifiable)} tracks excluded)</h2>"
            "<ul class='unclassifiable'>"
        )
        for t in unclassifiable:
            artist = _h(", ".join(t.artist_names) or "Unknown")
            sections.append(f"<li>{_h(t.name)} — {artist}</li>")
        sections.append("</ul>")

    sections.append("</body></html>")
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------

def format_markdown_report(
    profile: PlaylistProfile,
    scored_tracks: list[ScoredTrack],
) -> str:
    """Render a full playlist report as a Markdown string."""
    outliers = sorted(_all_outliers(scored_tracks), key=lambda st: st.fit_score)
    lines: list[str] = []

    # Header
    lines.append(f"# {profile.playlist_name}")
    lines.append(
        f"_{profile.total_tracks} tracks · {profile.classifiable_count} classifiable · "
        f"{len(profile.unclassifiable_tracks)} no genre data_\n"
    )

    # Theme summary
    lines.append("## Theme Summary\n")
    if profile.top_genres:
        top5 = " · ".join(f"{g} ({v:.0%})" for g, v in profile.top_genres[:5])
        lines.append(f"- **Top genres:** {top5}")
    tier = profile.dominant_energy_tier or "unknown"
    lines.append(f"- **Energy tier:** {tier}")
    if profile.year_median is not None:
        lines.append(
            f"- **Release era:** median {int(profile.year_median)}, σ = {profile.year_stddev:.1f} yrs"
        )
    if profile.popularity_median is not None:
        lines.append(
            f"- **Popularity:** median {profile.popularity_median:.0f}/100, "
            f"σ = {profile.popularity_stddev:.1f}"
        )
    if profile.duration_median is not None:
        lines.append(
            f"- **Duration:** median {_fmt_ms(profile.duration_median)}, "
            f"σ = {_fmt_ms(profile.duration_stddev or 0)}"
        )
    lines.append(f"- **Explicit:** {profile.explicit_ratio:.0%} of tracks")
    if profile.artist_popularity_median is not None:
        lines.append(f"- **Artist popularity:** median {profile.artist_popularity_median:.0f}/100")
    if profile.tempo_median is not None:
        lines.append(
            f"- **Tempo:** median {profile.tempo_median:.0f} BPM, σ = {profile.tempo_stddev:.1f}"
        )
    lines.append("")

    # Outliers table
    if outliers:
        lines.append(f"## Outliers ({len(outliers)} tracks)\n")
        lines.append("| # | Track | Artist | Fit | Flags |")
        lines.append("|---|-------|--------|-----|-------|")
        for i, st in enumerate(outliers, start=1):
            t = st.track
            flag_tokens = [
                ("genre", st.is_genre_outlier),
                ("vibe", st.is_vibe_outlier),
                ("era", st.is_year_outlier),
                ("pop", st.is_popularity_outlier),
                ("dur", st.is_duration_outlier),
                ("explicit", st.is_explicit_outlier),
                ("artpop", st.is_artist_popularity_outlier),
                ("tempo", st.is_tempo_outlier),
            ]
            flags = " ".join(label for label, active in flag_tokens if active)
            name = t.name.replace("|", "\\|")
            artist = ", ".join(t.artist_names).replace("|", "\\|")
            lines.append(f"| {i} | {name} | {artist} | {st.fit_score:.2f} | {flags} |")

            if st.explanation and st.explanation != "No specific flags":
                expl = st.explanation.replace("\n       ", " · ").replace("|", "\\|")
                lines.append(f"| | _{expl}_ | | | |")
        lines.append("")
    else:
        lines.append("## Outliers\n\n_None detected._\n")

    # Unclassifiable
    if profile.unclassifiable_tracks:
        lines.append(f"## No Genre Data ({len(profile.unclassifiable_tracks)} tracks excluded)\n")
        for t in profile.unclassifiable_tracks:
            artist = ", ".join(t.artist_names) or "Unknown"
            lines.append(f'- "{t.name}" by {artist}')
        lines.append("")

    return "\n".join(lines)
