"""Independently bind boundary-run paths and live validators to frozen hashes."""
import argparse
import hashlib
import json
from pathlib import Path


def hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(directory: Path) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest["experiment"] != "chunk_boundary_ablation" or manifest["common"]["max_chunk_chars"] != 3200:
        raise ValueError("Experiment or protocol character budget mismatch")
    artifacts = {Path(row["snapshot"]).resolve(): row["sha256"] for row in manifest["artifacts"]}
    for path, expected in artifacts.items():
        if hash_file(path) != expected:
            raise ValueError(f"Frozen artifact changed: {path}")
    for name in ("run_chunk_boundary_ablation.py", "score_chunk_boundary_ablation.py", "run_memory_ablation.py"):
        frozen = directory / "snapshot/protocol" / name
        live = Path(__file__).with_name(name)
        if hash_file(live) != hash_file(frozen):
            raise ValueError(f"Live code differs from frozen protocol: {name}")
    universe = json.loads((directory / "eligible_source.json").read_text(encoding="utf-8"))
    observed = []
    for arm in manifest["arms"]:
        for key in ("plan", "payloads"):
            path = Path(arm[key]).resolve()
            if path not in artifacts or hash_file(path) != artifacts[path]:
                raise ValueError(f"Unbound actual {key} path")
        rows = json.loads(Path(arm["plan"]).read_text(encoding="utf-8"))
        if "".join(r["raw_body"] for r in rows) != universe["raw_stream"]:
            raise ValueError("Core character universe mismatch")
        if arm["name"] == "character_boundaries" and any(
                len(r["raw_body"]) != 3200 for r in rows[:-1]):
            raise ValueError("Actual character windows do not match frozen 3200 budget")
        observed.append({"arm": arm["name"], "chunks": len(rows), "plan_sha256": artifacts[Path(arm["plan"]).resolve()]})
    return {"status": "passed", "budget": 3200, "arms": observed,
            "core_universe_sha256": manifest["core_universe_sha256"],
            "audit_script_sha256": hash_file(Path(__file__))}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.run_dir.resolve())
    (args.run_dir / "protocol_binding_audit.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
