"""Stage 2 — match each liked track against Deezer, falling back to iTunes.

Deezer answers with an empty result set from this network (geo-restricted), so
its calls run through the SOCKS proxy on the `ciu` host. Deezer is preferred
because /track/{id} exposes BPM and /album/{id} exposes genre labels; iTunes is
the fallback and supplies 30s previews when Deezer has no match.

Everything is cached to data/enriched.json and is resumable.
"""

from __future__ import annotations

import concurrent.futures as cf
from difflib import SequenceMatcher

from .util import DATA, HttpClient, ensure_dirs, jaccard, make_client, norm, read_json, tokens, write_json

DEEZER = "https://api.deezer.com"
ITUNES = "https://itunes.apple.com"
ACCEPT = 0.62  # minimum confidence to accept a catalogue hit


# Stripped before comparing titles: they never change the underlying song.
_NOISE = {
    "official", "video", "audio", "lyrics", "lyric", "hd", "hq", "4k", "mv",
    "visualizer", "explicit", "feat", "ft", "featuring", "with", "prod",
    "remaster", "remastered", "version", "edit", "radio",
}


def clean_title(text: str) -> str:
    return " ".join(w for w in norm(text).split() if w not in _NOISE)


def score(track: dict, cand_title: str, cand_artist: str, cand_dur: int | None) -> float:
    """0..1 confidence that a catalogue hit is the same recording."""
    t_ratio = SequenceMatcher(None, clean_title(track["title"]), clean_title(cand_title)).ratio()
    t_jac = jaccard(tokens(clean_title(track["title"])), tokens(clean_title(cand_title)))
    title_score = max(t_ratio, t_jac)

    want = {*tokens(" ".join(track.get("artists") or [])), *tokens(track.get("album") or "")}
    got = tokens(cand_artist)
    artist_score = jaccard(want, got) if want and got else 0.4

    dur_score = 0.5
    if track.get("duration") and cand_dur:
        delta = abs(track["duration"] - cand_dur)
        dur_score = 1.0 if delta <= 3 else (0.6 if delta <= 8 else 0.1)

    return 0.55 * title_score + 0.33 * artist_score + 0.12 * dur_score


def _deezer(api: HttpClient, track: dict) -> tuple[float, dict] | None:
    """Plain-text query only: Deezer silently returns 0 rows for advanced syntax."""
    artist = " ".join(track.get("artists") or [])
    query = f"{artist} {track['title']}".strip()
    data = api.get_json(f"{DEEZER}/search/track", params={"q": query, "limit": 8})
    if not data or not data.get("data"):
        return None
    best: tuple[float, dict] | None = None
    for cand in data["data"]:
        if not cand.get("readable", True):
            continue
        s = score(track, cand.get("title", ""), (cand.get("artist") or {}).get("name", ""),
                  cand.get("duration"))
        if best is None or s > best[0]:
            best = (s, cand)
    return best


def _itunes(api: HttpClient, track: dict, country: str | None) -> tuple[float, dict] | None:
    artist = " ".join(track.get("artists") or [])
    params = {"term": f"{artist} {track['title']}".strip(), "entity": "song",
              "limit": 8, "media": "music"}
    if country:
        params["country"] = country
    data = api.get_json(f"{ITUNES}/search", params=params)
    results = (data or {}).get("results") or []
    best: tuple[float, dict] | None = None
    for cand in results:
        dur = int(cand["trackTimeMillis"] / 1000) if cand.get("trackTimeMillis") else None
        s = score(track, cand.get("trackName", ""), cand.get("artistName", ""), dur)
        if best is None or s > best[0]:
            best = (s, cand)
    return best


