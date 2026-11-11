"""Generate unjudged, identity-masked prediction packets from a local manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from pathlib import Path


DEFAULT_SEED = 20261004
NODE_FIELDS = ("type", "name", "description", "logical_form", "aliases", "claim_kind")
PROTOCOL = """# Prediction Review Protocol

Status: pending human review. Packets contain predictions, not assistant judgments
or Gold. Identity masking does not create a pristine blind experiment: disclose
previous exposure and any guessed identity before review. Do not consult the
private directory, previous reviews, reference judgments or model outputs elsewhere.
The coordinator must distribute only this protocol, INDEX.md and G*.json/G*.md;
private/ is separated on disk, not encrypted or access-controlled.

## Review Unit and Source

Review every listed node and relation, including duplicates and example-specific
items. Each has a stable masked review ID. Read the predicted description and
logical form as submitted, without silently repairing them. Endpoint cards show
the actual predicted node type, name and description, not reference identities.
The complete original textbook section is supplied for context. Evidence spans
are re-read from the hash-verified textbook, with one-based inclusive chapter
lines = local lines + section start - 1. Generated evidence quotations, chunk
labels and file paths are not authoritative and are not displayed.
No evidence span means no cited evidence, not an automatic unsupported verdict:
check the supplied section and record the lines used. Citation accuracy and
content support are separate questions. Do not import assumptions absent from
the original source or apply a theorem outside its stated conditions.

## Empty Ratings

Fill source_support and ontology_type_correctness with yes/no/uncertain.
For applicable_condition_completeness use yes/no/uncertain/NA. NA means the
item has no applicable-condition obligation, not that its missing hypotheses
can be ignored. Assess description self-containment separately from conditions
in logical_form: a bare reference to a named theorem can preserve content while
leaving the description incomplete. Use description_self_contained to record
this distinction (yes/no/uncertain/NA). Cite chapter lines and reasons in notes.

For relations, rate source_support, ontology_type_correctness and
direction_correctness independently; conditions may be NA. A true statement
about two valid endpoints need not support the predicted relation or direction.
Record duplicate_group as a reviewer-assigned within-packet group ID, or leave
it null with a note if no duplication. Preserve all instances; do not delete
duplicates or example-specific statements to improve a metric. Use scope_notes
to distinguish general methods, derivations and worked-example-only facts.

Named formula as Concept versus Claim can be genuinely ambiguous. Mark type
uncertain with rationale rather than forcing a favorable or unfavorable label.
Implicit inherited hypotheses, proof-only tools, local solution branches,
example-specific facts promoted to general rules, and reverse relations require
individual inspection. Correct example-specific content may be grounded even
when its ontology/generalization scope is unsuitable. An uncertain item is not
automatically false or true. A second reviewer/adjudicator should resolve
disagreements without changing original predictions. Retain both original
ratings, reviewer provenance, prior-exposure disclosures and adjudication reasons.

## Separate Metrics After Review

No scores are produced by this generator. Report all-item counts and missing
ratings first. For nodes and relations separately, grounding precision is the
fraction of predictions judged source-supported, not finite-reference recall.
Report yes/no/uncertain/unrated counts; with complete ratings, provide a strict
lower bound yes/N and upper bound (yes+uncertain)/N, plus resolved-only
yes/(yes+no) with its denominator. Missing ratings must remain explicitly
unreviewed; do not silently exclude them or publish final precision early.

Report ontology/type correctness separately using its own yes/no/uncertain
counts and denominator. Report condition completeness only for applicable
items, explicitly counting NA, uncertainty and missingness. Description
self-containment, citation accuracy, duplicate groups and example contamination
are separate diagnostics. Relation support and direction correctness are
separate metrics; any joint-valid precision must be explicitly defined and must
not replace the component metrics. Report micro all-prediction totals and
per-packet/per-section counts; do not hide graph-size effects in a single average.

