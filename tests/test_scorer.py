import unittest
from datetime import datetime, timezone

from classifier.profiler import PlaylistProfile, TrackData
from classifier.scorer import (
    _stddev_outlier,
    _compute_explicit_outlier,
    build_tag_vectors,
    run_isolation_forest,
    score_tracks,
    ScoredTrack,
)


class TestStddevOutlier(unittest.TestCase):
    """Tests for _stddev_outlier threshold detection."""

    def test_no_outlier_within_threshold(self):
        """Value within threshold * stddev should not be flagged."""
        result = _stddev_outlier(
            value=100.0,
            median=100.0,
            stddev=10.0,
            threshold=2.0,
        )
        self.assertFalse(result)

    def test_outlier_exceeds_threshold(self):
        """Value exceeding threshold * stddev should be flagged."""
        result = _stddev_outlier(
            value=140.0,  # 40 units from median
            median=100.0,
            stddev=10.0,
            threshold=2.0,  # threshold is 2 * 10 = 20 units
        )
        self.assertTrue(result)

    def test_negative_deviation_is_outlier(self):
        """Negative deviations exceeding threshold should be flagged."""
        result = _stddev_outlier(
            value=60.0,  # 40 units below median
            median=100.0,
            stddev=10.0,
            threshold=2.0,
        )
        self.assertTrue(result)

    def test_none_value_returns_false(self):
        """None value should return False."""
        result = _stddev_outlier(
            value=None,
            median=100.0,
            stddev=10.0,
            threshold=2.0,
        )
        self.assertFalse(result)

    def test_none_median_returns_false(self):
        """None median should return False."""
        result = _stddev_outlier(
            value=100.0,
            median=None,
            stddev=10.0,
            threshold=2.0,
        )
        self.assertFalse(result)

    def test_zero_stddev_returns_false(self):
        """Zero stddev should return False (no variance)."""
        result = _stddev_outlier(
            value=100.0,
            median=100.0,
            stddev=0.0,
            threshold=2.0,
        )
        self.assertFalse(result)

    def test_boundary_at_exact_threshold(self):
        """Value exactly at threshold boundary should not be flagged."""
        result = _stddev_outlier(
            value=120.0,  # exactly 2 * 10 = 20 units from median
            median=100.0,
            stddev=10.0,
            threshold=2.0,
        )
        self.assertFalse(result)


