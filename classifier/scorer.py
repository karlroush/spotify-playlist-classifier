from __future__ import annotations

from dataclasses import dataclass

from .profiler import PlaylistProfile, TrackData


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class ScoredTrack:
    track: TrackData
    is_tag_outlier: bool
    is_year_outlier: bool
    is_duration_outlier: bool
    is_explicit_outlier: bool
    explanation: str


# ---------------------------------------------------------------------------
# Tag vector helpers
# ---------------------------------------------------------------------------

def build_tag_vectors(
    tracks: list[TrackData],
) -> tuple[list[str], dict[str, list[float]]]:
    """
    Build tag vectors from ALL tracks (full vocabulary).
    Returns (vocabulary, {track_id: vector}).
    Only includes classifiable tracks (those with tags).
    """
    # Build vocabulary from all classifiable tracks
    vocab_set: set[str] = set()
    classifiable = [t for t in tracks if not t.unclassifiable]
    for t in classifiable:
        vocab_set.update(t.tags.keys())

    vocabulary = sorted(vocab_set)
    if not vocabulary:
        return vocabulary, {}

    vectors: dict[str, list[float]] = {}
    for t in classifiable:
        vectors[t.id] = [t.tags.get(tag, 0.0) for tag in vocabulary]

    return vocabulary, vectors


def run_isolation_forest(
    tracks: list[TrackData],
    vocabulary: list[str],
    vectors: dict[str, list[float]],
    contamination: float = 0.16,
    score_gate: float = -0.05,
    score_gate_fraction: float = 0.5,
) -> dict[str, bool]:
    """
    Train Isolation Forest on oldest max(20%, 35) tracks, predict all.
    Returns {track_id: is_outlier}.

    contamination sets the maximum outlier fraction (upper bound, not guarantee).
    score_gate suppresses borderline predictions: tracks flagged by contamination
    but with a decision_function score above the effective gate are not marked as
    outliers.  Scores are negative-is-anomalous; 0 is the decision boundary.

    The effective gate is adaptive: max(score_gate, score_gate_fraction * most_extreme),
    where most_extreme is the lowest (most anomalous) outlier score in this run.
    This prevents score_gate from wiping out all predictions on large, genre-coherent
    playlists where IF scores are compressed near 0.
    score_gate_fraction=0.5 means "only flag the most-anomalous half of IF predictions".
    """
    classifiable = [t for t in tracks if not t.unclassifiable and t.id in vectors]

    # Edge case: too few tracks for meaningful IF
    if len(classifiable) < 2 or not vocabulary:
        return {t.id: False for t in classifiable}

    # Sort by added_at ascending; None sorts last
    sorted_tracks = sorted(
        classifiable,
        key=lambda t: t.added_at or "9999-12-31T23:59:59Z",
    )

    training_size = max(int(0.2 * len(sorted_tracks)), 35)
    training_size = min(training_size, len(sorted_tracks))
    training_ids = {t.id for t in sorted_tracks[:training_size]}

    training_vectors = [vectors[t.id] for t in sorted_tracks if t.id in training_ids]
    all_vectors = [vectors[t.id] for t in classifiable]
    all_ids = [t.id for t in classifiable]

    from sklearn.ensemble import IsolationForest

    model = IsolationForest(
        contamination=contamination,
        random_state=42,
    )
    model.fit(training_vectors)
    predictions = model.predict(all_vectors)
    scores = model.decision_function(all_vectors)

    # Adaptive gate: scale the minimum acceptable anomaly score relative to the
    # most extreme outlier score in this run, so coherent playlists with compressed
    # score distributions are not fully suppressed by a fixed absolute threshold.
    outlier_scores = [s for pred, s in zip(predictions, scores) if pred == -1]
    if outlier_scores:
        most_extreme = min(outlier_scores)  # most negative = most anomalous
        effective_gate = max(score_gate, score_gate_fraction * most_extreme)
    else:
        effective_gate = score_gate

    # -1 = outlier, 1 = inlier; suppress borderline outliers via effective_gate
    return {
        tid: bool(pred == -1 and score <= effective_gate)
        for tid, pred, score in zip(all_ids, predictions, scores)
    }


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

    if scored.is_tag_outlier:
        avg = profile.tag_averages
        distinctive = sorted(
            [(tag, w, w - avg.get(tag, 0.0)) for tag, w in track.tags.items()
             if w - avg.get(tag, 0.0) >= 0.25],
            key=lambda x: x[2], reverse=True,
        )[:3]
        missing = sorted(
            [(tag, avg_w) for tag, avg_w in avg.items()
             if avg_w >= 0.35 and track.tags.get(tag, 0.0) <= 0.05],
            key=lambda x: x[1], reverse=True,
        )[:3]
        if distinctive or missing:
            expl_parts = []
            if distinctive:
                expl_parts.append("has [" + ", ".join(f"{t} ({w:.0%})" for t, w, _ in distinctive) + "]")
            if missing:
                expl_parts.append("lacks [" + ", ".join(f"{t} ({a:.0%} avg)" for t, a in missing) + "]")
            parts.append("Tags: " + " \u00b7 ".join(expl_parts))
        else:
            top_tags = sorted(track.tags.items(), key=lambda x: x[1], reverse=True)[:5]
            tag_str = ", ".join(f"{name} ({w:.0%})" for name, w in top_tags)
            parts.append(f"Tags: [{tag_str}] — atypical combination for this playlist")

    if scored.is_year_outlier and track.release_year is not None:
        parts.append(
            f"Era: released {track.release_year} — "
            f"playlist median {int(profile.year_median)} "  # type: ignore[arg-type]
            f"(\u00b1{profile.year_stddev:.1f} yrs)"
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

    return "\n       ".join(parts) if parts else "No specific flags"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_tracks(
    tracks: list[TrackData],
    profile: PlaylistProfile,
    tag_outliers: dict[str, bool],
    year_threshold: float = 1.5,
    duration_threshold: float = 1.5,
) -> list[ScoredTrack]:
    """
    Score all classifiable tracks. Returns ScoredTrack list.
    tag_outliers comes from run_isolation_forest().
    Returns empty list (with warning) if all tracks are unclassifiable.
    """
    classifiable = [t for t in tracks if not t.unclassifiable]

    if not classifiable:
        print("WARNING: All tracks in this playlist are unclassifiable (no tag data). "
              "Skipping scoring.")
        return []

    results: list[ScoredTrack] = []
    for track in classifiable:
        stub = ScoredTrack(
            track=track,
            is_tag_outlier=tag_outliers.get(track.id, False),
            is_year_outlier=_stddev_outlier(
                float(track.release_year) if track.release_year else None,
                profile.year_median, profile.year_stddev, year_threshold,
            ),
            is_duration_outlier=_stddev_outlier(
                float(track.duration_ms) if track.duration_ms is not None else None,
                profile.duration_median, profile.duration_stddev, duration_threshold,
            ),
            is_explicit_outlier=_compute_explicit_outlier(track, profile),
            explanation="",
        )
        results.append(stub)

    return results


def build_explanations(scored: list[ScoredTrack], profile: PlaylistProfile) -> None:
    """
    Populate explanation strings based on current flag state.
    Must be called after _disable_flags() so that disabled detectors are not
    mentioned in the text shown to the user.
    """
    for st in scored:
        st.explanation = _build_explanation(st.track, profile, st)
