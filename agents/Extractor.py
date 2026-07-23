#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx
import requests
from pydantic import BaseModel, Field, ValidationError, model_validator
from typing_extensions import TypedDict

warnings.filterwarnings("ignore", category=PendingDeprecationWarning, module=r"langgraph\\..*")
warnings.filterwarnings("ignore", message=r"The default value of `allowed_objects` will change.*")
try:
    from langchain_core._api.deprecation import LangChainPendingDeprecationWarning
    warnings.filterwarnings("ignore", category=LangChainPendingDeprecationWarning)
except Exception:
    pass

from langgraph.graph import END, StateGraph

ROOT = Path(__file__).resolve().parents[1]
PROMPT_DIR = ROOT / "prompts"
DEFAULT_INPUT = ROOT / "data" / "raw" / "第一章第一节_映射与函数.md"
DEFAULT_OUTPUT = ROOT / "KG" / "第一章第一节_映射与函数_kg_v8.json"
SCHEMA_VERSION = "maf-kg.agent.v8"
NODE_TYPES = ["Concept", "Formula", "Property", "Theorem", "Corollary", "Proof", "Example", "Figure"]
RELATION_SIGNATURES: dict[str, tuple[tuple[str, str], ...]] = {
    "has_formula": (("Concept", "Formula"), ("Property", "Formula"), ("Theorem", "Formula"), ("Corollary", "Formula")),
    "has_property": (("Concept", "Property"), ("Theorem", "Property")),
    "corollary_of": (("Corollary", "Theorem"),),
    "example_of": (("Example", "Concept"), ("Example", "Property"), ("Example", "Theorem"), ("Example", "Corollary")),
    "illustrated_by": (("Concept", "Figure"), ("Property", "Figure"), ("Theorem", "Figure"), ("Corollary", "Figure"), ("Example", "Figure")),
    "similar_to": (("Concept", "Concept"), ("Property", "Property"), ("Theorem", "Theorem"), ("Corollary", "Corollary"), ("Formula", "Formula")),
    "proved_by": (("Theorem", "Proof"), ("Corollary", "Proof"), ("Property", "Proof")),
}
RELATION_TYPES = list(RELATION_SIGNATURES)
EXAMPLE_PROOF_TYPES = {"Example", "Proof"}
OCCURRENCE_NODE_TYPES = {"Example", "Proof", "Figure"}
LINK_REQUIRED_NODE_TYPES = {"Formula", "Example", "Proof", "Figure"}

class Evidence(BaseModel):
    source_path: str
    start_line: int
    end_line: int
    text: str
    chunk_id: str

class EvidenceRef(BaseModel):
    start_line: int
    end_line: int

class KGNode(BaseModel):
    id: str
    type: Literal["Concept", "Formula", "Property", "Theorem", "Corollary", "Proof", "Example", "Figure"]
    name: str
    aliases: list[str] = Field(default_factory=list)
    description: str = ""
    content: dict[str, Any] = Field(default_factory=dict)

class KGRelation(BaseModel):
    source_id: str
    relation_type: Literal["has_formula", "has_property", "corollary_of", "example_of", "illustrated_by", "similar_to", "proved_by"]
    target_id: str
    score: float = Field(ge=0, le=1)
    evidence: Evidence

class ExtractionResult(BaseModel):
    schema_version: str = SCHEMA_VERSION
    source_path: str
    title: str
    provider: str
    model: str
    node_types: list[str] = Field(default_factory=lambda: list(NODE_TYPES))
    relation_types: list[str] = Field(default_factory=lambda: list(RELATION_TYPES))
    nodes: list[KGNode] = Field(default_factory=list)
    relations: list[KGRelation] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)

class NodeMemoryItem(BaseModel):
    id: str
    type: Literal["Concept", "Formula", "Property", "Theorem", "Corollary", "Proof", "Example", "Figure"]
    name: str
    aliases: list[str] = Field(default_factory=list)
    description: str = ""
    content: dict[str, Any] = Field(default_factory=dict)
    occurrences: list[Evidence] = Field(default_factory=list)
    retrieval_score: float = 0.0
    retrieval_reasons: list[str] = Field(default_factory=list)

class RelationMemoryItem(BaseModel):
    source_id: str
    relation_type: Literal["has_formula", "has_property", "corollary_of", "example_of", "illustrated_by", "similar_to", "proved_by"]
    target_id: str
    score: float = Field(ge=0, le=1)
    evidence: Evidence

class NodeAction(BaseModel):
    ref_id: str
    action: Literal["reuse", "create"]
    existing_id: str | None = None
    mention: str
    type: Literal["Concept", "Formula", "Property", "Theorem", "Corollary", "Proof", "Example", "Figure"] | None = None
    name: str = ""
    aliases: list[str] = Field(default_factory=list)
    description: str = ""
    content: dict[str, Any] = Field(default_factory=dict)
    evidence: EvidenceRef
    reason: str = ""

    @model_validator(mode="after")
    def validate_action(self) -> "NodeAction":
        if self.action == "reuse":
            if not self.existing_id:
                raise ValueError("reuse actions require existing_id")
        else:
            if self.existing_id is not None:
                raise ValueError("create actions must not include existing_id")
            if self.type is None or not self.name.strip():
                raise ValueError("create actions require type and name")
        if not self.ref_id.strip():
            raise ValueError("node action ref_id must not be empty")
        return self

class ProposedRelation(BaseModel):
    source_ref: str
    relation_type: Literal["has_formula", "has_property", "corollary_of", "example_of", "illustrated_by", "similar_to", "proved_by"]
    target_ref: str
    score: float = Field(ge=0, le=1)
    evidence: EvidenceRef

class ChunkExtractionResponse(BaseModel):
    node_actions: list[NodeAction] = Field(default_factory=list)
    relations: list[ProposedRelation] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

class NodeRecord(BaseModel):
    id: str
    type: Literal["Concept", "Formula", "Property", "Theorem", "Corollary", "Proof", "Example", "Figure"]
    name: str
    aliases: list[str] = Field(default_factory=list)
    description: str = ""
    content: dict[str, Any] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)

class RelationRecord(BaseModel):
    source_id: str
    relation_type: Literal["has_formula", "has_property", "corollary_of", "example_of", "illustrated_by", "similar_to", "proved_by"]
    target_id: str
    score: float = Field(ge=0, le=1)
    evidences: list[Evidence] = Field(default_factory=list)

@dataclass
class DocumentBlock:
    start_line: int
    end_line: int
    heading_path: list[str]
    lines: list[tuple[int, str]]
    kind: str
    @property
    def char_count(self) -> int:
        return sum(len(f"{n}: {t}") + 1 for n, t in self.lines)

@dataclass
class DocumentChunk:
    id: str
    start_line: int
    end_line: int
    heading_path: list[str]
    text: str
    context_before_text: str = ""
    context_after_text: str = ""
    block_count: int = 0
    block_types: tuple[str, ...] = ()
    break_reason: str = ""

class ExtractionState(TypedDict, total=False):
    input_path: str
    output_path: str
    provider: str
    model: str
    base_url: str
    api_key_env: str | None
    temperature: float
    max_chunk_chars: int
    chunk_context_before_lines: int
    chunk_context_after_lines: int
    max_retries: int
    request_timeout: float
    max_tokens: int
    save_output: bool
    use_response_format: bool
    no_proxy: bool
    request_delay: float
    resume: bool
    include_exercises: bool
    verbose: bool
    thinking: str
    reasoning_effort: str
    raw_lines: list[str]
    chunks: list[DocumentChunk]
    result: ExtractionResult
    diagnostics: dict[str, Any]


def _env_first(names: list[str]) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None

def _load_prompt(filename: str) -> str:
    path = PROMPT_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8").strip()

def _stable_hash(*parts: str) -> str:
    return hashlib.sha1("::".join(p.strip() for p in parts).encode("utf-8")).hexdigest()[:14]

def _slug(text: str, limit: int = 48) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", text).strip("_")[:limit] or "item"

def _normalize_name(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.strip().lower(), flags=re.UNICODE)

def _normalized_text(text: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text.lower())

def _char_ngrams(text: str, sizes: tuple[int, ...] = (2, 3)) -> set[str]:
    compact = _normalized_text(text)
    grams: set[str] = set()
    for size in sizes:
        grams.update(compact[index : index + size] for index in range(max(0, len(compact) - size + 1)))
    return grams

def _node_id(node_type: str, name: str) -> str:
    return f"{node_type.lower()}_{_slug(name)}_{_stable_hash(node_type, _normalize_name(name))}"

