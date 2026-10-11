"""Stage 5 — cluster the library in Discogs style space and name each cluster.

Why style space rather than the 1280-d EffNet embedding: on a real library the
mean-pooled embedding collapses into a single blob (measured: 96% of 1,903
tracks in one HDBSCAN cluster), while the 400-way Discogs style head separates
genres cleanly (43 clusters, largest 5%). The embedding is kept for reference.

Tracks HDBSCAN marks as outliers are snapped to the nearest cluster centroid in
style space; tracks with no audio at all are placed later by tag similarity.
"""

from __future__ import annotations

import collections

import numpy as np

from .util import DATA, read_json, write_json


def _top(counter: collections.Counter, n: int = 5) -> list[str]:
    return [k for k, _ in counter.most_common(n) if k]


def build_tag_text(rec: dict, lastfm: dict) -> list[str]:
    tags: list[str] = []
    t = rec.get("track") or {}
    artist = (rec.get("artist") or (t.get("artists") or [""])[0] or "").strip()
    title = (rec.get("title") or t.get("title") or "").strip()
    for key, store in ((f"{artist}\x1f{title}", lastfm.get("tracks", {})),
                       (artist, lastfm.get("artists", {}))):
        for name, _count in (store.get(key) or []):
            name = name.strip().lower()
            if name and name not in tags:
                tags.append(name)
    tags += [g.lower() for g in (rec.get("deezer_genres") or [])]
    if rec.get("itunes_genre"):
        tags.append(rec["itunes_genre"].lower())
    return tags


def _tag_fallback(records: list[dict], lastfm: dict, clusters: list[dict],
                  embedded: set[str]) -> dict[str, tuple[int, float]]:
    """Place tracks that have no audio embedding by tag similarity to cluster centroids.

    ~7% of a library (live sessions, uploads, obscure edits) never matches a
    catalogue and therefore never gets a preview. Rather than drop those songs,
    project their crowd tags onto the clusters we did find in audio space.
    """
    from sklearn.feature_extraction.text import TfidfVectorizer

    texts = [" ".join(build_tag_text(r, lastfm)) for r in records]
    if not any(t.strip() for t in texts):
        return {}
    matrix = TfidfVectorizer(min_df=2, sublinear_tf=True).fit_transform(texts)
    row_of = {r["videoId"]: i for i, r in enumerate(records)}

    centroids: dict[int, np.ndarray] = {}
    for c in clusters:
        rows = [row_of[v] for v in c["videoIds"] if v in row_of]
        if not rows:
            continue
        centroid = np.asarray(matrix[rows].mean(axis=0)).ravel()
        norm = float(np.linalg.norm(centroid))
        if norm > 0:
            centroids[c["id"]] = centroid / norm
    if not centroids:
        return {}

    out: dict[str, tuple[int, float]] = {}
    for r in records:
        vid = r["videoId"]
        if vid in embedded or vid not in row_of:
            continue
        row = np.asarray(matrix[row_of[vid]].todense()).ravel()
        norm = float(np.linalg.norm(row))
        if norm == 0:
            continue
        row /= norm
        cid, centroid = max(centroids.items(), key=lambda kv: float(row @ kv[1]))
        similarity = float(row @ centroid)
        if similarity >= 0.12:
            out[vid] = (cid, round(similarity, 3))
    return out


def _merge_members(members: dict[int, list[int]], style: np.ndarray,
                   threshold: float = 0.94) -> dict[int, list[int]]:
    """Merge clusters whose style centroids are near-identical.

    Complete linkage is deliberate: single-link chaining collapses this library
    from 43 clusters to 13 because ambient ~ indie ~ folk overlap pairwise.
    """
    from sklearn.cluster import AgglomerativeClustering

    keys = sorted(members)
    if len(keys) < 2:
        return members
    centroids = []
    for key in keys:
        v = style[members[key]].mean(axis=0)
        centroids.append(v / (np.linalg.norm(v) + 1e-9))
    labels = AgglomerativeClustering(n_clusters=None, distance_threshold=1 - threshold,
                                     metric="cosine", linkage="complete").fit_predict(
        np.stack(centroids))
    merged: dict[int, list[int]] = collections.defaultdict(list)
    for key, label in zip(keys, labels):
        merged[int(label)].extend(members[key])
    return merged


