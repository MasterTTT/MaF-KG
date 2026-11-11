"""Pinned iText2KG v0.0.5 graph-building adapter with injected model backends."""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Callable

PINNED_COMMIT = "43bc581c99a3a35ace99dc4699acdabb0f1a98f7"
PINNED_SOURCE_SHA256 = "c93a08d7aa80f52320ab65afa428cd3e5675ac4f3d9a1b0bfd5907bef520b499"
DEFAULT_MAX_CHAT_CALLS = 120
METHOD_ID = f"itext2kg-v0.0.5-{PINNED_COMMIT[:8]}"


class ChatCallLimitReached(RuntimeError):
    """Raised before an iText2KG call would exceed the explicit call ceiling."""


class OutputContractError(ValueError):
    """Raised when a response cannot be consumed by the pinned upstream code."""


def _json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, default=_json_default) + "\n").encode("utf-8")


def _json_default(value):
    if hasattr(value, "tolist"):
        return value.tolist()
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _write_json(path: Path, value) -> bytes:
    data = _json_bytes(value)
    path.write_bytes(data)
    return data


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _resolve_source_package(vendor_dir: str | Path) -> tuple[Path, Path]:
    root = Path(vendor_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"iText2KG vendor directory does not exist: {root}")

    if (root / "graph_integration" / "itext2kg.py").is_file() and root.name == "itext2kg":
        package_root = root
    elif (root / "itext2kg" / "graph_integration" / "itext2kg.py").is_file():
        package_root = root / "itext2kg"
    else:
        candidates = [path.parent.parent for path in root.rglob("itext2kg/graph_integration/itext2kg.py")]
        if len(candidates) != 1:
            raise FileNotFoundError(
                f"Expected one vendored itext2kg package under {root}; found {len(candidates)}"
            )
        package_root = candidates[0]

    return package_root.parent, package_root


