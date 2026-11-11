"""S0.1 freeze and version isolation for the WWW narrative evidence supplement.

Plan: docs/DSH_WWW_NARRATIVE_EVIDENCE_PLAN_2026-10-11.md section 2.1.

Creates evaluation/www_evidence_supplement_2026-10-11/ and records:
  - git HEAD, branch, and the full dirty-file list (never reset/cleaned)
  - hashes of the two new source inputs and their declared line ranges
  - hashes of the frozen code snapshot that the main comparison actually used
  - the plan document itself and the current manuscript

This script performs no model calls and modifies no existing artifact.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
ROOT = WORKSPACE / "evaluation" / "www_evidence_supplement_2026-10-11"
SOURCE_REPO = Path("E:/MaF-KG")

SUBDIRS = [
    "freeze",
    "references",
    "e1_content",
    "e1_prefix",
    "e2_prerequisite",
    "reviews",
    "author_packet",
    "analysis",
    "budgets",
    "reports",
]

# Declared in plan section 3.1. Verified independently before this freeze.
NEW_INPUTS = [
    {
        "input_id": "I",
        "label": "indefinite_integral",
        "source_file": "data/raw/第四章_全章_无习题.md",
        "declared_sha256": "E9915CD5FD1EA0791BB0912FC7D12DD7D75361247F41F5FD88FEAE8788EA6ED6",
        "line_start": 5,
        "line_end": 1126,
        "declared_lines": 1122,
        "declared_chars": 39115,
        "sections": "第四章 第一节概念与性质、第二节换元积分法、第三节分部积分法",
    },
    {
        "input_id": "O",
        "label": "differential_equation",
        "source_file": "data/raw/第七章_全章_无习题.md",
        "declared_sha256": "93B4B3B103CC3817BA87400B43CD4BDACBE3BD003886EBE3A0250D5C9C763EE5",
        "line_start": 973,
        "line_end": 1809,
        "declared_lines": 837,
        "declared_chars": 17167,
        "sections": "第七章 第四节一阶线性方程、第五节可降阶高阶方程",
    },
]

# Frozen snapshot that produced the reported main comparison.
SNAPSHOT = WORKSPACE / "evaluation" / "external_baselines_merged_2026-10-09" / "itext_v4" / "snapshot"

FROZEN_ARTIFACTS = [
    "docs/DSH_WWW_NARRATIVE_EVIDENCE_PLAN_2026-10-11.md",
    "docs/manuscript/maf_kg_draft_v0_3.md",
    "docs/manuscript/maf_kg_draft_v0_2.md",
    "evaluation/external_baselines_merged_2026-10-09/four_arm_table.json",
    "evaluation/external_baselines_merged_2026-10-09/manifest.json",
    "evaluation/external_baselines_merged_2026-10-09/freeze/FREEZE_RECORD.md",
    "evaluation/paper_closeout_2026-10-10/final_closeout_v1/prerequisite_adjudication/reference_v3.json",
    "evaluation/prerequisite_new_targets_2026-10-07/run02/frozen_inputs01/catalog.json",
    "evaluation/prerequisite_new_targets_2026-10-07/run02/frozen_inputs01/ku.json",
    "evaluation/prerequisite_new_targets_2026-10-07/run02/frozen_inputs01/graph.json",
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest().upper()


def git(*args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(WORKSPACE), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {out.stderr}")
    return out.stdout


def expand(text: str, base: Path) -> str:
    """Normalise CRLF to LF and report both character conventions."""
    lf = text.replace("\r\n", "\n")
    lines = lf.split("\n")
    return lf, lines


def main() -> int:
    for sub in SUBDIRS:
        (ROOT / sub).mkdir(parents=True, exist_ok=True)

    freeze: dict = {
        "schema": "maf_kg.www_supplement.freeze.v1",
        "plan": "docs/DSH_WWW_NARRATIVE_EVIDENCE_PLAN_2026-10-11.md",
        "workspace": str(WORKSPACE),
        "source_repo": str(SOURCE_REPO),
        "no_model_calls": True,
    }

    freeze["git"] = {
        "branch": git("rev-parse", "--abbrev-ref", "HEAD").strip(),
        "head": git("rev-parse", "HEAD").strip(),
        "dirty_entries": len([x for x in git("status", "--porcelain=v1").split("\n") if x.strip()]),
        "dirty_list": [x for x in git("status", "--porcelain=v1").split("\n") if x.strip()],
        "policy": "not reset, not checked out, not cleaned",
    }

    # --- new source inputs -------------------------------------------------
    inputs = []
    problems = []
    for spec in NEW_INPUTS:
        path = SOURCE_REPO / spec["source_file"]
        entry = dict(spec)
        if not path.exists():
            problems.append(f"missing source file: {path}")
            entry["exists"] = False
            inputs.append(entry)
            continue
        raw = path.read_bytes()
        entry["exists"] = True
        entry["actual_sha256"] = sha256_file(path)
        entry["sha256_match"] = entry["actual_sha256"] == spec["declared_sha256"]
        if not entry["sha256_match"]:
            problems.append(f"sha mismatch for {spec['source_file']}")

        text = raw.decode("utf-8")
        lf, lines = expand(text, path)
        entry["file_total_lines"] = len(lines)
        entry["file_total_bytes"] = len(raw)
        lo, hi = spec["line_start"], spec["line_end"]
        seg = lines[lo - 1 : hi]
        seg_text = "\n".join(seg)
        entry["actual_lines"] = len(seg)
        entry["actual_chars_incl_lf"] = len(seg_text)
        entry["actual_chars_excl_lf"] = len(seg_text) - len(seg) + 1
        entry["line_count_match"] = entry["actual_lines"] == spec["declared_lines"]
        entry["char_count_match_incl_lf"] = entry["actual_chars_incl_lf"] == spec["declared_chars"]
        if not entry["line_count_match"]:
            problems.append(f"line count mismatch for {spec['input_id']}")
        if not entry["char_count_match_incl_lf"]:
            problems.append(f"char count mismatch for {spec['input_id']}")
        entry["first_line"] = seg[0][:120]
        entry["last_nonempty_line"] = next((x[:120] for x in reversed(seg) if x.strip()), "")
        entry["char_convention"] = "declared chars equal decoded characters including LF separators"
        inputs.append(entry)
    freeze["new_inputs"] = inputs

    # --- frozen code snapshot ---------------------------------------------
    snap = {}
    if SNAPSHOT.exists():
        # The bundled runtime is a virtual environment with ~13k files. Hash the
        # code, prompts and vendored patches per file; hash the environment as a
        # single aggregate plus its lock files, so the freeze record stays
        # reviewable while still pinning the installed interpreter contents.
        code_files = sorted(
            p
            for p in SNAPSHOT.rglob("*")
            if p.is_file()
            and "__pycache__" not in p.parts
            and "runtime" not in p.relative_to(SNAPSHOT).parts
        )
        env_files = sorted(
            p
            for p in (SNAPSHOT / "runtime").rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        ) if (SNAPSHOT / "runtime").exists() else []
        snap["path"] = str(SNAPSHOT.relative_to(WORKSPACE)).replace("\\", "/")
        snap["file_count"] = len(code_files)
        snap["files"] = {
            str(p.relative_to(SNAPSHOT)).replace("\\", "/"): sha256_file(p) for p in code_files
        }
        snap["aggregate_sha256"] = hashlib.sha256(
            json.dumps(snap["files"], sort_keys=True).encode("utf-8")
        ).hexdigest().upper()
        env_hashes = {
            str(p.relative_to(SNAPSHOT)).replace("\\", "/"): sha256_file(p) for p in env_files
        }
        snap["runtime_env"] = {
            "file_count": len(env_files),
            "aggregate_sha256": hashlib.sha256(
                json.dumps(env_hashes, sort_keys=True).encode("utf-8")
            ).hexdigest().upper(),
            "pyvenv_cfg_sha256": env_hashes.get("runtime/pyvenv.cfg"),
            "lock_file_sha256": {
                name: sha256_file(SNAPSHOT / name)
                for name in ("requirements.lock.txt", "embedding_lock.json")
                if (SNAPSHOT / name).exists()
            },
        }
    else:
        problems.append(f"frozen snapshot missing: {SNAPSHOT}")
        snap["path"] = None
    freeze["code_snapshot"] = snap

    # --- frozen artifacts --------------------------------------------------
    arts = {}
    for rel in FROZEN_ARTIFACTS:
        path = WORKSPACE / rel
        if path.exists():
            arts[rel] = {"sha256": sha256_file(path), "bytes": path.stat().st_size}
        else:
            problems.append(f"missing frozen artifact: {rel}")
            arts[rel] = {"sha256": None}
    freeze["frozen_artifacts"] = arts

    # --- reference structure cross-check ----------------------------------
    ref_path = (
        WORKSPACE
        / "evaluation/paper_closeout_2026-10-10/final_closeout_v1"
        / "prerequisite_adjudication/reference_v3.json"
    )
    if ref_path.exists():
        ref = json.loads(ref_path.read_text(encoding="utf-8"))
        pairs = ref.get("pairs")
        from collections import Counter

        freeze["reference_v3_structure"] = {
            "top_level_keys": sorted(ref.keys()),
            "has_pairs": pairs is not None,
            "has_annotations": "annotations" in ref,
            "pair_count": len(pairs or []),
            "role_counts": dict(Counter(p.get("role") for p in (pairs or []))),
            "directness_counts": {
                str(k): v
                for k, v in Counter(p.get("directness") for p in (pairs or [])).items()
            },
        }

    catalog = (
        WORKSPACE
        / "evaluation/prerequisite_new_targets_2026-10-07/run02/frozen_inputs01/catalog.json"
    )
    if catalog.exists():
        cat = json.loads(catalog.read_text(encoding="utf-8"))
        kus = cat.get("knowledge_units", [])
        freeze["e2_catalog"] = {
            "count": len(kus),
            "ids_and_names": {k["id"]: k["name"] for k in kus},
        }

    freeze["problems"] = problems
    freeze["ok"] = not problems

    out = ROOT / "freeze" / "freeze_record.json"
    out.write_text(json.dumps(freeze, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"freeze root : {ROOT}")
    print(f"git         : {freeze['git']['branch']} @ {freeze['git']['head'][:12]}")
    print(f"dirty       : {freeze['git']['dirty_entries']} entries (preserved)")
    for e in inputs:
        if e.get("exists"):
            print(
                f"input {e['input_id']:2s}   : {e['actual_lines']} lines / "
                f"{e['actual_chars_incl_lf']} chars (incl LF)  sha_match={e['sha256_match']}  "
                f"counts_match={e['line_count_match'] and e['char_count_match_incl_lf']}"
            )
    print(f"snapshot    : {snap.get('file_count')} files, agg {str(snap.get('aggregate_sha256'))[:16]}")
    if "reference_v3_structure" in freeze:
        r = freeze["reference_v3_structure"]
        print(f"reference_v3: pairs={r['pair_count']} roles={r['role_counts']} directness={r['directness_counts']}")
    if "e2_catalog" in freeze:
        print(f"e2 catalog  : {freeze['e2_catalog']['count']} KUs")
    print(f"problems    : {len(problems)}")
    for p in problems:
        print(f"  - {p}")
    print(f"\nwrote {out.relative_to(WORKSPACE)}")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