class TestComputeExplicitOutlier(unittest.TestCase):
    """Tests for explicit flag outlier detection."""

    def test_explicit_in_clean_playlist(self):
        """Explicit track in mostly-clean playlist should be flagged."""
        profile = PlaylistProfile(
            playlist_id="test",
            playlist_name="Clean",
            year_median=2020.0,
            year_stddev=5.0,
            duration_median=200000.0,
            duration_stddev=20000.0,
            explicit_ratio=0.1,
            total_tracks=100,
            classifiable_count=100,
            unclassifiable_tracks=[],
            tag_averages={},
            description="",
        )
        track = TrackData(
            id="t1",
            name="Song",
            added_at="2024-01-01T00:00:00Z",
            release_year=2020,
            duration_ms=200000,
            explicit=True,
            artist_ids=["a1"],
            artist_names=["Artist"],
            tags={},
            unclassifiable=False,
        )
        result = _compute_explicit_outlier(track, profile)
        self.assertTrue(result)

    def test_clean_in_explicit_playlist(self):
        """Clean track in mostly-explicit playlist should be flagged."""
        profile = PlaylistProfile(
            playlist_id="test",
            playlist_name="Explicit",
            year_median=2020.0,
            year_stddev=5.0,
            duration_median=200000.0,
            duration_stddev=20000.0,
            explicit_ratio=0.9,
            total_tracks=100,
            classifiable_count=100,
            unclassifiable_tracks=[],
            tag_averages={},
            description="",
        )
        track = TrackData(
            id="t1",
            name="Song",
            added_at="2024-01-01T00:00:00Z",
            release_year=2020,
            duration_ms=200000,
            explicit=False,
            artist_ids=["a1"],
            artist_names=["Artist"],
            tags={},
            unclassifiable=False,
        )
        result = _compute_explicit_outlier(track, profile)
        self.assertTrue(result)

    def test_no_flag_mixed_explicit_ratio(self):
        """Mixed explicit ratio (15-85%) should not flag."""
        profile = PlaylistProfile(
            playlist_id="test",
            playlist_name="Mixed",
            year_median=2020.0,
            year_stddev=5.0,
            duration_median=200000.0,
            duration_stddev=20000.0,
            explicit_ratio=0.5,
            total_tracks=100,
            classifiable_count=100,
            unclassifiable_tracks=[],
            tag_averages={},
            description="",
        )
        track = TrackData(
            id="t1",
            name="Song",
            added_at="2024-01-01T00:00:00Z",
            release_year=2020,
            duration_ms=200000,
            explicit=True,
            artist_ids=["a1"],
            artist_names=["Artist"],
            tags={},
            unclassifiable=False,
        )
        result = _compute_explicit_outlier(track, profile)
        self.assertFalse(result)

    def test_boundary_at_15_percent(self):
        """At exactly 15% explicit, explicit track should not be flagged."""
        profile = PlaylistProfile(
            playlist_id="test",
            playlist_name="Boundary",
            year_median=2020.0,
            year_stddev=5.0,
            duration_median=200000.0,
            duration_stddev=20000.0,
            explicit_ratio=0.15,
            total_tracks=100,
            classifiable_count=100,
            unclassifiable_tracks=[],
            tag_averages={},
            description="",
        )
        track = TrackData(
            id="t1",
            name="Song",
            added_at="2024-01-01T00:00:00Z",
            release_year=2020,
            duration_ms=200000,
            explicit=True,
            artist_ids=["a1"],
            artist_names=["Artist"],
            tags={},
            unclassifiable=False,
        )
        result = _compute_explicit_outlier(track, profile)
        self.assertFalse(result)


class TestBuildTagVectors(unittest.TestCase):
    """Tests for tag vector construction."""

    def test_empty_tracks_list(self):
        """Empty track list should return empty vocabulary and vectors."""
        vocab, vectors = build_tag_vectors([])
        self.assertEqual(vocab, [])
        self.assertEqual(vectors, {})

    def test_unclassifiable_excluded(self):
        """Unclassifiable tracks should be excluded from vocabulary."""
        track1 = TrackData(
            id="t1",
            name="Song1",
            added_at="2024-01-01T00:00:00Z",
            release_year=2020,
            duration_ms=200000,
            explicit=False,
            artist_ids=["a1"],
            artist_names=["Artist1"],
            tags={"rock": 0.8, "indie": 0.6},
            unclassifiable=False,
        )
        track2 = TrackData(
            id="t2",
            name="Song2",
            added_at="2024-01-02T00:00:00Z",
            release_year=2020,
            duration_ms=200000,
            explicit=False,
            artist_ids=["a2"],
            artist_names=["Artist2"],
            tags={},
            unclassifiable=True,
        )
        vocab, vectors = build_tag_vectors([track1, track2])
        self.assertEqual(vocab, ["indie", "rock"])
        self.assertIn("t1", vectors)
        self.assertNotIn("t2", vectors)

    def test_vocabulary_sorted(self):
        """Vocabulary should be alphabetically sorted."""
        track = TrackData(
            id="t1",
            name="Song",
            added_at="2024-01-01T00:00:00Z",
            release_year=2020,
            duration_ms=200000,
            explicit=False,
            artist_ids=["a1"],
            artist_names=["Artist"],
            tags={"zebra": 0.8, "apple": 0.6, "music": 0.7},
            unclassifiable=False,
        )
        vocab, vectors = build_tag_vectors([track])
        self.assertEqual(vocab, ["apple", "music", "zebra"])

    def test_vector_values_match_tags(self):
        """Vector values should match track tag weights."""
        track = TrackData(
            id="t1",
            name="Song",
            added_at="2024-01-01T00:00:00Z",
            release_year=2020,
            duration_ms=200000,
            explicit=False,
            artist_ids=["a1"],
            artist_names=["Artist"],
            tags={"indie": 0.6, "rock": 0.8},
            unclassifiable=False,
        )
        vocab, vectors = build_tag_vectors([track])
        self.assertEqual(vectors["t1"], [0.6, 0.8])

    def test_missing_tags_zero_filled(self):
        """Tags not in a track should be zero-filled in vector."""
        track1 = TrackData(
            id="t1",
            name="Song1",
            added_at="2024-01-01T00:00:00Z",
            release_year=2020,
            duration_ms=200000,
            explicit=False,
            artist_ids=["a1"],
            artist_names=["Artist1"],
            tags={"rock": 0.8},
            unclassifiable=False,
        )
        track2 = TrackData(
            id="t2",
            name="Song2",
            added_at="2024-01-02T00:00:00Z",
            release_year=2020,
            duration_ms=200000,
            explicit=False,
            artist_ids=["a2"],
            artist_names=["Artist2"],
            tags={"indie": 0.6},
            unclassifiable=False,
        )
        vocab, vectors = build_tag_vectors([track1, track2])
        self.assertEqual(vectors["t1"], [0.0, 0.8])
        self.assertEqual(vectors["t2"], [0.6, 0.0])


