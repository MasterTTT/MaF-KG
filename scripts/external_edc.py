"""Thin, auditable adapter for the pinned EDC Target Alignment stages."""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
import math
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable


UPSTREAM_COMMIT = "02aad17e2fc1e9845c84746b384e7fd3160edc1a"
UPSTREAM_TREE = "25ea1bcdd6841713ba67db1c9942d2f98bd746dc"
METHOD_ID = f"edc-target-alignment-{UPSTREAM_COMMIT[:8]}-adapted-embedding"
MAX_CHAT_CALLS = 120
MAX_SECONDS = 5400
EMBEDDING_DIMENSION = 384
INJECTED_ROUTE = "edc-injected-call"
_RUN_LOCK = threading.RLock()


class ChatCallLimitReached(RuntimeError):
    """Raised before an EDC request would exceed the runner's hard call ceiling."""


class TaskTimeLimitReached(RuntimeError):
    """Raised when the caller-managed task deadline has elapsed."""


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else repr(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_json_safe(item) for item in sorted(value, key=repr)]
    if hasattr(value, "tolist"):
        return _json_safe(value.tolist())
    if hasattr(value, "item"):
        return _json_safe(value.item())
    return {"__python_type__": type(value).__name__, "repr": repr(value)}


def _write_json(path: Path, value: Any) -> None:
    text = json.dumps(_json_safe(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def _json_sha256(value: Any) -> str:
    data = json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _source_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py"), key=lambda item: item.relative_to(root).as_posix()):
        if "__pycache__" in path.parts:
            continue
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _load_native_stages(vendor: Path):
    lock_path = vendor / "UPSTREAM_LOCK.json"
    if not lock_path.is_file():
        raise FileNotFoundError(f"EDC lock file is missing: {lock_path}")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    if lock.get("upstream_commit") != UPSTREAM_COMMIT or lock.get("upstream_tree") != UPSTREAM_TREE:
        raise ValueError("Vendored EDC commit/tree does not match the adapter lock.")

    package_root = vendor / "edc"
    if not (package_root / "extract.py").is_file():
        raise FileNotFoundError(f"EDC source package is missing: {package_root}")
    if _source_sha256(package_root) != lock.get("adapted_python_sha256"):
        raise ValueError("Vendored adapted EDC Python source does not match UPSTREAM_LOCK.json.")
    loaded_package = sys.modules.get("edc")
    if loaded_package is not None:
        loaded_paths = [Path(item).resolve() for item in getattr(loaded_package, "__path__", [])]
        if package_root.resolve() not in loaded_paths:
            raise RuntimeError("A different edc package is already loaded in this Python process.")

    inserted = str(vendor) not in sys.path
    if inserted:
        sys.path.insert(0, str(vendor))
    try:
        llm_utils = importlib.import_module("edc.utils.llm_utils")
        extractor = importlib.import_module("edc.extract").Extractor
        schema_definer = importlib.import_module("edc.schema_definition").SchemaDefiner
        canonicalizer = importlib.import_module("edc.schema_canonicalization").SchemaCanonicalizer
    finally:
        if inserted:
            sys.path.remove(str(vendor))
    return lock, llm_utils, extractor, schema_definer, canonicalizer


def _prompt_bundle(vendor: Path) -> tuple[dict[str, str], dict[str, str]]:
    paths = {
        "extract_template": "prompt_templates/oie_template.txt",
        "extract_examples": "few_shot_examples/example/oie_few_shot_examples.txt",
        "define_template": "prompt_templates/sd_template.txt",
        "define_examples": "few_shot_examples/example/sd_few_shot_examples.txt",
        "canonicalize_template": "prompt_templates/sc_template.txt",
    }
    texts = {}
    hashes = {}
    for name, relative in paths.items():
        path = vendor / relative
        data = path.read_bytes()
        texts[name] = data.decode("utf-8")
        hashes[relative] = hashlib.sha256(data).hexdigest()
    return texts, hashes


def _bracket_fragments(raw: str) -> tuple[list[str], int, int]:
    starts = []
    pairs = []
    unmatched_right = 0
    for index, character in enumerate(raw):
        if character == "[":
            starts.append(index)
        elif character == "]":
            if starts:
                pairs.append((starts.pop(), index))
            else:
                unmatched_right += 1
    pairs.sort()
    return [raw[left : right + 1] for left, right in pairs], len(starts), unmatched_right


def _triplet_parse_audit(raw: str, parsed: Any, parser: Callable) -> dict[str, Any]:
    fragments, unmatched_left, unmatched_right = _bracket_fragments(raw)
    outcomes = []
    dropped = []
    for fragment in fragments:
        fragment_result = parser(fragment)
        row = {
            "fragment": fragment,
            "parser_output": _json_safe(fragment_result),
            "accepted_by_upstream_parser": bool(fragment_result),
        }
        outcomes.append(row)
        if not fragment_result:
            dropped.append({"fragment": fragment, "reason": "upstream parser returned no triplet"})
    if raw.strip() and not fragments:
        dropped.append({"fragment": raw, "reason": "no bracketed triplet candidate"})
    return {
        "parser": "parse_raw_triplets",
        "request_label": None,
        "raw_response": raw,
        "parsed": _json_safe(parsed),
        "candidate_fragments": outcomes,
        "dropped": dropped,
        "unmatched_left_brackets": unmatched_left,
        "unmatched_right_brackets": unmatched_right,
    }


def _definition_parse_audit(raw: str, parsed: Any) -> dict[str, Any]:
    latest_line: dict[str, int] = {}
    dropped = []
    for index, line in enumerate(raw.splitlines(), start=1):
        if ":" not in line:
            dropped.append({"line_number": index, "line": line, "reason": "missing colon"})
            continue
        relation = line.split(":", 1)[0].strip()
        if relation == "Answer":
            dropped.append({"line_number": index, "line": line, "reason": "native parser skips Answer"})
        elif relation in latest_line:
            previous = latest_line[relation]
            dropped.append({
                "line_number": previous,
                "line": raw.splitlines()[previous - 1],
                "reason": "overwritten by a later definition with the same relation name",
            })
            latest_line[relation] = index
        else:
            latest_line[relation] = index
    return {
        "parser": "parse_relation_definition",
        "request_label": None,
        "raw_response": raw,
        "parsed": _json_safe(parsed),
        "dropped": dropped,
    }


class _InjectedEmbedder:
    prompts: dict[str, str] = {}

    def __init__(self, callback: Callable, trace: list[dict[str, Any]]) -> None:
        self.callback = callback
        self.trace = trace
        self.dimension: int | None = None

    def encode(self, text: str, prompt_name: str | None = None) -> list[float]:
        row = {"text": text, "prompt_name": prompt_name, "status": "started"}
        self.trace.append(row)
        try:
            vectors = self.callback([text])
            if not isinstance(vectors, (list, tuple)) or len(vectors) != 1:
                raise ValueError("embed(texts) must return one vector for each input text")
            vector = vectors[0]
            if not isinstance(vector, (list, tuple)) or not vector:
                raise ValueError("Each embedding must be a non-empty list of numbers")
            normalized = []
            for value in vector:
                if isinstance(value, bool):
                    raise TypeError("Embedding values must be finite numbers")
                number = float(value)
                if not math.isfinite(number):
                    raise ValueError("Embedding values must be finite numbers")
                normalized.append(number)
            norm = math.sqrt(sum(value * value for value in normalized))
            if len(normalized) != EMBEDDING_DIMENSION:
                raise ValueError(
                    f"EDC adapted embedding must have {EMBEDDING_DIMENSION} dimensions; got {len(normalized)}"
                )
            if not math.isclose(norm, 1.0, rel_tol=1e-4, abs_tol=1e-4):
                raise ValueError(
                    "EDC adapted embedding must be L2-normalized: native canonicalizer uses dot product, "
                    "which equals cosine only for unit vectors"
                )
            if self.dimension is None:
                self.dimension = len(normalized)
            elif len(normalized) != self.dimension:
                raise ValueError("Embedding dimensions changed during EDC execution")
            row.update(status="completed", dimension=len(normalized), l2_norm=norm)
            return normalized
        except Exception as exc:
            row.update(status="failed", error_type=type(exc).__name__)
            raise


def run(
    vendor,
    texts: list[str],
    client: Callable,
    embed: Callable,
    directory,
    TARGET_SCHEMA: dict[str, str],
    *,
    model_name: str | None = None,
    embedding_model_id: str | None = None,
) -> list[list[list[str]]]:
    """Run native EDC Extract, Define, Canonicalize once, with injected IO.

    The return value matches EDC.extract_kg: one canonical triplet list per text.
    Raw responses and parser diagnostics are written to edc_audit.json.
    """
    if not isinstance(texts, list) or not texts or any(not isinstance(text, str) or not text.strip() for text in texts):
        raise ValueError("texts must be a non-empty list of non-empty strings")
    if not callable(client) or not callable(embed):
        raise TypeError("client and embed must be callable")
    if not isinstance(TARGET_SCHEMA, dict) or not TARGET_SCHEMA:
        raise ValueError("TARGET_SCHEMA must be an explicit, non-empty relation-definition mapping")
    if any(
        not isinstance(relation, str)
        or not relation.strip()
        or not isinstance(definition, str)
        or not definition.strip()
        for relation, definition in TARGET_SCHEMA.items()
    ):
        raise ValueError("TARGET_SCHEMA keys and general relation definitions must be non-empty strings")
    if model_name is not None and (not isinstance(model_name, str) or not model_name.strip()):
        raise ValueError("model_name must be a non-empty string when provided")

    vendor_root = Path(vendor).resolve()
    if not vendor_root.is_dir():
        raise FileNotFoundError(f"EDC vendor directory does not exist: {vendor_root}")
    output = Path(directory).resolve()
    if output == vendor_root or vendor_root in output.parents:
        raise ValueError("EDC output directory must not be inside the vendored source tree")
    output.mkdir(parents=True, exist_ok=True)
    audit_path = output / "edc_audit.json"
    if audit_path.exists():
        raise FileExistsError(f"Refusing to overwrite an existing EDC audit: {audit_path}")

    schema_copy = copy.deepcopy(TARGET_SCHEMA)
    call_limit = min(MAX_CHAT_CALLS, getattr(client, "max_calls", MAX_CHAT_CALLS))
    second_limit = min(MAX_SECONDS, getattr(client, "max_seconds", MAX_SECONDS))
    client_started = getattr(client, "started", time.monotonic())
    resolved_embedding_id = (
        embedding_model_id
        or getattr(embed, "model_name", None)
        or "caller-managed; identifier not declared"
    )
    resolved_chat_model = model_name or getattr(client, "model_name", None) or "client-managed"
    audit: dict[str, Any] = {
        "schema_version": 1,
        "method": METHOD_ID,
        "status": "running",
        "upstream_repository": "https://github.com/clear-nus/edc",
        "upstream_commit": UPSTREAM_COMMIT,
        "upstream_tree": UPSTREAM_TREE,
        "chat_model": resolved_chat_model,
        "embedding_model_id": resolved_embedding_id,
        "embedding_variant": "adapted baseline: caller-supplied embed interface; not the upstream 7B default",
        "embedding_dimension": EMBEDDING_DIMENSION,
        "embedding_similarity": "L2-normalized vectors; upstream dot product equals cosine",
        "target_schema": schema_copy,
        "target_schema_sha256": _json_sha256(schema_copy),
        "texts": list(texts),
        "texts_sha256": _json_sha256(texts),
        "chat_policy": {
            "retry_attempts": 1,
            "max_calls": call_limit,
            "max_seconds": second_limit,
            "kwargs_policy": "JSON-serializable only; request limits and cost are enforced by the injected client",
        },
        "client_budget": {
            "usd_limit": getattr(client, "usd_limit", None),
            "token_limit": getattr(client, "token_limit", None),
        },
        "prompt_sha256": {},
        "adapted_source_sha256": None,
        "chat_calls": [],
        "embedding_calls": [],
        "parse_events": [],
        "gate": None,
        "result": None,
        "failure": None,
    }

    with _RUN_LOCK:
        original_triplet_parser = None
        original_definition_parser = None
        original_chat_call = None
        chat_call_installed = False
        llm_utils = None
        try:
            lock, llm_utils, Extractor, SchemaDefiner, SchemaCanonicalizer = _load_native_stages(vendor_root)
            audit["upstream_commit"] = lock["upstream_commit"]
            audit["upstream_tree"] = lock["upstream_tree"]
            audit["adapted_source_sha256"] = _source_sha256(vendor_root / "edc")
            prompt_texts, prompt_hashes = _prompt_bundle(vendor_root)
            audit["prompt_sha256"] = prompt_hashes

            native_triplet_parser = llm_utils.parse_raw_triplets
            native_definition_parser = llm_utils.parse_relation_definition
            original_triplet_parser = native_triplet_parser
            original_definition_parser = native_definition_parser

            def audited_triplet_parser(raw: str):
                parsed = native_triplet_parser(raw)
                event = _triplet_parse_audit(raw, parsed, native_triplet_parser)
                event["request_label"] = llm_utils.current_chat_label()
                audit["parse_events"].append(event)
                return parsed

            def audited_definition_parser(raw: str):
                parsed = native_definition_parser(raw)
                event = _definition_parse_audit(raw, parsed)
                event["request_label"] = llm_utils.current_chat_label()
                audit["parse_events"].append(event)
                return parsed

            calls: list[dict[str, Any]] = audit["chat_calls"]
            started_at = time.monotonic()

            def injected_client(messages: list[dict], label: str, **kwargs) -> str:
                if len(calls) >= call_limit:
                    audit["gate"] = {"status": "max_chat_calls_reached", "next_label": label}
                    raise ChatCallLimitReached(f"EDC chat-call ceiling reached ({call_limit})")
                elapsed = time.monotonic() - client_started
                if elapsed >= second_limit or time.monotonic() - started_at >= second_limit:
                    audit["gate"] = {"status": "max_seconds_reached", "next_label": label}
                    raise TaskTimeLimitReached(f"EDC task-time ceiling reached ({second_limit}s)")

                callback_kwargs = {
                    key: value for key, value in kwargs.items()
                    if key in {"temperature", "max_tokens"}
                }
                if model_name is not None:
                    callback_kwargs["model"] = model_name
                json.dumps(_json_safe(messages), ensure_ascii=False, allow_nan=False)
                json.dumps(callback_kwargs, ensure_ascii=False, allow_nan=False)
                row = {
                    "label": label,
                    "messages": copy.deepcopy(messages),
                    "upstream_call_options": _json_safe(kwargs),
                    "client_kwargs": callback_kwargs,
                    "status": "started",
                    "raw_response": None,
                }
                calls.append(row)
                try:
                    response = client(copy.deepcopy(messages), label, **callback_kwargs)
                    if not isinstance(response, str):
                        raise TypeError("client(messages, label, **kwargs) must return a string")
                    row.update(status="completed", raw_response=response)
                    if time.monotonic() - client_started > second_limit:
                        audit["gate"] = {"status": "max_seconds_reached_after_call", "label": label}
                        raise TaskTimeLimitReached(f"EDC task-time ceiling reached ({second_limit}s)")
                    return response
                except Exception as exc:
                    row.update(status="failed", error_type=type(exc).__name__)
                    raise

            llm_utils.parse_raw_triplets = audited_triplet_parser
            llm_utils.parse_relation_definition = audited_definition_parser
            original_chat_call = llm_utils.set_chat_call(injected_client)
            chat_call_installed = True

            model_route = model_name or INJECTED_ROUTE
            extractor = Extractor(openai_model=model_route)
            schema_definer = SchemaDefiner(openai_model=model_route)

            extracted_triplets = []
            for index, text in enumerate(texts, start=1):
                label = f"edc.extract.section-{index:04d}"
                with llm_utils.chat_label(label):
                    extracted_triplets.append(
                        extractor.extract(
                            text,
                            prompt_texts["extract_examples"],
                            prompt_texts["extract_template"],
                        )
                    )

            schema_definitions = []
            for index, (text, triplets) in enumerate(zip(texts, extracted_triplets), start=1):
                label = f"edc.define.section-{index:04d}"
                with llm_utils.chat_label(label):
                    schema_definitions.append(
                        schema_definer.define_schema(
                            text,
                            triplets,
                            prompt_texts["define_examples"],
                            prompt_texts["define_template"],
                        )
                    )

            embedder = _InjectedEmbedder(embed, audit["embedding_calls"])
            canonicalizer = SchemaCanonicalizer(
                schema_copy,
                embedder,
                verify_openai_model=model_route,
            )
            native_verify = canonicalizer.llm_verify

            def audited_verify(*args, **kwargs):
                first_call = len(calls)
                try:
                    result = native_verify(*args, **kwargs)
                except Exception as exc:
                    if len(calls) > first_call:
                        row = calls[first_call]
                        audit["parse_events"].append({
                            "parser": "native_canonicalization_choice",
                            "request_label": row["label"],
                            "raw_response": row["raw_response"],
                            "dropped": [{"reason": f"native verifier raised {type(exc).__name__}"}],
                        })
                    raise
                if len(calls) > first_call:
                    row = calls[first_call]
                    raw = row["raw_response"]
                    audit["parse_events"].append({
                        "parser": "native_canonicalization_choice",
                        "request_label": row["label"],
                        "raw_response": raw,
                        "first_character": raw[:1],
                        "accepted_triplet": _json_safe(result),
                        "dropped": [] if result is not None else [{
                            "reason": "native verifier response did not begin with a candidate letter"
                        }],
                    })
                return result

            canonicalizer.llm_verify = audited_verify
            canonicalized_triplets = []
            candidate_records = []
            for section_index, (text, triplets, definitions) in enumerate(
                zip(texts, extracted_triplets, schema_definitions), start=1
            ):
                section_result = []
                section_candidates = []
                for triple_index, triple in enumerate(triplets, start=1):
                    label = f"edc.canonicalize.section-{section_index:04d}.triplet-{triple_index:04d}"
                    with llm_utils.chat_label(label):
                        canonicalized, candidates = canonicalizer.canonicalize(
                            text,
                            triple,
                            definitions,
                            prompt_texts["canonicalize_template"],
                            enrich=False,
                        )
                    section_result.append(canonicalized)
                    section_candidates.append(candidates)
                canonicalized_triplets.append(section_result)
                candidate_records.append(section_candidates)

            audit["result"] = {
                "open_triplets": _json_safe(extracted_triplets),
                "schema_definitions": _json_safe(schema_definitions),
                "canonical_triplets": _json_safe(canonicalized_triplets),
                "canonicalization_candidates": _json_safe(candidate_records),
                "target_schema_after_run": _json_safe(canonicalizer.schema_dict),
                "target_schema_unchanged": canonicalizer.schema_dict == schema_copy,
            }
            audit["status"] = "completed"
            return _json_safe(canonicalized_triplets)
        except BaseException as exc:
            audit["status"] = "failed"
            audit["failure"] = {"type": type(exc).__name__}
            raise
        finally:
            if llm_utils is not None:
                if original_triplet_parser is not None:
                    llm_utils.parse_raw_triplets = original_triplet_parser
                if original_definition_parser is not None:
                    llm_utils.parse_relation_definition = original_definition_parser
                if chat_call_installed:
                    llm_utils.set_chat_call(original_chat_call)
            _write_json(audit_path, audit)