def _relation_key(source_id: str, relation_type: str, target_id: str) -> tuple[str, str, str]:
    if relation_type == "similar_to":
        left, right = sorted([source_id, target_id])
        return left, relation_type, right
    return source_id, relation_type, target_id

def _text_snippet(lines: list[str], start_line: int, end_line: int) -> str:
    start = max(1, start_line)
    end = min(len(lines), end_line)
    return "\n".join(lines[start - 1 : end]) if start <= end else ""

def _clamp_score(value: float | int | None, default: float = 0.8) -> float:
    return max(0.0, min(1.0, float(value))) if isinstance(value, (int, float)) else default

def _unique_preserve_order(items: list[str]) -> list[str]:
    seen, out = set(), []
    for item in items:
        item = item.strip()
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out

def _looks_like_latex(text: str) -> bool:
    return bool(text) and not re.search(r"[\u4e00-\u9fff]", text) and any(x in text for x in ["\\", "^", "_", "=", "\\frac", "\\sqrt", "\\lim", "\\in", "\\to"])

def _extract_latex_candidates(text: str) -> list[str]:
    items = []
    items.extend(m.group(1).strip() for m in re.finditer(r"\$\$(.*?)\$\$", text, flags=re.S))
    items.extend(m.group(1).strip() for m in re.finditer(r"\\\[(.*?)\\\]", text, flags=re.S))
    items.extend(m.group(1).strip() for m in re.finditer(r"(?<![\$\\])\$(?!\$)(.*?)(?<![\$\\])\$(?!\$)", text, flags=re.S) if m.group(1).strip())
    if not items and _looks_like_latex(text.strip()):
        items.append(text.strip())
    return [item for item in items if item]

def _best_latex_candidate(items: list[str]) -> str | None:
    return max(items, key=lambda s: (s.count("\\"), s.count("{"), len(s))) if items else None

def _load_source_latex_map(lines: list[str]) -> dict[int, list[str]]:
    candidates_by_line: dict[int, list[str]] = {}
    for line_no, line in enumerate(lines, start=1):
        candidates = _extract_latex_candidates(line)
        if candidates:
            candidates_by_line[line_no] = candidates

    opening_markers = {"$$": "$$", r"\[": r"\]", "```math": "```", "```latex": "```"}
    index = 0
    while index < len(lines):
        marker = lines[index].strip().lower()
        closing_marker = opening_markers.get(marker)
        if closing_marker is None:
            index += 1
            continue
        close_index = index + 1
        while close_index < len(lines) and lines[close_index].strip().lower() != closing_marker:
            close_index += 1
        if close_index >= len(lines):
            index += 1
            continue
        block_lines = lines[index + 1 : close_index]
        nonempty_offsets = [offset for offset, text in enumerate(block_lines) if text.strip()]
        if nonempty_offsets:
            first_offset = nonempty_offsets[0]
            last_offset = nonempty_offsets[-1]
            source_line = index + first_offset + 2
            latex = "\n".join(block_lines[first_offset : last_offset + 1]).strip()
            existing = candidates_by_line.setdefault(source_line, [])
            if latex and latex not in existing:
                existing.append(latex)
        index = close_index + 1

    return candidates_by_line

def _latex_candidate_id(line_no: int, candidate_index: int) -> str:
    return f"latex_L{line_no}_{candidate_index + 1}"

def _index_source_latex(source_latex_map: dict[int, list[str]]) -> dict[str, tuple[int, str]]:
    return {
        _latex_candidate_id(line_no, candidate_index): (line_no, latex)
        for line_no, candidates in source_latex_map.items()
        for candidate_index, latex in enumerate(candidates)
    }

def _extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise

class JsonParseFailure(RuntimeError):
    def __init__(self, message: str, raw_content: str) -> None:
        super().__init__(message)
        self.raw_content = raw_content


def _runtime_dir(name: str) -> Path:
    for candidate in [ROOT / "KG" / name, ROOT / "agents" / name, Path(tempfile.gettempdir()) / "maf_kg" / name]:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError:
            continue
    raise RuntimeError("Unable to create runtime directory.")

def _parse_response(content: str, label: str) -> dict[str, Any]:
    try:
        return _extract_json_object(content)
    except json.JSONDecodeError as exc:
        debug = _runtime_dir("_debug") / f"{int(time.time())}_{_slug(label)}_invalid.json.txt"
        debug.write_text(f"JSONDecodeError: {exc}\n\n{content}", encoding="utf-8")
        raise JsonParseFailure(f"JSON parsing failed and the raw response was saved to {debug}: {exc}", content) from exc

def _completion_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    return normalized if normalized.endswith("/chat/completions") else f"{normalized}/chat/completions"


def _response_json(response: Any) -> dict[str, Any]:
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Model API returned a non-object JSON response")
    return payload


