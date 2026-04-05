#!/usr/bin/env python3
"""Deploy playlist descriptions from DESCRIPTIONS.json to Spotify."""

import json
import urllib.request
import urllib.error
import os
from pathlib import Path
from datetime import datetime
import sys
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
from classifier.auth import load_or_refresh_token
from classifier.spotify_client import SpotifyClient, SPOTIFY_API_BASE, SpotifyAPIError

DESCRIPTIONS_FILE = Path(__file__).parent / "output" / "DESCRIPTIONS.json"
BACKUP_DIR = Path(__file__).parent / "output"

def put_playlist_description(token: str, playlist_id: str, description: str) -> None:
    """Update a playlist's description via PUT /playlists/{id}."""
    url = f"{SPOTIFY_API_BASE}/playlists/{playlist_id}"
    body = json.dumps({"description": description}).encode()

    req = urllib.request.Request(
        url,
        data=body,
        method="PUT",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
    )
    try:
        with urllib.request.urlopen(req) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise SpotifyAPIError(exc.code, body) from exc

def main():
    # Load environment
    load_dotenv()
    client_id = os.environ.get("SPOTIFY_CLIENT_ID", "")
    if not client_id:
        print("ERROR: SPOTIFY_CLIENT_ID is required. Set it in .env or pass it directly.")
        sys.exit(1)

    token_path = Path.home() / ".spotify_classifier_tokens.json"

    # Load token
    token = load_or_refresh_token(client_id, token_path, redirect_port=8888)
    client = SpotifyClient(token)

    # Get user playlists
    print("Fetching playlists...")
    playlists = client.get_user_playlists()
    playlists_by_name = {p["name"]: p for p in playlists}

    # Save current descriptions as backup
    backup_data = {}
    for playlist in playlists:
        backup_data[playlist["name"]] = playlist.get("description", "")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = BACKUP_DIR / f"DESCRIPTIONS_backup_{timestamp}.json"
    backup_file.write_text(json.dumps(backup_data, indent=2))
    print(f"[OK] Backed up current descriptions to {backup_file.name}")

    # Load new descriptions
    if not DESCRIPTIONS_FILE.exists():
        print(f"Error: {DESCRIPTIONS_FILE} not found")
        sys.exit(1)

    new_descriptions = json.loads(DESCRIPTIONS_FILE.read_text())
    print(f"\nDeploying {len(new_descriptions)} descriptions...")

    # Deploy
    for playlist_name, new_description in new_descriptions.items():
        if playlist_name not in playlists_by_name:
            print(f"  [SKIP] {playlist_name}: playlist not found")
            continue

        playlist_id = playlists_by_name[playlist_name]["id"]
        try:
            put_playlist_description(token, playlist_id, new_description)
            print(f"  [OK] {playlist_name}")
        except Exception as e:
            print(f"  [FAIL] {playlist_name}: {e}")

    print("\nDone!")

if __name__ == "__main__":
    main()
