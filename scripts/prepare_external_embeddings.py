"""Download a pinned multilingual ONNX embedding and freeze its asset hashes."""
import argparse
import json
import os
from pathlib import Path

from external_baseline_runtime import Embeddings, sha256
from prospective_native_client import write

REPO = "qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q"
REVISION = "faf4aa4225822f3bc6376869cb1164e8e3feedd0"
MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def prepare(output, reuse_lock=None):
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    from huggingface_hub import snapshot_download
    output.mkdir(parents=True, exist_ok=False)
    if reuse_lock is not None:
        old = json.loads(reuse_lock.read_text(encoding="utf-8"))
        if old["repo"] != REPO or old["revision"] != REVISION:
            raise ValueError("Embedding revision differs")
        for row in old["files"]:
            if sha256(Path(row["path"])) != row["sha256"]:
                raise ValueError("Existing model asset changed")
        model = Path(old["model_path"])
    else:
        model = output / "model"
        snapshot_download(REPO, revision=REVISION, local_dir=str(model),
                          allow_patterns=["*.json", "model_optimized.onnx", "README.md"])
    files = [{"path": str(p.resolve()), "sha256": sha256(p), "bytes": p.stat().st_size}
             for p in sorted(model.iterdir()) if p.is_file()]
    engine = Embeddings(output / "smoke", MODEL, output / "cache", model)
    vectors = engine(["数学函数的极限", "函数收敛的定义", "这是无关的建筑材料"])
    import numpy as np
    similarities = (np.asarray(vectors) @ np.asarray(vectors).T).tolist()
    write(output / "embedding_lock.json", {"model": MODEL, "repo": REPO, "revision": REVISION,
        "model_path": str(model.resolve()), "cache_dir": str((output / "cache").resolve()),
        "files": files, "dimension": len(vectors[0]), "provider": "CPUExecutionProvider",
        "cosine_similarities_smoke": similarities, "normalization": "L2", "pooling": "mean",
        "purpose": "Compatibility smoke only; no quality-threshold tuning",
        "adaptation": "Shared Chinese/multilingual CPU embedding; not the original baselines' paper embedder."})
    print(f"Frozen {len(files)} model assets; dimension={len(vectors[0])}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("output", type=Path)
    p.add_argument("--reuse-lock", type=Path)
    args = p.parse_args()
    prepare(args.output.resolve(), args.reuse_lock)