def _unique_name(signature: list[str], top_styles: list[str], top_probs: list[float],
                 tags: list[str], used: set[str]) -> str:
    """Genre name for a cluster, disambiguated only by equally strong genres.

    A cluster's most-voted style is the honest label; a second style is only
    appended when it is genuinely co-dominant (>=60% of the leading style's
    probability). Weak runners-up produce misleading pairs like "house k-pop",
    so those fall back to a plain numeric suffix instead.
    """
    base = signature[0] if signature else (top_styles[0] if top_styles else "cluster")
    base_prob = top_probs[0] if top_probs else 1.0
    candidates = [base]
    for name, prob in zip(top_styles[1:5], top_probs[1:5]):
        if (name and name != base and base not in name and name not in base
                and prob >= 0.6 * base_prob):
            candidates.append(f"{base} {name}")
    for tag in tags[:3]:
        if tag and tag != base and base not in tag and tag not in base:
            candidates.append(f"{base} {tag}")
    for candidate in candidates:
        if candidate not in used:
            return candidate
    n = 2
    while f"{base} {n}" in used:
        n += 1
    return f"{base} {n}"


def run(min_cluster_size: int = 4, min_samples: int = 2, n_neighbors: int = 10,
        selection: str = "leaf", merge_threshold: float = 0.98) -> dict:
    import hdbscan
    import umap

    npz = np.load(DATA / "embeddings.npz", allow_pickle=True)
    ids = list(npz["videoIds"])
    S = npz["S"]
    bpms_local = list(npz["BPM"]) if "BPM" in npz else None
    classes = (read_json(DATA / "styles.json", {}) or {}).get("classes", [])
    records = (read_json(DATA / "enriched.json", {}) or {}).get("tracks", [])
    lastfm = read_json(DATA / "lastfm.json", {}) or {}
    by_id = {r["videoId"]: r for r in records}

    # Hellinger-style transform: sqrt of a probability vector, then cosine.
    style = np.sqrt(np.clip(S, 0, None))
    style /= np.linalg.norm(style, axis=1, keepdims=True) + 1e-9
    print(f"cluster: {len(ids)} style vectors of {style.shape[1]}d")

    reduced = umap.UMAP(n_neighbors=n_neighbors, n_components=5, min_dist=0.0,
                        metric="cosine", random_state=42).fit_transform(style)
    labels = hdbscan.HDBSCAN(min_cluster_size=min_cluster_size, min_samples=min_samples,
                             metric="euclidean", cluster_selection_method=selection,
                             prediction_data=True).fit_predict(reduced)

    # Outliers still belong somewhere: snap each to the nearest style centroid.
    found = sorted({int(l) for l in labels if l >= 0})
    centroids = np.stack([style[labels == lab].mean(axis=0) for lab in found])
    centroids /= np.linalg.norm(centroids, axis=1, keepdims=True) + 1e-9
    similarity = style @ centroids.T
    nearest = np.argmax(similarity, axis=1)

    members: dict[int, list[int]] = collections.defaultdict(list)
    snapped = 0
    for i, lab in enumerate(labels):
        if lab >= 0:
            members[int(lab)].append(i)
        else:
            members[found[nearest[i]]].append(i)
            snapped += 1
    before = len(members)
    members = _merge_members(dict(members), style, merge_threshold)
    print(f"cluster: {before} raw clusters -> {len(members)} after merging "
          f"(threshold {merge_threshold}), {snapped} outliers snapped "
          f"({snapped / len(ids):.1%})")

    tag_lookup = {vid: build_tag_text(by_id.get(vid, {}), lastfm) for vid in ids}
    clusters = []
    for lab, idxs in members.items():
        style_vec = S[idxs].mean(axis=0)
        order = np.argsort(style_vec)[::-1][:6]
        top_styles = [classes[j] for j in order] if len(classes) == len(style_vec) else []
        top_probs = [float(style_vec[j]) for j in order] if top_styles else []
        tags = collections.Counter()
        for i in idxs:
            for tag in tag_lookup.get(ids[i], [])[:12]:
                tags[tag] += 1
        artists = collections.Counter()
        years = collections.Counter()
        bpms = []
        for i in idxs:
            rec = by_id.get(ids[i], {})
            t = rec.get("track") or {}
            for a in (t.get("artists") or []):
                artists[a] += 1
            year = str(rec.get("release_date") or t.get("year") or "")[:4]
            if year.isdigit():
                years[year] += 1
            local = bpms_local[i] if bpms_local is not None else 0.0
            if local and local > 20:  # degara emits 0 on failure
                bpms.append(float(local))
            elif rec.get("bpm"):
                bpms.append(float(rec["bpm"]))

        # Discogs labels are "Genre---Style"; the last segment is the useful one.
        def simplify(name: str) -> str:
            parts = [p.strip() for p in name.split("---") if p.strip()]
            return (parts[-1] if parts else name).lower()

        styles = [simplify(s) for s in top_styles]
        # What the tracks themselves classify as. A majority vote over per-track
        # argmax beats both averaging probabilities (blurs sub-styles together)
        # and z-scoring against the library mean (surfaces near-zero styles as
        # noise, e.g. "sludge metal" naming an ambient cluster).
        votes = collections.Counter(simplify(classes[int(np.argmax(S[i]))]) for i in idxs) \
            if len(classes) == S.shape[1] else collections.Counter()
        signature = [name for name, _ in votes.most_common(8)] or styles
        tags_ranked = _top(tags, 12)

        clusters.append({
            "id": int(lab),
            "size": len(idxs),
            "core": sum(1 for i in idxs if labels[i] >= 0),
            "signature": signature,
            "styles": styles,
            "style_probs": [round(p, 4) for p in top_probs],
            "tags": tags_ranked,
            "artists": _top(artists, 6),
            "years": [y for y, _ in years.most_common(3)],
            "bpm": round(sum(bpms) / len(bpms), 1) if bpms else None,
            "videoIds": [ids[i] for i in idxs],
        })

    clusters.sort(key=lambda c: -c["size"])
    used: set[str] = set()
    for c in clusters:
        c["name"] = _unique_name(c["signature"], c["styles"], c["style_probs"],
                                 c["tags"], used)
        used.add(c["name"])

    assignment = {ids[i]: lab for lab, idxs in members.items() for i in idxs}

    fallback = _tag_fallback(records, lastfm, clusters, set(ids))
    by_cid = {c["id"]: c for c in clusters}
    for vid, (cid, sim) in fallback.items():
        c = by_cid.get(cid)
        if c is None:
            continue
        c["videoIds"].append(vid)
        c["size"] += 1
        c.setdefault("tagged", {})[vid] = sim
        assignment[vid] = cid

    library_ids = {t["videoId"] for t in (read_json(DATA / "library.json", {}) or {}).get("tracks", [])}
    result = {
        "n_clusters": len(clusters),
        "n_snapped": snapped,
        "n_fallback": len(fallback),
        "clusters": clusters,
        "unclustered": sorted(vid for vid in library_ids if vid not in assignment),
        "assignment": assignment,
    }
    write_json(DATA / "clusters.json", result)
    print("cluster: -> data/clusters.json")
    for c in clusters:
        print(f"  {c['name']:<28} {c['size']:>4}  {'/'.join(c['styles'][:3])}"
              f"  {' | '.join(c['artists'][:3])}")
    return result
