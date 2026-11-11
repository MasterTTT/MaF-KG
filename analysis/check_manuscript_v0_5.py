"""Check manuscript tables against frozen evidence without editing that evidence."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANUSCRIPT = ROOT / "docs/manuscript/maf_kg_draft_v0_5.md"
SUPPLEMENT = ROOT / "evaluation/www_evidence_supplement_2026-10-11"
CLOSEOUT = SUPPLEMENT / "closeout_v2"
OUTPUT = ROOT / "docs/manuscript/CHECKS_v0_5_2026-10-11.json"
METHODS = {"MaF-KG": "maf", "Direct": "direct", "EDC": "edc", "iText2KG": "itext2kg"}
PROTECTED = {
    "docs/manuscript/maf_kg_draft_v0_4.md":
        "63f9bcd5b6ba358606531614ed6e5957e2bd3e1ab4f50a2109907130e9d8d3da",
    "agents/Extractor.py":
        "15b0508aa38018c55701b20948572de235540c8c0b953a0f24e8034c3fd546a0",
    "scripts/build_knowledge_units.py":
        "fd74eaa8a0ee271dd86e848329026522eebeefc9d7283c4bee6ef5573e8810db",
    "docs/experiment_knowledge_unit_layer2.md":
        "713f97c6a53738600ee8e5f801fc3020e6225f94b7fb047e795dc94bfa20812a",
    "evaluation/www_evidence_supplement_2026-10-11/closeout_v2/master_metrics.json":
        "13ec6b454afe4bf8d7972d361e2e9b15ab20168ef932b06b34bdb05ab5df53dd",
    "evaluation/www_evidence_supplement_2026-10-11/closeout_v2/author_packet.json":
        "75955e18780c3b04dd7bfe488054f8de2409f2e2a3a12cf582246c56f3764e38",
}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ratio(metric: dict, percentage: bool = True) -> str:
    value = f"{metric['numerator']}/{metric['denominator']}"
    if percentage:
        value += f" ({100 * metric['numerator'] / metric['denominator']:.1f}%)"
    return value


def table(text: str, caption: str) -> dict[str, list[str]]:
    start = text.index(f"**Table {caption}.")
    body = text[start:]
    block = re.search(r"(?m)^\|[^\n]+\n(?:\|[^\n]+\n)+", body)
    if block is None:
        raise ValueError(f"Table {caption} missing")
    rows = block.group().strip().splitlines()[2:]
    return {
        cells[0]: cells[1:]
        for line in rows
        if (cells := [cell.strip() for cell in line.strip().strip("|").split("|")])
    }


def word_count(text: str) -> int:
    return len(re.findall(r"\b[A-Za-z]+(?:[-'][A-Za-z]+)*\b", text))


def sections(text: str) -> dict[str, int]:
    blocks = re.split(r"(?m)^(## .+)$", text)
    return {blocks[i][3:]: word_count(blocks[i + 1]) for i in range(1, len(blocks), 2)}


def main() -> None:
    text = MANUSCRIPT.read_text(encoding="utf-8")
    checks: list[str] = []

    def check(name: str, condition: bool) -> None:
        if not condition:
            raise AssertionError(name)
        checks.append(name)

    e1 = load(CLOSEOUT / "e1_metrics.json")
    e3 = load(CLOSEOUT / "e3_metrics.json")
    t2, t3, t4 = (table(text, name) for name in ("2", "3", "4"))
    s1, s2 = table(text, "S1"), table(text, "S2")
    costs = {}
    for label, method in METHODS.items():
        metrics = e1["per_method"][method]
        ledger = load(SUPPLEMENT / f"budgets/e1_ledgers_a2/{method}.json")
        check(f"{method}: all usage reported", all(
            "total_tokens" in row.get("usage", {}) for row in ledger["requests"]
        ))
        tokens = sum(row["usage"]["total_tokens"] for row in ledger["requests"])
        costs[method] = {"tokens": tokens, "requests": len(ledger["requests"])}
        expected = [ratio(metrics[key]) for key in (
            "complete_required", "recognized_required", "condition_retention"
        )] + [str(len(ledger["requests"])), f"{tokens:,}"]
        check(f"Table 2: {label}", t2[label] == expected)
        den = metrics["relation_denominator"]
        full, partial = metrics["relation_full"], metrics["relation_partial"]
        expected = [ratio({"numerator": n, "denominator": den})
                    for n in (full, partial, full + partial)]
        check(f"Table 3: {label}", t3[label] == expected)
        runs = list(e1["per_run"].values())
        matched = {(r["section"], r["repeat"]): r for r in runs if r["method"] == method}
        expected = [ratio(matched[(section, repeat)]["complete_required"], False)
                    for section, repeat in (("input_I", 1), ("input_I", 2),
                                            ("input_O", 1), ("input_O", 2))]
        check(f"Table S1: {label}", s1[label] == expected)
        check(f"Table S2: {label}", s2[label] == [
            ratio(metrics["complete_core"], False), ratio(metrics["complete_detail"], False)
        ])

    for label, data in (("Present", e1), ("Absent", e3)):
        metrics = data["per_method"]["maf"]
        expected = [ratio(metrics["complete_required"]), ratio(metrics["condition_retention"]),
                    f"{metrics['relation_full']}/{metrics['relation_denominator']}",
                    f"{metrics['relation_partial']}/{metrics['relation_denominator']}"]
        check(f"Table 4: {label}", t4[label] == expected)

    prerequisite = load(CLOSEOUT / "prerequisite/www_supplement_prerequisite_closeout.json")
    for scope, caption in (("expanded_nine_target_v4", "5"), ("original_four_target_v3", "S4")):
        rows = prerequisite["scoring"][scope]["rounds"]
        rendered = table(text, caption)
        for row in rows:
            arm = "P+S" if row["arm"] == "P_plus_S" else "P"
            label = f"{arm}, r{row['repeat']}"
            direct = row["direct_required"]
            counts = direct["counts"]
            verdict = row["actual_verdict_coverage"]
            tp_fp_fn = "/".join(str(counts[k]) for k in ("tp", "fp", "fn"))
            coverage = f"{verdict['covered_decisive_pairs']}/{verdict['decisive_reference_pairs']}"
            expected = [tp_fp_fn]
            expected.extend(f"{direct[k]:.3f}" for k in (
                ("precision", "recall", "f1") if caption == "5" else ("f1",)
            ))
            expected.append(coverage)
            check(f"Table {caption}: {label}", rendered[label] == expected)

    prefix = load(CLOSEOUT / "prefix_metrics.json")
    trajectory = prefix["trajectory"]
    check("Prefix trajectory exists", bool(trajectory))
    s3 = table(text, "S3")
    for label, method in (("Direct", "direct"), ("MaF-KG", "maf")):
        for repeat in (1, 2):
            rows = {row["range"]: row for row in trajectory
                    if row["method"] == method and row["repeat"] == repeat}
            expected = ["Failed" if rows[f"input_I{i}"]["status"] == "failed"
                        else ratio(rows[f"input_I{i}"]["complete"], False)
                        for i in (1, 2, 3)]
            check(f"Table S3: {label} r{repeat}", s3[f"{label}, r{repeat}"] == expected)
    contrasts = load(CLOSEOUT / "memory_item_contrasts.json")["contrasts"]
    gains = sum(len(row["with_only_full"]) for row in contrasts)
    losses = sum(len(row["without_only_full"]) for row in contrasts)
    check("Memory item contrasts 4 gains / 11 losses", (gains, losses) == (4, 11))
    check("Token ratio 5.42", f"{costs['maf']['tokens'] / costs['direct']['tokens']:.2f}" == "5.42")
    packet = load(CLOSEOUT / "author_packet.json")
    check("Author recheck status", (packet["item_count"], packet["pending_count"]) == (64, 51))
    check("Draft exposes pending author review", "51 of the 64" in text and "pending author recheck" in text)

    graph_root = SUPPLEMENT / "e1_content/run_a2/runs/r1/input_I"
    maf = load(graph_root / "maf/graph.json")
    direct = load(graph_root / "direct/graph.json")
    mid = "claim_线性复合函数积分的换元公式_b1d74d8cd2f86e"
    did = "claim_第一类换元公式_ae8b5e32c71385"
    mnode = next(node for node in maf["nodes"] if node["id"] == mid)
    dnode = next(node for node in direct["nodes"] if node["id"] == did)
    check("Example: source Claim explicitly has a nonzero condition", "a≠0" in mnode["description"])
    check("Example: owned expression", any(
        expression["id"] == "expression_c32e6d54a7c7d1" and expression["owner_id"] == mid
        for expression in maf["math_expressions"]
    ))
    check("Example: actual Direct general theorem", "φ" in dnode["description"] and "a≠0" not in dnode["description"])
    for name, coverage in (("C04", "full"), ("C01", "partial")):
        review = load(CLOSEOUT / f"reviews/{name}.json")
        row = next(row for row in review["item_rows"] if row["item_id"] == "I-N13")
        check(f"Example: {name} binding", row["coverage"] == coverage)

    for path, expected in PROTECTED.items():
        check(f"Protected: {path}", digest(ROOT / path) == expected)

    for path in (MANUSCRIPT, ROOT / "docs/manuscript/REVISION_NOTES_v0_5_2026-10-11.md"):
        raw = path.read_bytes()
        decoded = raw.decode("utf-8", errors="strict")
        check(f"UTF-8/LF: {path.name}", not raw.startswith(b"\xef\xbb\xbf") and b"\r" not in raw)
        check(f"No corruption: {path.name}", "\ufffd" not in decoded and not re.search(r"\?{4,}", decoded))

    main_text, references = text.split("## References", 1)
    reference_text, appendix = references.split("## Appendix A.", 1)
    refs = {int(number) for number in re.findall(r"(?m)^(\d+)\. ", reference_text)}
    used = {int(number) for number in re.findall(r"\[(\d+)\]", main_text)}
    check("All seven references cited and numbered", refs == used == set(range(1, 8)))
    for old in ("89/118", "75.4%", "Memory contributes nothing", "unchanged to the item",
                "21.4%) | 202", "every run extracts the nine"):
        check(f"Retired result absent: {old}", old not in text)
    check("All five main tables present", all(f"**Table {i}." in text for i in range(1, 6)))
    check("No fake finished figures", not re.search(r"Figure [123] shows", text))

    before = (ROOT / "docs/manuscript/maf_kg_draft_v0_4.md").read_text(encoding="utf-8")
    checks_by_section = {}
    for title, body in re.findall(r"(?ms)^### ([^\n]+)\n(.*?)(?=^### |^## |\Z)", text):
        if title.startswith("6."):
            checks_by_section[title] = word_count(body)
    output = {
        "schema": "manuscript_evidence_checks.v1",
        "date": "2026-10-11",
        "status": "passed",
        "check_count": len(checks),
        "checks": checks,
        "manuscript": {"path": str(MANUSCRIPT), "sha256": digest(MANUSCRIPT)},
        "protected_files": PROTECTED,
        "costs": costs,
        "word_counts": {
            "rule": "English lexical tokens; includes tables, code labels and headings; excludes reference list for main count",
            "v0_4_sections": sections(before),
            "v0_5_sections": sections(text),
            "v0_5_results_subsections": checks_by_section,
            "v0_4_results_subsections": {
                title: word_count(body)
                for title, body in re.findall(r"(?ms)^### ([^\n]+)\n(.*?)(?=^### |^## |\Z)", before)
                if title.startswith("6.")
            },
            "v0_4_main": word_count(before.split("## References", 1)[0]),
            "v0_5_main": word_count(main_text),
            "v0_5_appendix": word_count(appendix),
        },
        "pending_author_items": packet["pending_count"],
        "placeholders": re.findall(r"\[[A-Z][^\]\n]*REQUIRED[^\]\n]*\]|\[[^\]\n]* required\.\]", text),
        "boundary": "Consistency checks are not independent mathematical adjudication, statistical significance or submission readiness.",
    }
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"status": output["status"], "checks": len(checks), "main_words": output["word_counts"]["v0_5_main"],
                      "old_main_words": output["word_counts"]["v0_4_main"], "author_pending": packet["pending_count"],
                      "output": str(OUTPUT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