def _source_tree_sha256(package_root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(package_root.rglob("*.py"), key=lambda item: item.relative_to(package_root).as_posix()):
        digest.update(path.relative_to(package_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _namespace(name: str, directory: Path) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__path__ = [str(directory)]
    module.__package__ = name
    sys.modules[name] = module
    return module


def _load_module(name: str, path: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load pinned iText2KG source module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    write_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = write_bytecode
    return module


def _load_pinned_iText2KG(source_root: Path, package_root: Path):
    try:
        numpy = importlib.import_module("numpy")
        importlib.import_module("sklearn.metrics.pairwise")
        importlib.import_module("pydantic")
        PromptTemplate = importlib.import_module("langchain_core.prompts").PromptTemplate
        JsonOutputParser = importlib.import_module("langchain_core.output_parsers").JsonOutputParser
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            f"Pinned iText2KG runtime dependency is missing: {exc.name}; "
            "required by the original matcher or prompt/parser bridge"
        ) from exc

    alias = "_itext2kg_v005_" + hashlib.sha256(str(source_root).encode("utf-8")).hexdigest()[:12]
    if alias in sys.modules:
        return sys.modules[alias].iText2KG

    _namespace(alias, package_root)
    utils = _namespace(f"{alias}.utils", package_root / "utils")
    graph_package = _namespace(f"{alias}.graph_integration", package_root / "graph_integration")
    entities_package = _namespace(f"{alias}.ientities_extraction", package_root / "ientities_extraction")
    relations_package = _namespace(f"{alias}.irelations_extraction", package_root / "irelations_extraction")

    schemas = _load_module(f"{alias}.utils.schemas", package_root / "utils" / "schemas.py")
    data_handling = _load_module(f"{alias}.utils.data_handling", package_root / "utils" / "data_handling.py")
    matcher = _load_module(f"{alias}.utils.matcher", package_root / "utils" / "matcher.py")

    class InjectedLangchainOutputParser:
        """Preserve the upstream prompt/parser contract while injecting bounded IO."""

        def __init__(self, llm_model, embeddings_model, sleep_time: int = 5) -> None:
            self.model = llm_model
            self.embeddings_model = embeddings_model

        def calculate_embeddings(self, text):
            if isinstance(text, str):
                vectors = self.embeddings_model.embed([text])
                if len(vectors) != 1:
                    raise ValueError("embed(texts) must return one vector for one input")
                return numpy.array(vectors[0])
            if isinstance(text, list) and all(isinstance(item, str) for item in text):
                return numpy.array(self.embeddings_model.embed(text))
            raise TypeError("Expected a string or a list of strings for embedding")

        def extract_information_as_json_for_context(
            self,
            output_data_structure,
            context: str,
            IE_query: str = (
                "\n        # DIRECTIVES : \n"
                "        - Act like an experienced information extractor. \n"
                "        - If you do not find the right information, keep its place empty.\n"
                "        "
            ),
        ):
            parser = JsonOutputParser(pydantic_object=output_data_structure)
            template = """
        Context: {context}

        Question: {query}
        Format_instructions : {format_instructions}
        Answer: """
            prompt = PromptTemplate(
                template=template,
                input_variables=["context", "query"],
                partial_variables={"format_instructions": parser.get_format_instructions()},
            )
            messages = [{"role": "user", "content": prompt.format(context=context, query=IE_query)}]
            schema_name = output_data_structure.__name__
            label_prefix = "entities" if schema_name == "EntitiesExtractor" else "relationships"
            raw = self.model.invoke(messages, label_prefix)
            parsed = parser.parse(raw)
            expected_key = {
                "EntitiesExtractor": "entities",
                "RelationshipsExtractor": "relationships",
            }.get(schema_name)
            if expected_key is None or not isinstance(parsed, dict):
                raise OutputContractError(f"Unexpected iText2KG output schema: {schema_name}")
            records = parsed.get(expected_key)
            required_fields = ("label", "name") if expected_key == "entities" else ("startNode", "endNode", "name")
            if not isinstance(records, list) or any(
                not isinstance(record, dict)
                or any(not isinstance(record.get(field), str) for field in required_fields)
                for record in records
            ):
                raise OutputContractError(f"Response does not satisfy the {expected_key} contract")
            return parsed

    utils.Matcher = matcher.Matcher
    utils.DataHandler = data_handling.DataHandler
    utils.LangchainOutputParser = InjectedLangchainOutputParser
    utils.EntitiesExtractor = schemas.EntitiesExtractor
    utils.RelationshipsExtractor = schemas.RelationshipsExtractor

    entities_module = _load_module(
        f"{alias}.ientities_extraction.ientities_extractor",
        package_root / "ientities_extraction" / "ientities_extractor.py",
    )
    relations_module = _load_module(
        f"{alias}.irelations_extraction.irelations_extractor",
        package_root / "irelations_extraction" / "irelations_extractor.py",
    )
    entities_package.iEntitiesExtractor = entities_module.iEntitiesExtractor
    relations_package.iRelationsExtractor = relations_module.iRelationsExtractor
    graph_module = _load_module(
        f"{alias}.graph_integration.itext2kg",
        package_root / "graph_integration" / "itext2kg.py",
    )
    graph_package.iText2KG = graph_module.iText2KG
    sys.modules[alias].iText2KG = graph_module.iText2KG
    return graph_module.iText2KG


class _ChatBackend:
    def __init__(self, callback: Callable, limit: int, trace: dict) -> None:
        self.callback = callback
        self.limit = limit
        self.trace = trace

    def invoke(self, messages: list[dict], label_prefix: str) -> str:
        call_number = len(self.trace["chat_calls"]) + 1
        label = f"itext2kg.{label_prefix}.call-{call_number:04d}"
        if call_number > self.limit:
            self.trace["gate"] = {"status": "max_chat_calls_reached", "next_label": label}
            raise ChatCallLimitReached(f"iText2KG chat-call ceiling reached ({self.limit})")

        row = {"label": label, "messages": messages, "status": "started"}
        self.trace["chat_calls"].append(row)
        try:
            response = self.callback(messages, label)
            if not isinstance(response, str):
                raise TypeError("chat(messages, label, **kwargs) must return a string")
            row.update(status="completed", response=response)
            return response
        except Exception as exc:
            row.update(status="failed", error_type=type(exc).__name__)
            raise


class _EmbeddingBackend:
    def __init__(self, callback: Callable, trace: dict) -> None:
        self.callback = callback
        self.trace = trace

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not isinstance(texts, list) or any(not isinstance(text, str) for text in texts):
            raise TypeError("embed(texts) expects a list of strings")
        row = {"texts": texts, "status": "started"}
        self.trace["embedding_calls"].append(row)
        try:
            if not texts:
                row.update(status="completed", vector_count=0, dimension=None, skipped_empty_batch=True)
                return []
            vectors = self.callback(texts)
            if not isinstance(vectors, (list, tuple)) or len(vectors) != len(texts):
                raise ValueError("embed(texts) must return one vector per input")
            validated_vectors = []
            dimension = None
            for vector in vectors:
                if not isinstance(vector, (list, tuple)) or not vector:
                    raise ValueError("Each embedding must be a non-empty numeric vector")
                values = [float(value) for value in vector]
                if dimension is None:
                    dimension = len(values)
                elif len(values) != dimension:
                    raise ValueError("Embedding dimensions are inconsistent")
                validated_vectors.append(values)
            row.update(status="completed", vector_count=len(validated_vectors), dimension=dimension)
            return validated_vectors
        except Exception as exc:
            row.update(status="failed", error_type=type(exc).__name__)
            raise


def run(
    vendor: str | Path,
    texts: list[str],
    client: Callable,
    embed: Callable,
    directory: str | Path,
    *,
    max_chat_calls: int = DEFAULT_MAX_CHAT_CALLS,
    embedding_model_id: str | None = None,
    no_progress_limit: int | None = None,
) -> dict:
    """Run the pinned upstream graph builder with single-attempt injected backends.

    The output directory must be fresh. Upstream entity/relation extraction, matching,
    thresholds and fusion remain the original v0.0.5 code. An explicitly enabled
    no-progress limit bounds the two isolated-entity loops as a labelled variant.
    """
    if not isinstance(texts, list) or not texts or any(not isinstance(text, str) for text in texts):
        raise ValueError("texts must be a non-empty list of strings")
    if not callable(client) or not callable(embed):
        raise TypeError("client and embed must be callable")
    if type(max_chat_calls) is not int or max_chat_calls < 1:
        raise ValueError("max_chat_calls must be a positive integer")
    if no_progress_limit is not None and (type(no_progress_limit) is not int or no_progress_limit < 1):
        raise ValueError("no_progress_limit must be a positive integer")

    output = Path(directory).resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "manifest.json"
    trace_path = output / "call_trace.json"
    result_path = output / "result.json"
    existing = [path.name for path in (manifest_path, trace_path, result_path) if path.exists()]
    if existing:
        raise FileExistsError(f"Refusing to overwrite existing iText2KG artifacts: {existing}")

    source_root, package_root = _resolve_source_package(vendor)
    source_sha256 = _source_tree_sha256(package_root)
    if source_sha256 != PINNED_SOURCE_SHA256:
        raise ValueError(f"Vendored iText2KG source hash mismatch for pinned commit {PINNED_COMMIT}")

    trace = {"chat_calls": [], "embedding_calls": [], "gate": None}
    method_id = METHOD_ID if no_progress_limit is None else f"{METHOD_ID}-progress-bound-{no_progress_limit}"
    manifest = {
        "method": method_id,
        "upstream_commit": PINNED_COMMIT,
        "upstream_source_sha256": source_sha256,
        "status": "running",
        "sections_sha256": _sha256(_json_bytes(texts)),
        "embedding_model_id": embedding_model_id or getattr(embed, "model_name", None)
        or "caller-supplied; identifier not declared",
        "embedding_transform": "none; injected vectors are not normalized or otherwise modified",
        "chat_contract": {"messages_only": True, "response_format": False, "retry_policy": "single attempt"},
        "document_distiller": False,
        "entity_threshold": 0.7,
        "relationship_threshold": 0.7,
        "isolated_entity_match_threshold": 0.5,
        "max_chat_calls": max_chat_calls,
        "call_count": 0,
        "embedding_call_count": 0,
        "no_progress_limit": no_progress_limit,
    }
    _write_json(manifest_path, manifest)

    try:
        graph_class = _load_pinned_iText2KG(source_root, package_root)
        if no_progress_limit is not None:
            from itext_progress_guard import bounded_graph_class
            graph_class = bounded_graph_class(graph_class, no_progress_limit)
            trace["no_progress_exits"] = []
        chat_backend = _ChatBackend(client, max_chat_calls, trace)
        embedding_backend = _EmbeddingBackend(embed, trace)
        graph = graph_class(llm_model=chat_backend, embeddings_model=embedding_backend)
        if no_progress_limit is not None:
            graph._no_progress_events = trace["no_progress_exits"]
        entities, relationships = graph.build_graph(sections=texts)
        result = {
            "method": method_id,
            "upstream_commit": PINNED_COMMIT,
            "entities": entities,
            "relationships": relationships,
        }
        result_bytes = _write_json(result_path, result)
        result = json.loads(result_bytes.decode("utf-8"))
        manifest.update(
            status="succeeded",
            call_count=len(trace["chat_calls"]),
            embedding_call_count=len(trace["embedding_calls"]),
            result_sha256=_sha256(result_bytes),
        )
        return result
    except BaseException as exc:
        manifest.update(
            status="failed",
            call_count=len(trace["chat_calls"]),
            embedding_call_count=len(trace["embedding_calls"]),
            error_type=type(exc).__name__,
        )
        raise
    finally:
        trace_bytes = _write_json(trace_path, trace)
        manifest["prompt_sha256"] = _sha256(
            _json_bytes([call["messages"] for call in trace["chat_calls"]])
        )
        manifest["call_trace_sha256"] = _sha256(trace_bytes)
        _write_json(manifest_path, manifest)