class TestRunIsolationForest(unittest.TestCase):
    """Tests for Isolation Forest outlier detection."""

    def test_too_few_tracks(self):
        """Less than 2 classifiable tracks should return all False."""
        track = TrackData(
            id="t1",
            name="Song",
            added_at="2024-01-01T00:00:00Z",
            release_year=2020,
            duration_ms=200000,
            explicit=False,
            artist_ids=["a1"],
            artist_names=["Artist"],
            tags={"rock": 0.8},
            unclassifiable=False,
        )
        result = run_isolation_forest([track], ["rock"], {"t1": [0.8]})
        self.assertEqual(result, {"t1": False})

    def test_unclassifiable_excluded(self):
        """Unclassifiable tracks should not be in results."""
        tracks = [
            TrackData(
                id="t1",
                name="Song1",
                added_at="2024-01-01T00:00:00Z",
                release_year=2020,
                duration_ms=200000,
                explicit=False,
                artist_ids=["a1"],
                artist_names=["Artist1"],
                tags={"rock": 0.8},
                unclassifiable=False,
            ),
            TrackData(
                id="t2",
                name="Song2",
                added_at="2024-01-02T00:00:00Z",
                release_year=2020,
                duration_ms=200000,
                explicit=False,
                artist_ids=["a2"],
                artist_names=["Artist2"],
                tags={},
                unclassifiable=True,
            ),
        ]
        result = run_isolation_forest(tracks, ["rock"], {"t1": [0.8]})
        self.assertIn("t1", result)
        self.assertNotIn("t2", result)

    def test_empty_vocabulary(self):
        """Empty vocabulary should return all False."""
        tracks = [
            TrackData(
                id="t1",
                name="Song1",
                added_at="2024-01-01T00:00:00Z",
                release_year=2020,
                duration_ms=200000,
                explicit=False,
                artist_ids=["a1"],
                artist_names=["Artist1"],
                tags={"rock": 0.8},
                unclassifiable=False,
            ),
            TrackData(
                id="t2",
                name="Song2",
                added_at="2024-01-02T00:00:00Z",
                release_year=2020,
                duration_ms=200000,
                explicit=False,
                artist_ids=["a2"],
                artist_names=["Artist2"],
                tags={"pop": 0.7},
                unclassifiable=False,
            ),
        ]
        result = run_isolation_forest(tracks, [], {"t1": [], "t2": []})
        self.assertEqual(result, {"t1": False, "t2": False})

    def test_large_coherent_playlist_adaptive_gate(self):
        """Large genre-coherent playlist should use adaptive gate."""
        # Build a coherent EDM playlist with one anomaly
        tracks = []
        for i in range(50):
            tracks.append(
                TrackData(
                    id=f"t{i}",
                    name=f"Track{i}",
                    added_at=f"2024-01-{(i % 28) + 1:02d}T00:00:00Z",
                    release_year=2020,
                    duration_ms=200000,
                    explicit=False,
                    artist_ids=[f"a{i}"],
                    artist_names=[f"Artist{i}"],
                    tags={"edm": 0.9, "electronic": 0.8, "dance": 0.7},
                    unclassifiable=False,
                )
            )
        # Add one anomalous track with different tags
        tracks.append(
            TrackData(
                id="anomaly",
                name="Classical",
                added_at="2024-02-01T00:00:00Z",
                release_year=2020,
                duration_ms=300000,
                explicit=False,
                artist_ids=["aclassical"],
                artist_names=["ClassicalArtist"],
                tags={"classical": 0.95, "orchestral": 0.8},
                unclassifiable=False,
            )
        )
        vocab, vectors = build_tag_vectors(tracks)
        result = run_isolation_forest(tracks, vocab, vectors)
        self.assertIn("anomaly", result)


