"""Replay frozen context-state extraction caches against each completed arm graph."""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


EXPERIMENT = "structured_extraction_context_state"
ARM_NAMES = {"flat_context", "structured_state"}
CACHE_DIR_NAME = "semantic_layer_v1_7_0"
SAFE_OFFLINE_FLAGS = (
    "verify",
    "semantic_dedupe",
    "embedding_memory",
    "coverage_review",
    "logical_form_review",
)


class AuditError(ValueError):
    """An input is incomplete, ambiguous, or inconsistent with frozen replay."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuditError(f"Cannot read UTF-8 JSON {path}: {exc}") from exc


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _load_module(path: Path, label: str) -> ModuleType:
    name = f"_context_state_replay_{label}_{hashlib.sha256(str(path).encode('utf-8')).hexdigest()[:12]}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AuditError(f"Cannot import frozen module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    previous_dont_write_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise AuditError(f"Cannot load frozen module {path}: {type(exc).__name__}: {exc}") from exc
    finally:
        sys.dont_write_bytecode = previous_dont_write_bytecode
    return module


def _verify_artifacts(pair_dir: Path, manifest: dict[str, Any]) -> dict[Path, str]:
    rows = manifest.get("artifacts")
    if not isinstance(rows, list) or not rows:
        raise AuditError("Manifest has no frozen artifact bindings")
    verified: dict[Path, str] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("snapshot"), str):
            raise AuditError("Malformed frozen artifact binding")
        path = Path(row["snapshot"]).resolve()
        if not _is_within(path, pair_dir):
            raise AuditError(f"Frozen artifact escapes the experiment directory: {path}")
        if path in verified:
            raise AuditError(f"Duplicate frozen artifact binding: {path}")
        if not path.is_file():
            raise AuditError(f"Frozen artifact is missing: {path}")
        expected = row.get("sha256")
        actual = hash_file(path)
        if not isinstance(expected, str) or actual != expected:
            raise AuditError(f"Frozen artifact hash mismatch: {path}")
        verified[path] = actual
    return verified


def _strict_rows(module: ModuleType, graph: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    specs = {
        "nodes": (module.KGNode, "evidence", module.Evidence),
        "relations": (module.KGRelation, "evidence", module.Evidence),
        "math_expressions": (module.MathExpression, "evidence", module.Evidence),
    }
    normalized: dict[str, list[dict[str, Any]]] = {}
    for collection, (model, evidence_field, evidence_model) in specs.items():
        rows = graph.get(collection)
        if not isinstance(rows, list):
            raise AuditError(f"Graph field {collection!r} must be an array")
        allowed_fields = set(model.model_fields)
        evidence_fields = set(evidence_model.model_fields)
        parsed: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise AuditError(f"Graph {collection}[{index}] must be an object")
            unknown = set(row) - allowed_fields
            if unknown:
                raise AuditError(f"Unknown fields in graph {collection}[{index}]: {sorted(unknown)}")
            evidences = row.get(evidence_field, [])
            if not isinstance(evidences, list):
                raise AuditError(f"Graph {collection}[{index}].{evidence_field} must be an array")
            for evidence_index, evidence in enumerate(evidences):
                if not isinstance(evidence, dict):
                    raise AuditError(
                        f"Graph {collection}[{index}].{evidence_field}[{evidence_index}] must be an object"
                    )
                unknown_evidence = set(evidence) - evidence_fields
                if unknown_evidence:
                    raise AuditError(
                        f"Unknown evidence fields in graph {collection}[{index}]: "
                        f"{sorted(unknown_evidence)}"
                    )
            try:
                parsed.append(model.model_validate(row).model_dump(mode="json", exclude_none=True))
            except Exception as exc:
                raise AuditError(
                    f"Frozen schema rejected graph {collection}[{index}]: {type(exc).__name__}: {exc}"
                ) from exc
        identity_field = "id"
        identities = [row[identity_field] for row in parsed]
        if len(identities) != len(set(identities)):
            raise AuditError(f"Graph has duplicate identities in {collection}")
        for row in parsed:
            row[evidence_field] = sorted(row.get(evidence_field, []), key=_canonical_json)
        normalized[collection] = sorted(parsed, key=lambda row: row[identity_field])
    return normalized


def _plain_rows(graph: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Normalize graph collections for comparison; schema validation is done separately."""
    normalized: dict[str, list[dict[str, Any]]] = {}
    for collection in ("nodes", "relations", "math_expressions"):
        rows = graph.get(collection)
        if not isinstance(rows, list):
            raise AuditError(f"Graph field {collection!r} must be an array")
        parsed = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise AuditError(f"Graph {collection}[{index}] must be an object")
            item = json.loads(_canonical_json(row))
            evidence = item.get("evidence", [])
            if not isinstance(evidence, list):
                raise AuditError(f"Graph {collection}[{index}].evidence must be an array")
            item["evidence"] = sorted(evidence, key=_canonical_json)
            parsed.append(item)
        identities = [row.get("id") for row in parsed]
        if len(identities) != len(set(identities)):
            raise AuditError(f"Graph has duplicate identities in {collection}")
        normalized[collection] = sorted(parsed, key=lambda row: str(row.get("id", "")))
    return normalized


