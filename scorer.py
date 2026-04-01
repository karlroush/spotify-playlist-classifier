from __future__ import annotations

from dataclasses import dataclass

from profiler import PlaylistProfile, TrackData


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class ScoredTrack:
    track: TrackData
    fit_score: float
    is_genre_outlier: bool
    is_year_outlier: bool
    is_vibe_outlier: bool
    is_popularity_outlier: bool
    is_duration_outlier: bool
    is_explicit_outlier: bool
    is_artist_popularity_outlier: bool
    is_tempo_outlier: bool
    explanation: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _stddev_outlier(
    value: float | None,
    median: float | None,
    stddev: float | None,
    threshold: float,
) -> bool:
    """True if value deviates more than threshold * stddev from median."""
    if value is None or median is None or stddev is None or stddev == 0:
        return False
    return abs(value - median) > threshold * stddev


def _compute_fit_score(track: TrackData, profile: PlaylistProfile) -> float:
    if not track.genres:
        return 0.0
    total = sum(profile.genre_weights.get(g, 0.0) for g in track.genres)
    return total / max(len(track.genres), 1)


def _compute_explicit_outlier(track: TrackData, profile: PlaylistProfile) -> bool:
    """
    Flag if the track's explicit status is inconsistent with the playlist norm.
    Only fires when the playlist has a strong lean (< 15% or > 85% explicit).
    """
    if profile.explicit_ratio < 0.15 and track.explicit:
        return True
    if profile.explicit_ratio > 0.85 and not track.explicit:
        return True
    return False


def _build_explanation(track: TrackData, profile: PlaylistProfile, scored: ScoredTrack) -> str:
    parts: list[str] = []

    if scored.is_genre_outlier:
        playlist_top = [g for g, _ in profile.top_genres[:3]]
        parts.append(
            f"Genre: [{', '.join(track.genres[:3]) or 'none'}] "
            f"vs playlist: [{', '.join(playlist_top)}]"
        )

    if scored.is_vibe_outlier:
        parts.append(
            f"Vibe: {track.energy_tier} energy genres "
            f"in a {profile.dominant_energy_tier} energy playlist"
        )

    if scored.is_year_outlier and track.release_year is not None:
        parts.append(
            f"Era: released {track.release_year} — "
            f"playlist median {int(profile.year_median)} "  # type: ignore[arg-type]
            f"(±{profile.year_stddev:.1f} yrs)"
        )

    if scored.is_popularity_outlier and track.popularity is not None:
        direction = "popular" if track.popularity > (profile.popularity_median or 0) else "obscure"
        parts.append(
            f"Popularity: {track.popularity}/100 — playlist median "
            f"{profile.popularity_median:.0f} (track is more {direction})"
        )

    if scored.is_duration_outlier and track.duration_ms is not None:
        track_min = track.duration_ms / 60000
        median_min = (profile.duration_median or 0) / 60000
        parts.append(
            f"Duration: {track_min:.1f} min — playlist median {median_min:.1f} min"
        )

    if scored.is_explicit_outlier:
        status = "explicit" if track.explicit else "clean"
        ratio_pct = profile.explicit_ratio * 100
        parts.append(
            f"Explicit: track is {status} in a "
            f"{'mostly clean' if profile.explicit_ratio < 0.15 else 'mostly explicit'} "
            f"playlist ({ratio_pct:.0f}% explicit)"
        )

    if scored.is_artist_popularity_outlier and track.artist_popularity is not None:
        direction = "mainstream" if track.artist_popularity > (profile.artist_popularity_median or 0) else "underground"
        parts.append(
            f"Artist: popularity {track.artist_popularity:.0f}/100 — playlist median "
            f"{profile.artist_popularity_median:.0f} (more {direction})"
        )

    if scored.is_tempo_outlier and track.tempo is not None:
        parts.append(
            f"Tempo: {track.tempo:.0f} BPM — playlist median "
            f"{profile.tempo_median:.0f} BPM"
        )

    return "\n       ".join(parts) if parts else "No specific flags"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_tracks(
    tracks: list[TrackData],
    profile: PlaylistProfile,
    outlier_percentile: float = 20.0,
    year_threshold: float = 1.5,
    popularity_threshold: float = 1.5,
    duration_threshold: float = 1.5,
    artist_popularity_threshold: float = 1.5,
    tempo_threshold: float = 1.5,
) -> list[ScoredTrack]:
    """
    Score all classifiable tracks. Returns ScoredTrack list.
    Callers can zero out individual flag fields to disable specific detections.
    Returns empty list (with warning) if all tracks are unclassifiable.
    """
    classifiable = [t for t in tracks if not t.unclassifiable]

    if not classifiable:
        print("WARNING: All tracks in this playlist are unclassifiable (no genre data). "
              "Skipping scoring.")
        return []

    # Genre fit scores
    raw_scores: list[tuple[TrackData, float]] = [
        (t, _compute_fit_score(t, profile)) for t in classifiable
    ]
    sorted_scores = sorted(raw_scores, key=lambda x: x[1])
    cutoff_index = max(0, int(len(sorted_scores) * outlier_percentile / 100) - 1)
    cutoff_score = sorted_scores[cutoff_index][1] if sorted_scores else 0.0

    results: list[ScoredTrack] = []
    for track, fit in raw_scores:
        stub = ScoredTrack(
            track=track,
            fit_score=fit,
            is_genre_outlier=fit <= cutoff_score,
            is_year_outlier=_stddev_outlier(
                float(track.release_year) if track.release_year else None,
                profile.year_median, profile.year_stddev, year_threshold,
            ),
            is_vibe_outlier=(
                track.energy_tier is not None
                and profile.dominant_energy_tier is not None
                and track.energy_tier != profile.dominant_energy_tier
            ),
            is_popularity_outlier=_stddev_outlier(
                float(track.popularity) if track.popularity is not None else None,
                profile.popularity_median, profile.popularity_stddev, popularity_threshold,
            ),
            is_duration_outlier=_stddev_outlier(
                float(track.duration_ms) if track.duration_ms is not None else None,
                profile.duration_median, profile.duration_stddev, duration_threshold,
            ),
            is_explicit_outlier=_compute_explicit_outlier(track, profile),
            is_artist_popularity_outlier=_stddev_outlier(
                track.artist_popularity,
                profile.artist_popularity_median, profile.artist_popularity_stddev,
                artist_popularity_threshold,
            ),
            is_tempo_outlier=_stddev_outlier(
                track.tempo,
                profile.tempo_median, profile.tempo_stddev, tempo_threshold,
            ),
            explanation="",
        )
        stub.explanation = _build_explanation(track, profile, stub)
        results.append(stub)

    return results
