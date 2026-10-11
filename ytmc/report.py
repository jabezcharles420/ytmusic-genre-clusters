"""Stage 6 — human-readable cluster report (markdown + csv)."""

from __future__ import annotations

import csv

from .util import DATA, read_json


def run(samples: int = 25) -> str:
    clusters = read_json(DATA / "clusters.json", {}) or {}
    records = (read_json(DATA / "enriched.json", {}) or {}).get("tracks", [])
    if not clusters:
        raise SystemExit("no data/clusters.json — run the cluster stage first")

    by_id = {r["videoId"]: r for r in records}
    library = read_json(DATA / "library.json", {}) or {}
    for t in library.get("tracks", []):
        by_id.setdefault(t["videoId"], {})["track"] = t

    cluster_of = {vid: c["name"] for c in clusters["clusters"] for vid in c["videoIds"]}
    total = len(by_id)
    clustered = len(cluster_of)

    rows = []
    for vid, rec in by_id.items():
        t = rec.get("track") or {}
        rows.append({
            "videoId": vid,
            "title": t.get("title", ""),
            "artist": "; ".join(t.get("artists") or []) or (rec.get("artist") or ""),
            "album": t.get("album", ""),
            "cluster": cluster_of.get(vid, "UNCLUSTERED"),
            "confidence": rec.get("confidence"),
            "release_date": rec.get("release_date"),
            "bpm": rec.get("bpm"),
            "deezer_genres": "|".join(rec.get("deezer_genres") or []),
            "itunes_genre": rec.get("itunes_genre"),
            "matched": rec.get("matched"),
        })
    rows.sort(key=lambda r: (r["cluster"], r["artist"]))
    with open(DATA / "clusters.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    core = sum(c.get("core", 0) for c in clusters["clusters"])
    lines = [
        "# YouTube Music — liked songs by genre cluster",
        "",
        f"- Liked songs in account: **{total}**",
        f"- Clustered by sound: **{core}** ({core / total:.0%})",
        f"- Nearest-centroid (audio outliers): **{clusters.get('n_snapped', 0)}**",
        f"- Placed by tags (no catalogue match): **{clusters.get('n_fallback', 0)}**",
        f"- Left unclustered: **{len(clusters['unclustered'])}**",
        f"- Clusters found: **{clusters['n_clusters']}**",
        "- Method: 30s preview → Discogs-EffNet → 400-way style head → UMAP → HDBSCAN",
        "- Clusters named from Discogs style probabilities + Last.fm tags",
        "",
        "| cluster | songs | bpm | top artists |",
        "|---|---:|---:|---|",
    ]
    for c in clusters["clusters"]:
        lines.append(f"| {c['name']} | {c['size']} | {c['bpm'] or '—'} | "
                     f"{', '.join(c['artists'][:3])} |")
    lines.append("")

    for c in clusters["clusters"]:
        lines += [
            f"## {c['name']} — {c['size']} songs",
            "",
            f"- Discogs styles: {', '.join(c['styles'][:6]) or '—'}",
            f"- Tags: {', '.join(c['tags'][:10]) or '—'}",
            f"- Mean BPM: {c['bpm'] or '—'} · Top years: {', '.join(c['years']) or '—'}",
            f"- Artists: {', '.join(c['artists'])}",
            "",
        ]
        for vid in c["videoIds"][:samples]:
            rec = by_id.get(vid, {})
            t = rec.get("track") or {}
            artist = ", ".join(t.get("artists") or []) or "?"
            lines.append(f"- {t.get('title', vid)} — {artist}"
                         f"{f'  · {rec.get('release_date')}' if rec.get('release_date') else ''}")
        if c["size"] > samples:
            lines.append(f"- … and {c['size'] - samples} more")
        lines.append("")

    text = "\n".join(lines)
    (DATA / "clusters.md").write_text(text, encoding="utf-8")
    print(f"report: {clustered}/{total} clustered -> data/clusters.md, data/clusters.csv")
    return text
