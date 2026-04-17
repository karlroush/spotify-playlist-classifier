from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


LASTFM_API_BASE = "https://ws.audioscrobbler.com/2.0/"
MAX_RETRIES = 3
MIN_REQUEST_INTERVAL = 0.2  # ~5 req/s
CACHE_TTL_DAYS = 90   # entries with tags expire after 90 days
CACHE_TTL_EMPTY_DAYS = 7  # empty results expire sooner — artist may get tagged over time


class LastFmClient:
    def __init__(self, api_key: str, cache_path: Path | None = None, refresh_mode: bool = False) -> None:
        self._api_key = api_key
        self._cache_path = cache_path
        self._refresh_mode = refresh_mode
        # key -> {"tags": [(name, weight), ...], "fetched_at": ISO str}
        self._artist_tag_cache: dict[str, dict] = {}
        self._track_tag_cache: dict[str, dict] = {}
        self._last_request_time: float = 0.0
        if cache_path and cache_path.exists():
            self._load_disk_cache()

    # -------------------------------------------------------------------
    # Disk cache
    # -------------------------------------------------------------------

    def _is_expired(self, entry: dict) -> bool:
        """True if the cache entry is too old to trust."""
        if self._refresh_mode:
            return True
        fetched_at = entry.get("fetched_at", "")
        if not fetched_at:
            return True
        try:
            age_days = (datetime.now(timezone.utc) - datetime.fromisoformat(fetched_at)).days
            ttl = CACHE_TTL_EMPTY_DAYS if not entry.get("tags") else CACHE_TTL_DAYS
            return age_days >= ttl
        except (ValueError, TypeError):
            return True

    def _load_disk_cache(self) -> None:
        try:
            with open(self._cache_path, encoding="utf-8") as f:  # type: ignore[arg-type]
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return

        epoch = "1970-01-01T00:00:00+00:00"  # forces expiry for old-format entries

        for k, v in data.get("artists", {}).items():
            if isinstance(v, list):
                # Old format (list of tags) — treat as expired so it re-fetches
                self._artist_tag_cache[k] = {"tags": [tuple(t) for t in v], "fetched_at": epoch}  # type: ignore[misc]
            elif isinstance(v, dict):
                self._artist_tag_cache[k] = {"tags": [tuple(t) for t in v.get("tags", [])], "fetched_at": v.get("fetched_at", epoch)}  # type: ignore[misc]

        for k, v in data.get("tracks", {}).items():
            if isinstance(v, list):
                self._track_tag_cache[k] = {"tags": [tuple(t) for t in v], "fetched_at": epoch}  # type: ignore[misc]
            elif isinstance(v, dict):
                self._track_tag_cache[k] = {"tags": [tuple(t) for t in v.get("tags", [])], "fetched_at": v.get("fetched_at", epoch)}  # type: ignore[misc]

    def save_cache(self) -> None:
        """Write artist + track tag caches to disk atomically."""
        if not self._cache_path:
            return
        tmp = self._cache_path.with_suffix(".tmp")
        try:
            # Convert tuples to lists for JSON serialization
            artist_data = {k: {**v, "tags": [list(t) for t in v.get("tags", [])]}
                          for k, v in self._artist_tag_cache.items()}
            track_data = {k: {**v, "tags": [list(t) for t in v.get("tags", [])]}
                         for k, v in self._track_tag_cache.items()}
            data = {"artists": artist_data, "tracks": track_data}
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            tmp.replace(self._cache_path)
        except OSError:
            pass

    # -------------------------------------------------------------------
    # Internal HTTP
    # -------------------------------------------------------------------

    def _get(self, params: dict) -> dict | None:
        """GET with rate limiting and retry. Returns parsed JSON or None."""
        params = {**params, "api_key": self._api_key, "format": "json"}
        url = f"{LASTFM_API_BASE}?{urllib.parse.urlencode(params)}"

        delay = 1.0
        for attempt in range(MAX_RETRIES):
            # Rate limiting
            elapsed = time.time() - self._last_request_time
            if elapsed < MIN_REQUEST_INTERVAL:
                time.sleep(MIN_REQUEST_INTERVAL - elapsed)

            req = urllib.request.Request(url)
            try:
                self._last_request_time = time.time()
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read())
                    if "error" in data:
                        return None
                    return data
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    retry_after = float(exc.headers.get("Retry-After", delay))
                    time.sleep(retry_after)
                    delay = retry_after * 2
                elif exc.code in (500, 502, 503, 504):
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(delay)
                        delay = min(delay * 2, 30.0)
                    else:
                        return None
                else:
                    return None
            except (urllib.error.URLError, OSError, json.JSONDecodeError):
                if attempt < MAX_RETRIES - 1:
                    time.sleep(delay)
                    delay = min(delay * 2, 30.0)
                else:
                    return None

        return None

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------

    def get_artist_tags(self, artist_name: str) -> list[tuple[str, int]]:
        """
        Fetch top tags for an artist. Returns list of (tag_name, weight).
        Cached by lowered artist name. Expired entries are re-fetched silently.
        """
        key = artist_name.lower()
        entry = self._artist_tag_cache.get(key)
        if entry and not self._is_expired(entry):
            return entry["tags"]  # type: ignore[return-value]

        data = self._get({
            "method": "artist.getTopTags",
            "artist": artist_name,
        })

        tags: list[tuple[str, int]] = []
        if data:
            for tag in (data.get("toptags") or {}).get("tag") or []:
                name = tag.get("name", "").strip().lower()
                count = int(tag.get("count", 0))
                if name and count > 0:
                    tags.append((name, count))

        self._artist_tag_cache[key] = {"tags": tags, "fetched_at": datetime.now(timezone.utc).isoformat()}
        return tags

    def get_track_tags(self, artist_name: str, track_name: str) -> list[tuple[str, int]]:
        """
        Fetch top tags for a specific track. Cached by lowered artist + track name.
        Expired entries are re-fetched silently.
        """
        key = f"{artist_name.lower()}\x00{track_name.lower()}"
        entry = self._track_tag_cache.get(key)
        if entry and not self._is_expired(entry):
            return entry["tags"]  # type: ignore[return-value]

        data = self._get({
            "method": "track.getTopTags",
            "artist": artist_name,
            "track": track_name,
        })

        tags: list[tuple[str, int]] = []
        if data:
            for tag in (data.get("toptags") or {}).get("tag") or []:
                name = tag.get("name", "").strip().lower()
                count = int(tag.get("count", 0))
                if name and count > 0:
                    tags.append((name, count))

        self._track_tag_cache[key] = {"tags": tags, "fetched_at": datetime.now(timezone.utc).isoformat()}
        return tags

    def get_merged_tags(self, artist_names: list[str], track_name: str) -> dict[str, float]:
        """
        Merge artist + track tags. For shared tags, take the max weight.
        Normalize all weights to 0.0-1.0 (divide by 100).
        Returns empty dict if no tags found.

        artist_names should be the full list of Spotify artists for the track.
        Artist tags are fetched for the primary artist only. Track tags are tried
        with the full credited string first (e.g. "NCT & Zombie Cats") so that
        collaborative tracks match the correct Last.fm page, then fall back to
        the primary artist alone.
        """
        primary = artist_names[0] if artist_names else ""
        if not primary:
            return {}

        artist_tags = self.get_artist_tags(primary)

        # Try the joined artist string first for track lookup so that tracks
        # credited to e.g. "NCT & Zombie Cats" on Last.fm are not mismatched to
        # a same-named track by the primary artist alone.
        joined = " & ".join(artist_names)
        track_tags = self.get_track_tags(joined, track_name) if joined != primary else []
        if not track_tags:
            track_tags = self.get_track_tags(primary, track_name)

        merged: dict[str, float] = {}

        for name, weight in artist_tags:
            merged[name] = max(merged.get(name, 0.0), weight / 100.0)

        for name, weight in track_tags:
            merged[name] = max(merged.get(name, 0.0), weight / 100.0)

        return merged