def _post_httpx(endpoint_url: str, trust_env: bool, timeout: float, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    with httpx.Client(timeout=timeout, http2=False, headers=headers, trust_env=trust_env, limits=httpx.Limits(max_keepalive_connections=0, max_connections=2)) as client:
        response = client.post(endpoint_url, json=payload)
    return _response_json(response)


def _post_requests(endpoint_url: str, trust_env: bool, timeout: float, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    session = requests.Session()
    session.trust_env = trust_env
    response = session.post(endpoint_url, headers=headers, json=payload, timeout=timeout)
    return _response_json(response)


def _post_powershell(endpoint_url: str, api_key: str, timeout: float, payload: dict[str, Any], label: str) -> dict[str, Any]:
    runtime = _runtime_dir("_tmp")
    stamp = f"{int(time.time())}_{_slug(label)}"
    payload_path = runtime / f"{stamp}_payload.json"
    response_path = runtime / f"{stamp}_response.json"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    pp = str(payload_path).replace("'", "''")
    rp = str(response_path).replace("'", "''")
    url = endpoint_url.replace("'", "''")
    script = f"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Net.Http
$body=[System.IO.File]::ReadAllText('{pp}',[System.Text.Encoding]::UTF8)
$client=[System.Net.Http.HttpClient]::new()
$client.Timeout=[System.TimeSpan]::FromSeconds({int(timeout)})
$request=[System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Post,'{url}')
$request.Headers.Authorization=[System.Net.Http.Headers.AuthenticationHeaderValue]::new('Bearer',$env:MAF_KG_MODEL_API_KEY)
$request.Headers.Accept.ParseAdd('application/json')
$content=[System.Net.Http.ByteArrayContent]::new([System.Text.Encoding]::UTF8.GetBytes($body))
$content.Headers.ContentType=[System.Net.Http.Headers.MediaTypeHeaderValue]::Parse('application/json; charset=utf-8')
$request.Content=$content
$response=$client.SendAsync($request).GetAwaiter().GetResult()
$responseBody=$response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
if(-not $response.IsSuccessStatusCode){{throw "HTTP $([int]$response.StatusCode): $responseBody"}}
[System.IO.File]::WriteAllText('{rp}',$responseBody,[System.Text.Encoding]::UTF8)
"""
    env = os.environ.copy()
    env["MAF_KG_MODEL_API_KEY"] = api_key
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout + 30,
        env=env,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    data = json.loads(response_path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise RuntimeError("Model API returned a non-object JSON response")
    return data


class ModelProviderAdapter:
    name = ""
    default_model = ""
    default_base_url = ""
    api_key_envs: tuple[str, ...] = ()
    model_envs: tuple[str, ...] = ()
    base_url_envs: tuple[str, ...] = ()
    supports_response_format = False

    def build_payload(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        use_response_format: bool,
        thinking: str,
        reasoning_effort: str | None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def extract_content(self, response: dict[str, Any]) -> str:
        code = response.get("code")
        if code not in (None, 0):
            raise RuntimeError(f"{self.name} API error {code}: {response.get('message', 'unknown error')}")
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"{self.name} API response does not contain choices[0].message.content") from exc
        if not isinstance(content, str):
            raise RuntimeError(f"{self.name} API returned non-text message content")
        return content


class DeepSeekProviderAdapter(ModelProviderAdapter):
    name = "deepseek"
    default_model = "deepseek-v4-flash"
    default_base_url = "https://api.deepseek.com"
    api_key_envs = ("DEEPSEEK_API_KEY", "DS_API_KEY", "DEEPSEEK_KEY", "OPENAI_API_KEY")
    model_envs = ("DEEPSEEK_MODEL", "DS_MODEL")
    base_url_envs = ("DEEPSEEK_BASE_URL", "DS_BASE_URL", "OPENAI_BASE_URL")
    supports_response_format = True

    def build_payload(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        use_response_format: bool,
        thinking: str,
        reasoning_effort: str | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "stream": False,
            "max_tokens": max_tokens,
            "thinking": {"type": thinking},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if thinking == "disabled":
            payload["temperature"] = temperature
        elif reasoning_effort and "pro" in model.lower():
            payload["reasoning_effort"] = reasoning_effort
        if use_response_format:
            payload["response_format"] = {"type": "json_object"}
        return payload


class SparkProviderAdapter(ModelProviderAdapter):
    name = "spark"
    default_model = "spark-x"
    default_base_url = "https://spark-api-open.xf-yun.com/x2"
    api_key_envs = ("SPARK_API_PASSWORD", "XFYUN_API_PASSWORD", "SPARK_API_KEY", "XFYUN_API_KEY")
    model_envs = ("SPARK_MODEL", "XFYUN_MODEL")
    base_url_envs = ("SPARK_BASE_URL", "XFYUN_BASE_URL")

    def build_payload(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        use_response_format: bool,
        thinking: str,
        reasoning_effort: str | None,
    ) -> dict[str, Any]:
        # Spark X2 documents user/assistant/tool roles but not a system role.
        # Keep the same instruction content in a single user message.
        combined_prompt = (
            "SYSTEM INSTRUCTIONS\n"
            "===================\n"
            f"{system_prompt}\n\n"
            "CURRENT EXTRACTION INPUT\n"
            "========================\n"
            f"{user_prompt}"
        )
        return {
            "model": model,
            "stream": False,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "thinking": {"type": thinking},
            "messages": [{"role": "user", "content": combined_prompt}],
        }


PROVIDER_REGISTRY: dict[str, type[ModelProviderAdapter]] = {
    "deepseek": DeepSeekProviderAdapter,
    "spark": SparkProviderAdapter,
}
PROVIDER_ALIASES = {
    "ds": "deepseek",
    "xfyun": "spark",
    "spark-x": "spark",
}


def _provider_name(value: str | None) -> str:
    name = (value or os.getenv("MODEL_PROVIDER") or "deepseek").strip().lower()
    return PROVIDER_ALIASES.get(name, name)


def _provider_class(name: str) -> type[ModelProviderAdapter]:
    try:
        return PROVIDER_REGISTRY[name]
    except KeyError as exc:
        supported = ", ".join(sorted(PROVIDER_REGISTRY))
        raise ValueError(f"Unsupported model provider '{name}'. Supported providers: {supported}") from exc


class ProviderChatClient:
    def __init__(
        self,
        provider: str,
        model: str,
        base_url: str,
        api_key_env: str | None,
        temperature: float,
        timeout: float,
        max_retries: int,
        max_tokens: int,
        use_response_format: bool = True,
        trust_env: bool = True,
        thinking: Literal["enabled", "disabled"] = "disabled",
        reasoning_effort: str | None = None,
    ) -> None:
        adapter_class = _provider_class(provider)
        self.adapter = adapter_class()
        key_names = (api_key_env,) if api_key_env else adapter_class.api_key_envs
        self.api_key_name = next((name for name in key_names if os.getenv(name)), None)
        self.api_key = os.getenv(self.api_key_name) if self.api_key_name else None
        if not self.api_key:
            expected = api_key_env or " or ".join(adapter_class.api_key_envs)
            raise RuntimeError(f"{provider} API key not found. Set {expected}.")
        self.provider = provider
        self.endpoint_url = _completion_url(base_url)
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_tokens = max_tokens
        self.use_response_format = use_response_format and adapter_class.supports_response_format
        self.trust_env = trust_env
        self.thinking = thinking
        self.reasoning_effort = reasoning_effort

    def complete_json(self, system_prompt: str, user_prompt: str, label: str = "request") -> dict[str, Any]:
        payload = self.adapter.build_payload(
            model=self.model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            use_response_format=self.use_response_format,
            thinking=self.thinking,
            reasoning_effort=self.reasoning_effort,
        )
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Connection": "close",
            "User-Agent": f"MaF-KG-SequentialExtractor/0.2 ({self.provider})",
        }
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = _post_httpx(self.endpoint_url, self.trust_env, self.timeout, headers, payload)
                return _parse_response(self.adapter.extract_content(response), label)
            except Exception as exc:
                last_error = exc
                if attempt == self.max_retries:
                    fallbacks = [
                        lambda: _post_requests(self.endpoint_url, self.trust_env, self.timeout, headers, payload),
                        lambda: _post_powershell(self.endpoint_url, str(self.api_key), self.timeout, payload, label),
                    ]
                    for fallback in fallbacks:
                        try:
                            response = fallback()
                            return _parse_response(self.adapter.extract_content(response), label)
                        except Exception as fallback_exc:
                            last_error = fallback_exc
                else:
                    time.sleep(min(60, 2 ** (attempt + 1)))
        raise RuntimeError(f"{self.provider} API call failed: {last_error}") from last_error

def _make_heartbeat(message: str, verbose: bool, interval: float = 20.0) -> tuple[threading.Event, threading.Thread | None]:
    stop = threading.Event()
    if not verbose:
        return stop, None
    def worker() -> None:
        while not stop.wait(interval):
            print(message, flush=True)
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return stop, thread

class Extractor:
    def __init__(
        self,
        input_path: str | Path = DEFAULT_INPUT,
        output_path: str | Path = DEFAULT_OUTPUT,
        provider: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        api_key_env: str | None = None,
        temperature: float = 0.1,
        max_chunk_chars: int = 3200,
        chunk_context_before_lines: int = 6,
        chunk_context_after_lines: int = 4,
        max_retries: int = 2,
        request_timeout: float = 180,
        max_tokens: int = 12000,
        save_output: bool = True,
        use_response_format: bool = True,
        no_proxy: bool = False,
        request_delay: float = 3.0,
        thinking: str = "disabled",
        reasoning_effort: str = "medium",
        resume: bool = True,
        include_exercises: bool = False,
        verbose: bool = True,
    ) -> None:
        self.input_path = Path(input_path)
        self.output_path = Path(output_path)
        self.provider = _provider_name(provider)
        adapter_class = _provider_class(self.provider)
        self.model = model or _env_first(list(adapter_class.model_envs)) or adapter_class.default_model
        self.base_url = (base_url or _env_first(list(adapter_class.base_url_envs)) or adapter_class.default_base_url).rstrip("/")
        self.api_key_env = api_key_env
        self.temperature = temperature
        self.max_chunk_chars = max_chunk_chars
        self.chunk_context_before_lines = chunk_context_before_lines
        self.chunk_context_after_lines = chunk_context_after_lines
        self.max_retries = max_retries
        self.request_timeout = request_timeout
        self.max_tokens = max_tokens
        self.save_output = save_output
        self.use_response_format = use_response_format
        self.no_proxy = no_proxy
        self.request_delay = request_delay
        self.thinking = thinking
        self.reasoning_effort = reasoning_effort
        self.resume = resume
        self.include_exercises = include_exercises
        self.verbose = verbose
        self.system_prompt = _load_prompt("extraction_system.txt")
        self.client: ProviderChatClient | None = None
        self.source_latex_map: dict[int, list[str]] = {}
        self.source_latex_candidates: dict[str, tuple[int, str]] = {}
        self.graph = self._build_graph()

    def run(self) -> ExtractionResult:
        self._log(f"Starting Extractor with provider={self.provider} model={self.model}")
        state: ExtractionState = {
            "input_path": str(self.input_path),
            "output_path": str(self.output_path),
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "temperature": self.temperature,
            "max_chunk_chars": self.max_chunk_chars,
            "chunk_context_before_lines": self.chunk_context_before_lines,
            "chunk_context_after_lines": self.chunk_context_after_lines,
            "max_retries": self.max_retries,
            "request_timeout": self.request_timeout,
            "max_tokens": self.max_tokens,
            "save_output": self.save_output,
            "use_response_format": self.use_response_format,
            "no_proxy": self.no_proxy,
            "request_delay": self.request_delay,
            "resume": self.resume,
            "include_exercises": self.include_exercises,
            "verbose": self.verbose,
            "thinking": self.thinking,
            "reasoning_effort": self.reasoning_effort,
            "diagnostics": {},
        }
        return self.graph.invoke(state)["result"]
    def _build_graph(self):
        workflow = StateGraph(ExtractionState)
        workflow.add_node("load_document", self._load_document)
        workflow.add_node("chunk_document", self._chunk_document)
        workflow.add_node("extract_sequentially", self._extract_sequentially)
        workflow.add_node("save_result", self._save_result)
        workflow.set_entry_point("load_document")
        workflow.add_edge("load_document", "chunk_document")
        workflow.add_edge("chunk_document", "extract_sequentially")
        workflow.add_edge("extract_sequentially", "save_result")
        workflow.add_edge("save_result", END)
        return workflow.compile()

    def _client(self, state: ExtractionState) -> ProviderChatClient:
        if self.client is None:
            self.client = ProviderChatClient(
                provider=str(state["provider"]),
                model=str(state["model"]),
                base_url=str(state["base_url"]),
                api_key_env=state.get("api_key_env"),
                temperature=float(state["temperature"]),
                timeout=float(state["request_timeout"]),
                max_retries=int(state["max_retries"]),
                max_tokens=int(state["max_tokens"]),
                use_response_format=bool(state["use_response_format"]),
                trust_env=not bool(state.get("no_proxy", False)),
                thinking=state.get("thinking", "disabled"),
                reasoning_effort=state.get("reasoning_effort"),
            )
        return self.client
    def _log(self, message: str) -> None:
        if self.verbose:
            print(f"[Extractor] {message}", flush=True)

    def _load_document(self, state: ExtractionState) -> ExtractionState:
        self._log(f"Loading markdown: {self.input_path}")
        if not self.input_path.exists():
            raise FileNotFoundError(f"Input file not found: {self.input_path}")
        lines = self.input_path.read_text(encoding="utf-8").splitlines()
        if not state.get("include_exercises", False):
            cut = self._find_exercise_start(lines)
            if cut is not None:
                self._log(f"Skipping exercises from line {cut}; pass --include-exercises to extract them")
                lines = lines[: cut - 1]
        self.source_latex_map = _load_source_latex_map(lines)
        self.source_latex_candidates = _index_source_latex(self.source_latex_map)
        diagnostics = dict(state.get("diagnostics", {}))
        diagnostics.update({"line_count": len(lines), "source_latex_line_count": len(self.source_latex_map), "source_latex_candidate_count": len(self.source_latex_candidates), "input_path": str(self.input_path)})
        return {**state, "raw_lines": lines, "diagnostics": diagnostics}

    def _find_exercise_start(self, lines: list[str]) -> int | None:
        patterns = [
            r"^\s*#*\s*习题",
            r"^\s*#*\s*练习",
            r"^\s*#*\s*本节练习",
        ]
        for idx, line in enumerate(lines, start=1):
            if any(re.match(pattern, line.strip()) for pattern in patterns):
                return idx
        return None

    def _chunk_document(self, state: ExtractionState) -> ExtractionState:
        self._log("Preparing structure-aware line-numbered chunks")
        lines = state["raw_lines"]
        max_chars = int(state["max_chunk_chars"])
        before_lines = int(state["chunk_context_before_lines"])
        after_lines = int(state["chunk_context_after_lines"])

        heading_path: list[str] = []
        line_items: list[tuple[int, str, list[str]]] = []
        for line_no, line in enumerate(lines, start=1):
            if line.startswith("#"):
                level = len(line) - len(line.lstrip("#"))
                heading_path = heading_path[: level - 1] + [line[level:].strip()]
            line_items.append((line_no, line, list(heading_path)))

        def is_heading(line: str) -> bool:
            return line.startswith("#")

        def is_math(line: str) -> bool:
            stripped = line.strip()
            return stripped in {"$$", r"\[", r"\]"} or stripped.startswith("```math") or stripped.startswith("```latex")

        def is_image(line: str) -> bool:
            return bool(re.search(r"!\[.*?\]\((.*?)\)", line) or re.search(r"<img\s", line, flags=re.I))

        def is_example(text: str) -> bool:
            return bool(re.match(r"^\s*(例\s*\d+|例题\s*\d+)", text))

        def is_theorem(text: str) -> bool:
            return bool(re.match(r"^\s*(定理\s*\d+|定理\b)", text))

        def is_property(text: str) -> bool:
            return bool(re.match(r"^\s*(性质\s*\d*|推论\s*\d*)", text))

        def is_proof(text: str) -> bool:
            return bool(re.match(r"^\s*(证|证明)\s*[:：]?", text))

        def is_special(text: str) -> bool:
            return (
                is_heading(text)
                or is_example(text)
                or is_theorem(text)
                or is_property(text)
                or is_proof(text)
                or is_image(text)
                or is_math(text)
            )

        def block(items: list[tuple[int, str, list[str]]], kind: str) -> DocumentBlock:
            return DocumentBlock(
                start_line=items[0][0],
                end_line=items[-1][0],
                heading_path=items[0][2],
                lines=[(n, t) for n, t, _ in items],
                kind=kind,
            )

        blocks: list[DocumentBlock] = []
        i = 0
        while i < len(line_items):
            _, line, _ = line_items[i]
            stripped = line.strip()
            if not stripped:
                i += 1
                continue
            if is_heading(line):
                blocks.append(block([line_items[i]], "heading"))
                i += 1
                continue
            if is_math(line):
                start = i
                i += 1
                while i < len(line_items):
                    current = line_items[i][1]
                    i += 1
                    if is_math(current):
                        break
                blocks.append(block(line_items[start:i], "formula_block"))
                continue
            if is_image(line):
                start = i
                i += 1
                while i < len(line_items) and line_items[i][1].strip() and not (is_special(line_items[i][1].strip()) and not is_image(line_items[i][1])):
                    i += 1
                blocks.append(block(line_items[start:i], "figure"))
                continue

            kind_fn = None
            if is_example(stripped):
                kind_fn = "example"
            elif is_theorem(stripped):
                kind_fn = "theorem"
            elif is_property(stripped):
                kind_fn = "property"
            elif is_proof(stripped):
                kind_fn = "proof"

            if kind_fn:
                start = i
                i += 1
                while i < len(line_items):
                    nxt = line_items[i][1].strip()
                    if nxt and (is_heading(nxt) or is_example(nxt) or is_theorem(nxt) or (kind_fn in {"property", "proof"} and is_property(nxt))):
                        break
                    i += 1
                blocks.append(block(line_items[start:i], kind_fn))
                continue

            start = i
            i += 1
            while i < len(line_items):
                nxt = line_items[i][1].strip()
                if not nxt or is_special(nxt):
                    break
                i += 1
            blocks.append(block(line_items[start:i], "paragraph"))
            while i < len(line_items) and not line_items[i][1].strip():
                i += 1

        groups: list[tuple[list[DocumentBlock], str]] = []
        current: list[DocumentBlock] = []
        current_chars = 0

        def flush(reason: str) -> None:
            nonlocal current, current_chars
            if current:
                groups.append((current, reason))
            current = []
            current_chars = 0

        for blk in blocks:
            current_has_content = any(x.kind != "heading" for x in current)
            if blk.kind == "heading" and current and current_has_content:
                flush("heading_boundary")
            elif current and current_chars + blk.char_count > max_chars:
                flush("char_budget")
            current.append(blk)
            current_chars += blk.char_count
        flush("end")

        merged: list[tuple[list[DocumentBlock], str]] = []
        min_tail_chars = max(600, max_chars // 5)
        for group_blocks, reason in groups:
            group_chars = sum(b.char_count for b in group_blocks)
            if merged:
                prev_blocks, prev_reason = merged[-1]
                prev_chars = sum(b.char_count for b in prev_blocks)
                if group_chars < min_tail_chars and prev_blocks[-1].heading_path == group_blocks[0].heading_path and group_blocks[0].kind != "heading" and prev_chars + group_chars <= int(max_chars * 1.35):
                    merged[-1] = (prev_blocks + group_blocks, f"{prev_reason}+merged_small")
                    continue
            merged.append((group_blocks, reason))

        chunks: list[DocumentChunk] = []
        for idx, (group_blocks, reason) in enumerate(merged, start=1):
            start_line = group_blocks[0].start_line
            end_line = group_blocks[-1].end_line
            text = "\n".join(f"{n}: {t}" for blk in group_blocks for n, t in blk.lines)
            before = "\n".join(
                f"{n}: {lines[n - 1]}"
                for n in range(max(1, start_line - before_lines), start_line)
                if 1 <= n <= len(lines)
            )
            after = "\n".join(
                f"{n}: {lines[n - 1]}"
                for n in range(end_line + 1, min(len(lines), end_line + after_lines) + 1)
                if 1 <= n <= len(lines)
            )
            chunks.append(
                DocumentChunk(
                    id=f"chunk_{idx:03d}_{start_line}_{end_line}",
                    start_line=start_line,
                    end_line=end_line,
                    heading_path=group_blocks[0].heading_path,
                    text=text,
                    context_before_text=before,
                    context_after_text=after,
                    block_count=len(group_blocks),
                    block_types=tuple(blk.kind for blk in group_blocks),
                    break_reason=reason,
                )
            )

        diagnostics = dict(state.get("diagnostics", {}))
        diagnostics["chunk_count"] = len(chunks)
        diagnostics["chunk_spans"] = [
            {
                "id": c.id,
                "start_line": c.start_line,
                "end_line": c.end_line,
                "block_count": c.block_count,
                "block_types": list(c.block_types),
                "break_reason": c.break_reason,
            }
            for c in chunks
        ]
        self._log("Prepared " + str(len(chunks)) + " chunks: " + ", ".join(f"{c.id}[L{c.start_line}-L{c.end_line}]" for c in chunks))
        return {**state, "chunks": chunks, "diagnostics": diagnostics}

    def _extract_sequentially(self, state: ExtractionState) -> ExtractionState:
        client = self._client(state)
        lines = state["raw_lines"]
        node_store: dict[str, NodeRecord] = {}
        relation_store: dict[tuple[str, str, str], RelationRecord] = {}
        chunk_summaries: list[dict[str, Any]] = []
        decision_ledger: list[dict[str, Any]] = []
        processed_chunk_ids: list[str] = []
        notes: list[str] = []
        cache_hits = 0
        for idx, chunk in enumerate(state["chunks"], start=1):
            node_memory, relation_memory = self._build_memory_for_chunk(
                chunk,
                node_store,
                relation_store,
                recent_chunk_ids=set(processed_chunk_ids[-2:]),
            )
            cache_fingerprint = self._cache_fingerprint(chunk, state, node_memory, relation_memory)
            cached = self._read_chunk_cache(chunk.id, cache_fingerprint, state) if state.get("resume", True) else None
            cache_hit = cached is not None
            if cache_hit:
                response = ChunkExtractionResponse.model_validate(cached)
                cache_hits += 1
                self._log(f"Extractor chunk {idx}/{len(state['chunks'])} resume: {chunk.id} L{chunk.start_line}-L{chunk.end_line}")
            else:
                if idx > 1 and state.get("request_delay", 0) > 0:
                    time.sleep(float(state["request_delay"]))
                self._log(
                    f"Extractor chunk {idx}/{len(state['chunks'])} start: {chunk.id} "
                    f"L{chunk.start_line}-L{chunk.end_line}, memory={len(node_memory)} nodes/{len(relation_memory)} relations"
                )
                stop, thread = _make_heartbeat(
                    f"[Extractor] chunk {idx}/{len(state['chunks'])} waiting on model: {chunk.id}",
                    self.verbose,
                )
                try:
                    payload = client.complete_json(self.system_prompt, self._chunk_extraction_prompt(chunk, state["input_path"], node_memory, relation_memory), label=chunk.id)
                finally:
                    stop.set()
                    if thread is not None:
                        thread.join(timeout=0.1)
                response = ChunkExtractionResponse.model_validate(payload)
            working_node_store = {node_id: node.model_copy(deep=True) for node_id, node in node_store.items()}
            working_relation_store = {key: relation.model_copy(deep=True) for key, relation in relation_store.items()}
            summary = self._merge_chunk_response(
                response,
                chunk,
                state["input_path"],
                lines,
                working_node_store,
                working_relation_store,
                allowed_existing_ids={item.id for item in node_memory},
            )
            node_store = working_node_store
            relation_store = working_relation_store
            if not cache_hit:
                self._write_chunk_cache(chunk.id, cache_fingerprint, response.model_dump(mode="json"), state)
            decision_ledger.extend(summary.pop("decisions"))
            notes.extend(response.notes)
            summary["memory_node_count"] = len(node_memory)
            summary["memory_relation_count"] = len(relation_memory)
            chunk_summaries.append(summary)
            processed_chunk_ids.append(chunk.id)
            self._log(
                f"Extractor chunk {idx}/{len(state['chunks'])} done: "
                f"create={summary['created_node_count']}, reuse={summary['reused_node_count']}, "
                f"+{summary['new_relation_count']} relations, totals={len(node_store)} nodes/{len(relation_store)} relations"
            )
        result = self._finalize_result(
            state["input_path"],
            lines,
            node_store,
            relation_store,
            {
                **dict(state.get("diagnostics", {})),
                "cache_hits": cache_hits,
                "chunk_summaries": chunk_summaries,
                "memory_decisions": decision_ledger,
                "notes": notes,
            },
        )
        return {**state, "result": result}

    def _save_result(self, state: ExtractionState) -> ExtractionState:
        if state.get("save_output", True):
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            self.output_path.write_text(state["result"].model_dump_json(indent=2, ensure_ascii=False), encoding="utf-8")
            self._log(f"Saved result to {self.output_path}")
        return state
    def _build_memory_for_chunk(
        self,
        chunk: DocumentChunk,
        node_store: dict[str, NodeRecord],
        relation_store: dict[tuple[str, str, str], RelationRecord],
        recent_chunk_ids: set[str],
        max_nodes: int = 40,
        max_relations: int = 60,
    ) -> tuple[list[NodeMemoryItem], list[RelationMemoryItem]]:
        if not node_store:
            return [], []

        ranked: list[tuple[float, list[str], NodeRecord]] = []
        for node in node_store.values():
            score, reasons = self._node_memory_relevance(chunk, node, recent_chunk_ids)
            if score > 0:
                ranked.append((score, reasons, node))
        ranked.sort(key=lambda item: (-item[0], item[2].type, item[2].name, item[2].id))

        selected: dict[str, tuple[NodeRecord, float, list[str]]] = {}
        for score, reasons, node in ranked[:max_nodes]:
            selected[node.id] = (node, score, reasons)

        # Add one-hop neighbors after topical and recent seeds so relations have
        # enough local graph context without sending the entire historical graph.
        seed_ids = set(selected)
        neighbor_candidates: list[tuple[float, str, NodeRecord]] = []
        for rel in relation_store.values():
            if rel.source_id in seed_ids and rel.target_id not in selected and rel.target_id in node_store:
                neighbor_candidates.append((rel.score, rel.source_id, node_store[rel.target_id]))
            if rel.target_id in seed_ids and rel.source_id not in selected and rel.source_id in node_store:
                neighbor_candidates.append((rel.score, rel.target_id, node_store[rel.source_id]))
        for rel_score, seed_id, node in sorted(neighbor_candidates, key=lambda item: (-item[0], item[2].name, item[2].id)):
            if len(selected) >= max_nodes:
                break
            selected.setdefault(node.id, (node, rel_score, [f"one_hop:{seed_id}"]))

        selected_ids = set(selected)
        selected_rels = [
            relation
            for relation in sorted(
                relation_store.values(),
                key=lambda rel: (-rel.score, rel.relation_type, rel.source_id, rel.target_id),
            )
            if relation.source_id in selected_ids and relation.target_id in selected_ids
        ][:max_relations]
        memory_nodes = [
            self._node_memory_item(node, score, reasons)
            for node, score, reasons in sorted(
                selected.values(),
                key=lambda item: (-item[1], item[0].type, item[0].name, item[0].id),
            )
        ]
        return memory_nodes, [self._relation_memory_item(rel) for rel in selected_rels]

    def _node_memory_relevance(
        self,
        chunk: DocumentChunk,
        node: NodeRecord,
        recent_chunk_ids: set[str],
    ) -> tuple[float, list[str]]:
        haystack = "\n".join(
            [chunk.text, chunk.context_before_text, chunk.context_after_text, " ".join(chunk.heading_path)]
        )
        normalized_haystack = _normalized_text(haystack)
        chunk_grams = _char_ngrams(haystack)
        score = 0.0
        reasons: list[str] = []
        for token in [node.name, *node.aliases]:
            token = token.strip()
            normalized_token = _normalized_text(token)
            if len(normalized_token) >= 2 and normalized_token in normalized_haystack:
                score += 8.0 if token == node.name else 6.0
                reasons.append("exact_name" if token == node.name else "exact_alias")
                break

        node_text = " ".join([node.name, *node.aliases, node.description[:500]])
        node_grams = _char_ngrams(node_text)
        if node_grams and chunk_grams:
            overlap = len(node_grams & chunk_grams) / len(node_grams)
            if overlap >= 0.12:
                score += overlap * 4.0
                reasons.append(f"semantic_ngram:{overlap:.2f}")

        latex = str(node.content.get("latex") or "").strip()
        if latex and latex in chunk.text:
            score += 8.0
            reasons.append("exact_latex")

        if recent_chunk_ids and any(evidence.chunk_id in recent_chunk_ids for evidence in node.evidence):
            score += 2.5
            reasons.append("recent_chunk")
        return score, _unique_preserve_order(reasons)

    def _node_memory_item(self, node: NodeRecord, score: float, reasons: list[str]) -> NodeMemoryItem:
        occurrences = sorted(node.evidence, key=lambda evidence: (evidence.start_line, evidence.end_line))[-3:]
        return NodeMemoryItem(
            id=node.id,
            type=node.type,
            name=node.name,
            aliases=node.aliases,
            description=node.description,
            content=node.content,
            occurrences=occurrences,
            retrieval_score=round(score, 4),
            retrieval_reasons=reasons,
        )

    def _relation_memory_item(self, relation: RelationRecord) -> RelationMemoryItem:
        return RelationMemoryItem(source_id=relation.source_id, relation_type=relation.relation_type, target_id=relation.target_id, score=relation.score, evidence=self._best_relation_evidence(relation))

    def _chunk_extraction_prompt(
        self,
        chunk: DocumentChunk,
        source_path: str,
        node_memory: list[NodeMemoryItem],
        relation_memory: list[RelationMemoryItem],
    ) -> str:
        evidence_line_numbers = [int(match.group(1)) for line in chunk.text.splitlines() if (match := re.match(r"^([0-9]+):", line))]
        source_latex_candidates = self._source_latex_candidates_for_chunk(chunk)
        payload = {"source_path": source_path, "chunk": {"chunk_id": chunk.id, "start_line": chunk.start_line, "end_line": chunk.end_line, "heading_path": chunk.heading_path, "context_before_text": chunk.context_before_text, "text": chunk.text, "context_after_text": chunk.context_after_text, "evidence_line_numbers": evidence_line_numbers}, "source_latex_candidates": source_latex_candidates, "node_memory": [item.model_dump(mode="json") for item in node_memory], "relation_memory": [item.model_dump(mode="json") for item in relation_memory], "allowed_node_types": NODE_TYPES, "allowed_relation_types": RELATION_TYPES, "allowed_relation_signatures": {relation_type: [{"source_type": source_type, "target_type": target_type} for source_type, target_type in signatures] for relation_type, signatures in RELATION_SIGNATURES.items()}, "output_contract": OUTPUT_CONTRACT}
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _source_latex_candidates_for_chunk(self, chunk: DocumentChunk) -> list[dict[str, Any]]:
        visible_lines = {
            int(match.group(1))
            for line in chunk.text.splitlines()
            if (match := re.match(r"^([0-9]+):", line))
        }
        return [
            {"candidate_id": candidate_id, "line": line_no, "latex": latex}
            for candidate_id, (line_no, latex) in self.source_latex_candidates.items()
            if line_no in visible_lines
        ]

    def _merge_chunk_response(
        self,
        response: ChunkExtractionResponse,
        chunk: DocumentChunk,
        source_path: str,
        raw_lines: list[str],
        node_store: dict[str, NodeRecord],
        relation_store: dict[tuple[str, str, str], RelationRecord],
        allowed_existing_ids: set[str],
    ) -> dict[str, Any]:
        created_node_count = 0
        reused_node_count = 0
        new_relation_count = 0
        ref_to_final: dict[str, str] = {}
        created_refs: set[str] = set()
        link_required_refs: set[str] = set()
        relation_refs: set[str] = set()
        decisions: list[dict[str, Any]] = []
        skipped_items: list[dict[str, str]] = []

        def skip_item(kind: str, item_ref: str, reason: str) -> None:
            skipped_items.append({"kind": kind, "ref": item_ref, "reason": reason})
            self._log(f"Skipping {kind} in {chunk.id} ({item_ref}): {reason}")

        for action in response.node_actions:
            ref_id = action.ref_id.strip()
            if ref_id in ref_to_final:
                skip_item("node_action", ref_id, "duplicate ref_id")
                continue
            try:
                evidence = self._sanitize_evidence(action.evidence, chunk, source_path, raw_lines)
                if action.action == "reuse":
                    existing_id = str(action.existing_id)
                    if existing_id not in allowed_existing_ids:
                        raise ValueError(f"reuse target was not supplied in node_memory: {existing_id}")
                    if existing_id not in node_store:
                        raise ValueError(f"reuse target does not exist in canonical memory: {existing_id}")
                    existing = node_store[existing_id]
                    if action.type is not None and action.type != existing.type:
                        raise ValueError(
                            f"reuse type mismatch: {action.type} != canonical {existing.type} for {existing_id}"
                        )
                    self._merge_reuse_action(existing, action, evidence)
                    final_id = existing_id
                    reused_node_count += 1
                else:
                    sanitized = self._sanitize_create_action(action, evidence)
                    node_type = sanitized.type
                    if node_type is None:
                        raise ValueError(f"create action missing node type: {ref_id}")
                    final_id = self._allocate_node_id(sanitized, evidence, source_path, node_store)
                    node_store[final_id] = NodeRecord(
                        id=final_id,
                        type=node_type,
                        name=sanitized.name,
                        aliases=sanitized.aliases,
                        description=sanitized.description,
                        content=sanitized.content,
                        evidence=[evidence],
                    )
                    created_refs.add(ref_id)
                    if node_type in LINK_REQUIRED_NODE_TYPES:
                        link_required_refs.add(ref_id)
                    created_node_count += 1
            except ValueError as exc:
                skip_item("node_action", ref_id, str(exc))
                continue
            ref_to_final[ref_id] = final_id
            decisions.append(
                {
                    "chunk_id": chunk.id,
                    "ref_id": ref_id,
                    "mention": action.mention.strip(),
                    "action": action.action,
                    "node_id": final_id,
                    "start_line": evidence.start_line,
                    "end_line": evidence.end_line,
                    "reason": action.reason.strip(),
                    "status": "accepted",
                }
            )

        for relation in response.relations:
            source_ref = relation.source_ref.strip()
            target_ref = relation.target_ref.strip()
            relation_ref = f"{source_ref} -[{relation.relation_type}]-> {target_ref}"
            source_id = ref_to_final.get(source_ref)
            target_id = ref_to_final.get(target_ref)
            if source_id is None or target_id is None:
                skip_item("relation", relation_ref, "endpoint does not reference a valid node_action ref_id")
                continue
            if source_id == target_id:
                skip_item("relation", relation_ref, "self relation")
                continue
            if not self._relation_signature_valid(relation.relation_type, node_store[source_id].type, node_store[target_id].type):
                skip_item(
                    "relation",
                    relation_ref,
                    f"invalid type signature: {node_store[source_id].type} -> {node_store[target_id].type}",
                )
                continue
            try:
                evidence = self._sanitize_evidence(relation.evidence, chunk, source_path, raw_lines)
            except ValueError as exc:
                skip_item("relation", relation_ref, str(exc))
                continue
            relation_refs.update([source_ref, target_ref])
            key = _relation_key(source_id, relation.relation_type, target_id)
            if key in relation_store:
                relation_store[key].score = max(relation_store[key].score, _clamp_score(relation.score))
                self._append_relation_evidence(relation_store[key], evidence)
                continue
            left_source, rel_type, left_target = key
            relation_store[key] = RelationRecord(source_id=left_source, relation_type=rel_type, target_id=left_target, score=_clamp_score(relation.score), evidences=[evidence])
            new_relation_count += 1

        unlinked_created_refs = sorted(link_required_refs - relation_refs)
        if unlinked_created_refs:
            self._log(f"Removing unlinked occurrence/formula nodes in {chunk.id}: {unlinked_created_refs}")
            for ref_id in unlinked_created_refs:
                orphan_id = ref_to_final.pop(ref_id)
                node_store.pop(orphan_id, None)
                created_node_count -= 1
                for decision in decisions:
                    if decision["ref_id"] == ref_id:
                        decision["status"] = "removed_unlinked"
                        break
        pending_refs = sorted((created_refs - relation_refs) - link_required_refs)
        for ref_id in pending_refs:
            for decision in decisions:
                if decision["ref_id"] == ref_id:
                    decision["status"] = "pending_unlinked"
                    break
        return {
            "chunk_id": chunk.id,
            "created_node_count": created_node_count,
            "reused_node_count": reused_node_count,
            "new_relation_count": new_relation_count,
            "total_nodes": len(node_store),
            "total_relations": len(relation_store),
            "skipped_item_count": len(skipped_items),
            "skipped_items": skipped_items,
            "pending_node_count": len(pending_refs),
            "pending_node_refs": pending_refs,
            "decisions": decisions,
        }

    def _sanitize_create_action(self, action: NodeAction, evidence: Evidence) -> NodeAction:
        node_type = action.type
        name = action.name.strip()
        description = re.sub(r"\s+", " ", action.description).strip()
        if node_type is None or not name:
            raise ValueError(f"Create action {action.ref_id} requires type and name")
        if not description:
            raise ValueError(f"Create action {action.ref_id} requires a concrete description")
        return NodeAction(
            ref_id=action.ref_id.strip(),
            action="create",
            mention=action.mention.strip(),
            type=node_type,
            name=name,
            aliases=_unique_preserve_order(action.aliases),
            description=description,
            content=self._sanitize_node_content(node_type, action.content, evidence, required=True),
            evidence=EvidenceRef(start_line=evidence.start_line, end_line=evidence.end_line),
            reason=action.reason.strip(),
        )

    def _allocate_node_id(
        self,
        action: NodeAction,
        evidence: Evidence,
        source_path: str,
        node_store: dict[str, NodeRecord],
    ) -> str:
        if action.type is None:
            raise ValueError(f"Cannot allocate id without node type: {action.ref_id}")
        base_id = _node_id(action.type, action.name)
        if action.type in OCCURRENCE_NODE_TYPES:
            candidate_id = f"{base_id}_{_stable_hash(source_path, str(evidence.start_line), str(evidence.end_line))[:8]}"
        elif base_id not in node_store:
            candidate_id = base_id
        else:
            # The model explicitly chose create after seeing memory. Respect that
            # semantic decision and disambiguate the id by source occurrence.
            candidate_id = f"{base_id}_{_stable_hash(source_path, str(evidence.start_line), str(evidence.end_line))[:8]}"
        if candidate_id in node_store:
            raise ValueError(f"Duplicate create action resolved to existing id: {candidate_id}")
        return candidate_id

    def _sanitize_node_content(
        self,
        node_type: str,
        content: dict[str, Any],
        evidence: Evidence,
        required: bool,
    ) -> dict[str, Any]:
        content = dict(content or {})
        if node_type == "Formula":
            candidate_id = str(content.get("latex_candidate_id") or "").strip()
            if required and not candidate_id:
                raise ValueError("Formula content requires latex_candidate_id")
            if not candidate_id:
                return {}
            candidate = self.source_latex_candidates.get(candidate_id)
            if candidate is None:
                raise ValueError(f"Unknown Formula latex_candidate_id: {candidate_id}")
            line_no, latex = candidate
            if line_no < evidence.start_line or line_no > evidence.end_line:
                raise ValueError(
                    f"Formula candidate {candidate_id} at L{line_no} is outside evidence "
                    f"L{evidence.start_line}-L{evidence.end_line}"
                )
            return {"latex": latex}
        if node_type in EXAMPLE_PROOF_TYPES:
            result = {}
            approach = str(content.get("approach") or "").strip()
            steps = [str(step).strip() for step in content.get("steps", []) if str(step).strip()] if isinstance(content.get("steps", []), list) else []
            if required and (not approach or not steps):
                raise ValueError(f"{node_type} content requires approach and non-empty steps")
            if approach:
                result["approach"] = approach
            if steps:
                result["steps"] = steps
            return result
        if node_type == "Figure":
            result = {}
            label = str(content.get("label") or "").strip()
            url = str(content.get("url") or "").strip()
            if required and not label and not url:
                raise ValueError("Figure content requires label or url")
            if label:
                result["label"] = label
            if url:
                result["url"] = url
            return result
        if content:
            raise ValueError(f"{node_type} nodes must not contain type-specific content")
        return {}

    def _sanitize_evidence(self, evidence: EvidenceRef, chunk: DocumentChunk, source_path: str, raw_lines: list[str]) -> Evidence:
        start = evidence.start_line
        end = evidence.end_line
        evidence_line_numbers = {
            int(match.group(1))
            for line in chunk.text.splitlines()
            if (match := re.match(r"^([0-9]+):", line))
        }
        if start not in evidence_line_numbers or end not in evidence_line_numbers:
            raise ValueError(
                f"Evidence endpoints are not visible source lines in {chunk.id}: L{start}-L{end}"
            )
        if start < chunk.start_line or end > chunk.end_line or end < start:
            raise ValueError(
                f"Evidence lines outside current chunk {chunk.id}: L{start}-L{end}, "
                f"expected L{chunk.start_line}-L{chunk.end_line}"
            )
        source_excerpt = _text_snippet(raw_lines, start, end)
        if not source_excerpt.strip():
            raise ValueError(f"Evidence lines contain no source text in {chunk.id}: L{start}-L{end}")
        return Evidence(
            source_path=source_path,
            start_line=start,
            end_line=end,
            text=source_excerpt,
            chunk_id=chunk.id,
        )
    def _merge_reuse_action(self, existing: NodeRecord, action: NodeAction, evidence: Evidence) -> None:
        aliases = [*existing.aliases, *action.aliases]
        mention = action.mention.strip()
        if mention and _normalize_name(mention) != _normalize_name(existing.name):
            aliases.append(mention)
        existing.aliases = _unique_preserve_order(aliases)
        description = re.sub(r"\s+", " ", action.description).strip()
        if description:
            existing.description = description
        if action.content:
            incoming = self._sanitize_node_content(existing.type, action.content, evidence, required=False)
            for key, value in incoming.items():
                if key not in existing.content or not existing.content[key]:
                    existing.content[key] = value
        self._append_node_evidence(existing, evidence)

    def _append_node_evidence(self, node: NodeRecord, evidence: Evidence) -> None:
        signature = (evidence.source_path, evidence.start_line, evidence.end_line, evidence.text.strip())
        existing = {
            (item.source_path, item.start_line, item.end_line, item.text.strip())
            for item in node.evidence
        }
        if signature not in existing:
            node.evidence.append(evidence)

    def _relation_signature_valid(self, relation_type: str, source_type: str, target_type: str) -> bool:
        return (source_type, target_type) in RELATION_SIGNATURES.get(relation_type, ())
    def _append_relation_evidence(self, relation: RelationRecord, evidence: Evidence) -> None:
        sig = (evidence.start_line, evidence.end_line, evidence.text.strip())
        existing = {(e.start_line, e.end_line, e.text.strip()) for e in relation.evidences}
        if sig not in existing:
            relation.evidences.append(evidence)

    def _best_relation_evidence(self, relation: RelationRecord) -> Evidence:
        if not relation.evidences:
            return Evidence(source_path=str(self.input_path), start_line=1, end_line=1, text="")
        return max(relation.evidences, key=lambda e: (len(e.text.strip()), -(e.end_line - e.start_line)))

    def _finalize_result(self, source_path: str, lines: list[str], node_store: dict[str, NodeRecord], relation_store: dict[tuple[str, str, str], RelationRecord], diagnostics: dict[str, Any]) -> ExtractionResult:
        nodes = [KGNode(id=node.id, type=node.type, name=node.name, aliases=_unique_preserve_order(node.aliases), description=node.description, content=node.content) for node in sorted(node_store.values(), key=lambda x: (x.type, x.name, x.id))]
        relations = [KGRelation(source_id=rel.source_id, relation_type=rel.relation_type, target_id=rel.target_id, score=rel.score, evidence=self._best_relation_evidence(rel)) for rel in sorted(relation_store.values(), key=lambda x: (x.relation_type, x.source_id, x.target_id))]
        diagnostics = dict(diagnostics)
        diagnostics["node_count"] = len(nodes)
        diagnostics["relation_count"] = len(relations)
        diagnostics["node_type_counts"] = self._count_by_type(nodes)
        diagnostics["relation_type_counts"] = self._count_by_relation_type(relations)
        diagnostics["title"] = self._title_from_lines(lines)
        return ExtractionResult(source_path=source_path, title=self._title_from_lines(lines), provider=self.provider, model=self.model, nodes=nodes, relations=relations, diagnostics=diagnostics)

    def _count_by_type(self, nodes: list[KGNode]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for node in nodes:
            counts[node.type] = counts.get(node.type, 0) + 1
        return dict(sorted(counts.items()))

    def _count_by_relation_type(self, relations: list[KGRelation]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for relation in relations:
            counts[relation.relation_type] = counts.get(relation.relation_type, 0) + 1
        return dict(sorted(counts.items()))

    def _title_from_lines(self, lines: list[str]) -> str:
        for line in lines:
            if line.startswith("#"):
                title = line.lstrip("#").strip()
                if title:
                    return title
        return self.input_path.stem

    def _cache_dir(self) -> Path:
        path = ROOT / "node_cache" / "sequential_extractor_v8"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _cache_fingerprint(
        self,
        chunk: DocumentChunk,
        state: ExtractionState,
        node_memory: list[NodeMemoryItem],
        relation_memory: list[RelationMemoryItem],
    ) -> str:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "system_prompt_sha256": hashlib.sha256(self.system_prompt.encode("utf-8")).hexdigest(),
            "output_contract": OUTPUT_CONTRACT,
            "chunk": {
                "id": chunk.id,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "heading_path": chunk.heading_path,
                "context_before_text": chunk.context_before_text,
                "text": chunk.text,
                "context_after_text": chunk.context_after_text,
            },
            "node_memory": [item.model_dump(mode="json") for item in node_memory],
            "relation_memory": [item.model_dump(mode="json") for item in relation_memory],
            "source_latex_candidates": self._source_latex_candidates_for_chunk(chunk),
            "provider": state["provider"],
            "model": state["model"],
            "base_url": state["base_url"],
            "temperature": state["temperature"],
            "thinking": state.get("thinking"),
            "reasoning_effort": state.get("reasoning_effort"),
            "max_tokens": state["max_tokens"],
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:20]

    def _cache_path(self, chunk_id: str, fingerprint: str, state: ExtractionState) -> Path:
        filename = (
            f"{_slug(Path(state['input_path']).stem, 60)}__"
            f"{_slug(str(state['provider']), 20)}__{_slug(str(state['model']), 40)}__{chunk_id}__{fingerprint}.json"
        )
        return self._cache_dir() / filename

    def _read_chunk_cache(self, chunk_id: str, fingerprint: str, state: ExtractionState) -> dict[str, Any] | None:
        path = self._cache_path(chunk_id, fingerprint, state)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _write_chunk_cache(self, chunk_id: str, fingerprint: str, payload: dict[str, Any], state: ExtractionState) -> None:
        self._cache_path(chunk_id, fingerprint, state).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

OUTPUT_CONTRACT = {
    "node_actions": [
        {
            "ref_id": "Local reference used by relations.",
            "action": "reuse or create",
            "existing_id": "Required only for reuse; must come from node_memory.",
            "mention": "Exact current-chunk mention.",
            "type": "Required only for create.",
            "name": "Required only for create.",
            "aliases": ["Optional aliases."],
            "description": "Required for create; optional enrichment for reuse.",
            "content": {
                "latex_candidate_id": "Formula only; exact candidate_id from source_latex_candidates.",
                "approach": "Example or Proof only.",
                "steps": ["Example or Proof only."],
                "label": "Figure only.",
                "url": "Figure only.",
            },
            "evidence": {
                "start_line": "Inclusive supporting line from chunk.evidence_line_numbers.",
                "end_line": "Inclusive supporting line from chunk.evidence_line_numbers.",
            },
            "reason": "Short identity decision reason.",
        }
    ],
    "relations": [
        {
            "source_ref": "ref_id from node_actions",
            "relation_type": "Allowed type whose source/target types exactly match allowed_relation_signatures.",
            "target_ref": "ref_id from node_actions",
            "score": "Number from 0 to 1.",
            "evidence": {
                "start_line": "Inclusive supporting line from chunk.evidence_line_numbers.",
                "end_line": "Inclusive supporting line from chunk.evidence_line_numbers.",
            },
        }
    ],
    "notes": ["Optional diagnostic observations. Not persistent memory."],
}

def check_config(
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key_env: str | None = None,
    thinking: str = "disabled",
    reasoning_effort: str = "medium",
) -> int:
    provider_name = _provider_name(provider)
    adapter_class = _provider_class(provider_name)
    resolved_model = model or _env_first(list(adapter_class.model_envs)) or adapter_class.default_model
    resolved_base_url = (base_url or _env_first(list(adapter_class.base_url_envs)) or adapter_class.default_base_url).rstrip("/")
    key_names = (api_key_env,) if api_key_env else adapter_class.api_key_envs
    key_name = next((name for name in key_names if os.getenv(name)), None)
    print(
        json.dumps(
            {
                "provider": provider_name,
                "api_key_env": key_name or api_key_env,
                "api_key_found": bool(key_name),
                "base_url": resolved_base_url,
                "endpoint_url": _completion_url(resolved_base_url),
                "model": resolved_model,
                "thinking": thinking,
                "reasoning_effort": reasoning_effort if provider_name == "deepseek" else None,
                "response_format_supported": adapter_class.supports_response_format,
                "chunk_context_before_lines": 6,
                "chunk_context_after_lines": 4,
                "schema_version": SCHEMA_VERSION,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if key_name else 1


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sequential multi-provider KG extractor using LangGraph.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Markdown source path.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="JSON output path.")
    parser.add_argument(
        "--provider",
        default=None,
        choices=sorted(PROVIDER_REGISTRY),
        help="Model provider. Defaults to MODEL_PROVIDER or deepseek.",
    )
    parser.add_argument("--model", default=None, help="Provider model name. Uses the provider-specific environment variable or default.")
    parser.add_argument("--base-url", default=None, help="Provider base URL or full /chat/completions endpoint.")
    parser.add_argument("--api-key-env", default=None, help="Name of an environment variable containing the provider API key.")
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--max-chunk-chars", type=int, default=3200)
    parser.add_argument("--chunk-context-before-lines", type=int, default=6)
    parser.add_argument("--chunk-context-after-lines", type=int, default=4)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--request-timeout", type=float, default=180)
    parser.add_argument("--max-tokens", type=int, default=12000)
    parser.add_argument("--request-delay", type=float, default=3.0, help="Seconds to sleep between API calls.")
    parser.add_argument("--thinking", default="disabled", choices=["enabled", "disabled"], help="Provider thinking mode when supported.")
    parser.add_argument("--reasoning-effort", default="medium", choices=["low", "medium", "high"], help="Reasoning effort for providers/models that support it.")
    parser.add_argument("--no-save", action="store_true", help="Run the graph but do not write output file.")
    parser.add_argument("--no-resume", action="store_true", help="Ignore cached chunk outputs.")
    parser.add_argument("--include-exercises", action="store_true", help="Also extract exercise content.")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress logs.")
    parser.add_argument("--no-response-format", action="store_true", help="Disable JSON response_format for providers that support it.")
    parser.add_argument("--no-proxy", action="store_true", help="Ignore HTTP(S)_PROXY environment variables for model API calls.")
    parser.add_argument("--check-config", action="store_true", help="Check the selected provider configuration without calling the model.")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    if args.check_config:
        raise SystemExit(
            check_config(
                provider=args.provider,
                model=args.model,
                base_url=args.base_url,
                api_key_env=args.api_key_env,
                thinking=args.thinking,
                reasoning_effort=args.reasoning_effort,
            )
        )
    extractor = Extractor(
        input_path=args.input,
        output_path=args.output,
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        api_key_env=args.api_key_env,
        temperature=args.temperature,
        max_chunk_chars=args.max_chunk_chars,
        chunk_context_before_lines=args.chunk_context_before_lines,
        chunk_context_after_lines=args.chunk_context_after_lines,
        max_retries=args.max_retries,
        request_timeout=args.request_timeout,
        max_tokens=args.max_tokens,
        save_output=not args.no_save,
        use_response_format=not args.no_response_format,
        no_proxy=args.no_proxy,
        request_delay=args.request_delay,
        thinking=args.thinking,
        reasoning_effort=args.reasoning_effort,
        resume=not args.no_resume,
        include_exercises=args.include_exercises,
        verbose=not args.quiet,
    )
    try:
        result = extractor.run()
    except (RuntimeError, ValueError, ValidationError, json.JSONDecodeError) as exc:
        print(f"Extractor failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(result.diagnostics, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
