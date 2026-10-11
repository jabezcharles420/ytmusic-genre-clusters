"""Stage 8 — build the static site bundle in docs/.

Writes docs/data.json: every liked song with its cluster, plus the cluster
profiles (styles, tags, artists, bpm) that the page uses for search and display.
Short keys keep the payload small enough to ship on GitHub Pages.
"""

from __future__ import annotations

import time

from .util import DATA, ROOT, read_json, write_json

OUT = ROOT / "docs"


def run() -> dict:
    clusters = read_json(DATA / "clusters.json", {}) or {}
    library = read_json(DATA / "library.json", {}) or {}
    enriched = {r["videoId"]: r for r in
                (read_json(DATA / "enriched.json", {}) or {}).get("tracks", [])}
    if not clusters or not library:
        raise SystemExit("run the cluster and report stages first")

    assignment = clusters.get("assignment", {})
    name_of = {c["id"]: c["name"] for c in clusters["clusters"]}

    profiles = [
        {
            "id": c["id"],
            "name": c["name"],
            "n": c["size"],
            "bpm": c["bpm"],
            "styles": c["styles"][:5],
            "tags": c["tags"][:8],
            "artists": c["artists"][:5],
        }
        for c in clusters["clusters"]
    ]

    tracks = []
    seen: set[str] = set()
    for t in library.get("tracks", []):
        vid = t["videoId"]
        if vid in seen:  # the liked playlist can list the same video twice
            continue
        seen.add(vid)
        rec = enriched.get(vid) or {}
        cid = assignment.get(vid, -1)
        tracks.append({
            "v": vid,
            "t": t.get("title") or "",
            "a": "; ".join(t.get("artists") or []) or rec.get("artist") or "",
            "al": t.get("album") or "",
            "y": (rec.get("release_date") or t.get("year") or "")[:4],
            "b": rec.get("bpm"),
            "c": cid,
            "cn": name_of.get(cid, "Unclustered"),
            "g": (rec.get("deezer_genres") or [])[:2],
            "m": bool(rec.get("matched")),
        })
    tracks.sort(key=lambda r: (r["cn"], r["a"].lower(), r["t"].lower()))

    payload = {
        "generated": time.strftime("%Y-%m-%d"),
        "stats": {
            "songs": len(tracks),
            "clustered": sum(1 for t in tracks if t["c"] >= 0),
            "clusters": len(profiles),
        },
        "clusters": profiles,
        "tracks": tracks,
    }
    OUT.mkdir(exist_ok=True)
    write_json(OUT / "data.json", payload)
    size = (OUT / "data.json").stat().st_size
    print(f"site: {len(tracks)} songs, {len(profiles)} clusters -> docs/data.json "
          f"({size / 1024:.0f} KB)")
    return payload
