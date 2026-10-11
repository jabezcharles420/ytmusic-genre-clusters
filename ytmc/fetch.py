"""Stage 1 — pull the Liked Music playlist for one signed-in account."""

from __future__ import annotations

from .util import DATA, ensure_dirs, write_json


def fetch_library(authuser: int = 0, limit: int = 20000) -> list[dict]:
    from . import auth

    yt = auth.client(authuser)
    raw = yt.get_liked_songs(limit=limit)
    tracks = []
    for t in raw.get("tracks") or []:
        if not t:
            continue
        video_id = t.get("videoId")
        if not video_id:
            continue
        album = t.get("album") or {}
        tracks.append(
            {
                "videoId": video_id,
                "setVideoId": t.get("setVideoId"),
                "title": t.get("title") or "",
                "artists": [a.get("name", "") for a in (t.get("artists") or [])],
                "album": album.get("name") or "",
                "album_id": album.get("id"),
                "duration": t.get("duration_seconds"),
                "year": album.get("year") or t.get("year"),
                "videoType": t.get("videoType"),
                "likeStatus": t.get("likeStatus"),
                "isAvailable": t.get("isAvailable", True),
            }
        )
    return tracks


def run(authuser: int = 0) -> list[dict]:
    ensure_dirs()
    tracks = fetch_library(authuser)
    write_json(DATA / "library.json", {"authuser": authuser, "count": len(tracks), "tracks": tracks})
    missing_artist = sum(1 for t in tracks if not t["artists"])
    print(f"library: {len(tracks)} tracks ({missing_artist} without artist), -> data/library.json")
    return tracks