def compare_semantics(expected_graph: dict[str, Any], actual_graph: dict[str, Any]) -> dict[str, Any]:
    expected = _plain_rows(expected_graph)
    actual = _plain_rows(actual_graph)
    comparisons: dict[str, bool] = {}
    evidence_fields: dict[str, bool] = {}
    for collection, label in (
        ("nodes", "node"),
        ("relations", "relation"),
        ("math_expressions", "math_expression"),
    ):
        expected_by_id = {row["id"]: row for row in expected[collection]}
        actual_by_id = {row["id"]: row for row in actual[collection]}
        evidence_fields[f"{label}_evidence_equal"] = (
            set(expected_by_id) == set(actual_by_id)
            and all(
                expected_by_id[item_id].get("evidence", []) == actual_by_id[item_id].get("evidence", [])
                for item_id in expected_by_id
            )
        )
        semantic_expected = [
            {key: value for key, value in row.items() if key != "evidence"}
            for row in expected[collection]
        ]
        semantic_actual = [
            {key: value for key, value in row.items() if key != "evidence"}
            for row in actual[collection]
        ]
        comparisons[f"{label}_semantics_equal"] = semantic_expected == semantic_actual
        comparisons[f"{collection}_equal"] = (
            comparisons[f"{label}_semantics_equal"] and evidence_fields[f"{label}_evidence_equal"]
        )
    projection_equal = all(comparisons[f"{collection}_equal"] for collection in expected)
    return {
        **comparisons,
        **evidence_fields,
        "all_semantics_and_evidence_equal": projection_equal,
        "expected_counts": {key: len(rows) for key, rows in expected.items()},
        "graph_counts": {key: len(rows) for key, rows in actual.items()},
        "expected_semantic_sha256": _canonical_hash(expected),
        "graph_semantic_sha256": _canonical_hash(actual),
    }


def _select_unique_cache(cache_dir: Path, chunk_prefix: str) -> Path:
    if not cache_dir.is_dir():
        raise AuditError(f"Frozen response cache directory is missing: {cache_dir}")
    matches = [path for path in cache_dir.iterdir() if path.is_file() and path.name.startswith(chunk_prefix)]
    if not matches:
        raise AuditError(f"No cached response matches chunk prefix {chunk_prefix!r}")
    if len(matches) != 1:
        raise AuditError(
            f"Expected one cached response for {chunk_prefix!r}, found {len(matches)}: "
            f"{sorted(path.name for path in matches)}"
        )
    path = matches[0]
    if re.fullmatch(re.escape(chunk_prefix) + r"[0-9a-f]{20}\.json", path.name) is None:
        raise AuditError(f"Malformed cached response filename for chunk: {path.name}")
    return path


def _response_fingerprint(
    extractor: Any,
    chunk: Any,
    state: dict[str, Any],
    node_memory: list[Any],
    relation_memory: list[Any],
    context_schema: str,
    arm_name: str,
    sent_payload: dict[str, Any],
) -> str:
    base = extractor._cache_fingerprint(chunk, state, node_memory, relation_memory)
    wrapped = {
        "base_fingerprint": base,
        "context_schema": context_schema,
        "arm": arm_name,
        "system_prompt": extractor.system_prompt,
        "payload": sent_payload,
    }
    return _canonical_hash(wrapped)[:20]


