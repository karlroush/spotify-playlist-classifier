from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
import json


SPOTIFY_API_BASE = "https://api.spotify.com/v1"
ARTIST_BATCH_SIZE = 50
MAX_RETRIES = 5


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SpotifyAPIError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(f"Spotify API {status}: {message}")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class SpotifyClient:
    def __init__(self, access_token: str) -> None:
        self._token = access_token
        # artist_id -> {"genres": [...], "popularity": int, "followers": int}
        self._artist_cache: dict[str, dict] = {}
        # Set to False after first 403/404 on audio-analysis — skips all subsequent calls
        self._audio_analysis_available: bool = True

    # -----------------------------------------------------------------------
    # Internal HTTP
    # -----------------------------------------------------------------------

    def _get(self, url: str, params: dict | None = None) -> dict:
        """GET with retry loop: honors Retry-After on 429, exponential backoff."""
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"

        delay = 1.0
        for attempt in range(MAX_RETRIES):
            req = urllib.request.Request(
                url,
                headers={"Authorization": f"Bearer {self._token}"},
            )
            try:
                with urllib.request.urlopen(req) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    retry_after = float(exc.headers.get("Retry-After", delay))
                    print(f"  Rate limited — waiting {retry_after:.0f}s...")
                    time.sleep(retry_after)
                    delay = retry_after * 2
                elif exc.code in (500, 502, 503, 504):
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(delay)
                        delay = min(delay * 2, 60.0)
                    else:
                        body = exc.read().decode(errors="replace")
                        raise SpotifyAPIError(exc.code, body) from exc
                else:
                    body = exc.read().decode(errors="replace")
                    raise SpotifyAPIError(exc.code, body) from exc

        raise SpotifyAPIError(429, "Max retries exceeded.")

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def get_current_user_id(self) -> str:
        """Return the current user's Spotify user ID."""
        data = self._get(f"{SPOTIFY_API_BASE}/me")
        return data["id"]

    def get_user_playlists(self) -> list[dict]:
        """Return playlists owned by the current user (all pages)."""
        user_id = self.get_current_user_id()
        playlists: list[dict] = []
        url = f"{SPOTIFY_API_BASE}/me/playlists"
        params: dict = {"limit": 50}

        while url:
            data = self._get(url, params if "?" not in url else None)
            for pl in data.get("items") or []:
                if (pl.get("owner") or {}).get("id") == user_id:
                    playlists.append(pl)
            url = data.get("next") or ""
            params = {}

        return playlists

    def get_playlist_tracks(self, playlist_id: str) -> list[dict]:
        """
        Return all track dicts for a playlist, filtering out:
        - null items
        - podcast episodes (type != "track")
        - tracks with no id
        """
        tracks: list[dict] = []
        url = f"{SPOTIFY_API_BASE}/playlists/{playlist_id}/items"
        params: dict = {
            "limit": 100,
            "fields": (
                "next,items(added_at,item(id,name,explicit,duration_ms,type,"
                "artists(id,name),album(release_date)))"
            ),
        }

        while url:
            data = self._get(url, params if "?" not in url else None)
            for item in data.get("items") or []:
                track = item.get("item")
                if not track:
                    continue
                if track.get("type") == "episode":
                    continue
                if not track.get("id"):
                    continue
                track["added_at"] = item.get("added_at")
                tracks.append(track)
            url = data.get("next") or ""
            params = {}

        return tracks

    def get_artist_data(self, artist_ids: list[str]) -> dict[str, dict]:
        """
        Return artist data (genres, popularity, followers) for all given IDs.
        Results are cached across calls within a session.
        Only fetches IDs not already in the cache.
        """
        uncached = list({aid for aid in artist_ids if aid not in self._artist_cache})

        for i in range(0, len(uncached), ARTIST_BATCH_SIZE):
            batch = uncached[i : i + ARTIST_BATCH_SIZE]
            self._fetch_artists_batch(batch)

        return {aid: self._artist_cache.get(aid, {"genres": [], "popularity": None, "followers": None})
                for aid in artist_ids}

    def _fetch_artists_batch(self, ids: list[str]) -> None:
        """Fetch artists individually and populate cache."""
        for artist_id in ids:
            data = self._get(f"{SPOTIFY_API_BASE}/artists/{artist_id}")
            if data and data.get("id"):
                self._artist_cache[data["id"]] = {
                    "genres": data.get("genres") or [],
                    "popularity": data.get("popularity"),
                    "followers": (data.get("followers") or {}).get("total"),
                }

    def get_audio_analysis(self, track_id: str) -> dict | None:
        """
        Fetch audio analysis for a single track (tempo, key, mode, etc.).
        Returns None if the endpoint is unavailable for this app or on error.
        Once a 403/404 is received, all subsequent calls skip immediately
        to avoid hammering a restricted endpoint.
        """
        if not self._audio_analysis_available:
            return None
        try:
            return self._get(f"{SPOTIFY_API_BASE}/audio-analysis/{track_id}")
        except SpotifyAPIError as exc:
            if exc.status in (403, 404):
                self._audio_analysis_available = False
                print("  Audio analysis unavailable for this app — skipping for all tracks.")
                return None
            # Other errors (5xx, rate limit handled by _get) — return None, don't crash
            return None