def enrich_track(api: HttpClient, track: dict, country: str = "IN") -> dict:
    out: dict = {"videoId": track["videoId"], "matched": False}

    dz = _deezer(api, track)
    if dz and dz[0] >= ACCEPT:
        conf, cand = dz
        album = cand.get("album") or {}
        detail = api.get_json(f"{DEEZER}/track/{cand.get('id')}") or {}
        out.update(
            source="deezer",
            matched=True,
            confidence=round(conf, 3),
            deezer_track_id=cand.get("id"),
            title=cand.get("title"),
            artist=(cand.get("artist") or {}).get("name"),
            album=album.get("title"),
            deezer_album_id=album.get("id"),
            preview=cand.get("preview") or None,
            preview_kind="mp3",
            bpm=detail.get("bpm") or None,
            release_date=detail.get("release_date") or album.get("release_date"),
            track_duration=cand.get("duration"),
        )
        return out

    it = _itunes(api, track, country)
    if (not it or it[0] < ACCEPT) and country:
        alt = _itunes(api, track, None)
        if alt and (not it or alt[0] > it[0]):
            it = alt
    if it and it[0] >= ACCEPT:
        conf, cand = it
        out.update(
            source="itunes",
            matched=True,
            confidence=round(conf, 3),
            itunes_id=cand.get("trackId"),
            title=cand.get("trackName"),
            artist=cand.get("artistName"),
            album=cand.get("collectionName"),
            itunes_genre=cand.get("primaryGenreName"),
            preview=cand.get("previewUrl") or None,
            preview_kind="m4a",
            release_date=(cand.get("releaseDate") or "")[:10] or None,
            track_duration=int(cand["trackTimeMillis"] / 1000) if cand.get("trackTimeMillis") else None,
        )
    else:
        # Nothing accepted: record the near-miss for auditing, but stay unmatched.
        if dz:
            out["rejected"] = {"source": "deezer", "title": dz[1].get("title"),
                               "confidence": round(dz[0], 3)}
        elif it:
            out["rejected"] = {"source": "itunes", "title": it[1].get("trackName"),
                               "confidence": round(it[0], 3)}
    return out


def _album_genres(api: HttpClient, album_id) -> list[str]:
    data = api.get_json(f"{DEEZER}/album/{album_id}")
    if not data:
        return []
    return [g["name"] for g in (data.get("genres") or {}).get("data", [])]


def run(workers: int = 6, country: str = "IN") -> list[dict]:
    ensure_dirs()
    library = read_json(DATA / "library.json", {}) or {}
    tracks = library.get("tracks", [])
    if not tracks:
        raise SystemExit("no data/library.json — run the fetch stage first")

    cache_path = DATA / "enriched.json"
    cache = {r["videoId"]: r for r in (read_json(cache_path, {}) or {}).get("tracks", [])}
    todo = [t for t in tracks if t["videoId"] not in cache]
    print(f"enrich: {len(cache)} cached, {len(todo)} to match")

    api = make_client()
    done = 0
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(enrich_track, api, t, country): t for t in todo}
        for fut in cf.as_completed(futures):
            t = futures[fut]
            try:
                rec = fut.result()
            except Exception as exc:  # one bad match must not kill the run
                rec = {"videoId": t["videoId"], "matched": False, "error": str(exc)[:120]}
            rec["track"] = t
            cache[t["videoId"]] = rec
            done += 1
            if done % 100 == 0:
                print(f"  {done}/{len(todo)}")
                write_json(cache_path, {"tracks": list(cache.values())})

    records = [cache[t["videoId"]] for t in tracks if t["videoId"] in cache]

    # album genre lookup is one call per album, so dedupe first
    album_ids = sorted({r.get("deezer_album_id") for r in records if r.get("deezer_album_id")})
    need = [a for a in album_ids if not any(r.get("deezer_genres") is not None
                                           for r in records if r.get("deezer_album_id") == a)]
    if need:
        print(f"enrich: album genres for {len(need)} albums")
        with cf.ThreadPoolExecutor(max_workers=workers) as pool:
            genres = dict(zip(need, pool.map(lambda a: _album_genres(api, a), need)))
        for r in records:
            aid = r.get("deezer_album_id")
            if aid in genres:
                r["deezer_genres"] = genres[aid]

    write_json(cache_path, {"tracks": records})

    matched = sum(1 for r in records if r.get("matched"))
    with_preview = sum(1 for r in records if r.get("preview"))
    print(f"enrich: matched {matched}/{len(records)} ({matched/len(records):.1%}), "
          f"preview available {with_preview} ({with_preview/len(records):.1%})")
    return records