def _offline_configuration(manifest: dict[str, Any]) -> None:
    common = manifest.get("common")
    if not isinstance(common, dict):
        raise AuditError("Manifest common configuration is missing")
    enabled = [flag for flag in SAFE_OFFLINE_FLAGS if common.get(flag, False)]
    if enabled:
        raise AuditError(f"Offline replay refuses model-dependent passes: {', '.join(enabled)}")
    if common.get("seed_graph_path"):
        raise AuditError("Offline replay does not support a seed graph")


def audit_arm(arm_dir: Path) -> dict[str, Any]:
    arm_dir = arm_dir.resolve()
    pair_dir = arm_dir.parent
    graph_path = arm_dir / "graph.json"
    if not graph_path.is_file():
        raise AuditError(f"graph.json is required; pending/running arms cannot be audited: {graph_path}")

    manifest_path = pair_dir / "manifest.json"
    if not manifest_path.is_file():
        raise AuditError(f"Experiment manifest is missing: {manifest_path}")
    manifest = _read_json(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("experiment") != EXPERIMENT:
        raise AuditError(f"Expected {EXPERIMENT!r} manifest")
    arms = manifest.get("arms")
    if not isinstance(arms, list) or {row.get("name") for row in arms if isinstance(row, dict)} != ARM_NAMES:
        raise AuditError("Manifest must bind exactly the flat_context and structured_state arms")
    if len(arms) != len(ARM_NAMES):
        raise AuditError("Manifest contains duplicate or malformed arm entries")
    arm_matches = [row for row in arms if row.get("name") == arm_dir.name]
    if len(arm_matches) != 1:
        raise AuditError(f"Arm directory does not match exactly one manifest arm: {arm_dir.name}")
    arm_meta = arm_matches[0]
    if arm_meta.get("status") != "completed":
        raise AuditError(f"Arm status is {arm_meta.get('status')!r}; only completed arms can be audited")
    graph_sha256 = hash_file(graph_path)
    if not isinstance(arm_meta.get("graph_sha256"), str) or arm_meta["graph_sha256"] != graph_sha256:
        raise AuditError("graph.json hash differs from manifest graph_sha256")

    verified_artifacts = _verify_artifacts(pair_dir, manifest)
    artifact_hashes = {
        path.relative_to(pair_dir).as_posix(): digest for path, digest in verified_artifacts.items()
    }
    artifact_set_sha256 = _canonical_hash(artifact_hashes)

    input_value = manifest.get("input")
    if not isinstance(input_value, str):
        raise AuditError("Manifest frozen input path is missing")
    input_path = Path(input_value).resolve()
    frozen_input_root = (pair_dir / "snapshot" / "input").resolve()
    if not _is_within(input_path, frozen_input_root):
        raise AuditError(f"Manifest input is not the frozen snapshot input: {input_path}")
    chunks_path = pair_dir / "chunks.json"
    frozen_extractor = pair_dir / "snapshot" / "agents" / "Extractor.py"
    runtime_extractor = arm_dir / "runtime" / "agents" / "Extractor.py"
    runtime_prompt = arm_dir / "runtime" / "prompts" / "extraction_system.txt"
    for required in (input_path, chunks_path, frozen_extractor, runtime_extractor, runtime_prompt):
        if not required.is_file():
            raise AuditError(f"Required frozen replay input is missing: {required}")
    if input_path not in verified_artifacts or chunks_path.resolve() not in verified_artifacts:
        raise AuditError("Frozen input and chunk plan must be bound by manifest artifact hashes")
    if frozen_extractor.resolve() not in verified_artifacts:
        raise AuditError("Frozen Extractor is not bound by manifest artifact hashes")
    runtime_extractor_sha256 = hash_file(runtime_extractor)
    frozen_extractor_sha256 = verified_artifacts[frozen_extractor.resolve()]
    if runtime_extractor_sha256 != frozen_extractor_sha256:
        raise AuditError("Arm runtime Extractor differs from its frozen snapshot")

    prompt_value = arm_meta.get("system_prompt")
    prompt_expected = arm_meta.get("system_prompt_sha256")
    if not isinstance(prompt_value, str) or not isinstance(prompt_expected, str):
        raise AuditError("Manifest arm system prompt binding is missing")
    prompt_snapshot = Path(prompt_value).resolve()
    if verified_artifacts.get(prompt_snapshot) != prompt_expected:
        raise AuditError("System prompt is not bound to its frozen hash")
    if hash_file(runtime_prompt) != prompt_expected:
        raise AuditError("Arm runtime system prompt differs from its frozen prompt")

    context_schema = manifest.get("context_schema")
    if not isinstance(context_schema, str) or not context_schema:
        raise AuditError("Frozen context schema is missing")
    _offline_configuration(manifest)
    common = manifest["common"]

    extractor_kwargs = {
        key: common[key]
        for key in (
            "provider",
            "model",
            "base_url",
            "api_key_env",
            "temperature",
            "max_chunk_chars",
            "chunk_strategy",
            "chunk_context_before_lines",
            "chunk_context_after_lines",
            "experimental_chunk_spans",
            "max_retries",
            "request_timeout",
            "max_tokens",
            "use_response_format",
            "no_proxy",
            "request_delay",
            "thinking",
            "reasoning_effort",
        )
        if key in common
    }
    if "experimental_chunk_spans" in extractor_kwargs:
        extractor_kwargs["experimental_chunk_spans"] = tuple(
            tuple(span) for span in extractor_kwargs["experimental_chunk_spans"]
        )
    extractor_kwargs.update(
        input_path=input_path,
        output_path=graph_path,
        memory_node_bytes=arm_meta["memory_node_bytes"],
        memory_relation_bytes=arm_meta["memory_relation_bytes"],
        save_output=False,
        resume=False,
        verbose=False,
        verify=False,
        semantic_dedupe=False,
        embedding_memory=False,
        seed_graph_path=None,
        coverage_review=False,
        logical_form_review=False,
    )
    extractor_module = _load_module(runtime_extractor, "extractor")
    protocol_path = pair_dir / "snapshot" / "protocol" / "extraction_context_state.py"
    if not protocol_path.is_file() or protocol_path.resolve() not in verified_artifacts:
        raise AuditError("Frozen context-state protocol helper is missing or unbound")
    context_module = _load_module(protocol_path, "context")
    if context_module.SCHEMA == context_schema:
        state_class = context_module.ContextState
    elif context_schema == "maf-kg.extraction-context.layout.v1":
        if context_module.LAYOUT_SCHEMA != context_schema:
            raise AuditError("Layout schema differs from frozen protocol helper")
        state_class = context_module.LayoutContextState
    else:
        raise AuditError("Manifest context schema differs from frozen protocol helper")

    try:
        extractor = extractor_module.Extractor(**extractor_kwargs)
        state: dict[str, Any] = {
            **common,
            "input_path": input_value,
            "output_path": str(graph_path),
            "include_exercises": False,
            "diagnostics": {},
            "resume": False,
            "memory_node_bytes": arm_meta["memory_node_bytes"],
            "memory_relation_bytes": arm_meta["memory_relation_bytes"],
        }
        state = extractor._chunk_document(extractor._load_document(state))
    except Exception as exc:
        raise AuditError(f"Cannot prepare frozen source for replay: {type(exc).__name__}: {exc}") from exc

    chunks = _read_json(chunks_path)
    if not isinstance(chunks, list) or not chunks:
        raise AuditError("Frozen chunk plan must be a nonempty array")
    actual_chunks = json.loads(
        json.dumps([dataclasses.asdict(chunk) for chunk in state["chunks"]], ensure_ascii=False)
    )
    if actual_chunks != chunks:
        raise AuditError("Frozen Extractor chunking differs from the bound chunks.json")
    request_dir = arm_dir / "requests"
    if not request_dir.is_dir():
        raise AuditError(f"Request directory is missing: {request_dir}")
    expected_request_names = {f"{row['id']}.json" for row in chunks}
    request_files = [path for path in request_dir.iterdir() if path.is_file() and path.suffix == ".json"]
    if {path.name for path in request_files} != expected_request_names or len(request_files) != len(chunks):
        raise AuditError("Requests must contain exactly one JSON file for each frozen chunk")

    cache_dir = arm_dir / "runtime" / "node_cache" / CACHE_DIR_NAME
    input_slug = extractor_module._slug(Path(input_value).stem, 60)
    provider_slug = extractor_module._slug(str(common["provider"]), 20)
    model_slug = extractor_module._slug(str(common["model"]), 40)
    cache_base = f"{input_slug}__{provider_slug}__{model_slug}__"
    expected_cache_prefixes = {row["id"]: f"{cache_base}{row['id']}__" for row in chunks}
    cache_paths = [path for path in cache_dir.iterdir() if path.is_file()] if cache_dir.is_dir() else []
    related_cache_paths = [path for path in cache_paths if path.name.startswith(cache_base)]
    unexpected_caches = [
        path.name
        for path in related_cache_paths
        if not any(path.name.startswith(prefix) for prefix in expected_cache_prefixes.values())
    ]
    if unexpected_caches:
        raise AuditError(f"Unexpected cache files for this input/provider/model: {sorted(unexpected_caches)}")

    node_store: dict[str, Any] = {}
    relation_store: dict[tuple[str, str, str], Any] = {}
    expression_store: dict[tuple[str, str, str], Any] = {}
    tracker = state_class(chunks)
    chunk_reports: list[dict[str, Any]] = []
    request_hashes: dict[str, str] = {}
    response_hashes: dict[str, str] = {}
    for chunk_index, chunk in enumerate(state["chunks"]):
        chunk_id = chunk.id
        request_path = request_dir / f"{chunk_id}.json"
        request_sha256 = hash_file(request_path)
        request = _read_json(request_path)
        if not isinstance(request, dict) or request.get("chunk_id") != chunk_id:
            raise AuditError(f"Request identity mismatch for {chunk_id}")
        if request.get("committed_chunk_ids") != list(tracker.completed):
            raise AuditError(f"Committed chunk history mismatch in request {chunk_id}")
        legacy = request.get("legacy_payload")
        sent_payload = request.get("sent_payload")
        if not isinstance(legacy, dict) or not isinstance(sent_payload, dict):
            raise AuditError(f"Request payload is malformed for {chunk_id}")
        if legacy.get("source_path") != input_value:
            raise AuditError(f"Request source is not the frozen input in {chunk_id}")
        raw_nodes = legacy.get("node_memory")
        raw_relations = legacy.get("relation_memory")
        if not isinstance(raw_nodes, list) or not isinstance(raw_relations, list):
            raise AuditError(f"Legacy memory arrays are missing in request {chunk_id}")
        try:
            node_memory = [extractor_module.NodeMemoryItem.model_validate(row) for row in raw_nodes]
            relation_memory = [extractor_module.RelationMemoryItem.model_validate(row) for row in raw_relations]
        except Exception as exc:
            raise AuditError(f"Frozen schema rejected legacy memory in {chunk_id}: {exc}") from exc
        memory_ids = [item.id for item in node_memory]
        if len(memory_ids) != len(set(memory_ids)):
            raise AuditError(f"Duplicate node_memory identity in request {chunk_id}")
        try:
            expected_legacy = json.loads(
                extractor._chunk_extraction_prompt(chunk, input_value, node_memory, relation_memory)
            )
        except Exception as exc:
            raise AuditError(f"Cannot reconstruct legacy request {chunk_id}: {type(exc).__name__}: {exc}") from exc
        if expected_legacy != legacy:
            raise AuditError(f"Legacy request differs from frozen source and chunk in {chunk_id}")
        if request.get("system_prompt_sha256") != _canonical_hash(extractor.system_prompt):
            raise AuditError(f"Request system prompt hash mismatch in {chunk_id}")

        before = context_module.graph_snapshot(node_store, relation_store)
        tracker.observe(before)
        if arm_dir.name == "structured_state":
            try:
                expected_sent = tracker.render(legacy)
            except Exception as exc:
                raise AuditError(f"Structured request validation failed in {chunk_id}: {exc}") from exc
        else:
            expected_sent = legacy
        if sent_payload != expected_sent:
            raise AuditError(f"Sent payload differs from the frozen arm contract in {chunk_id}")

        fingerprint = _response_fingerprint(
            extractor,
            chunk,
            state,
            node_memory,
            relation_memory,
            context_schema,
            arm_dir.name,
            sent_payload,
        )
        prefix = expected_cache_prefixes[chunk_id]
        cache_path = _select_unique_cache(cache_dir, prefix)
        expected_cache_name = f"{prefix}{fingerprint}.json"
        if cache_path.name != expected_cache_name:
            raise AuditError(f"Cached response fingerprint does not match request {chunk_id}")
        response_sha256 = hash_file(cache_path)
        raw_response = _read_json(cache_path)
        try:
            response = extractor_module.ChunkExtractionResponse.model_validate(raw_response)
        except Exception as exc:
            raise AuditError(f"Frozen response schema rejected cache for {chunk_id}: {exc}") from exc

        allowed_existing_ids = set(memory_ids)
        try:
            summary = extractor._merge_chunk_response(
                response,
                chunk,
                input_value,
                state["raw_lines"],
                node_store,
                relation_store,
                expression_store,
                allowed_existing_ids=allowed_existing_ids,
            )
        except Exception as exc:
            raise AuditError(f"Frozen merge rejected cached response for {chunk_id}: {type(exc).__name__}: {exc}") from exc
        after = context_module.graph_snapshot(node_store, relation_store)
        tracker.commit(chunk_id, before, after)

        request_hashes[chunk_id] = request_sha256
        response_hashes[chunk_id] = response_sha256
        chunk_reports.append(
            {
                "chunk_id": chunk_id,
                "order": chunk_index + 1,
                "request_sha256": request_sha256,
                "cache_file": cache_path.name,
                "cache_response_sha256": response_sha256,
                "cache_fingerprint": fingerprint,
                "response_schema_valid": True,
                "allowed_existing_ids": sorted(allowed_existing_ids),
                "merge": {
                    key: summary[key]
                    for key in (
                        "created_node_count",
                        "reused_node_count",
                        "new_relation_count",
                        "pruned_relation_count",
                        "new_expression_count",
                        "total_nodes",
                        "total_relations",
                        "total_expressions",
                        "identity_conflict_count",
                        "pending_node_count",
                    )
                },
                "replay_status": "passed",
            }
        )

    if tracker.completed != [chunk.id for chunk in state["chunks"]]:
        raise AuditError("Replay did not complete the frozen chunk sequence")
    try:
        replay_result = extractor._finalize_result(
            input_value,
            state["raw_lines"],
            node_store,
            relation_store,
            expression_store,
            {},
        )
        replay_graph = replay_result.model_dump(mode="json", exclude_none=True)
    except Exception as exc:
        raise AuditError(f"Frozen result finalization failed: {type(exc).__name__}: {exc}") from exc

    if hash_file(graph_path) != graph_sha256:
        raise AuditError("graph.json changed during replay")
    graph = _read_json(graph_path)
    if not isinstance(graph, dict):
        raise AuditError("graph.json must contain a JSON object")
    replay_rows = _strict_rows(extractor_module, replay_graph)
    graph_rows = _strict_rows(extractor_module, graph)
    comparison = compare_semantics(replay_rows, graph_rows)
    if not comparison["all_semantics_and_evidence_equal"]:
        failed = [key for key, value in comparison.items() if key.endswith("_equal") and value is False]
        raise AuditError(f"Replayed mathematical semantics/evidence differ from graph.json: {failed}")

    return {
        "schema": "maf-kg.context-state-replay-audit.v1",
        "audit_status": "passed",
        "audit_mode": "completed_arm_replay",
        "experiment": EXPERIMENT,
        "topic": pair_dir.name,
        "arm": arm_dir.name,
        "execution": {
            "manifest_status": arm_meta.get("status"),
            "exit_code": arm_meta.get("exit_code"),
        },
        "hashes": {
            "graph_sha256": graph_sha256,
            "frozen_input_sha256": hash_file(input_path),
            "frozen_chunks_sha256": hash_file(chunks_path),
            "runtime_extractor_sha256": runtime_extractor_sha256,
            "snapshot_extractor_sha256": frozen_extractor_sha256,
            "artifact_set_sha256": artifact_set_sha256,
            "snapshot_artifacts": artifact_hashes,
            "request_sha256": request_hashes,
            "cache_response_sha256": response_hashes,
            "audit_tool_sha256": hash_file(Path(__file__).resolve()),
        },
        "chunk_replay": chunk_reports,
        "final_comparison": comparison,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("arm_dir", type=Path, help="Completed arm directory containing graph.json")
    parser.add_argument("--output", type=Path, help="Write the successful audit JSON to a new file")
    args = parser.parse_args(argv)
    try:
        report = audit_arm(args.arm_dir)
        serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            output_path = args.output.resolve()
            if not output_path.parent.is_dir():
                raise AuditError(f"Output directory does not exist: {output_path.parent}")
            with output_path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(serialized)
        else:
            sys.stdout.write(serialized)
        return 0
    except (AuditError, OSError) as exc:
        print(f"context-state replay audit failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
