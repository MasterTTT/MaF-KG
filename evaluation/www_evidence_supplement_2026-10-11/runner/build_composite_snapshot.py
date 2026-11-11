"""Assemble the composite frozen snapshot for the E1 run.

Plan: docs/DSH_WWW_NARRATIVE_EVIDENCE_PLAN_2026-10-11.md section 2.2.

The reported four-arm comparison was NOT produced by one snapshot. Verified by
hash and by the run directories that actually hold the scored graphs:

  arm       round  run directory                    snapshot that produced it
  --------  -----  -------------------------------  --------------------------------
  direct    v2     runs/r{1,2}/merged/direct        external_baselines_merged_.../snapshot
  maf       v2     runs/r{1,2}/merged/maf           external_baselines_merged_.../snapshot
  edc       v3     edc_v3/r{1,2}/merged/edc         external_baselines_merged_.../edc_v3/snapshot
  itext2kg  v4     itext_v4/r{1,2}/merged/itext2kg  external_baselines_merged_.../itext_v4/snapshot

The two external adapters differ in ways that change output:

  * edc_v3's external_edc.py is the ONLY copy with `parse_compat`, which accepts
    U+FF1A as a definition separator. Without it the parser drops every
    full-width-colon definition and the run returns an all-null graph. The
    v4 and base copies have no such support.
  * itext_v4's external_itext2kg.py is the only copy with `preserve_symbols`
    and `preserve_names`, which is the reported display-symbol-preserving
    configuration.

No single frozen runner can drive both: edc_v3's runner omits the symbol flags,
itext_v4's runner omits parse_compat. This module therefore assembles a
composite snapshot and patches the ONE call site, recording the exact diff. The
adapters themselves are copied byte-identical to their frozen versions.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[3]
MERGED = WORKSPACE / "evaluation" / "external_baselines_merged_2026-10-09"

# per-file provenance: which frozen snapshot each script comes from
SCRIPT_SOURCE = {
    "external_edc.py": "edc_v3/snapshot",
    "external_itext2kg.py": "itext_v4/snapshot",
    # not present in the base snapshot; byte-identical in edc_v3 and itext_v4
    "itext_progress_guard.py": "itext_v4/snapshot",
    "external_baseline_runtime.py": "snapshot",
    "probe_go_streaming.py": "snapshot",
    "prospective_native_client.py": "snapshot",
    "run_direct_extraction_baseline.py": "snapshot",
    "run_memory_ablation.py": "snapshot",
}

RUNNER_BASE = "itext_v4/snapshot"          # supplies the full itext call
RUNNER_PATCH_FROM = "edc_v3/snapshot"      # supplies the edc parse_compat call

EDC_OLD = "            result = run(vendor, texts, client, embed, directory, TARGET_SCHEMA)\n"
EDC_NEW = (
    "            result = run(vendor, texts, client, embed, directory, TARGET_SCHEMA,\n"
    "                         replay_dir=(manifest.get(\"edc_replay_dirs\") or {}).get(str(job[\"repeat\"])),\n"
    "                         parse_compat=bool(manifest.get(\"edc_parse_compat\")))\n"
)

# Hunk 2 makes the visible-memory budgets readable from the job so the same
# runner can drive the E3 no-visible-memory arm at the E1 configuration. The
# defaults are the E1 values, so an E1 job (which carries no memory keys)
# resolves to exactly the frozen 12000/6000 and is unaffected.
MEM_HOOK_OLD = (
    "    arm = \"sequential\" if job[\"method\"] == \"maf\" else \"direct\"\n"
    "    hook_job = {\"name\": arm, \"memory_node_bytes\": 12000, \"memory_relation_bytes\": 6000}\n"
)
MEM_HOOK_NEW = (
    "    arm = \"sequential\" if job[\"method\"] == \"maf\" else \"direct\"\n"
    "    node_bytes = int(job.get(\"memory_node_bytes\", 12000))\n"
    "    relation_bytes = int(job.get(\"memory_relation_bytes\", 6000))\n"
    "    hook_job = {\"name\": arm, \"memory_node_bytes\": node_bytes,\n"
    "                \"memory_relation_bytes\": relation_bytes}\n"
)
MEM_EXTRACTOR_OLD = (
    "        \"memory_node_bytes\": 12000, \"memory_relation_bytes\": 6000})\n"
)
MEM_EXTRACTOR_NEW = (
    "        \"memory_node_bytes\": node_bytes, \"memory_relation_bytes\": relation_bytes})\n"
)

IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def build(dest: Path) -> dict:
    """Create `dest` as the composite snapshot; return a provenance record."""
    if dest.exists():
        raise FileExistsError(f"composite snapshot already exists: {dest}")
    dest.mkdir(parents=True)

    provenance: dict = {
        "schema": "maf_kg.www_supplement.composite_snapshot.v1",
        "purpose": "one snapshot able to drive all four reported arm configurations",
        "merged_root": str(MERGED),
        "files": {},
        "runner_patch": {},
        "reason": "no single frozen runner drives both the display-symbol-preserving "
                  "iText2KG configuration and the parse-compatible EDC configuration",
    }

    # shared trees are byte-identical across the three snapshots (verified)
    for group in ("agents", "prompts"):
        src = MERGED / "snapshot" / group
        shutil.copytree(src, dest / group, ignore=IGNORE)
        for p in sorted((dest / group).rglob("*")):
            if p.is_file():
                provenance["files"][f"{group}/{p.name}"] = {
                    "from": "snapshot", "sha256": sha256(p)}
    shutil.copytree(MERGED / "snapshot" / "vendor", dest / "vendor", ignore=IGNORE)
    provenance["files"]["vendor"] = {"from": "snapshot", "note": "shared vendor tree"}

    # scripts, per-file provenance
    (dest / "scripts").mkdir()
    for name, sub in SCRIPT_SOURCE.items():
        src = MERGED / sub / "scripts" / name
        if not src.exists():
            raise FileNotFoundError(src)
        shutil.copy2(src, dest / "scripts" / name)
        provenance["files"][f"scripts/{name}"] = {
            "from": sub, "sha256": sha256(dest / "scripts" / name)}

    # the runner: itext_v4 base, one call site replaced by the edc_v3 form
    base_src = MERGED / RUNNER_BASE / "scripts/run_external_baselines.py"
    base_text = base_src.read_text(encoding="utf-8")
    if base_text.count(EDC_OLD) != 1:
        raise ValueError(
            f"expected exactly one plain EDC call site in {RUNNER_BASE}, "
            f"found {base_text.count(EDC_OLD)}")
    patched = base_text.replace(EDC_OLD, EDC_NEW)
    for old, new, label in ((MEM_HOOK_OLD, MEM_HOOK_NEW, "memory hook"),
                            (MEM_EXTRACTOR_OLD, MEM_EXTRACTOR_NEW, "extractor memory")):
        if patched.count(old) != 1:
            raise ValueError(f"expected exactly one {label} site, found {patched.count(old)}")
        patched = patched.replace(old, new)
    target = dest / "scripts/run_external_baselines.py"
    target.write_text(patched, encoding="utf-8", newline="\n")

    edc_ref = (MERGED / RUNNER_PATCH_FROM / "scripts/run_external_baselines.py").read_text(
        encoding="utf-8")
    if EDC_NEW not in edc_ref:
        raise ValueError("the edc_v3 runner does not contain the expected parse_compat call")

    provenance["runner_patch"] = {
        "base": f"{RUNNER_BASE}/scripts/run_external_baselines.py",
        "base_sha256": sha256(base_src),
        "pattern_source": f"{RUNNER_PATCH_FROM}/scripts/run_external_baselines.py",
        "output_sha256": sha256(target),
        "hunks": 2,
        "hunk_1": {
            "removed": EDC_OLD.rstrip("\n"),
            "added": EDC_NEW.rstrip("\n"),
            "changed_lines": 4,
            "why": "the base runner calls EDC without parse_compat, which would silently drop "
                   "every full-width-colon definition and return an all-null graph; the patch "
                   "only forwards the two manifest flags, exactly as the edc_v3 runner does",
            "active_in": ["E1", "E1p"],
        },
        "hunk_2": {
            "removed": [MEM_HOOK_OLD.rstrip("\n"), MEM_EXTRACTOR_OLD.rstrip("\n")],
            "added": [MEM_HOOK_NEW.rstrip("\n"), MEM_EXTRACTOR_NEW.rstrip("\n")],
            "changed_lines": 7,
            "why": "reads the visible-memory budgets from the job so the same runner can drive "
                   "the E3 no-visible-memory arm at the E1 configuration instead of the older "
                   "official-interface configuration",
            "inert_for_e1": "E1 jobs carry no memory keys, so the defaults resolve to exactly "
                            "the frozen 12000/6000 bytes; asserted by the driver's guard",
            "active_in": ["E3"],
        },
        "not_changed": ["prompts", "Extractor.py", "chunking", "transport",
                        "the adapters themselves", "the default memory budgets"],
    }
    provenance["files"]["scripts/run_external_baselines.py"] = {
        "from": f"{RUNNER_BASE} + 1 hunk from {RUNNER_PATCH_FROM}",
        "sha256": sha256(target)}

    for name in ("embedding_lock.json", "requirements.lock.txt", "domain.json"):
        src = MERGED / "snapshot" / name
        if src.exists():
            shutil.copy2(src, dest / name)
            provenance["files"][name] = {"from": "snapshot", "sha256": sha256(dest / name)}

    (dest / "composite_provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    return provenance


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("composite_snapshot")
    prov = build(dest)
    print(f"composite snapshot -> {dest}")
    for name, meta in sorted(prov["files"].items()):
        if "sha256" in meta:
            print(f"  {name:44s} {meta['from']:22s} {meta['sha256'][:12]}")
    print(f"  runner patch: 1 hunk, {prov['runner_patch']['changed_lines']} lines changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