class TestScoreTracks(unittest.TestCase):
    """Tests for track scoring."""

    def test_unclassifiable_tracks_excluded(self):
        """Unclassifiable tracks should be excluded from scoring."""
        tracks = [
            TrackData(
                id="t1",
                name="Song1",
                added_at="2024-01-01T00:00:00Z",
                release_year=2020,
                duration_ms=200000,
                explicit=False,
                artist_ids=["a1"],
                artist_names=["Artist1"],
                tags={"rock": 0.8},
                unclassifiable=False,
            ),
            TrackData(
                id="t2",
                name="Song2",
                added_at="2024-01-02T00:00:00Z",
                release_year=2020,
                duration_ms=200000,
                explicit=False,
                artist_ids=["a2"],
                artist_names=["Artist2"],
                tags={},
                unclassifiable=True,
            ),
        ]
        profile = PlaylistProfile(
            playlist_id="pl",
            playlist_name="Test",
            year_median=2020.0,
            year_stddev=5.0,
            duration_median=200000.0,
            duration_stddev=20000.0,
            explicit_ratio=0.0,
            total_tracks=2,
            classifiable_count=1,
            unclassifiable_tracks=[tracks[1]],
            tag_averages={"rock": 0.8},
            description="",
        )
        scored = score_tracks(tracks, profile, {"t1": False, "t2": False})
        self.assertEqual(len(scored), 1)
        self.assertEqual(scored[0].track.id, "t1")

    def test_all_flags_set_correctly(self):
        """All four flag types should be set based on detectors."""
        track = TrackData(
            id="t1",
            name="Outlier",
            added_at="2024-01-01T00:00:00Z",
            release_year=1990,
            duration_ms=500000,
            explicit=True,
            artist_ids=["a1"],
            artist_names=["Artist1"],
            tags={"pop": 0.9},
            unclassifiable=False,
        )
        profile = PlaylistProfile(
            playlist_id="pl",
            playlist_name="Test",
            year_median=2020.0,
            year_stddev=5.0,
            duration_median=200000.0,
            duration_stddev=20000.0,
            explicit_ratio=0.0,
            total_tracks=1,
            classifiable_count=1,
            unclassifiable_tracks=[],
            tag_averages={"rock": 0.8},
            description="",
        )
        scored = score_tracks(
            [track],
            profile,
            {"t1": True},
            year_threshold=2.0,
            duration_threshold=2.0,
        )
        self.assertEqual(len(scored), 1)
        self.assertTrue(scored[0].is_tag_outlier)
        self.assertTrue(scored[0].is_year_outlier)
        self.assertTrue(scored[0].is_duration_outlier)
        self.assertTrue(scored[0].is_explicit_outlier)


if __name__ == "__main__":
    unittest.main()
