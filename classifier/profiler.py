from __future__ import annotations

import statistics
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TrackData:
    id: str
    name: str
    explicit: bool
    duration_ms: int | None
    artist_ids: list[str]
    artist_names: list[str]
    release_year: int | None
    added_at: str | None              # ISO 8601 from Spotify playlist item
    tags: dict[str, float]            # merged Last.fm tags {name: weight_0_to_1}
    unclassifiable: bool              # True when len(tags) == 0


@dataclass
class PlaylistProfile:
    playlist_id: str
    playlist_name: str
    year_median: float | None
    year_stddev: float | None
    duration_median: float | None     # milliseconds
    duration_stddev: float | None
    explicit_ratio: float             # fraction of classifiable tracks that are explicit
    total_tracks: int
    classifiable_count: int
    unclassifiable_tracks: list[TrackData] = field(default_factory=list)
    tag_averages: dict[str, float] = field(default_factory=dict)  # mean weight per tag across classifiable tracks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_year(release_date: str) -> int | None:
    """Parse year from YYYY, YYYY-MM, or YYYY-MM-DD format."""
    if not release_date:
        return None
    try:
        return int(release_date[:4])
    except (ValueError, TypeError):
        return None


def _median_stddev(values: list[float]) -> tuple[float | None, float | None]:
    """Return (median, population stddev) or (None, None) for empty input."""
    if not values:
        return None, None
    median = statistics.median(values)
    stddev = statistics.pstdev(values) if len(values) > 1 else 0.0
    return median, stddev


# ---------------------------------------------------------------------------
# Build functions
# ---------------------------------------------------------------------------

def build_track_data(
    raw_tracks: list[dict],
    track_tags: dict[str, dict[str, float]],
) -> list[TrackData]:
    """
    Convert raw Spotify track dicts + Last.fm tag data into TrackData list.

    track_tags maps track_id -> {tag_name: weight_0_to_1}
    """
    tracks: list[TrackData] = []
    for raw in raw_tracks:
        artists = raw.get("artists") or []
        artist_ids = [a["id"] for a in artists if a.get("id")]
        artist_names = [a.get("name", "") for a in artists if a.get("id")]

        release_date = (raw.get("album") or {}).get("release_date", "")
        year = extract_year(release_date)

        track_id = raw.get("id", "")
        tags = track_tags.get(track_id, {})

        tracks.append(TrackData(
            id=track_id,
            name=raw.get("name", ""),
            explicit=raw.get("explicit", False),
            duration_ms=raw.get("duration_ms"),
            artist_ids=artist_ids,
            artist_names=artist_names,
            release_year=year,
            added_at=raw.get("added_at"),
            tags=tags,
            unclassifiable=len(tags) == 0,
        ))
    return tracks


def build_playlist_profile(
    playlist_id: str,
    playlist_name: str,
    tracks: list[TrackData],
) -> PlaylistProfile:
    """
    Derive profile stats from track list.
    Only classifiable tracks (those with tags) contribute to numeric stats.
    """
    classifiable = [t for t in tracks if not t.unclassifiable]
    unclassifiable = [t for t in tracks if t.unclassifiable]
    n = len(classifiable)

    year_median, year_stddev = _median_stddev(
        [float(t.release_year) for t in classifiable if t.release_year is not None]
    )
    dur_median, dur_stddev = _median_stddev(
        [float(t.duration_ms) for t in classifiable if t.duration_ms is not None]
    )
    explicit_ratio = (
        sum(1 for t in classifiable if t.explicit) / n if n > 0 else 0.0
    )

    tag_sums: dict[str, float] = {}
    for t in classifiable:
        for tag, weight in t.tags.items():
            tag_sums[tag] = tag_sums.get(tag, 0.0) + weight
    tag_averages = {tag: total / n for tag, total in tag_sums.items()} if n > 0 else {}

    return PlaylistProfile(
        playlist_id=playlist_id,
        playlist_name=playlist_name,
        year_median=year_median,
        year_stddev=year_stddev,
        duration_median=dur_median,
        duration_stddev=dur_stddev,
        explicit_ratio=explicit_ratio,
        total_tracks=len(tracks),
        classifiable_count=n,
        unclassifiable_tracks=unclassifiable,
        tag_averages=tag_averages,
    )
