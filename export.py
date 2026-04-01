from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from profiler import PlaylistProfile, TrackData
from scorer import ScoredTrack


def build_playlist_export(
    profile: PlaylistProfile,
    scored_tracks: list[ScoredTrack],
) -> dict:
    """
    Build a serializable dict for one playlist, ready for ML training.

    Schema:
      playlist_id, playlist_name, exported_at
      profile  — playlist-level context features (needed by cross-playlist model)
      tracks   — one entry per classifiable track with:
                   features, statistical_flags, suggested_outlier, confirmed_outlier
    """
    profile_data = {
        "total_tracks": profile.total_tracks,
        "classifiable_count": profile.classifiable_count,
        "genre_weights": profile.genre_weights,
        "top_genres": [[g, round(w, 4)] for g, w in profile.top_genres],
        "dominant_energy_tier": profile.dominant_energy_tier,
        "year_median": profile.year_median,
        "year_stddev": profile.year_stddev,
        "popularity_median": profile.popularity_median,
        "popularity_stddev": profile.popularity_stddev,
        "duration_median_ms": profile.duration_median,
        "duration_stddev_ms": profile.duration_stddev,
        "explicit_ratio": round(profile.explicit_ratio, 4),
        "artist_popularity_median": profile.artist_popularity_median,
        "artist_popularity_stddev": profile.artist_popularity_stddev,
        "tempo_median": profile.tempo_median,
        "tempo_stddev": profile.tempo_stddev,
    }

    tracks_data = []
    for st in scored_tracks:
        t = st.track
        flags = {
            "genre_outlier": st.is_genre_outlier,
            "vibe_outlier": st.is_vibe_outlier,
            "year_outlier": st.is_year_outlier,
            "popularity_outlier": st.is_popularity_outlier,
            "duration_outlier": st.is_duration_outlier,
            "explicit_outlier": st.is_explicit_outlier,
            "artist_popularity_outlier": st.is_artist_popularity_outlier,
            "tempo_outlier": st.is_tempo_outlier,
        }
        suggested = any(flags.values())
        tracks_data.append({
            "track_id": t.id,
            "track_name": t.name,
            "artist_names": t.artist_names,
            "features": {
                "popularity": t.popularity,
                "duration_ms": t.duration_ms,
                "explicit": t.explicit,
                "release_year": t.release_year,
                "genres": t.genres,
                "energy_tier": t.energy_tier,
                "artist_popularity": t.artist_popularity,
                "artist_followers": t.artist_followers,
                "tempo": t.tempo,
                "fit_score": round(st.fit_score, 4),
            },
            "statistical_flags": flags,
            "suggested_outlier": suggested,
            # Set to true/false during manual curation; null = not yet reviewed
            "confirmed_outlier": None,
        })

    return {
        "playlist_id": profile.playlist_id,
        "playlist_name": profile.playlist_name,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "profile": profile_data,
        "tracks": tracks_data,
    }


def write_export(playlist_exports: list[dict], path: Path) -> None:
    """
    Write all playlist exports to a single JSON file.
    If the file already exists, merges by playlist_id — existing confirmed_outlier
    labels are preserved so manual curation is never overwritten.
    """
    existing: dict[str, dict] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            for entry in loaded:
                existing[entry["playlist_id"]] = entry
        except (json.JSONDecodeError, KeyError):
            pass  # corrupt or old format — start fresh

    for new_entry in playlist_exports:
        pl_id = new_entry["playlist_id"]
        if pl_id in existing:
            # Preserve confirmed_outlier labels the user has already set
            old_labels: dict[str, bool | None] = {
                t["track_id"]: t.get("confirmed_outlier")
                for t in existing[pl_id].get("tracks", [])
            }
            for track in new_entry["tracks"]:
                tid = track["track_id"]
                if tid in old_labels and old_labels[tid] is not None:
                    track["confirmed_outlier"] = old_labels[tid]

        existing[pl_id] = new_entry

    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(list(existing.values()), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(path)