Do not compute F1 by combining this prediction precision with recall against
a finite mandatory-item reference: those have different universes. Finite
reference coverage is a separate bounded recall diagnostic, not full recall.
This packet generator never reads that reference or any prior assistant labels.
After rating/adjudication is locked, the coordinator may use the private map to
aggregate identities. Hash bindings establish provenance, not source correctness
or independent human review. No definitive Gold freeze is implied.
"""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(data: bytes):
    return json.loads(data.decode("utf-8"))


def resolve_path(value: str, base: Path) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else base / path).resolve()


def verified_bytes(path: Path, expected: str, role: str) -> bytes:
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
        raise ValueError(f"Missing/invalid {role} SHA-256: {path}")
    data = path.read_bytes()
    if digest(data) != expected.lower():
        raise ValueError(f"{role} hash mismatch: {path}")
    return data


def line_number(value, role: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"Invalid {role} line number")
    return value


def node_ratings(frozen_protocol: bool = False) -> dict:
    if frozen_protocol:
        return {"source_support": None, "ontology_admission": None,
                "condition_completeness": None, "duplicate_group": None,
                "duplicate_uncertainty": None, "review_source_lines": [],
                "explicit_condition_references": [], "notes": ""}
    return {"source_support": None, "ontology_type_correctness": None,
            "applicable_condition_completeness": None,
            "description_self_contained": None, "duplicate_group": None,
            "citation_accuracy": None, "review_source_lines": [],
            "scope_notes": "", "notes": ""}


def relation_ratings(frozen_protocol: bool = False) -> dict:
    if frozen_protocol:
        return {"source_support": None, "direction_correctness": None,
                "schema_signature": None, "review_source_lines": [], "notes": ""}
    return {"source_support": None, "ontology_type_correctness": None,
            "direction_correctness": None, "applicable_condition_completeness": None,
            "duplicate_group": None, "citation_accuracy": None,
            "review_source_lines": [], "scope_notes": "", "notes": ""}


def mapped_evidence(items, original: list[str], first: int, last: int) -> list[dict]:
    if not isinstance(items, list):
        raise ValueError("Evidence must be an array")
    spans = []
    for item in items:
        start = line_number(item["start_line"], "evidence start")
        end = line_number(item["end_line"], "evidence end")
        if not 1 <= start <= end <= last - first + 1:
            raise ValueError("Evidence line bounds exceed the verified section")
        absolute_start, absolute_end = first + start - 1, first + end - 1
        spans.append({"local_lines": [start, end],
                      "chapter_lines": [absolute_start, absolute_end],
                      "authoritative_source": "\n".join(
                          original[absolute_start - 1:absolute_end])})
    return spans


def json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def fenced(text: str, language: str = "text") -> list[str]:
    runs = re.findall(r"`+", text)
    fence = "`" * max(3, max((len(run) + 1 for run in runs), default=3))
    return [fence + language, text.rstrip("\n"), fence, ""]


def packet_markdown(packet: dict) -> str:
    lines = [f"# {packet['packet_id']}", "", f"Status: {packet['status']}.", "",
             "Read review_protocol.md. Ratings below are intentionally empty.", "",
             "## Section", ""]
    if "review_materials" in packet:
        lines += ["Common frozen ontology prompt: extraction_system.txt.",
                  "Rubric: identity-masked frozen protocol; pending assistant audit, not human Gold.", ""]
        lines += fenced(json_text(packet["review_materials"]), "json")
    lines += fenced(json_text({k: v for k, v in packet["section"].items()
                               if k != "textbook_lines"}), "json")
    for kind in ("nodes", "relations"):
        lines += [f"## {kind.title()}", ""]
        for item in packet[kind]:
            lines += [f"### {item['review_id']}", ""]
            lines += fenced(json_text(item), "json")
    lines += ["## Complete Original Textbook Section", ""]
    lines += fenced("\n".join(f"L{row['line']}: {row['text']}"
                              for row in packet["section"]["textbook_lines"]))
    return "\n".join(lines)


def assert_blinded(packet: dict, labels: set[str], paths: set[str]) -> None:
    # Fail rather than silently rewriting submitted mathematical content.
    text = json_text(packet).casefold().replace("\\\\", "/").replace("\\", "/")
    for label in labels:
        if label and re.search(r"(?<!\w)" + re.escape(label.casefold()) + r"(?!\w)", text):
            raise ValueError("Identity label embedded in public prediction content")
    for path in paths:
        normalized = path.casefold().replace("\\", "/")
        if normalized and normalized in text:
            raise ValueError("Private input/graph path embedded in public prediction content")


def masked_protocol(text: str, packets: list[dict]) -> str:
    scope_prefix = "\u8303\u56f4\u56fa\u5b9a\uff1a"
    lines = text.splitlines()
    scope_lines = [i for i, line in enumerate(lines) if line.startswith(scope_prefix)]
    if len(scope_lines) != 1:
        raise ValueError("Frozen protocol must have one identifiable scope paragraph")
    titles = sorted({p["section"]["title"] for p in packets})
    lines[scope_lines[0]] = (
        f"Scope: {', '.join(titles)}; {len(packets)} anonymous completed graphs, "
        f"{sum(len(p['nodes']) for p in packets)} nodes and "
        f"{sum(len(p['relations']) for p in packets)} relations. Review every prediction.")
    return ("\n".join(lines) + "\n\n"
            "Blinding note: only the identity-containing opening scope paragraph was replaced.\n"
            "All remaining frozen rubric text is unchanged. Original protocol hash is bound\n"
            "in packet review_materials; the original copy is private. No ratings are supplied.\n"
            "The common frozen extraction_system.txt mentions pipeline organization; that is\n"
            "verbatim shared prompt wording, not an unblinding label for an individual graph.\n"
            "Distribute only G*.json/G*.md, INDEX.md, review_protocol.md and extraction_system.txt.\n"
            "Do not distribute private/. It is separate, not encrypted or access-controlled.\n"
            "Disclose previous exposure or identity guesses; this is not a pristine blind test.\n")


def prepare(manifest_path: Path, output: Path, seed: int = DEFAULT_SEED,
            section: str | None = None, arm: str | None = None,
            protocol: Path | None = None, attempt_number: int | None = None,
            exclude_graph_hashes: tuple[str, ...] = ()) -> dict:
    manifest_path, output = Path(manifest_path).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite a review directory: {output}")
    manifest_bytes = manifest_path.read_bytes()
    manifest = load_json(manifest_bytes)
    base = manifest_path.parent
    sections = {row["id"]: row for row in manifest["sections"]}
    if len(sections) != len(manifest["sections"]):
        raise ValueError("Duplicate section IDs")
    filters = {"section": section, "arm": arm}
    if attempt_number is not None:
        if attempt_number < 1:
            raise ValueError("Attempt number must be positive")
        filters["attempt_number"] = attempt_number
    if exclude_graph_hashes:
        if any(re.fullmatch(r"[0-9a-f]{64}", h) is None for h in exclude_graph_hashes):
            raise ValueError("Invalid excluded graph SHA-256")
        filters["exclude_graph_hashes"] = sorted(set(exclude_graph_hashes))
    selected_jobs = [row for row in manifest["jobs"]
                     if (section is None or row["section"] == section)
                     and (arm is None or row["name"] == arm)
                     and (attempt_number is None or row.get("attempt_number") == attempt_number)
                     and row.get("graph_sha256") not in exclude_graph_hashes]
    jobs = [row for row in selected_jobs if row.get("status") == "completed"]
    if not jobs:
        raise ValueError("No completed graphs in manifest")
    artifacts = {}
    for row in manifest.get("artifacts", []):
        path = resolve_path(row["path"], base)
        expected = row["sha256"].lower()
        if path in artifacts and artifacts[path] != expected:
            raise ValueError("Conflicting manifest artifact hashes")
        artifacts[path] = expected
    frozen_protocol = protocol is not None
    protocol_text, prompt_text, materials, private_materials = None, None, None, None
    if frozen_protocol:
        protocol_path = Path(protocol).resolve()
        protocol_bytes = protocol_path.read_bytes()
        protocol_text = protocol_bytes.decode("utf-8")
        if not manifest.get("snapshot"):
            raise ValueError("Frozen review needs a manifest snapshot for the extractor prompt")
        prompt_path = resolve_path(manifest["snapshot"], base) / "prompts/extraction_system.txt"
        prompt_bytes = verified_bytes(prompt_path, artifacts.get(prompt_path), "extractor prompt")
        prompt_text = prompt_bytes.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        materials = {"protocol_original_sha256": digest(protocol_bytes),
                     "protocol_copy": "review_protocol.md",
                     "protocol_scope_masked": True,
                     "extractor_prompt_copy": "extraction_system.txt",
                     "extractor_prompt_original_sha256": digest(prompt_bytes),
                     "extractor_prompt_copy_sha256": digest(prompt_text.encode("utf-8"))}
        private_materials = {"protocol_path": str(protocol_path),
                             "protocol_sha256": digest(protocol_bytes),
                             "extractor_prompt_path": str(prompt_path),
                             "extractor_prompt_sha256": digest(prompt_bytes),
                             "extractor_prompt_copy_sha256": materials["extractor_prompt_copy_sha256"]}
    labels = {row.get("model", "") for row in manifest["jobs"]}
    labels.update(row.get("name", "") for row in manifest["jobs"])
    private_paths = set()
    section_cache = {}
    for section_id in {row["section"] for row in jobs}:
        section = sections[section_id]
        source = resolve_path(section["source"], base)
        input_path = resolve_path(section["input"], base)
        source_bytes = verified_bytes(source, section["source_sha256"], "source")
        expected_input = section.get("input_sha256", artifacts.get(input_path))
        if (input_path in artifacts and expected_input is not None
                and expected_input.lower() != artifacts[input_path]):
            raise ValueError("Conflicting section/input artifact hashes")
        input_bytes = verified_bytes(input_path, expected_input, "input")
        original = source_bytes.decode("utf-8").splitlines()
        local = input_bytes.decode("utf-8").splitlines()
        first = line_number(section["start_line"], "section start")
        last = line_number(section["end_line"], "section end")
        if not first <= last <= len(original):
            raise ValueError("Section line bounds exceed original textbook")
        if original[first - 1:last] != local:
            raise ValueError("Input does not match original textbook section")
        section_cache[section_id] = (original, first, last, {
            "title": section["title"], "chapter_lines": [first, last],
            "source_sha256": digest(source_bytes), "input_sha256": digest(input_bytes),
            "textbook_lines": [{"line": i, "text": original[i - 1]}
                               for i in range(first, last + 1)]}, {
            "section_id": section_id, "source_path": str(source),
            "source_sha256": digest(source_bytes), "input_path": str(input_path),
            "input_sha256": digest(input_bytes), "chapter_lines": [first, last]})
        private_paths.update((str(source), str(input_path)))
    # Stable input ordering makes the seed independent of incidental manifest order.
    jobs.sort(key=lambda row: (row["section"], row["model"], row["name"],
                               row.get("repeat", 1), str(row["directory"])))
    random.Random(seed).shuffle(jobs)
    packets, mappings = [], []
    for index, job in enumerate(jobs, 1):
        code = f"G{index:02d}"
        graph_path = resolve_path(job["directory"], base) / "graph.json"
        graph_bytes = verified_bytes(graph_path, job.get("graph_sha256"), "graph")
        graph = load_json(graph_bytes)
        nodes, relations = graph["nodes"], graph["relations"]
        if not isinstance(nodes, list) or not isinstance(relations, list):
            raise ValueError("Graph nodes/relations must be arrays")
        for field, values in (("nodes", nodes), ("relations", relations)):
            if field in job and job[field] != len(values):
                raise ValueError(f"Manifest {field} count mismatch")
        ids = {}
        for n_index, node in enumerate(nodes, 1):
            node_id = node["id"]
            if not isinstance(node_id, str) or not node_id or node_id in ids:
                raise ValueError("Missing/duplicate node ID")
            ids[node_id] = f"{code}-N{n_index:03d}"
        original, first, last, public_section, private_section = section_cache[job["section"]]
        packet_nodes = []
        for node in nodes:
            prediction = {key: node.get(key) for key in NODE_FIELDS}
            packet_nodes.append({"review_id": ids[node["id"]], "prediction": prediction,
                                 "evidence": mapped_evidence(node.get("evidence", []),
                                                             original, first, last),
                                 "ratings": node_ratings(frozen_protocol)})
        endpoint_nodes = {node["id"]: node for node in nodes}

        def endpoint(node_id):
            if node_id not in ids:
                raise ValueError("Missing relation endpoint")
            node = endpoint_nodes[node_id]
            return {"review_id": ids[node_id], **{key: node.get(key)
                    for key in ("type", "name", "description")}}

        packet_relations = []
        for r_index, relation in enumerate(relations, 1):
            packet_relations.append({"review_id": f"{code}-R{r_index:03d}",
                                     "type": relation["type"],
                                     "source": endpoint(relation["source_id"]),
                                     "target": endpoint(relation["target_id"]),
                                     "evidence": mapped_evidence(relation.get("evidence", []),
                                                                 original, first, last),
                                     "ratings": relation_ratings(frozen_protocol)})
        packet = {"schema_version": "blind-prediction-review.v1", "packet_id": code,
                  "status": "pending_assistant_blind_review" if frozen_protocol else "pending_human_review",
                  "section": public_section,
                  "reviewer": {"name": "", "previously_seen_predictions": None,
                               "identity_guess": "", "review_date": ""},
                  "nodes": packet_nodes, "relations": packet_relations}
        if materials is not None:
            packet["review_materials"] = materials
        private_paths.update((str(graph_path), str(graph_path.parent)))
        mappings.append({"packet_id": code, "model": job["model"], "arm": job["name"],
                         "repeat": job.get("repeat", 1), "graph_path": str(graph_path),
                         "graph_sha256": digest(graph_bytes), **private_section,
                         "node_mapping": [{"review_id": ids[n["id"]], "original_id": n["id"]}
                                          for n in nodes],
                         "relation_mapping": [{"review_id": f"{code}-R{k:03d}",
                                               "original_id": r.get("id"),
                                               "original_source_id": r["source_id"],
                                               "original_target_id": r["target_id"]}
                                              for k, r in enumerate(relations, 1)]})
        packets.append(packet)
    for packet in packets:
        assert_blinded(packet, labels, private_paths)
    public_protocol = PROTOCOL
    if frozen_protocol:
        public_protocol = masked_protocol(protocol_text, packets)
        assert_blinded({"text": public_protocol}, labels, private_paths)
        # Shared frozen prompt wording is retained, including pipeline terminology.
        model_labels = {row.get("model", "") for row in manifest["jobs"]}
        assert_blinded({"text": prompt_text}, model_labels, private_paths)
    mapping = {"status": "private_do_not_distribute_to_reviewers", "seed": seed,
               "manifest_path": str(manifest_path), "manifest_sha256": digest(manifest_bytes),
               "generator_sha256": digest(Path(__file__).read_bytes()),
               "filters": filters,
               "manifest_attempted_jobs": len(manifest["jobs"]),
               "manifest_completed_jobs": sum(row.get("status") == "completed" for row in manifest["jobs"]),
               "manifest_failed_jobs": sum(row.get("status") == "failed" for row in manifest["jobs"]),
               "excluded_by_scope_jobs": len(manifest["jobs"]) - len(selected_jobs),
               "completed_jobs": len(jobs), "skipped_noncompleted_jobs": len(selected_jobs) - len(jobs),
               "packets": mappings}
    if private_materials is not None:
        mapping["review_materials"] = private_materials
    # Complete validation before creating any output. Existing reviews are never replaced.
    output.mkdir(parents=True, exist_ok=False)
    (output / "private").mkdir()
    index_lines = ["# Pending Prediction Review", "", "Distribute no private files.", "",
                   "| Packet | Section | Nodes | Relations |", "|---|---|---:|---:|"]
    for packet in packets:
        (output / f"{packet['packet_id']}.json").write_text(json_text(packet), encoding="utf-8", newline="\n")
        (output / f"{packet['packet_id']}.md").write_text(packet_markdown(packet), encoding="utf-8", newline="\n")
        title = packet["section"]["title"].replace("|", "\\|").replace("\n", " ")
        index_lines.append(f"| [{packet['packet_id']}]({packet['packet_id']}.md) | {title} | "
                           f"{len(packet['nodes'])} | {len(packet['relations'])} |")
    (output / "private/unblinding_map.json").write_text(json_text(mapping), encoding="utf-8", newline="\n")
    (output / "review_protocol.md").write_text(public_protocol, encoding="utf-8", newline="\n")
    if frozen_protocol:
        (output / "extraction_system.txt").write_text(prompt_text, encoding="utf-8", newline="\n")
        (output / "private/protocol_original.md").write_text(
            protocol_text.replace("\r\n", "\n").replace("\r", "\n"), encoding="utf-8", newline="\n")
    (output / "INDEX.md").write_text("\n".join(index_lines) + "\n", encoding="utf-8", newline="\n")
    return {"packets": len(packets), "nodes": sum(len(p["nodes"]) for p in packets),
            "relations": sum(len(p["relations"]) for p in packets),
            "skipped_noncompleted_jobs": mapping["skipped_noncompleted_jobs"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--section", help="Include only this manifest section ID")
    parser.add_argument("--arm", help="Include only this manifest arm name; no model filtering")
    parser.add_argument("--protocol", type=Path, help="Use the frozen validity rubric and verified snapshot prompt")
    parser.add_argument("--attempt-number", type=int, help="Review only an explicitly recorded attempt batch")
    parser.add_argument("--exclude-graph-sha256", action="append", default=[],
                        help="Exclude an already locked-reviewed graph, independent of model or quality")
    args = parser.parse_args()
    print(json.dumps(prepare(args.manifest, args.output, args.seed,
                             section=args.section, arm=args.arm, protocol=args.protocol,
                             attempt_number=args.attempt_number,
                             exclude_graph_hashes=tuple(args.exclude_graph_sha256))))


if __name__ == "__main__":
    main()
