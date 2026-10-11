"""Optional Last.fm tags — richer scene names than Deezer/iTunes genres.

Activated when LASTFM_API_KEY is set (env) or the key is in data/lastfm.key.
Without it every stage still runs; cluster names just come from Discogs styles.
"""

from __future__ import annotations

import concurrent.futures as cf
import os

from .util import DATA, HttpClient, ensure_dirs, read_json, write_json

API = "https://ws.audioscrobbler.com/2.0/"


def api_key() -> str | None:
    key = os.environ.get("LASTFM_API_KEY")
    if key:
        return key.strip()
    path = DATA / "lastfm.key"
    return path.read_text().strip() if path.exists() else None


def _tags(data) -> list[tuple[str, int]]:
    if not data:
        return []
    node = data.get("toptags") or {}
    return [(t.get("name", ""), int(t.get("count", 0))) for t in node.get("tag", []) if t.get("name")]


def run(workers: int = 4) -> dict:
    ensure_dirs()
    key = api_key()
    if not key:
        print("lastfm: no key, skipping (set LASTFM_API_KEY or write data/lastfm.key)")
        return {}

    records = (read_json(DATA / "enriched.json", {}) or {}).get("tracks", [])
    cache_path = DATA / "lastfm.json"
    cache = read_json(cache_path, {}) or {"tracks": {}, "artists": {}}
    api = HttpClient(min_interval=0.25, retries=3)  # Last.fm tolerates ~5 req/s

    def fetch(method: str, params: dict):
        q = {"method": method, "api_key": key, "format": "json", "autocorrect": 1, **params}
        data = api.get_json(API, params=q)
        return _tags(data)

    track_keys, artist_keys = {}, {}
    for r in records:
        t = r.get("track") or {}
        artist = (r.get("artist") or (t.get("artists") or [""])[0] or "").strip()
        title = (r.get("title") or t.get("title") or "").strip()
        if artist and title:
            track_keys[f"{artist}\x1f{title}"] = (artist, title)
        if artist:
            artist_keys[artist] = artist

    for name, store, keys, method in (
        ("lastfm track tags", cache["tracks"], track_keys, "track.getTopTags"),
        ("lastfm artist tags", cache["artists"], artist_keys, "artist.getTopTags"),
    ):
        todo = [k for k in keys if k not in store]
        if not todo:
            continue
        print(f"{name}: {len(todo)} to fetch")
        done = 0
        with cf.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {}
            for k in todo:
                artist, title = keys[k] if "\x1f" in k else (keys[k], None)
                params = {"artist": artist}
                if title:
                    params["track"] = title
                futures[pool.submit(fetch, method, params)] = k
            for fut in cf.as_completed(futures):
                k = futures[fut]
                try:
                    store[k] = fut.result()
                except Exception:
                    store[k] = []
                done += 1
                if done % 200 == 0:
                    print(f"  {done}/{len(todo)}")
                    write_json(cache_path, cache)
        write_json(cache_path, cache)

    print(f"lastfm: {len(cache['tracks'])} track tag sets, {len(cache['artists'])} artist tag sets")
    return cache
