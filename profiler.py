from __future__ import annotations

import statistics
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Energy tier keyword map — substring-based matching, highest tier wins
# ---------------------------------------------------------------------------

ENERGY_TIER_MAP: dict[str, list[str]] = {
    "HIGH": [
        "edm", "electro house", "big room", "hardstyle", "drum and bass",
        "dubstep", "trance", "rave", "techno", "hard dance", "jumpstyle",
    ],
    "MEDIUM": [
        "dance pop", "synthpop", "indie pop", "electropop",
        "synth", "retrowave", "outrun", "electro", "pop",
    ],
    "LOW": [
        "ambient", "chillwave", "downtempo", "lo-fi", "dream pop",
        "shoegaze", "new age", "meditation", "chillout", "acoustic",
    ],
}

_TIER_ORDER = ["LOW", "MEDIUM", "HIGH"]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TrackData:
    id: str
    name: str
    popularity: int | None          # Spotify track popularity (0–100)
    explicit: bool
    duration_ms: int | None         # track length in milliseconds
    artist_ids: list[str]
    artist_names: list[str]
    artist_popularity: float | None  # mean popularity across all artists (0–100)
    artist_followers: int | None     # max followers across all artists
    release_year: int | None
    genres: list[str]               # union across all artists; empty = unclassifiable
    unclassifiable: bool            # True when genres is empty
    energy_tier: str | None         # "HIGH" / "MEDIUM" / "LOW" / None
    tempo: float | None             # BPM from audio analysis; None if unavailable


@dataclass
class PlaylistProfile:
    playlist_id: str
    playlist_name: str
    genre_weights: dict[str, float]             # genre -> fraction of classifiable tracks
    top_genres: list[tuple[str, float]]         # top 10, descending
    dominant_energy_tier: str | None
    year_median: float | None
    year_stddev: float | None
    popularity_median: float | None
    popularity_stddev: float | None
    duration_median: float | None               # milliseconds
    duration_stddev: float | None
    explicit_ratio: float                       # fraction of classifiable tracks that are explicit
    artist_popularity_median: float | None
    artist_popularity_stddev: float | None
    tempo_median: float | None                  # BPM; None if audio analysis unavailable
    tempo_stddev: float | None
    total_tracks: int
    classifiable_count: int
    unclassifiable_tracks: list[TrackData] = field(default_factory=list)


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


def assign_energy_tier(genres: list[str]) -> str | None:
    """
    Return the highest energy tier matched by any genre keyword.
    Matching is substring-based. Returns None if no keywords match.
    """
    best_index = -1
    for genre in genres:
        genre_lower = genre.lower()
        for tier in _TIER_ORDER:
            if any(keyword in genre_lower for keyword in ENERGY_TIER_MAP[tier]):
                idx = _TIER_ORDER.index(tier)
                if idx > best_index:
                    best_index = idx
    return _TIER_ORDER[best_index] if best_index >= 0 else None


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
    artist_data: dict[str, dict],
    audio_analyses: dict[str, dict | None] | None = None,
) -> list[TrackData]:
    """
    Convert raw Spotify track dicts + artist data + optional audio analyses
    into TrackData list.

    artist_data maps artist_id -> {"genres": [...], "popularity": int|None, "followers": int|None}
    audio_analyses maps track_id -> raw audio-analysis dict (or None if unavailable)
    """
    if audio_analyses is None:
        audio_analyses = {}

    tracks: list[TrackData] = []
    for raw in raw_tracks:
        artists = raw.get("artists") or []
        artist_ids = [a["id"] for a in artists if a.get("id")]
        artist_names = [a.get("name", "") for a in artists]

        # Union of genres across all artists
        genre_set: list[str] = []
        seen: set[str] = set()
        artist_pops: list[float] = []
        artist_follower_values: list[int] = []

        for aid in artist_ids:
            info = artist_data.get(aid, {})
            for g in info.get("genres", []):
                if g not in seen:
                    seen.add(g)
                    genre_set.append(g)
            if info.get("popularity") is not None:
                artist_pops.append(float(info["popularity"]))
            if info.get("followers") is not None:
                artist_follower_values.append(int(info["followers"]))

        release_date = (raw.get("album") or {}).get("release_date", "")
        year = extract_year(release_date)
        tier = assign_energy_tier(genre_set)

        # Audio analysis — BPM from track-level summary
        track_id = raw.get("id", "")
        analysis = audio_analyses.get(track_id)
        tempo: float | None = None
        if analysis:
            tempo = (analysis.get("track") or {}).get("tempo")

        tracks.append(TrackData(
            id=track_id,
            name=raw.get("name", ""),
            popularity=raw.get("popularity"),
            explicit=raw.get("explicit", False),
            duration_ms=raw.get("duration_ms"),
            artist_ids=artist_ids,
            artist_names=artist_names,
            artist_popularity=sum(artist_pops) / len(artist_pops) if artist_pops else None,
            artist_followers=max(artist_follower_values) if artist_follower_values else None,
            release_year=year,
            genres=genre_set,
            unclassifiable=len(genre_set) == 0,
            energy_tier=tier,
            tempo=tempo,
        ))
    return tracks


