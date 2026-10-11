"""Stage 4 — Discogs-EffNet audio embeddings and 400-way style probabilities.

Model: https://essentia.upf.edu/models/ — EffNet trained on Discogs, plus the
genre_discogs400 classification head. Runs on CPU, ~0.3s per 30s preview.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import threading
from pathlib import Path

import numpy as np

from .util import DATA, ROOT, read_json, write_json

MODELS = ROOT / "models"
EFFNET = "discogs-effnet-bs64-1.pb"
STYLE_HEAD = "genre_discogs400-discogs-effnet-1.pb"
SAMPLE_RATE = 16000

_tls = threading.local()


def _models():
    """Lazily build one model set per worker thread."""
    if not hasattr(_tls, "set"):
        from essentia.standard import (
            RhythmExtractor2013,
            TensorflowPredict2D,
            TensorflowPredictEffnetDiscogs,
        )

        effnet = TensorflowPredictEffnetDiscogs(
            graphFilename=str(MODELS / EFFNET), output="PartitionedCall:1"
        )
        head = TensorflowPredict2D(
            graphFilename=str(MODELS / STYLE_HEAD),
            input="serving_default_model_Placeholder",
            output="PartitionedCall:0",
        )
        _tls.set = (effnet, head, RhythmExtractor2013(method="degara"))
    return _tls.set


def analyse(path: str) -> tuple[np.ndarray, np.ndarray, float]:
    """(1280-d embedding, 400-d styles, bpm) — the first two averaged over patches."""
    from essentia.standard import MonoLoader

    audio = MonoLoader(filename=path, sampleRate=SAMPLE_RATE, resampleQuality=4)()
    if audio.size < SAMPLE_RATE:  # under a second of audio is not worth modelling
        raise ValueError("audio too short")
    effnet, head, rhythm = _models()
    embeddings = effnet(audio)
    styles = head(embeddings)
    try:
        bpm = float(rhythm(audio)[0])
    except Exception:
        bpm = 0.0
    return embeddings.mean(axis=0), styles.mean(axis=0), bpm


def style_names() -> list[str]:
    meta = json.loads((MODELS / "genre_discogs400-discogs-effnet-1.json").read_text())
    return list(meta["classes"])


def run(workers: int = 4) -> dict:
    records = (read_json(DATA / "enriched.json", {}) or {}).get("tracks", [])
    index = (read_json(DATA / "previews.json", {}) or {}).get("index", {})
    if not records or not index:
        raise SystemExit("run the fetch, enrich and previews stages first")

    out_path = DATA / "embeddings.npz"
    ids: list[str] = []
    emb: list[np.ndarray] = []
    sty: list[np.ndarray] = []
    bpm: list[float] = []
    failed: list[dict] = []

    if out_path.exists():
        prev = np.load(out_path, allow_pickle=True)
        ids = list(prev["videoIds"])
        emb = list(prev["X"])
        sty = list(prev["S"])
        bpm = list(prev["BPM"]) if "BPM" in prev else [0.0] * len(ids)
        print(f"embed: reusing {len(ids)} cached vectors")

    todo = [vid for vid in index if vid not in set(ids)]
    print(f"embed: {len(todo)} previews to analyse on {workers} workers")

    def work(vid: str):
        return vid, analyse(index[vid])

    done = 0
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, v): v for v in todo}
        for fut in cf.as_completed(futures):
            vid = futures[fut]
            try:
                _, (e, s, b) = fut.result()
            except Exception as exc:
                failed.append({"videoId": vid, "error": str(exc)[:120]})
                done += 1
                continue
            ids.append(vid)
            emb.append(e.astype(np.float32))
            sty.append(s.astype(np.float32))
            bpm.append(float(b))
            done += 1
            if done % 100 == 0:
                print(f"  {done}/{len(todo)}")
                np.savez_compressed(out_path, videoIds=np.array(ids), X=np.stack(emb),
                                    S=np.stack(sty), BPM=np.array(bpm))

    if emb:
        np.savez_compressed(out_path, videoIds=np.array(ids), X=np.stack(emb),
                            S=np.stack(sty), BPM=np.array(bpm))
    write_json(DATA / "styles.json", {"classes": style_names()})
    print(f"embed: {len(ids)} vectors, {len(failed)} failed -> data/embeddings.npz")
    return {"vectors": len(ids), "failed": failed}