def build_playlist_profile(
    playlist_id: str,
    playlist_name: str,
    tracks: list[TrackData],
) -> PlaylistProfile:
    """
    Derive all profile stats from track list.
    Only classifiable tracks contribute to weights and numeric stats.
    """
    classifiable = [t for t in tracks if not t.unclassifiable]
    unclassifiable = [t for t in tracks if t.unclassifiable]
    n = len(classifiable)

    # --- Genre weights ---
    genre_counts: dict[str, int] = {}
    for track in classifiable:
        for g in track.genres:
            genre_counts[g] = genre_counts.get(g, 0) + 1
    genre_weights = {g: c / n for g, c in genre_counts.items()} if n > 0 else {}
    top_genres = sorted(genre_weights.items(), key=lambda x: x[1], reverse=True)[:10]

    # --- Dominant energy tier ---
    tier_counts: dict[str, int] = {}
    for track in classifiable:
        if track.energy_tier:
            tier_counts[track.energy_tier] = tier_counts.get(track.energy_tier, 0) + 1
    dominant_tier = max(tier_counts, key=lambda t: tier_counts[t]) if tier_counts else None

    # --- Numeric stats (median + stddev) ---
    year_median, year_stddev = _median_stddev(
        [float(t.release_year) for t in classifiable if t.release_year is not None]
    )
    pop_median, pop_stddev = _median_stddev(
        [float(t.popularity) for t in classifiable if t.popularity is not None]
    )
    dur_median, dur_stddev = _median_stddev(
        [float(t.duration_ms) for t in classifiable if t.duration_ms is not None]
    )
    artpop_median, artpop_stddev = _median_stddev(
        [t.artist_popularity for t in classifiable if t.artist_popularity is not None]
    )
    tempo_median, tempo_stddev = _median_stddev(
        [t.tempo for t in classifiable if t.tempo is not None]
    )

    explicit_ratio = (
        sum(1 for t in classifiable if t.explicit) / n if n > 0 else 0.0
    )

    return PlaylistProfile(
        playlist_id=playlist_id,
        playlist_name=playlist_name,
        genre_weights=genre_weights,
        top_genres=top_genres,
        dominant_energy_tier=dominant_tier,
        year_median=year_median,
        year_stddev=year_stddev,
        popularity_median=pop_median,
        popularity_stddev=pop_stddev,
        duration_median=dur_median,
        duration_stddev=dur_stddev,
        explicit_ratio=explicit_ratio,
        artist_popularity_median=artpop_median,
        artist_popularity_stddev=artpop_stddev,
        tempo_median=tempo_median,
        tempo_stddev=tempo_stddev,
        total_tracks=len(tracks),
        classifiable_count=n,
        unclassifiable_tracks=unclassifiable,
    )
