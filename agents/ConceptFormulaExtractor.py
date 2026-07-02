#!/usr/bin/env python3
"""Agent-driven Concept/Formula extractor for MaF-KG.

The extraction itself is done by LLM agents orchestrated with LangGraph.  Local
code only prepares line-numbered chunks, calls the DeepSeek-compatible API,
validates JSON, stabilizes ids, and saves the graph candidate set.
"""

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

warnings.filterwarnings("ignore", category=PendingDeprecationWarning, module=r"langgraph\..*")
warnings.filterwarnings("ignore", message=r"The default value of `allowed_objects` will change.*")
warnings.filterwarnings("ignore", category=Warning, message=r".*allowed_objects.*")
try:
    from langchain_core._api.deprecation import LangChainPendingDeprecationWarning

    warnings.filterwarnings("ignore", category=LangChainPendingDeprecationWarning)
except Exception:
    pass

from langgraph.graph import END, StateGraph
from langgraph.types import RetryPolicy
from pydantic import BaseModel, Field, ValidationError, model_validator
from typing_extensions import TypedDict


ROOT = Path(__file__).resolve().parents[1]
PROMPT_DIR = ROOT / "prompts"
DEFAULT_INPUT = ROOT / "data" / "raw" / "第一章第一节_映射与函数.md"
DEFAULT_OUTPUT = ROOT / "KG" / "第一章第一节_映射与函数_concept_formula_kg_agent.json"

NODE_TYPES = [
    "Concept",
    "Formula",
    "Property",
    "Theorem",
    "Example",
    "Proof",
    "Figure",
]

RELATIONSHIP_TYPES = [
    "has_formula",
    "has_property",
    "example_of",
    "illustrated_by",
    "prerequisite_of",
    "similar_to",
    "proved_by",
]


# Agent outputs are validated against these Pydantic models before they can
# move to the next LangGraph stage.
class Evidence(BaseModel):
    source_path: str
    start_line: int
    end_line: int
    text: str
    chunk_id: str | None = None


class KGNode(BaseModel):
    id: str = ""
    type: Literal[
        "Concept",
        "Formula",
        "Property",
        "Theorem",
        "Example",
        "Proof",
        "Figure",
    ]
    name: str
    aliases: list[str] = Field(default_factory=list)
    description: str = ""
    latex: str | None = None
    content: dict[str, Any] = Field(default_factory=dict)
    properties: dict[str, Any] = Field(default_factory=dict)
    quality: dict[str, Any] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1, exclude=True)

    @model_validator(mode="after")
    def _move_legacy_confidence_to_quality(self) -> "KGNode":
        if self.confidence is not None and "confidence" not in self.quality:
            self.quality["confidence"] = self.confidence
        if self.quality and "stage" not in self.quality:
            self.quality["stage"] = "candidate"
        return self


class KGRelationship(BaseModel):
    id: str = ""
    type: Literal[
        "has_formula",
        "has_property",
        "example_of",
        "illustrated_by",
        "prerequisite_of",
        "similar_to",
        "proved_by",
    ]
    source: str
    target: str
    evidence: list[Evidence] = Field(default_factory=list)
    content: dict[str, Any] = Field(default_factory=dict)
    confidence: float | None = Field(default=None, ge=0, le=1, exclude=True)
    properties: dict[str, Any] = Field(default_factory=dict)
    quality: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _move_legacy_confidence_to_quality(self) -> "KGRelationship":
        if self.confidence is not None and "confidence" not in self.quality:
            self.quality["confidence"] = self.confidence
        if self.quality and "stage" not in self.quality:
            self.quality["stage"] = "candidate"
        return self


class AgentGraphPayload(BaseModel):
    nodes: list[KGNode] = Field(default_factory=list)
    relationships: list[KGRelationship] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class FinalNode(BaseModel):
    """Compact published node schema."""

    id: str
    type: Literal[
        "Concept",
        "Formula",
        "Property",
        "Theorem",
        "Example",
        "Proof",
        "Figure",
    ]
    name: str
    aliases: list[str] = Field(default_factory=list)
    description: str = ""
    content: dict[str, Any] = Field(default_factory=dict)


class FinalRelationship(BaseModel):
    """Compact published relationship schema."""

    id: str
    type: Literal[
        "has_formula",
        "has_property",
        "example_of",
        "illustrated_by",
        "prerequisite_of",
        "similar_to",
        "proved_by",
    ]
    source: str
    target: str
    score: float = Field(ge=0, le=1)
    evidence: list[Evidence] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    schema_version: str = "maf-kg.agent.v5"
    source_path: str
    title: str
    model: str
    node_types: list[str] = Field(default_factory=lambda: NODE_TYPES)
    relationship_types: list[str] = Field(default_factory=lambda: RELATIONSHIP_TYPES)
    nodes: list[FinalNode]
    relationships: list[FinalRelationship]
    diagnostics: dict[str, Any] = Field(default_factory=dict)


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


@dataclass
class DocumentBlock:
    start_line: int
    end_line: int
    heading_path: list[str]
    lines: list[tuple[int, str]]
    kind: str

    @property
    def char_count(self) -> int:
        return sum(len(f"{line_no}: {line}") + 1 for line_no, line in self.lines)


class ExtractionState(TypedDict, total=False):
    # Shared LangGraph state passed between workflow nodes.
    input_path: str
    output_path: str
    model: str
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
    raw_lines: list[str]
    chunks: list[DocumentChunk]
    chunk_payloads: list[dict[str, Any]]
    resolved_payload: dict[str, Any]
    relation_payload: dict[str, Any]
    normalized_payload: dict[str, Any]
    verified_payload: dict[str, Any]
    extraction_thinking: str
    extraction_reasoning_effort: str
    relation_model: str
    relation_thinking: str
    relation_reasoning_effort: str
    normalizer_model: str
    normalizer_thinking: str
    normalizer_reasoning_effort: str
    verification_batch_size: int
    verification_model: str
    verification_reasoning_effort: str
    verification_thinking: str
    result: ExtractionResult
    diagnostics: dict[str, Any]


def _env_first(names: list[str]) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def _load_prompt(filename: str) -> str:
    prompt_path = PROMPT_DIR / filename
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8").strip()


def _stable_hash(*parts: str) -> str:
    data = "::".join(part.strip() for part in parts)
    return hashlib.sha1(data.encode("utf-8")).hexdigest()[:14]


def _slug(text: str, limit: int = 36) -> str:
    slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", text).strip("_")
    return slug[:limit] or "item"


def _node_id(node_type: str, name: str) -> str:
    # Final node ids are deterministic so repeated runs can be compared.
    return f"{node_type.lower()}_{_slug(name)}_{_stable_hash(node_type, name)}"


def _rel_id(rel_type: str, source: str, target: str) -> str:
    return f"rel_{_stable_hash(rel_type, source, target)}"


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


def _latex_candidates_from_text(text: str) -> list[str]:
    text = text.strip()
    candidates: list[str] = []
    candidates.extend(match.group(1).strip() for match in re.finditer(r"\$\$(.*?)\$\$", text, flags=re.S))
    candidates.extend(match.group(1).strip() for match in re.finditer(r"\\\[(.*?)\\\]", text, flags=re.S))
    candidates.extend(
        match.group(1).strip()
        for match in re.finditer(r"(?<![\$\\])\$(?!\$)(.*?)(?<![\$\\])\$(?!\$)", text, flags=re.S)
        if match.group(1).strip()
    )
    if not candidates and _looks_like_standalone_latex(text):
        candidates.append(text)
    return [candidate for candidate in candidates if candidate]


def _looks_like_standalone_latex(text: str) -> bool:
    if not text or re.search(r"[\u4e00-\u9fff]", text):
        return False
    if len(text) > 1600:
        return False
    math_markers = ["\\", "=", "<", ">", "\\in", "\\frac", "\\sqrt", "\\left", "\\begin", "^", "_"]
    return any(marker in text for marker in math_markers)


def _best_latex_candidate(candidates: list[str]) -> str | None:
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item.count("\\") + item.count("=") + item.count("{"), len(item)))


def _unique_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        normalized = item.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(normalized)
    return unique


def _quality_confidence(item: KGNode | KGRelationship) -> float:
    value = item.quality.get("confidence")
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    if item.confidence is not None:
        return max(0.0, min(1.0, float(item.confidence)))
    return 0.75


class JsonParseFailure(RuntimeError):
    def __init__(self, message: str, raw_content: str) -> None:
        super().__init__(message)
        self.raw_content = raw_content


class DeepSeekChatClient:
    """OpenAI-compatible DeepSeek client with retries and strict JSON parsing."""

    def __init__(
        self,
        model: str,
        temperature: float,
        timeout: float,
        max_retries: int,
        max_tokens: int,
        use_response_format: bool = True,
        trust_env: bool = True,
        thinking: Literal["enabled", "disabled"] = "disabled",
        reasoning_effort: str | None = None,
    ) -> None:
        self.api_key = _env_first(["DEEPSEEK_API_KEY", "DS_API_KEY", "DEEPSEEK_KEY", "OPENAI_API_KEY"])
        if not self.api_key:
            raise RuntimeError(
                "未找到 DeepSeek API key。请在系统环境变量中设置 DEEPSEEK_API_KEY "
                "或 DS_API_KEY。"
            )
        base_url = _env_first(["DEEPSEEK_BASE_URL", "DS_BASE_URL", "OPENAI_BASE_URL"])
        self.base_url = (base_url or "https://api.deepseek.com").rstrip("/")
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_tokens = max_tokens
        self.use_response_format = use_response_format
        self.trust_env = trust_env
        self.thinking = thinking
        self.reasoning_effort = reasoning_effort

    def complete_json(self, system_prompt: str, user_prompt: str, label: str = "request") -> dict[str, Any]:
        # Prefer Python HTTP clients; fall back to PowerShell/.NET on Windows SSL EOF issues.
        payload: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "max_tokens": self.max_tokens,
            "thinking": {"type": self.thinking},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if self.thinking == "disabled":
            payload["temperature"] = self.temperature
        elif self.reasoning_effort and self._supports_reasoning_effort():
            payload["reasoning_effort"] = self.reasoning_effort
        if self.use_response_format:
            payload["response_format"] = {"type": "json_object"}
        headers = self._headers()
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                content = self._post_with_httpx(payload, headers)
                return self._parse_or_repair_json(content, label)
            except Exception as exc:  # noqa: BLE001 - preserve API error context.
                last_error = exc
                if attempt == self.max_retries:
                    try:
                        content = self._post_with_requests(payload, headers)
                        return self._parse_or_repair_json(content, label)
                    except Exception as fallback_exc:  # noqa: BLE001
                        last_error = fallback_exc
                    try:
                        content = self._post_with_powershell(payload, label)
                        return self._parse_or_repair_json(content, label)
                    except Exception as fallback_exc:  # noqa: BLE001
                        last_error = fallback_exc
                if attempt >= self.max_retries:
                    break
                time.sleep(min(60, 2 ** (attempt + 1)))
        raise RuntimeError(f"DeepSeek API 调用失败: {last_error}") from last_error

    def _supports_reasoning_effort(self) -> bool:
        return "pro" in self.model.lower()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Connection": "close",
            "User-Agent": "MaF-KG-ConceptFormulaExtractor/0.1",
        }

    def _post_with_httpx(self, payload: dict[str, Any], headers: dict[str, str]) -> str:
        with httpx.Client(
            timeout=self.timeout,
            http2=False,
            headers=headers,
            trust_env=self.trust_env,
            limits=httpx.Limits(max_keepalive_connections=0, max_connections=2),
        ) as client:
            response = client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
            )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    def _post_with_requests(self, payload: dict[str, Any], headers: dict[str, str]) -> str:
        session = requests.Session()
        session.trust_env = self.trust_env
        response = session.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    def _post_with_powershell(self, payload: dict[str, Any], label: str) -> str:
        # Send raw UTF-8 bytes so PowerShell does not reinterpret Chinese text,
        # LaTeX backslashes, or large JSON strings.
        work_dir = self._runtime_dir("_tmp")
        payload_path = work_dir / f"{int(time.time())}_{_slug(label)}_payload.json"
        response_path = work_dir / f"{int(time.time())}_{_slug(label)}_response.json"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        payload_ps = str(payload_path).replace("'", "''")
        response_ps = str(response_path).replace("'", "''")
        url_ps = f"{self.base_url}/chat/completions".replace("'", "''")
        script = f"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Net.Http
$body = [System.IO.File]::ReadAllText('{payload_ps}', [System.Text.Encoding]::UTF8)
$client = [System.Net.Http.HttpClient]::new()
$client.Timeout = [System.TimeSpan]::FromSeconds({int(self.timeout)})
$request = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Post, '{url_ps}')
$request.Headers.Authorization = [System.Net.Http.Headers.AuthenticationHeaderValue]::new('Bearer', $env:DEEPSEEK_API_KEY)
$request.Headers.Accept.ParseAdd('application/json')
$content = [System.Net.Http.ByteArrayContent]::new([System.Text.Encoding]::UTF8.GetBytes($body))
$content.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse('application/json; charset=utf-8')
$request.Content = $content
$response = $client.SendAsync($request).GetAwaiter().GetResult()
$responseBody = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
if (-not $response.IsSuccessStatusCode) {{
    throw "HTTP $([int]$response.StatusCode): $responseBody"
}}
[System.IO.File]::WriteAllText('{response_ps}', $responseBody, [System.Text.Encoding]::UTF8)
"""
        env = os.environ.copy()
        env["DEEPSEEK_API_KEY"] = self.api_key
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout + 30,
            env=env,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
        response_json = json.loads(response_path.read_text(encoding="utf-8-sig"))
        return response_json["choices"][0]["message"]["content"]

    def _runtime_dir(self, name: str) -> Path:
        for candidate in [ROOT / "KG" / name, ROOT / "agents" / name, Path(tempfile.gettempdir()) / "maf_kg" / name]:
            try:
                candidate.mkdir(parents=True, exist_ok=True)
                return candidate
            except OSError:
                continue
        raise RuntimeError("无法创建运行时目录")

    def _parse_or_repair_json(self, content: str, label: str) -> dict[str, Any]:
        try:
            return _extract_json_object(content)
        except json.JSONDecodeError as exc:
            debug_path = self._write_debug_response(content, label, exc)
            raise JsonParseFailure(
                f"JSON parsing failed and the raw response was saved to {debug_path}: {exc}",
                content,
            ) from exc

    def _write_debug_response(self, content: str, label: str, error: json.JSONDecodeError) -> Path:
        debug_dir = self._runtime_dir("_debug")
        try:
            path = debug_dir / f"{int(time.time())}_{_slug(label)}_invalid.json.txt"
            path.write_text(
                f"JSONDecodeError: {error}\n\n{content}",
                encoding="utf-8",
            )
            return path
        except OSError:
            debug_dir = Path(tempfile.gettempdir()) / "maf_kg" / "_debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            path = debug_dir / f"{int(time.time())}_{_slug(label)}_invalid.json.txt"
            path.write_text(
                f"JSONDecodeError: {error}\n\n{content}",
                encoding="utf-8",
            )
            return path


class ConceptFormulaExtractor:
    """LangGraph workflow whose extraction decisions are made by LLM agents."""

    def __init__(
        self,
        input_path: str | Path = DEFAULT_INPUT,
        output_path: str | Path = DEFAULT_OUTPUT,
        model: str | None = None,
        temperature: float = 0.1,
        max_chunk_chars: int = 3000,
        chunk_context_before_lines: int = 6,
        chunk_context_after_lines: int = 4,
        max_retries: int = 2,
        request_timeout: float = 180,
        max_tokens: int = 16000,
        save_output: bool = True,
        use_response_format: bool = True,
        no_proxy: bool = False,
        request_delay: float = 3.0,
        extraction_thinking: str = "disabled",
        extraction_reasoning_effort: str = "high",
        relation_model: str | None = None,
        relation_thinking: str = "disabled",
        relation_reasoning_effort: str = "high",
        normalizer_model: str | None = None,
        normalizer_thinking: str = "disabled",
        normalizer_reasoning_effort: str = "high",
        verification_batch_size: int = 35,
        verification_model: str | None = None,
        verification_reasoning_effort: str = "high",
        verification_thinking: str = "enabled",
        resume: bool = True,
        include_exercises: bool = False,
        verbose: bool = True,
    ) -> None:
        self.input_path = Path(input_path)
        self.output_path = Path(output_path)
        self.model = model or os.getenv("DEEPSEEK_MODEL") or os.getenv("DS_MODEL") or "deepseek-v4-flash"
        self.temperature = temperature
        self.max_chunk_chars = max_chunk_chars
        self.chunk_context_before_lines = max(0, chunk_context_before_lines)
        self.chunk_context_after_lines = max(0, chunk_context_after_lines)
        self.max_retries = max_retries
        self.request_timeout = request_timeout
        self.max_tokens = max_tokens
        self.save_output = save_output
        self.use_response_format = use_response_format
        self.no_proxy = no_proxy
        self.request_delay = request_delay
        self.extraction_thinking = extraction_thinking
        self.extraction_reasoning_effort = extraction_reasoning_effort
        self.relation_model = relation_model or os.getenv("DEEPSEEK_RELATION_MODEL") or os.getenv("DS_RELATION_MODEL")
        self.relation_thinking = relation_thinking
        self.relation_reasoning_effort = relation_reasoning_effort
        self.relation_client: DeepSeekChatClient | None = None
        self.normalizer_model = (
            normalizer_model or os.getenv("DEEPSEEK_NORMALIZER_MODEL") or os.getenv("DS_NORMALIZER_MODEL")
        )
        self.normalizer_thinking = normalizer_thinking
        self.normalizer_reasoning_effort = normalizer_reasoning_effort
        self.normalizer_client: DeepSeekChatClient | None = None
        self.verification_batch_size = verification_batch_size
        self.verification_model = (
            verification_model
            or os.getenv("DEEPSEEK_VERIFICATION_MODEL")
            or os.getenv("DS_VERIFICATION_MODEL")
            or "deepseek-v4-flash"
        )
        self.verification_reasoning_effort = verification_reasoning_effort
        self.verification_thinking = verification_thinking
        self.resume = resume
        self.include_exercises = include_exercises
        self.verbose = verbose
        self.client: DeepSeekChatClient | None = None
        self.verification_client: DeepSeekChatClient | None = None
        self.graph = self._build_graph()

    def _build_graph(self):
        # LangGraph pipeline:
        # markdown -> chunks -> local extraction -> programmatic node resolution
        # -> cross-chunk relation completion -> normalization -> verification -> JSON output.
        workflow = StateGraph(ExtractionState)
        api_retry = RetryPolicy(
            initial_interval=8.0,
            backoff_factor=2.0,
            max_interval=90.0,
            max_attempts=3,
            jitter=True,
        )
        workflow.add_node("load_document", self._load_document)
        workflow.add_node("chunk_document", self._chunk_document)
        workflow.add_node("extract_with_agents", self._extract_with_agents, retry_policy=api_retry)
        workflow.add_node("resolve_nodes", self._resolve_nodes)
        workflow.add_node("infer_cross_chunk_relationships", self._infer_cross_chunk_relationships, retry_policy=api_retry)
        workflow.add_node("normalize_with_agent", self._normalize_with_agent, retry_policy=api_retry)
        workflow.add_node("verify_with_agent", self._verify_with_agent, retry_policy=api_retry)
        workflow.add_node("finalize_graph", self._finalize_graph)
        workflow.add_node("save_result", self._save_result)

        workflow.set_entry_point("load_document")
        workflow.add_edge("load_document", "chunk_document")
        workflow.add_edge("chunk_document", "extract_with_agents")
        workflow.add_edge("extract_with_agents", "resolve_nodes")
        workflow.add_edge("resolve_nodes", "infer_cross_chunk_relationships")
        workflow.add_edge("infer_cross_chunk_relationships", "normalize_with_agent")
        workflow.add_edge("normalize_with_agent", "verify_with_agent")
        workflow.add_edge("verify_with_agent", "finalize_graph")
        workflow.add_edge("finalize_graph", "save_result")
        workflow.add_edge("save_result", END)
        return workflow.compile()

    def run(self) -> ExtractionResult:
        state: ExtractionState = {
            "input_path": str(self.input_path),
            "output_path": str(self.output_path),
            "model": self.model,
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
            "extraction_thinking": self.extraction_thinking,
            "extraction_reasoning_effort": self.extraction_reasoning_effort,
            "relation_model": self.relation_model or self.model,
            "relation_thinking": self.relation_thinking,
            "relation_reasoning_effort": self.relation_reasoning_effort,
            "normalizer_model": self.normalizer_model or self.model,
            "normalizer_thinking": self.normalizer_thinking,
            "normalizer_reasoning_effort": self.normalizer_reasoning_effort,
            "verification_batch_size": self.verification_batch_size,
            "verification_model": self.verification_model,
            "verification_reasoning_effort": self.verification_reasoning_effort,
            "verification_thinking": self.verification_thinking,
            "resume": self.resume,
            "include_exercises": self.include_exercises,
            "verbose": self.verbose,
            "diagnostics": {},
        }
        self._log(state, f"Starting ConceptFormulaExtractor with model={self.model}")
        final_state = self.graph.invoke(state)
        return final_state["result"]

    def _log(self, state: ExtractionState, message: str) -> None:
        if state.get("verbose", True):
            print(f"[ConceptFormulaExtractor] {message}", flush=True)

    def _complete_json_with_heartbeat(
        self,
        state: ExtractionState,
        client: DeepSeekChatClient,
        system_prompt: str,
        user_prompt: str,
        *,
        label: str,
        stage_name: str,
        request_summary: str,
        heartbeat_seconds: int = 20,
    ) -> dict[str, Any]:
        self._log(state, f"{stage_name} request submitted: {request_summary}")
        started = time.time()
        done = threading.Event()
        outcome: dict[str, Any] = {}

        def _runner() -> None:
            try:
                outcome["value"] = client.complete_json(system_prompt, user_prompt, label=label)
            except BaseException as exc:  # noqa: BLE001
                outcome["error"] = exc
            finally:
                done.set()

        worker = threading.Thread(target=_runner, daemon=True)
        worker.start()
        while not done.wait(timeout=heartbeat_seconds):
            elapsed = int(time.time() - started)
            self._log(state, f"{stage_name} still running: {elapsed}s elapsed, waiting for model response")
        worker.join()
        elapsed = int(time.time() - started)
        if "error" in outcome:
            self._log(state, f"{stage_name} failed after {elapsed}s")
            raise outcome["error"]
        self._log(state, f"{stage_name} response received after {elapsed}s")
        return outcome["value"]

    def _client(self, state: ExtractionState) -> DeepSeekChatClient:
        if self.client is None:
            self.client = DeepSeekChatClient(
                model=state["model"],
                temperature=state["temperature"],
                timeout=state["request_timeout"],
                max_retries=state["max_retries"],
                max_tokens=state["max_tokens"],
                use_response_format=bool(state.get("use_response_format", False)),
                trust_env=not bool(state.get("no_proxy", False)),
                thinking=state.get("extraction_thinking", "disabled"),  # type: ignore[arg-type]
                reasoning_effort=state.get("extraction_reasoning_effort"),
            )
        return self.client

    def _verification_client(self, state: ExtractionState) -> DeepSeekChatClient:
        if self.verification_client is None:
            self.verification_client = DeepSeekChatClient(
                model=state.get("verification_model", "deepseek-v4-flash"),
                temperature=state["temperature"],
                timeout=state["request_timeout"],
                max_retries=state["max_retries"],
                max_tokens=state["max_tokens"],
                use_response_format=bool(state.get("use_response_format", False)),
                trust_env=not bool(state.get("no_proxy", False)),
                thinking=state.get("verification_thinking", "enabled"),  # type: ignore[arg-type]
                reasoning_effort=state.get("verification_reasoning_effort", "high"),
            )
        return self.verification_client

    def _relation_client(self, state: ExtractionState) -> DeepSeekChatClient:
        if self.relation_client is None:
            self.relation_client = DeepSeekChatClient(
                model=state.get("relation_model", state["model"]),
                temperature=state["temperature"],
                timeout=state["request_timeout"],
                max_retries=state["max_retries"],
                max_tokens=state["max_tokens"],
                use_response_format=bool(state.get("use_response_format", False)),
                trust_env=not bool(state.get("no_proxy", False)),
                thinking=state.get("relation_thinking", "disabled"),  # type: ignore[arg-type]
                reasoning_effort=state.get("relation_reasoning_effort"),
            )
        return self.relation_client

    def _normalizer_client(self, state: ExtractionState) -> DeepSeekChatClient:
        if self.normalizer_client is None:
            self.normalizer_client = DeepSeekChatClient(
                model=state.get("normalizer_model", state["model"]),
                temperature=state["temperature"],
                timeout=state["request_timeout"],
                max_retries=state["max_retries"],
                max_tokens=state["max_tokens"],
                use_response_format=bool(state.get("use_response_format", False)),
                trust_env=not bool(state.get("no_proxy", False)),
                thinking=state.get("normalizer_thinking", "disabled"),  # type: ignore[arg-type]
                reasoning_effort=state.get("normalizer_reasoning_effort"),
            )
        return self.normalizer_client

    def _load_document(self, state: ExtractionState) -> ExtractionState:
        input_path = Path(state["input_path"])
        self._log(state, f"Loading markdown: {input_path}")
        lines = input_path.read_text(encoding="utf-8").splitlines()
        # Skip exercises by default so this experiment focuses on the textbook section body.
        if not state.get("include_exercises", False):
            for index, line in enumerate(lines):
                if re.match(r"^#\s*习题", line.strip()):
                    lines = lines[:index]
                    self._log(state, f"Skipping exercises from line {index + 1}; pass --include-exercises to extract them")
                    break
        diagnostics = dict(state.get("diagnostics", {}))
        diagnostics["raw_line_count"] = len(lines)
        return {**state, "raw_lines": lines, "diagnostics": diagnostics}

    def _chunk_document(self, state: ExtractionState) -> ExtractionState:
        self._log(state, "Preparing structure-aware line-numbered chunks")
        lines = state["raw_lines"]
        max_chars = state["max_chunk_chars"]
        context_before_lines = max(0, int(state.get("chunk_context_before_lines", 0)))
        context_after_lines = max(0, int(state.get("chunk_context_after_lines", 0)))
        heading_stack: list[tuple[int, str]] = []

        def heading_level(line: str) -> int:
            marker = line.split(" ", 1)[0]
            return marker.count("#") if marker.startswith("#") else 99

        def is_heading(line: str) -> bool:
            return line.startswith("#")

        def is_math_delimiter(line: str) -> bool:
            return line.strip() in {"$$", "\\[", "\\]"}

        def is_image(line: str) -> bool:
            return bool(re.match(r"^\s*!\[.*?\]\(.*?\)", line))

        def is_special_start(line: str) -> bool:
            stripped = line.strip()
            return bool(
                is_heading(stripped)
                or is_math_delimiter(stripped)
                or is_image(stripped)
                or re.match(r"^\*?\s*例\s*\d+", stripped)
                or re.match(r"^\*?\s*定理\s*\d+", stripped)
                or re.match(r"^\*?\s*推论", stripped)
                or re.match(r"^(证|证明|解)\s", stripped)
            )

        line_items: list[tuple[int, str, list[str]]] = []
        for line_no, line in enumerate(lines, start=1):
            if line.startswith("#"):
                level = heading_level(line)
                title = line.lstrip("#").strip()
                heading_stack = [item for item in heading_stack if item[0] < level]
                heading_stack.append((level, title))
            heading_path = [title for _, title in heading_stack]
            line_items.append((line_no, line, heading_path))

        def make_block(items: list[tuple[int, str, list[str]]], kind: str) -> DocumentBlock:
            start = items[0][0]
            end = items[-1][0]
            heading_path = items[0][2]
            return DocumentBlock(
                start_line=start,
                end_line=end,
                heading_path=heading_path,
                lines=[(line_no, line) for line_no, line, _ in items],
                kind=kind,
            )

        blocks: list[DocumentBlock] = []
        index = 0
        while index < len(line_items):
            line_no, line, _ = line_items[index]
            stripped = line.strip()
            if not stripped:
                index += 1
                continue
            if is_heading(line):
                blocks.append(make_block([line_items[index]], "heading"))
                index += 1
                continue
            if is_math_delimiter(line):
                start = index
                index += 1
                while index < len(line_items):
                    current_line = line_items[index][1]
                    index += 1
                    if is_math_delimiter(current_line):
                        break
                blocks.append(make_block(line_items[start:index], "formula_block"))
                continue
            if is_image(line):
                start = index
                index += 1
                while index < len(line_items) and not line_items[index][1].strip():
                    index += 1
                if index < len(line_items) and re.match(r"^\s*图\s*\d+", line_items[index][1].strip()):
                    index += 1
                blocks.append(make_block(line_items[start:index], "figure"))
                continue
            if re.match(r"^\*?\s*例\s*\d+", stripped):
                start = index
                index += 1
                while index < len(line_items):
                    next_line = line_items[index][1].strip()
                    if re.match(r"^#|^\*?\s*例\s*\d+|^\*?\s*定理\s*\d+|^\*?\s*推论", next_line):
                        break
                    index += 1
                blocks.append(make_block(line_items[start:index], "example"))
                continue
            if re.match(r"^\*?\s*定理\s*\d+", stripped):
                start = index
                index += 1
                while index < len(line_items):
                    next_line = line_items[index][1].strip()
                    if re.match(r"^#|^\*?\s*例\s*\d+|^\*?\s*定理\s*\d+|^\*?\s*推论", next_line):
                        break
                    index += 1
                blocks.append(make_block(line_items[start:index], "theorem"))
                continue
            if re.match(r"^\*?\s*推论", stripped):
                start = index
                index += 1
                while index < len(line_items):
                    next_line = line_items[index][1].strip()
                    if re.match(r"^#|^\*?\s*例\s*\d+|^\*?\s*定理\s*\d+|^\*?\s*推论", next_line):
                        break
                    index += 1
                blocks.append(make_block(line_items[start:index], "property"))
                continue
            if re.match(r"^(证|证明|解)\s", stripped):
                start = index
                index += 1
                while index < len(line_items):
                    next_line = line_items[index][1].strip()
                    if is_special_start(next_line):
                        break
                    index += 1
                blocks.append(make_block(line_items[start:index], "proof"))
                continue

            start = index
            index += 1
            while index < len(line_items):
                next_line = line_items[index][1]
                if not next_line.strip() or is_special_start(next_line.strip()):
                    break
                index += 1
            blocks.append(make_block(line_items[start:index], "paragraph"))
            while index < len(line_items) and not line_items[index][1].strip():
                index += 1

        chunk_groups: list[tuple[list[DocumentBlock], str]] = []
        current_blocks: list[DocumentBlock] = []
        current_chars = 0

        def flush(reason: str) -> None:
            nonlocal current_blocks, current_chars
            if not current_blocks:
                return
            chunk_groups.append((current_blocks, reason))
            current_blocks = []
            current_chars = 0

        for block in blocks:
            current_has_content = any(existing.kind != "heading" for existing in current_blocks)
            if block.kind == "heading" and current_blocks and current_has_content:
                flush("heading_boundary")
            elif current_blocks and current_chars + block.char_count > max_chars:
                flush("char_budget")
            current_blocks.append(block)
            current_chars += block.char_count
        flush("end")

        min_tail_chars = max(600, max_chars // 5)
        merged_groups: list[tuple[list[DocumentBlock], str]] = []
        for group_blocks, reason in chunk_groups:
            group_chars = sum(block.char_count for block in group_blocks)
            if merged_groups:
                prev_blocks, prev_reason = merged_groups[-1]
                prev_chars = sum(block.char_count for block in prev_blocks)
                same_heading = prev_blocks[-1].heading_path == group_blocks[0].heading_path
                starts_with_heading = group_blocks[0].kind == "heading"
                can_merge = prev_chars + group_chars <= int(max_chars * 1.35)
                if group_chars < min_tail_chars and same_heading and not starts_with_heading and can_merge:
                    merged_groups[-1] = (prev_blocks + group_blocks, f"{prev_reason}+merged_small")
                    continue
            merged_groups.append((group_blocks, reason))
        chunk_groups = merged_groups

        chunks: list[DocumentChunk] = []
        for group_index, (group_blocks, reason) in enumerate(chunk_groups, start=1):
            start = group_blocks[0].start_line
            end = group_blocks[-1].end_line
            heading_path = group_blocks[0].heading_path
            text = "\n".join(f"{line_no}: {line}" for block in group_blocks for line_no, line in block.lines)
            before_start = max(1, start - context_before_lines)
            before_lines = [
                (line_no, lines[line_no - 1])
                for line_no in range(before_start, start)
                if 1 <= line_no <= len(lines)
            ]
            after_end = min(len(lines), end + context_after_lines)
            after_lines = [
                (line_no, lines[line_no - 1])
                for line_no in range(end + 1, after_end + 1)
                if 1 <= line_no <= len(lines)
            ]
            chunk_id = f"chunk_{group_index:03d}_{start}_{end}"
            chunks.append(
                DocumentChunk(
                    id=chunk_id,
                    start_line=start,
                    end_line=end,
                    heading_path=heading_path,
                    text=text,
                    context_before_text="\n".join(f"{line_no}: {line}" for line_no, line in before_lines),
                    context_after_text="\n".join(f"{line_no}: {line}" for line_no, line in after_lines),
                    block_count=len(group_blocks),
                    block_types=tuple(block.kind for block in group_blocks),
                    break_reason=reason,
                )
            )

        diagnostics = dict(state.get("diagnostics", {}))
        diagnostics["chunk_count"] = len(chunks)
        diagnostics["chunk_context_before_lines"] = context_before_lines
        diagnostics["chunk_context_after_lines"] = context_after_lines
        diagnostics["chunk_block_count"] = len(blocks)
        diagnostics["chunk_block_type_counts"] = {
            kind: sum(1 for block in blocks if block.kind == kind)
            for kind in sorted({block.kind for block in blocks})
        }
        diagnostics["chunk_spans"] = [
            {
                "id": chunk.id,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "block_count": chunk.block_count,
                "block_types": list(chunk.block_types),
                "break_reason": chunk.break_reason,
                "context_before_line_count": len(chunk.context_before_text.splitlines()),
                "context_after_line_count": len(chunk.context_after_text.splitlines()),
            }
            for chunk in chunks
        ]
        self._log(
            state,
            "Prepared "
            + str(len(chunks))
            + " chunks: "
            + ", ".join(f"{chunk.id}[L{chunk.start_line}-L{chunk.end_line}]" for chunk in chunks),
        )
        return {**state, "chunks": chunks, "diagnostics": diagnostics}

    def _extract_with_agents(self, state: ExtractionState) -> ExtractionState:
        # ExtractionAgent reads one chunk at a time and proposes local KG facts.
        client = self._client(state)
        chunk_payloads: list[dict[str, Any]] = []
        chunks = state["chunks"]
        for index, chunk in enumerate(chunks, start=1):
            cached = self._read_stage_cache("extract", chunk.id, state)
            if cached and state.get("resume", True):
                validated = AgentGraphPayload.model_validate(cached)
                validated = self._enforce_chunk_core_evidence(validated, chunk, state["raw_lines"])
                scoped = self._namespace_payload_ids(validated, f"extract_{chunk.id}")
                chunk_payloads.append(scoped.model_dump(mode="json"))
                self._log(
                    state,
                    f"ExtractionAgent chunk {index}/{len(chunks)} resume: "
                    f"{len(scoped.nodes)} nodes, {len(scoped.relationships)} relationships",
                )
                continue
            if chunk_payloads and state.get("request_delay", 0) > 0:
                time.sleep(float(state["request_delay"]))
            canonical_memory = self._extraction_canonical_memory(chunk_payloads)
            self._log(
                state,
                f"ExtractionAgent chunk {index}/{len(chunks)} start: {chunk.id} L{chunk.start_line}-L{chunk.end_line}",
            )
            payload = client.complete_json(
                EXTRACTION_SYSTEM_PROMPT,
                self._chunk_extraction_prompt(chunk, state["input_path"], canonical_memory),
                label=chunk.id,
            )
            validated = AgentGraphPayload.model_validate(payload)
            validated = self._enforce_chunk_core_evidence(validated, chunk, state["raw_lines"])
            scoped = self._namespace_payload_ids(validated, f"extract_{chunk.id}")
            self._write_stage_cache("extract", chunk.id, scoped.model_dump(mode="json"), state)
            chunk_payloads.append(scoped.model_dump(mode="json"))
            self._log(
                state,
                f"ExtractionAgent chunk {index}/{len(chunks)} done: "
                f"{len(scoped.nodes)} nodes, {len(scoped.relationships)} relationships",
            )
        diagnostics = dict(state.get("diagnostics", {}))
        diagnostics["chunk_payload_count"] = len(chunk_payloads)
        diagnostics["extraction_canonical_memory_size"] = len(self._extraction_canonical_memory(chunk_payloads))
        return {**state, "chunk_payloads": chunk_payloads, "diagnostics": diagnostics}

    def _resolve_nodes(self, state: ExtractionState) -> ExtractionState:
        self._log(state, "NodeResolver start: merging duplicate nodes across chunks")
        cache_label = "resolved_nodes_v5_compact_schema"
        cached = self._read_stage_cache("resolve", cache_label, state) if state.get("resume", True) else None
        if cached:
            payload = AgentGraphPayload.model_validate(cached)
            diagnostics = dict(state.get("diagnostics", {}))
            diagnostics["resolved_node_count"] = len(payload.nodes)
            diagnostics["resolved_relationship_count"] = len(payload.relationships)
            self._log(state, f"NodeResolver resume: {len(payload.nodes)} nodes, {len(payload.relationships)} relationships")
            return {**state, "resolved_payload": payload.model_dump(mode="json"), "diagnostics": diagnostics}

        node_by_key: dict[tuple[str, str], KGNode] = {}
        relationship_by_key: dict[tuple[str, str, str], KGRelationship] = {}
        dropped_node_count = 0
        dropped_relationship_count = 0

        for raw_payload in state.get("chunk_payloads", []):
            payload = AgentGraphPayload.model_validate(raw_payload)
            local_id_map: dict[str, str] = {}
            for node in payload.nodes:
                coerced = self._coerce_supported_node_type(node)
                if coerced is None:
                    dropped_node_count += 1
                    continue
                self._repair_evidence(coerced.evidence, state["input_path"])
                self._preserve_node_latex(coerced, state["raw_lines"])
                key = (coerced.type, self._node_resolution_key(coerced))
                existing = node_by_key.get(key)
                if existing is None:
                    existing = coerced.model_copy(deep=True)
                    existing.id = _node_id(existing.type, key[1])
                    existing.aliases = _unique_preserve_order([alias for alias in [*existing.aliases, existing.name] if alias])
                    node_by_key[key] = existing
                else:
                    self._merge_resolved_node(existing, coerced)
                local_id_map[node.id] = existing.id

            for rel in payload.relationships:
                mapped_type = self._map_relationship_type(rel.type)
                if not mapped_type:
                    dropped_relationship_count += 1
                    continue
                source_id = local_id_map.get(rel.source)
                target_id = local_id_map.get(rel.target)
                if not source_id or not target_id or source_id == target_id:
                    dropped_relationship_count += 1
                    continue
                merged_rel = rel.model_copy(deep=True)
                merged_rel.type = mapped_type
                merged_rel.source = source_id
                merged_rel.target = target_id
                self._repair_evidence(merged_rel.evidence, state["input_path"])
                rel_key = (merged_rel.type, merged_rel.source, merged_rel.target)
                existing_rel = relationship_by_key.get(rel_key)
                if existing_rel is None:
                    merged_rel.id = _rel_id(merged_rel.type, merged_rel.source, merged_rel.target)
                    relationship_by_key[rel_key] = merged_rel
                else:
                    existing_rel.evidence.extend(merged_rel.evidence)
                    existing_rel.properties.update(merged_rel.properties)
                    existing_rel.content.update(merged_rel.content)
                    existing_rel.quality["confidence"] = max(
                        _quality_confidence(existing_rel),
                        _quality_confidence(merged_rel),
                    )

        resolved = AgentGraphPayload(
            nodes=sorted(
                node_by_key.values(),
                key=lambda item: (
                    item.evidence[0].start_line if item.evidence else 10**9,
                    NODE_TYPES.index(item.type),
                    item.name,
                ),
            ),
            relationships=sorted(
                relationship_by_key.values(),
                key=lambda item: (RELATIONSHIP_TYPES.index(item.type), item.source, item.target),
            ),
            notes=[
                "NodeResolver merged chunk-local candidates into a global node pool.",
                f"Dropped unsupported nodes: {dropped_node_count}.",
                f"Dropped unsupported relationships: {dropped_relationship_count}.",
            ],
        )
        self._write_stage_cache("resolve", cache_label, resolved.model_dump(mode="json"), state)
        diagnostics = dict(state.get("diagnostics", {}))
        diagnostics["resolved_node_count"] = len(resolved.nodes)
        diagnostics["resolved_relationship_count"] = len(resolved.relationships)
        diagnostics["resolver_dropped_node_count"] = dropped_node_count
        diagnostics["resolver_dropped_relationship_count"] = dropped_relationship_count
        self._log(state, f"NodeResolver done: {len(resolved.nodes)} nodes, {len(resolved.relationships)} relationships")
        return {**state, "resolved_payload": resolved.model_dump(mode="json"), "diagnostics": diagnostics}

    def _coerce_supported_node_type(self, node: KGNode) -> KGNode | None:
        if node.type not in NODE_TYPES:
            return None
        return node.model_copy(deep=True)

    def _node_resolution_key(self, node: KGNode) -> str:
        if node.type == "Formula":
            latex = str(node.content.get("latex") or node.latex or "").strip()
            if latex:
                return latex
        if node.type == "Figure":
            label = str(node.content.get("label") or "").strip()
            url = str(node.content.get("url") or "").strip()
            if label:
                return label
            if url:
                return url
        return self._canonical_name_key(node.name)

    def _merge_resolved_node(self, existing: KGNode, incoming: KGNode) -> None:
        if incoming.name != existing.name:
            existing.aliases.append(incoming.name)
        existing.aliases.extend(incoming.aliases)
        existing.aliases = _unique_preserve_order([alias for alias in existing.aliases if alias and alias != existing.name])
        existing.evidence.extend(incoming.evidence)
        existing.properties.update(incoming.properties)
        existing.quality["confidence"] = max(_quality_confidence(existing), _quality_confidence(incoming))
        if len(incoming.description) > len(existing.description):
            existing.description = incoming.description
        if len(incoming.name) > len(existing.name) and self._canonical_name_key(incoming.name) == self._canonical_name_key(existing.name):
            existing.aliases = _unique_preserve_order([existing.name, *existing.aliases])
            existing.name = incoming.name
        if not existing.latex and incoming.latex:
            existing.latex = incoming.latex
        for key, value in incoming.content.items():
            if value not in (None, "", [], {}):
                existing.content.setdefault(key, value)

    def _map_relationship_type(self, rel_type: str) -> str | None:
        return rel_type if rel_type in RELATIONSHIP_TYPES else None

    def _enforce_chunk_core_evidence(
        self,
        payload: AgentGraphPayload,
        chunk: DocumentChunk,
        raw_lines: list[str],
    ) -> AgentGraphPayload:
        removed_node_ids: set[str] = set()
        kept_nodes: list[KGNode] = []
        trimmed_evidence_count = 0

        for node in payload.nodes:
            original_count = len(node.evidence)
            node.evidence = self._core_only_evidence(node.evidence, chunk, raw_lines)
            trimmed_evidence_count += max(0, original_count - len(node.evidence))
            if node.evidence:
                kept_nodes.append(node)
            else:
                removed_node_ids.add(node.id)

        kept_rels: list[KGRelationship] = []
        for rel in payload.relationships:
            if rel.source in removed_node_ids or rel.target in removed_node_ids:
                continue
            original_count = len(rel.evidence)
            rel.evidence = self._core_only_evidence(rel.evidence, chunk, raw_lines)
            trimmed_evidence_count += max(0, original_count - len(rel.evidence))
            if rel.evidence:
                kept_rels.append(rel)

        notes = list(payload.notes)
        removed_nodes = len(payload.nodes) - len(kept_nodes)
        removed_rels = len(payload.relationships) - len(kept_rels)
        if removed_nodes or removed_rels or trimmed_evidence_count:
            notes.append(
                "Local overlap guard removed context-only evidence/items: "
                f"{removed_nodes} nodes, {removed_rels} relationships, {trimmed_evidence_count} evidence spans."
            )
        return AgentGraphPayload(nodes=kept_nodes, relationships=kept_rels, notes=notes)

    def _core_only_evidence(
        self,
        evidence_items: list[Evidence],
        chunk: DocumentChunk,
        raw_lines: list[str],
    ) -> list[Evidence]:
        core_items: list[Evidence] = []
        for ev in evidence_items:
            start = max(ev.start_line, chunk.start_line)
            end = min(ev.end_line, chunk.end_line)
            if start > end:
                continue
            ev.start_line = start
            ev.end_line = end
            ev.chunk_id = chunk.id
            ev.text = "\n".join(raw_lines[line_no - 1] for line_no in range(start, end + 1))
            core_items.append(ev)
        return core_items

    def _infer_cross_chunk_relationships(self, state: ExtractionState) -> ExtractionState:
        # RelationAgent adds relationships that require global context after node resolution.
        client = self._relation_client(state)
        self._log(state, "RelationAgent start: inferring cross-chunk relationships")
        relation_cache_label = "cross_chunk_v5_resolved_graph"
        cached = self._read_stage_cache("relation", relation_cache_label, state)
        if cached and state.get("resume", True):
            validated = self._namespace_payload_ids(
                AgentGraphPayload.model_validate(cached),
                "relation_cross_chunk",
            )
            diagnostics = dict(state.get("diagnostics", {}))
            diagnostics["cross_chunk_relationship_nodes"] = len(validated.nodes)
            diagnostics["cross_chunk_relationship_count"] = len(validated.relationships)
            self._log(
                state,
                f"RelationAgent resume: {len(validated.nodes)} nodes, {len(validated.relationships)} relationships",
            )
            return {
                **state,
                "relation_payload": validated.model_dump(mode="json"),
                "diagnostics": diagnostics,
            }
        if state.get("request_delay", 0) > 0:
            time.sleep(float(state["request_delay"]))
        compact = self._compact_payloads([state["resolved_payload"]])
        relation_request = json.dumps(
            {
                "source_path": state["input_path"],
                "allowed_relationship_types": RELATIONSHIP_TYPES,
                "candidate_graph": compact,
            },
            ensure_ascii=False,
        )
        payload = self._complete_json_with_heartbeat(
            state,
            client,
            RELATION_SYSTEM_PROMPT,
            relation_request,
            label="cross_chunk_relationships",
            stage_name="RelationAgent",
            request_summary=(
                f"{len(compact.get('nodes', []))} nodes, {len(compact.get('relationships', []))} relationships, "
                f"payload_chars={len(relation_request)}, model={state.get('relation_model', state['model'])}"
            ),
        )
        validated = self._namespace_payload_ids(
            AgentGraphPayload.model_validate(payload),
            "relation_cross_chunk",
        )
        self._write_stage_cache("relation", relation_cache_label, validated.model_dump(mode="json"), state)
        diagnostics = dict(state.get("diagnostics", {}))
        diagnostics["cross_chunk_relationship_nodes"] = len(validated.nodes)
        diagnostics["cross_chunk_relationship_count"] = len(validated.relationships)
        self._log(
            state,
            f"RelationAgent done: {len(validated.nodes)} nodes, {len(validated.relationships)} relationships",
        )
        return {
            **state,
            "relation_payload": validated.model_dump(mode="json"),
            "diagnostics": diagnostics,
        }

    def _normalize_with_agent(self, state: ExtractionState) -> ExtractionState:
        # NormalizerAgent cleans the resolved candidate graph in batches, then merges them programmatically.
        client = self._normalizer_client(state)
        self._log(state, "NormalizerAgent start: cleaning resolved candidate graph")
        normalized_cache_label = "normalized_v5_resolved_graph"
        base_payload = AgentGraphPayload.model_validate(state["resolved_payload"])
        relation_payload = AgentGraphPayload.model_validate(state.get("relation_payload", {}))
        merged_source = self._merge_agent_payloads([base_payload, relation_payload], note="Resolved graph plus cross-chunk relations.")
        cached = self._read_stage_cache("normalize", normalized_cache_label, state)
        if cached and state.get("resume", True):
            validated = AgentGraphPayload.model_validate(cached)
            self._assert_normalizer_relationship_retention(merged_source, validated)
            diagnostics = dict(state.get("diagnostics", {}))
            diagnostics["normalized_node_count_from_agent"] = len(validated.nodes)
            diagnostics["normalized_relationship_count_from_agent"] = len(validated.relationships)
            diagnostics["normalized_duplicate_temp_id_count"] = self._duplicate_node_id_count(validated.nodes)
            self._log(
                state,
                f"NormalizerAgent resume: {len(validated.nodes)} nodes, {len(validated.relationships)} relationships",
            )
            return {
                **state,
                "normalized_payload": validated.model_dump(mode="json"),
                "diagnostics": diagnostics,
            }

        batches = self._build_normalizer_batches(merged_source, state.get("chunks", []))
        self._log(state, f"NormalizerAgent batching resolved graph into {len(batches)} batches")
        normalized_batches: list[AgentGraphPayload] = []
        batch_summaries: list[dict[str, Any]] = []

        for index, batch_payload in enumerate(batches, start=1):
            batch_label = f"normalized_v5_batch_{index:03d}_of_{len(batches):03d}"
            cached_batch = self._read_stage_cache("normalize", batch_label, state) if state.get("resume", True) else None
            if cached_batch:
                validated_batch = AgentGraphPayload.model_validate(cached_batch)
                self._assert_normalizer_relationship_retention(batch_payload, validated_batch)
                self._log(
                    state,
                    f"NormalizerAgent batch {index}/{len(batches)} resume: {len(validated_batch.nodes)} nodes, {len(validated_batch.relationships)} relationships",
                )
            else:
                if index > 1 and state.get("request_delay", 0) > 0:
                    time.sleep(float(state["request_delay"]))
                normalize_request = json.dumps(
                    {
                        "source_path": state["input_path"],
                        "allowed_node_types": NODE_TYPES,
                        "allowed_relationship_types": RELATIONSHIP_TYPES,
                        "candidate_graph": batch_payload.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                )
                self._log(
                    state,
                    f"NormalizerAgent batch {index}/{len(batches)} start: {len(batch_payload.nodes)} nodes, {len(batch_payload.relationships)} relationships",
                )
                response = self._complete_json_with_heartbeat(
                    state,
                    client,
                    NORMALIZER_SYSTEM_PROMPT,
                    normalize_request,
                    label=batch_label,
                    stage_name=f"NormalizerAgent batch {index}/{len(batches)}",
                    request_summary=(
                        f"{len(batch_payload.nodes)} nodes, {len(batch_payload.relationships)} relationships, "
                        f"payload_chars={len(normalize_request)}, model={state.get('normalizer_model', state['model'])}"
                    ),
                )
                validated_batch = AgentGraphPayload.model_validate(response)
                self._assert_normalizer_relationship_retention(batch_payload, validated_batch)
                self._write_stage_cache("normalize", batch_label, validated_batch.model_dump(mode="json"), state)
                self._log(
                    state,
                    f"NormalizerAgent batch {index}/{len(batches)} done: {len(validated_batch.nodes)} nodes, {len(validated_batch.relationships)} relationships",
                )
            normalized_batches.append(validated_batch)
            batch_summaries.append(
                {
                    "batch": index,
                    "input_nodes": len(batch_payload.nodes),
                    "input_relationships": len(batch_payload.relationships),
                    "output_nodes": len(validated_batch.nodes),
                    "output_relationships": len(validated_batch.relationships),
                    "candidate_graph_chars": self._normalizer_payload_chars(batch_payload),
                }
            )

        validated = self._merge_normalized_batch_payloads(normalized_batches, state)
        self._assert_normalizer_relationship_retention(merged_source, validated)
        self._write_stage_cache("normalize", normalized_cache_label, validated.model_dump(mode="json"), state)
        diagnostics = dict(state.get("diagnostics", {}))
        diagnostics["normalizer_batch_count"] = len(batches)
        diagnostics["normalizer_batch_summaries"] = batch_summaries[:20]
        diagnostics["normalized_node_count_from_agent"] = len(validated.nodes)
        diagnostics["normalized_relationship_count_from_agent"] = len(validated.relationships)
        diagnostics["normalized_duplicate_temp_id_count"] = self._duplicate_node_id_count(validated.nodes)
        self._log(
            state,
            f"NormalizerAgent done: {len(validated.nodes)} nodes, {len(validated.relationships)} relationships",
        )
        return {
            **state,
            "normalized_payload": validated.model_dump(mode="json"),
            "diagnostics": diagnostics,
        }

    def _assert_normalizer_relationship_retention(
        self,
        source_payload: AgentGraphPayload,
        normalized_payload: AgentGraphPayload,
    ) -> None:
        if source_payload.relationships and not normalized_payload.relationships:
            raise RuntimeError(
                "NormalizerAgent removed all relationships from a non-empty candidate graph; "
                f"source had {len(source_payload.relationships)} relationships."
            )

    def _build_normalizer_batches(
        self,
        payload: AgentGraphPayload,
        chunks: list[DocumentChunk],
        *,
        max_nodes: int = 48,
        max_relationships: int = 36,
        max_chars: int = 22000,
    ) -> list[AgentGraphPayload]:
        if not payload.nodes:
            return [payload]

        node_by_id = {node.id: node for node in payload.nodes}
        chunk_order = {chunk.id: index for index, chunk in enumerate(chunks)}
        anchor_chunk_by_node: dict[str, str] = {}
        grouped_node_ids: dict[str, list[str]] = {}
        grouped_relationships: dict[str, list[KGRelationship]] = {}

        for node in payload.nodes:
            anchor_chunk = self._node_anchor_chunk_id(node, chunks)
            anchor_chunk_by_node[node.id] = anchor_chunk
            grouped_node_ids.setdefault(anchor_chunk, []).append(node.id)

        for rel in payload.relationships:
            anchor_candidates = [
                anchor_chunk_by_node.get(rel.source),
                anchor_chunk_by_node.get(rel.target),
            ]
            anchor_chunk = min(
                [item for item in anchor_candidates if item is not None],
                key=lambda item: chunk_order.get(item, 10**9),
                default="__unanchored__",
            )
            grouped_relationships.setdefault(anchor_chunk, []).append(rel)

        ordered_group_ids = sorted(
            set(grouped_node_ids) | set(grouped_relationships),
            key=lambda item: self._normalizer_group_sort_key(item, grouped_node_ids, grouped_relationships, node_by_id, chunk_order),
        )

        batches: list[AgentGraphPayload] = []
        current_node_ids: set[str] = set()
        current_relationships: list[KGRelationship] = []

        def flush_current() -> None:
            nonlocal current_node_ids, current_relationships
            if not current_node_ids and not current_relationships:
                return
            batch_payload = self._materialize_normalizer_batch_payload(current_node_ids, current_relationships, node_by_id)
            if self._normalizer_payload_exceeds(batch_payload, max_nodes=max_nodes, max_relationships=max_relationships, max_chars=max_chars):
                batches.extend(
                    self._split_oversized_normalizer_batch(
                        batch_payload,
                        max_nodes=max_nodes,
                        max_relationships=max_relationships,
                        max_chars=max_chars,
                    )
                )
            else:
                batches.append(batch_payload)
            current_node_ids = set()
            current_relationships = []

        for group_id in ordered_group_ids:
            candidate_node_ids = set(current_node_ids)
            candidate_node_ids.update(grouped_node_ids.get(group_id, []))
            candidate_relationships = [*current_relationships, *grouped_relationships.get(group_id, [])]
            candidate_payload = self._materialize_normalizer_batch_payload(candidate_node_ids, candidate_relationships, node_by_id)
            if current_node_ids and self._normalizer_payload_exceeds(
                candidate_payload,
                max_nodes=max_nodes,
                max_relationships=max_relationships,
                max_chars=max_chars,
            ):
                flush_current()
                current_node_ids.update(grouped_node_ids.get(group_id, []))
                current_relationships.extend(grouped_relationships.get(group_id, []))
            else:
                current_node_ids = candidate_node_ids
                current_relationships = candidate_relationships

        flush_current()
        return batches or [payload]

    def _node_anchor_chunk_id(self, node: KGNode, chunks: list[DocumentChunk]) -> str:
        if node.evidence:
            chunk_id = node.evidence[0].chunk_id
            if chunk_id:
                return chunk_id
            return self._chunk_id_for_line(chunks, node.evidence[0].start_line) or "__unanchored__"
        return "__unanchored__"

    def _normalizer_group_sort_key(
        self,
        group_id: str,
        grouped_node_ids: dict[str, list[str]],
        grouped_relationships: dict[str, list[KGRelationship]],
        node_by_id: dict[str, KGNode],
        chunk_order: dict[str, int],
    ) -> tuple[int, int, str]:
        lines: list[int] = []
        for node_id in grouped_node_ids.get(group_id, []):
            node = node_by_id.get(node_id)
            if node and node.evidence:
                lines.append(node.evidence[0].start_line)
        for rel in grouped_relationships.get(group_id, []):
            for node_id in [rel.source, rel.target]:
                node = node_by_id.get(node_id)
                if node and node.evidence:
                    lines.append(node.evidence[0].start_line)
        return (chunk_order.get(group_id, 10**9), min(lines) if lines else 10**9, group_id)

    def _materialize_normalizer_batch_payload(
        self,
        node_ids: set[str],
        relationships: list[KGRelationship],
        node_by_id: dict[str, KGNode],
    ) -> AgentGraphPayload:
        expanded_node_ids = set(node_ids)
        relationship_by_key: dict[tuple[str, str, str], KGRelationship] = {}
        for rel in relationships:
            relationship_by_key[(rel.type, rel.source, rel.target)] = rel
            expanded_node_ids.add(rel.source)
            expanded_node_ids.add(rel.target)
        nodes = [node_by_id[node_id] for node_id in expanded_node_ids if node_id in node_by_id]
        nodes.sort(
            key=lambda item: (
                item.evidence[0].start_line if item.evidence else 10**9,
                NODE_TYPES.index(item.type),
                item.name,
            )
        )
        rels = sorted(
            relationship_by_key.values(),
            key=lambda item: (
                RELATIONSHIP_TYPES.index(item.type),
                item.source,
                item.target,
            ),
        )
        return AgentGraphPayload(
            nodes=nodes,
            relationships=rels,
            notes=["Normalizer batch candidate graph."],
        )

    def _normalizer_payload_chars(self, payload: AgentGraphPayload) -> int:
        return len(json.dumps(payload.model_dump(mode="json"), ensure_ascii=False))

    def _normalizer_payload_exceeds(
        self,
        payload: AgentGraphPayload,
        *,
        max_nodes: int,
        max_relationships: int,
        max_chars: int,
    ) -> bool:
        return (
            len(payload.nodes) > max_nodes
            or len(payload.relationships) > max_relationships
            or self._normalizer_payload_chars(payload) > max_chars
        )

    def _split_oversized_normalizer_batch(
        self,
        payload: AgentGraphPayload,
        *,
        max_nodes: int,
        max_relationships: int,
        max_chars: int,
    ) -> list[AgentGraphPayload]:
        node_by_id = {node.id: node for node in payload.nodes}
        ordered_nodes = sorted(
            payload.nodes,
            key=lambda item: (
                item.evidence[0].start_line if item.evidence else 10**9,
                NODE_TYPES.index(item.type),
                item.name,
            ),
        )
        batches: list[AgentGraphPayload] = []
        current_core_ids: list[str] = []

        def build_from_core(core_ids: list[str]) -> AgentGraphPayload:
            core_set = set(core_ids)
            rels = [rel for rel in payload.relationships if rel.source in core_set or rel.target in core_set]
            return self._materialize_normalizer_batch_payload(core_set, rels, node_by_id)

        for node in ordered_nodes:
            candidate_core_ids = [*current_core_ids, node.id]
            candidate_payload = build_from_core(candidate_core_ids)
            if current_core_ids and self._normalizer_payload_exceeds(
                candidate_payload,
                max_nodes=max_nodes,
                max_relationships=max_relationships,
                max_chars=max_chars,
            ):
                batches.append(build_from_core(current_core_ids))
                current_core_ids = [node.id]
            else:
                current_core_ids = candidate_core_ids

        if current_core_ids:
            batches.append(build_from_core(current_core_ids))
        return batches or [payload]

    def _merge_normalized_batch_payloads(
        self,
        payloads: list[AgentGraphPayload],
        state: ExtractionState,
    ) -> AgentGraphPayload:
        node_by_key: dict[tuple[str, str], KGNode] = {}
        relationship_by_key: dict[tuple[str, str, str], KGRelationship] = {}

        for payload in payloads:
            local_id_map: dict[str, str] = {}
            for node in payload.nodes:
                coerced = self._coerce_supported_node_type(node)
                if coerced is None:
                    continue
                self._repair_evidence(coerced.evidence, state["input_path"])
                self._preserve_node_latex(coerced, state["raw_lines"])
                key = (coerced.type, self._node_resolution_key(coerced))
                existing = node_by_key.get(key)
                if existing is None:
                    existing = coerced.model_copy(deep=True)
                    existing.id = _node_id(existing.type, key[1])
                    existing.aliases = _unique_preserve_order([alias for alias in [*existing.aliases, existing.name] if alias])
                    node_by_key[key] = existing
                else:
                    self._merge_resolved_node(existing, coerced)
                local_id_map[node.id] = existing.id

            for rel in payload.relationships:
                mapped_type = self._map_relationship_type(rel.type)
                if not mapped_type:
                    continue
                source_id = local_id_map.get(rel.source)
                target_id = local_id_map.get(rel.target)
                if not source_id or not target_id or source_id == target_id:
                    continue
                merged_rel = rel.model_copy(deep=True)
                merged_rel.type = mapped_type
                merged_rel.source = source_id
                merged_rel.target = target_id
                self._repair_evidence(merged_rel.evidence, state["input_path"])
                rel_key = (merged_rel.type, merged_rel.source, merged_rel.target)
                existing_rel = relationship_by_key.get(rel_key)
                if existing_rel is None:
                    merged_rel.id = _rel_id(merged_rel.type, merged_rel.source, merged_rel.target)
                    relationship_by_key[rel_key] = merged_rel
                else:
                    existing_rel.evidence.extend(merged_rel.evidence)
                    existing_rel.properties.update(merged_rel.properties)
                    existing_rel.content.update(merged_rel.content)
                    existing_rel.quality["confidence"] = max(
                        _quality_confidence(existing_rel),
                        _quality_confidence(merged_rel),
                    )

        return AgentGraphPayload(
            nodes=sorted(
                node_by_key.values(),
                key=lambda item: (
                    item.evidence[0].start_line if item.evidence else 10**9,
                    NODE_TYPES.index(item.type),
                    item.name,
                ),
            ),
            relationships=sorted(
                relationship_by_key.values(),
                key=lambda item: (RELATIONSHIP_TYPES.index(item.type), item.source, item.target),
            ),
            notes=["Merged normalized batches into one candidate graph."],
        )

    def _merge_agent_payloads(self, payloads: list[AgentGraphPayload], note: str) -> AgentGraphPayload:
        nodes: list[KGNode] = []
        relationships: list[KGRelationship] = []
        notes: list[str] = [note]
        for payload in payloads:
            nodes.extend(payload.nodes)
            relationships.extend(payload.relationships)
            notes.extend(payload.notes)
        return AgentGraphPayload(nodes=nodes, relationships=relationships, notes=notes)

    def _namespace_payload_ids(self, payload: AgentGraphPayload, namespace: str) -> AgentGraphPayload:
        scoped = payload.model_copy(deep=True)
        id_map: dict[str, str] = {}
        for index, node in enumerate(scoped.nodes, start=1):
            old_id = (node.id or "").strip()
            if not old_id:
                old_id = f"node_{index:03d}"
                node.id = old_id
            if self._is_scoped_temp_id(old_id):
                continue
            new_id = f"{namespace}::{old_id}"
            id_map[old_id] = new_id
            node.id = new_id
        for rel in scoped.relationships:
            rel.source = id_map.get(rel.source, rel.source)
            rel.target = id_map.get(rel.target, rel.target)
        if id_map:
            scoped.notes.append(f"Local temporary ids scoped with namespace `{namespace}`.")
        return scoped

    def _is_scoped_temp_id(self, node_id: str) -> bool:
        return "::" in node_id

    def _duplicate_node_id_count(self, nodes: list[KGNode]) -> int:
        counts: dict[str, int] = {}
        for node in nodes:
            if not node.id:
                continue
            counts[node.id] = counts.get(node.id, 0) + 1
        return sum(1 for count in counts.values() if count > 1)

    def _section_tree_from_nodes(self, nodes: list[KGNode]) -> list[dict[str, Any]]:
        # The published v5 graph no longer uses chapter/section hierarchy nodes.
        return []

    def _is_hierarchy_node(self, node: KGNode) -> bool:
        return False

    def _hierarchy_level(self, node: KGNode) -> int:
        return 0

    def _section_level(self, name: str) -> int:
        return 0

    def _verify_with_agent(self, state: ExtractionState) -> ExtractionState:
        # VerificationAgent reviews relationships in batches to avoid large JSON responses.
        client = self._verification_client(state)
        self._log(state, f"VerificationAgent start: semantic relation validation with model={state.get('verification_model')}")
        verified_cache_label = "verified_v5_semantic_id_namespace"
        cached = self._read_stage_cache("verify", verified_cache_label, state)
        if cached and state.get("resume", True):
            validated = AgentGraphPayload.model_validate(cached)
            diagnostics = dict(state.get("diagnostics", {}))
            diagnostics["verified_node_count"] = len(validated.nodes)
            diagnostics["verified_relationship_count"] = len(validated.relationships)
            self._log(
                state,
                f"VerificationAgent resume: {len(validated.nodes)} nodes, {len(validated.relationships)} relationships",
            )
            return {
                **state,
                "verified_payload": validated.model_dump(mode="json"),
                "diagnostics": diagnostics,
            }

        payload = AgentGraphPayload.model_validate(state["normalized_payload"])
        relationships = payload.relationships
        batch_size = max(1, int(state.get("verification_batch_size", 35)))
        node_summaries = [self._verification_node_context(node, compact=True) for node in payload.nodes]
        node_by_id = {node.id: node for node in payload.nodes}
        section_tree = self._section_tree_from_nodes(payload.nodes)

        verified_relationships: list[KGRelationship] = []
        notes: list[str] = []
        typed_batches: list[tuple[str, list[KGRelationship]]] = []
        for rel_type in RELATIONSHIP_TYPES:
            typed = [rel for rel in relationships if rel.type == rel_type]
            typed_batches.extend(
                (rel_type, typed[index : index + batch_size]) for index in range(0, len(typed), batch_size)
            )
        for index, (rel_type, batch) in enumerate(typed_batches, start=1):
            label = f"v5semantic_idns_{rel_type}_{index:03d}_of_{len(typed_batches):03d}_{len(relationships)}rels"
            cached_batch = self._read_stage_cache("verify", label, state) if state.get("resume", True) else None
            if cached_batch:
                batch_payload = AgentGraphPayload.model_validate(cached_batch)
                self._log(
                    state,
                    f"VerificationAgent batch {index}/{len(typed_batches)} resume: "
                    f"{rel_type}, "
                    f"{len(batch_payload.relationships)} relationships",
                )
            else:
                if index > 1 and state.get("request_delay", 0) > 0:
                    time.sleep(float(state["request_delay"]))
                self._log(state, f"VerificationAgent batch {index}/{len(typed_batches)} start: {rel_type}")
                verification_request = json.dumps(
                    {
                        "source_path": state["input_path"],
                        "verification_model": state.get("verification_model"),
                        "verification_reasoning_effort": state.get("verification_reasoning_effort"),
                        "allowed_node_types": NODE_TYPES,
                        "allowed_relationship_types": RELATIONSHIP_TYPES,
                        "output_contract": {
                            "nodes": [],
                            "relationships": OUTPUT_CONTRACT["relationships"],
                            "notes": OUTPUT_CONTRACT["notes"],
                        },
                        "candidate_nodes": node_summaries,
                        "section_tree": section_tree,
                        "relation_type_under_review": rel_type,
                        "relation_type_policy": self._relation_type_verification_policy(rel_type),
                        "relationship_batch": [
                            self._verification_relationship_context(rel, node_by_id) for rel in batch
                        ],
                    },
                    ensure_ascii=False,
                )
                response = self._complete_json_with_heartbeat(
                    state,
                    client,
                    VERIFICATION_SYSTEM_PROMPT,
                    verification_request,
                    label=f"verification_{label}",
                    stage_name=f"VerificationAgent batch {index}/{len(typed_batches)} {rel_type}",
                    request_summary=(
                        f"batch_relationships={len(batch)}, node_context={len(node_summaries)}, "
                        f"payload_chars={len(verification_request)}, model={state.get('verification_model')}"
                    ),
                )
                batch_payload = AgentGraphPayload.model_validate(response)
                self._write_stage_cache("verify", label, batch_payload.model_dump(mode="json"), state)
                self._log(
                    state,
                    f"VerificationAgent batch {index}/{len(typed_batches)} done: {rel_type}, "
                    f"{len(batch_payload.relationships)} relationships",
                )
            verified_relationships.extend(batch_payload.relationships)
            notes.extend(batch_payload.notes)

        verified = AgentGraphPayload(nodes=payload.nodes, relationships=verified_relationships, notes=notes)
        self._write_stage_cache("verify", verified_cache_label, verified.model_dump(mode="json"), state)
        diagnostics = dict(state.get("diagnostics", {}))
        diagnostics["verification_model"] = state.get("verification_model")
        diagnostics["verification_reasoning_effort"] = state.get("verification_reasoning_effort")
        diagnostics["verification_batch_count"] = len(typed_batches)
        diagnostics["verified_node_count"] = len(verified.nodes)
        diagnostics["verified_relationship_count"] = len(verified.relationships)
        diagnostics["verification_removed_relationship_count"] = max(0, len(relationships) - len(verified.relationships))
        self._log(
            state,
            f"VerificationAgent done: {len(verified.nodes)} nodes, {len(verified.relationships)} relationships",
        )
        return {
            **state,
            "verified_payload": verified.model_dump(mode="json"),
            "diagnostics": diagnostics,
        }

    def _verification_node_context(self, node: KGNode, compact: bool = False) -> dict[str, Any]:
        description_limit = 240 if compact else 900
        evidence_limit = 1 if compact else 2
        content = dict(node.content)
        if compact:
            content = {
                key: value
                for key, value in content.items()
                if key in {"latex", "approach", "steps", "label", "url"}
            }
        return {
            "id": node.id,
            "type": node.type,
            "name": node.name,
            "aliases": node.aliases[:5],
            "description": node.description[:description_limit],
            "latex": node.latex,
            "content": content,
            "evidence": [ev.model_dump(mode="json") for ev in node.evidence[:evidence_limit]],
        }

    def _verification_relationship_context(
        self,
        rel: KGRelationship,
        node_by_id: dict[str, KGNode],
    ) -> dict[str, Any]:
        source = node_by_id.get(rel.source)
        target = node_by_id.get(rel.target)
        return {
            "relationship": rel.model_dump(mode="json"),
            "source_node": self._verification_node_context(source) if source else None,
            "target_node": self._verification_node_context(target) if target else None,
            "decision_required": "Return this relationship only if source_node, target_node, and relationship evidence jointly support its type and direction.",
        }

    def _relation_type_verification_policy(self, rel_type: str) -> str:
        policies = {
            "has_formula": "Keep only when the formula directly represents, defines, or is explicitly attached to the source concept, property, or theorem.",
            "has_property": "Keep only when the target property is a reusable property or conclusion of the source concept or theorem.",
            "example_of": "Keep only when the example directly illustrates the target concept, property, or theorem.",
            "illustrated_by": "Keep only when the figure directly illustrates the source node, not merely appears nearby.",
            "prerequisite_of": "Keep only when the source knowledge unit is genuinely prior knowledge for understanding the target knowledge unit.",
            "similar_to": "Keep only when the two nodes are genuinely similar, analogous, or easily confusable at the knowledge level.",
            "proved_by": "Keep only when the source is a theorem and the target is its formal proof in the textbook.",
        }
        return policies.get(rel_type, "Keep only when evidence directly supports this relationship's type and direction.")

    def _finalize_graph(self, state: ExtractionState) -> ExtractionState:
        # Finalization keeps only compact published nodes and evidence-backed relationships.
        self._log(state, "Finalizing compact graph")
        payload = AgentGraphPayload.model_validate(state["verified_payload"] if "verified_payload" in state else state["normalized_payload"])
        node_map: dict[str, KGNode] = {}
        invalid_reference_count = 0
        self_loop_count = 0
        relationship_guard_removed_count = 0

        for node in payload.nodes:
            coerced = self._coerce_supported_node_type(node)
            if coerced is None:
                continue
            self._repair_evidence(coerced.evidence, state["input_path"])
            self._preserve_node_latex(coerced, state["raw_lines"])
            final_id = node.id or _node_id(coerced.type, self._node_resolution_key(coerced))
            coerced.id = final_id
            existing = node_map.get(final_id)
            if existing is None:
                node_map[final_id] = coerced
            else:
                self._merge_resolved_node(existing, coerced)

        relationships: dict[str, KGRelationship] = {}
        for rel in payload.relationships:
            if rel.source not in node_map or rel.target not in node_map:
                invalid_reference_count += 1
                continue
            if rel.source == rel.target:
                self_loop_count += 1
                continue
            if not self._relationship_allowed(rel, node_map):
                relationship_guard_removed_count += 1
                continue
            self._repair_evidence(rel.evidence, state["input_path"])
            rel.id = _rel_id(rel.type, rel.source, rel.target)
            existing = relationships.get(rel.id)
            if existing is None:
                relationships[rel.id] = rel
            else:
                existing.evidence.extend(rel.evidence)
                existing.content.update(rel.content)
                existing.properties.update(rel.properties)
                existing.quality["confidence"] = max(_quality_confidence(existing), _quality_confidence(rel))

        nodes = sorted(
            node_map.values(),
            key=lambda item: (
                item.evidence[0].start_line if item.evidence else 10**9,
                NODE_TYPES.index(item.type),
                item.name,
            ),
        )
        rels = sorted(relationships.values(), key=lambda item: (RELATIONSHIP_TYPES.index(item.type), item.source, item.target))
        final_nodes = self._build_final_nodes(nodes, rels)
        final_rels = self._build_final_relationships(rels)
        diagnostics = dict(state.get("diagnostics", {}))
        diagnostics["invalid_reference_relationship_count"] = invalid_reference_count
        diagnostics["self_loop_relationship_count"] = self_loop_count
        diagnostics["relationship_guard_removed_count"] = relationship_guard_removed_count
        diagnostics["node_count"] = len(final_nodes)
        diagnostics["relationship_count"] = len(final_rels)
        diagnostics["node_count_by_type"] = self._count_by_type(final_nodes)
        diagnostics["relationship_count_by_type"] = self._count_by_type(final_rels)

        result = ExtractionResult(
            source_path=state["input_path"],
            title=self._title_from_lines(state["raw_lines"]),
            model=state["model"],
            nodes=final_nodes,
            relationships=final_rels,
            diagnostics=diagnostics,
        )
        self._log(state, f"Finalized graph: {len(final_nodes)} nodes, {len(final_rels)} relationships")
        return {**state, "result": result, "diagnostics": diagnostics}

    def _save_result(self, state: ExtractionState) -> ExtractionState:
        result = state["result"]
        if state.get("save_output", True):
            output_path = Path(state["output_path"])
            self._log(state, f"Saving result: {output_path}")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            result.diagnostics["output_path"] = str(output_path)
            content = json.dumps(self._result_payload(result), ensure_ascii=False, indent=2)
            output_path.write_text(content, encoding="utf-8")
            self._log(state, "Saved result")
        return state

    def _result_payload(self, result: ExtractionResult) -> dict[str, Any]:
        return result.model_dump(mode="json")

    def _normalize_heading_node_type(self, node: KGNode) -> None:
        return None

    def _heading_node_type(self, name: str) -> str | None:
        return None

    def _heading_scaffold_nodes(
        self,
        raw_lines: list[str],
        source_path: str,
        chunks: list[DocumentChunk],
    ) -> list[KGNode]:
        return []

    def _chunk_id_for_line(self, chunks: list[DocumentChunk], line_no: int) -> str | None:
        for chunk in chunks:
            if chunk.start_line <= line_no <= chunk.end_line:
                return chunk.id
        return None

    def _relationship_allowed(self, rel: KGRelationship, node_map: dict[str, KGNode]) -> bool:
        source_type = node_map[rel.source].type
        target_type = node_map[rel.target].type
        confidence = _quality_confidence(rel)
        if confidence < 0.55:
            return False
        if rel.type == "has_formula":
            return target_type == "Formula" and source_type in {"Concept", "Property", "Theorem"}
        if rel.type == "has_property":
            return target_type == "Property" and source_type in {"Concept", "Theorem"}
        if rel.type == "example_of":
            return source_type == "Example" and target_type in {"Concept", "Property", "Theorem"}
        if rel.type == "illustrated_by":
            return target_type == "Figure" and source_type in {"Concept", "Property", "Theorem", "Example"}
        if rel.type == "prerequisite_of":
            return source_type in {"Concept", "Property", "Theorem"} and target_type in {"Concept", "Property", "Theorem"}
        if rel.type == "similar_to":
            return source_type == target_type and source_type in {"Concept", "Formula", "Property", "Theorem"}
        if rel.type == "proved_by":
            return source_type == "Theorem" and target_type == "Proof"
        return False

    def _add_section_membership_relationships(
        self,
        node_map: dict[str, KGNode],
        relationships: dict[str, KGRelationship],
    ) -> int:
        return 0

    def _nearest_parent_section(self, sections: list[KGNode], child: KGNode) -> KGNode | None:
        child_line = child.evidence[0].start_line
        child_level = self._hierarchy_level(child)
        candidates = [
            section
            for section in sections
            if section.id != child.id
            and section.evidence[0].start_line < child_line
            and self._hierarchy_level(section) < child_level
        ]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda section: (
                section.evidence[0].start_line,
                self._hierarchy_level(section),
            ),
        )

    def _nearest_section_for_line(self, sections: list[KGNode], line: int) -> KGNode | None:
        candidates = [section for section in sections if section.evidence[0].start_line <= line]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda section: (
                section.evidence[0].start_line,
                self._hierarchy_level(section),
            ),
        )

    def _build_final_nodes(self, nodes: list[KGNode], rels: list[KGRelationship]) -> list[FinalNode]:
        final_nodes: list[FinalNode] = []
        for node in nodes:
            final_nodes.append(
                FinalNode(
                    id=node.id,
                    type=node.type,
                    name=node.name,
                    aliases=[alias for alias in _unique_preserve_order(node.aliases) if alias != node.name],
                    description=node.description,
                    content=self._published_node_content(node),
                )
            )
        return final_nodes

    def _build_final_relationships(self, rels: list[KGRelationship]) -> list[FinalRelationship]:
        final_rels: list[FinalRelationship] = []
        for rel in rels:
            final_rels.append(
                FinalRelationship(
                    id=rel.id,
                    type=rel.type,
                    source=rel.source,
                    target=rel.target,
                    score=round(_quality_confidence(rel), 4),
                    evidence=rel.evidence,
                )
            )
        return final_rels

    def _published_node_content(self, node: KGNode) -> dict[str, Any]:
        content = dict(node.content)
        content.pop("text", None)
        content.pop("source_latex", None)
        content.pop("source_latex_source", None)
        content.pop("latex_agent", None)
        if node.type == "Formula":
            latex = str(content.get("latex") or node.latex or "").strip()
            return {"latex": latex} if latex else {}
        if node.type == "Figure":
            return {
                key: value
                for key, value in {
                    "label": content.get("label"),
                    "url": content.get("url"),
                }.items()
                if value not in (None, "", [], {})
            }
        if node.type in {"Example", "Proof"}:
            steps = content.get("steps")
            if isinstance(steps, str):
                steps = [steps]
            elif isinstance(steps, list):
                steps = [str(item).strip() for item in steps if str(item).strip()]
            else:
                steps = []
            published = {
                "approach": str(content.get("approach") or "").strip(),
                "steps": steps,
            }
            return {key: value for key, value in published.items() if value not in (None, "", [], {})}
        return {}

    def _section_value(self, node: KGNode, old_props: dict[str, Any]) -> str:
        section = old_props.get("section")
        if isinstance(section, list):
            return " > ".join(str(item) for item in section if item)
        if section:
            return str(section)
        if node.evidence:
            return node.evidence[0].chunk_id or ""
        return ""

    def _node_role(self, node: KGNode) -> str:
        return node.type.lower()

    def _node_tags(self, node: KGNode) -> list[str]:
        tags: list[str] = []
        for value in [node.name, *node.aliases]:
            for token in re.split(r"[、,，/（）()：:\s]+", value):
                token = token.strip()
                if 2 <= len(token) <= 12:
                    tags.append(token)
        return _unique_preserve_order(tags)[:8]

    def _extraction_canonical_memory(self, chunk_payloads: list[dict[str, Any]], limit: int = 45) -> list[dict[str, Any]]:
        priority = {
            "Concept": 0,
            "Property": 1,
            "Theorem": 2,
            "Formula": 3,
            "Example": 4,
            "Proof": 5,
            "Figure": 6,
        }
        memory_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        for raw_payload in chunk_payloads:
            payload = AgentGraphPayload.model_validate(raw_payload)
            for node in payload.nodes:
                key = (node.type, self._canonical_name_key(node.name))
                first_ev = node.evidence[0] if node.evidence else None
                item = memory_by_key.get(key)
                if item is None:
                    memory_by_key[key] = {
                        "type": node.type,
                        "preferred_name": node.name,
                        "aliases": node.aliases[:5],
                        "description_hint": node.description[:180],
                        "source_lines": [first_ev.start_line, first_ev.end_line] if first_ev else None,
                    }
                    continue
                aliases = [*item.get("aliases", []), *node.aliases]
                if node.name != item["preferred_name"]:
                    aliases.append(node.name)
                item["aliases"] = _unique_preserve_order([alias for alias in aliases if alias])[:8]
                if len(node.description) > len(item.get("description_hint", "")):
                    item["description_hint"] = node.description[:180]
        return sorted(
            memory_by_key.values(),
            key=lambda item: (priority.get(item["type"], 99), item["preferred_name"]),
        )[:limit]

    def _canonical_name_key(self, name: str) -> str:
        text = re.sub(r"\s+", "", name)
        text = re.sub(r"^定理\s*(\d+)[（(](.*?)[）)]$", r"定理\1\2", text)
        text = re.sub(r"^例\s*(\d+)[：:、.\s-]*(.*)$", r"例\1\2", text)
        return text.replace("的", "")

    def _chunk_extraction_prompt(
        self,
        chunk: DocumentChunk,
        source_path: str,
        canonical_memory: list[dict[str, Any]] | None = None,
    ) -> str:
        payload = {
            "task": "Extract candidate nodes and local candidate relationships from the given higher mathematics textbook chunk.",
            "source_path": source_path,
            "chunk_id": chunk.id,
            "line_span": [chunk.start_line, chunk.end_line],
            "heading_path": chunk.heading_path,
            "context_policy": {
                "instruction": (
                    "context_before_line_numbers and context_after_line_numbers are boundary context only. "
                    "Use them to disambiguate incomplete references, formulas, and transitions, but do not "
                    "create nodes or relationships whose only evidence is in context-only lines. All evidence "
                    "start_line/end_line values must stay inside line_span."
                ),
                "core_line_span": [chunk.start_line, chunk.end_line],
            },
            "allowed_node_types": NODE_TYPES,
            "allowed_relationship_types": RELATIONSHIP_TYPES,
            "output_contract": OUTPUT_CONTRACT,
            "limits": {
                "max_nodes": 28,
                "max_relationships": 24,
                "instruction": "Prefer a compact, source-faithful node set. Do not over-extract tiny terms or weak relations.",
            },
            "canonical_memory": {
                "instruction": (
                    "Use these previous nodes only as naming guidance. If the current chunk mentions the same "
                    "mathematical object, reuse the preferred_name and put local variants into aliases. "
                    "Do not reference previous ids; create local ids for nodes extracted from this chunk."
                ),
                "existing_nodes": canonical_memory or [],
            },
            "preflight_self_check": [
                "Do not output both a short label and a verbose label for the same object. Prefer the informative name and put the short label in aliases.",
                "The first-layer agent should be mechanically source-grounded. Do not invent textbook structure or semantic links that are not explicit in the current chunk.",
                "Every relationship source and target must refer to ids present in this chunk output.",
                "Every Formula node must have source-faithful content.latex copied from core text_with_line_numbers.",
                "If a concept is explicitly defined in the source, absorb the definition into Concept.description instead of creating a separate standalone definition node.",
                "Do not extract nodes or relationships from context-only lines; use context only to disambiguate core lines.",
                "Remove duplicate nodes before returning JSON.",
            ],
            "context_before_line_numbers": chunk.context_before_text,
            "text_with_line_numbers": chunk.text,
            "context_after_line_numbers": chunk.context_after_text,
        }
        return json.dumps(payload, ensure_ascii=False)

    def _cache_dir(self) -> Path:
        # Stage cache prevents successful chunks from being billed again after a failed run.
        cache_dir = ROOT / "KG" / "_cache"
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            return cache_dir
        except OSError:
            cache_dir = ROOT / "agents" / "_cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            return cache_dir

    def _cache_key(self, stage: str, label: str, state: ExtractionState) -> str:
        source = Path(state["input_path"]).resolve()
        raw = (
            f"{source}:{state['model']}:{state['max_chunk_chars']}:"
            f"{state.get('chunk_context_before_lines', 0)}:{state.get('chunk_context_after_lines', 0)}:"
            f"schema_v5_compact_graph_v1_theorem_proof_v1_example_steps_v1:{stage}:{label}"
        )
        if stage == "extract":
            raw += (
                f":{state.get('extraction_thinking', '')}"
                f":{state.get('extraction_reasoning_effort', '')}"
            )
        if stage == "relation":
            raw += (
                f":{state.get('relation_model', '')}"
                f":{state.get('relation_thinking', '')}"
                f":{state.get('relation_reasoning_effort', '')}"
            )
        if stage == "normalize":
            raw += (
                f":{state.get('normalizer_model', '')}"
                f":{state.get('normalizer_thinking', '')}"
                f":{state.get('normalizer_reasoning_effort', '')}"
            )
        if stage == "verify":
            raw += (
                f":{state.get('verification_model', '')}"
                f":{state.get('verification_thinking', '')}"
                f":{state.get('verification_reasoning_effort', '')}"
            )
        return f"{stage}_{_slug(label)}_{_stable_hash(raw)}.json"

    def _read_stage_cache(self, stage: str, label: str, state: ExtractionState) -> dict[str, Any] | None:
        path = self._cache_dir() / self._cache_key(stage, label, state)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _write_stage_cache(self, stage: str, label: str, payload: dict[str, Any], state: ExtractionState) -> None:
        path = self._cache_dir() / self._cache_key(stage, label, state)
        try:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _compact_payloads(self, payloads: list[dict[str, Any]]) -> dict[str, Any]:
        nodes: list[dict[str, Any]] = []
        relationships: list[dict[str, Any]] = []
        for payload in payloads:
            for node in payload.get("nodes", []):
                nodes.append(
                    {
                        "id": node.get("id"),
                        "type": node.get("type"),
                        "name": node.get("name"),
                        "description": node.get("description", "")[:300],
                        "latex": node.get("latex"),
                        "evidence": node.get("evidence", [])[:2],
                    }
                )
            for rel in payload.get("relationships", []):
                relationships.append(rel)
        return {"nodes": nodes, "relationships": relationships}

    def _repair_evidence(self, evidence_items: list[Evidence], source_path: str) -> None:
        for ev in evidence_items:
            ev.source_path = ev.source_path or source_path
            if ev.start_line <= 0:
                ev.start_line = 1
            if ev.end_line < ev.start_line:
                ev.end_line = ev.start_line
            ev.text = ev.text.strip()[:1200]

    def _source_latex_from_evidence(self, evidence_items: list[Evidence], raw_lines: list[str]) -> tuple[list[str], str | None]:
        source_line_candidates: list[str] = []
        evidence_candidates: list[str] = []
        for ev in evidence_items:
            start = max(ev.start_line, 1)
            end = max(ev.end_line, start)
            source_text = "\n".join(raw_lines[start - 1 : min(end, len(raw_lines))])
            source_line_candidates.extend(_latex_candidates_from_text(source_text))
            evidence_candidates.extend(_latex_candidates_from_text(ev.text))
        if source_line_candidates:
            return _unique_preserve_order(source_line_candidates), "source_lines"
        if evidence_candidates:
            return _unique_preserve_order(evidence_candidates), "evidence"
        return [], None

    def _preserve_node_latex(self, node: KGNode, raw_lines: list[str]) -> None:
        latex_items, latex_source = self._source_latex_from_evidence(node.evidence, raw_lines)
        if not latex_items:
            return
        node.content["source_latex"] = latex_items
        node.content["source_latex_source"] = latex_source
        if node.latex:
            raw_latex = _best_latex_candidate(latex_items)
            if raw_latex and node.latex != raw_latex:
                node.content.setdefault("latex_agent", node.latex)
                node.latex = raw_latex
            node.content["latex_source"] = latex_source
        elif node.type == "Formula":
            node.latex = _best_latex_candidate(latex_items)
            node.content["latex_source"] = latex_source

    def _preserve_relationship_latex(self, rel: KGRelationship, raw_lines: list[str]) -> None:
        latex_items, latex_source = self._source_latex_from_evidence(rel.evidence, raw_lines)
        if not latex_items:
            return
        rel.content["source_latex"] = latex_items
        rel.content["source_latex_source"] = latex_source

    def _count_by_type(self, items: list[Any]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in items:
            counts[item.type] = counts.get(item.type, 0) + 1
        return dict(sorted(counts.items()))

    def _title_from_lines(self, lines: list[str]) -> str:
        for line in lines:
            title = line.lstrip("#").strip() if line.startswith("#") else ""
            if title.startswith("第一节"):
                return title
        return self.input_path.stem


OUTPUT_CONTRACT = {
    "nodes": [
        {
            "id": "Optional temporary id, such as c1, f1, p1, t1, e1, pr1, or g1. Relationships may refer to it.",
            "type": "One allowed node type.",
            "name": "Canonical mathematical name, faithful to the Chinese textbook wording.",
            "aliases": ["Optional aliases or equivalent names."],
            "description": "The node's main source-grounded Chinese content, not a restatement of the node name.",
            "content": {
                "latex": "For Formula nodes, the source-faithful formula string.",
                "approach": "For Example or Proof nodes, the main source-grounded solving or proving idea.",
                "steps": ["For Example or Proof nodes, ordered source-grounded steps when clearly present."],
                "url": "For Figure nodes, the image URL when present.",
                "label": "For Figure nodes, the source figure label such as ?1-3.",
            },
            "properties": {},
            "quality": {"confidence": "Number from 0 to 1.", "stage": "candidate"},
            "evidence": [
                {
                    "source_path": "Original markdown source path.",
                    "start_line": "Evidence start line number.",
                    "end_line": "Evidence end line number.",
                    "text": "Original evidence snippet. Do not rewrite it.",
                    "chunk_id": "Chunk id.",
                }
            ],
        }
    ],
    "relationships": [
        {
            "id": "Optional temporary id.",
            "type": "One allowed relationship type.",
            "source": "Temporary source node id.",
            "target": "Temporary target node id.",
            "evidence": ["Evidence objects with the same format as node evidence."],
            "content": {
                "reason": "Short source-grounded reason why this edge should exist.",
            },
            "properties": {},
            "quality": {"confidence": "Number from 0 to 1.", "stage": "candidate"},
        }
    ],
    "notes": ["Uncertain observations or items that need later verification."],
}


EXTRACTION_SYSTEM_PROMPT = _load_prompt("concept_formula_extraction_system.txt")
RELATION_SYSTEM_PROMPT = _load_prompt("cross_chunk_relation_system.txt")
NORMALIZER_SYSTEM_PROMPT = _load_prompt("normalizer_system.txt")
VERIFICATION_SYSTEM_PROMPT = _load_prompt("verification_system.txt")


def check_config() -> int:
    key_name = None
    for name in ["DEEPSEEK_API_KEY", "DS_API_KEY", "DEEPSEEK_KEY", "OPENAI_API_KEY"]:
        if os.getenv(name):
            key_name = name
            break
    base_url = _env_first(["DEEPSEEK_BASE_URL", "DS_BASE_URL", "OPENAI_BASE_URL"]) or "https://api.deepseek.com"
    model = os.getenv("DEEPSEEK_MODEL") or os.getenv("DS_MODEL") or "deepseek-v4-flash"
    verification_model = (
        os.getenv("DEEPSEEK_VERIFICATION_MODEL") or os.getenv("DS_VERIFICATION_MODEL") or "deepseek-v4-flash"
    )
    print(
        json.dumps(
            {
                "api_key_env": key_name,
                "api_key_found": bool(key_name),
                "base_url": base_url,
                "model": model,
                "chunk_context_before_lines": 6,
                "chunk_context_after_lines": 4,
                "extraction_thinking": "disabled",
                "extraction_reasoning_effort": "high",
                "relation_model": os.getenv("DEEPSEEK_RELATION_MODEL") or os.getenv("DS_RELATION_MODEL") or model,
                "relation_thinking": "disabled",
                "relation_reasoning_effort": "high",
                "normalizer_model": os.getenv("DEEPSEEK_NORMALIZER_MODEL") or os.getenv("DS_NORMALIZER_MODEL") or model,
                "normalizer_thinking": "disabled",
                "normalizer_reasoning_effort": "high",
                "verification_model": verification_model,
                "verification_thinking": "enabled",
                "verification_reasoning_effort": "high",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if key_name else 1


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agent-driven Concept/Formula extractor using LangGraph.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Markdown source path.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="JSON output path.")
    parser.add_argument(
        "--model",
        default=None,
        help="DeepSeek model name. Defaults to DEEPSEEK_MODEL/DS_MODEL/deepseek-v4-flash.",
    )
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--max-chunk-chars", type=int, default=3000)
    parser.add_argument(
        "--chunk-context-before-lines",
        type=int,
        default=6,
        help="Read-only context lines prepended to each chunk for boundary understanding.",
    )
    parser.add_argument(
        "--chunk-context-after-lines",
        type=int,
        default=4,
        help="Read-only context lines appended to each chunk for boundary understanding.",
    )
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--request-timeout", type=float, default=180)
    parser.add_argument("--max-tokens", type=int, default=16000)
    parser.add_argument("--request-delay", type=float, default=3.0, help="Seconds to sleep between DeepSeek calls.")
    parser.add_argument(
        "--thinking",
        default="disabled",
        choices=["enabled", "disabled"],
        help="Enable or disable DeepSeek thinking mode for extraction/normalization stages. Enable when using v4-pro.",
    )
    parser.add_argument(
        "--reasoning-effort",
        default="high",
        choices=["low", "medium", "high"],
        help="Reasoning effort for extraction/normalization when thinking is enabled (v4-pro only).",
    )
    parser.add_argument(
        "--relation-model",
        default=None,
        help="Model for RelationAgent (cross-chunk). Defaults to --model value.",
    )
    parser.add_argument(
        "--relation-thinking",
        default="disabled",
        choices=["enabled", "disabled"],
        help="Enable DeepSeek thinking for RelationAgent (v4-pro only).",
    )
    parser.add_argument(
        "--relation-reasoning-effort",
        default="high",
        choices=["low", "medium", "high"],
        help="Reasoning effort for RelationAgent when thinking is enabled.",
    )
    parser.add_argument(
        "--normalizer-model",
        default=None,
        help="Model for NormalizerAgent. Defaults to --model value.",
    )
    parser.add_argument(
        "--normalizer-thinking",
        default="disabled",
        choices=["enabled", "disabled"],
        help="Enable DeepSeek thinking for NormalizerAgent (v4-pro only).",
    )
    parser.add_argument(
        "--normalizer-reasoning-effort",
        default="high",
        choices=["low", "medium", "high"],
        help="Reasoning effort for NormalizerAgent when thinking is enabled.",
    )
    parser.add_argument("--verification-batch-size", type=int, default=35, help="Relationships per VerificationAgent call.")
    parser.add_argument(
        "--verification-model",
        default=None,
        help="Model used only by VerificationAgent. Defaults to DEEPSEEK_VERIFICATION_MODEL/DS_VERIFICATION_MODEL/deepseek-v4-flash.",
    )
    parser.add_argument(
        "--verification-reasoning-effort",
        default="high",
        choices=["low", "medium", "high"],
        help="Reasoning effort used only by VerificationAgent when thinking is enabled.",
    )
    parser.add_argument(
        "--verification-thinking",
        default="enabled",
        choices=["enabled", "disabled"],
        help="Enable or disable DeepSeek thinking mode for VerificationAgent.",
    )
    parser.add_argument("--no-save", action="store_true", help="Run graph but do not write output file.")
    parser.add_argument("--no-resume", action="store_true", help="Ignore cached chunk/stage payloads and call the API again.")
    parser.add_argument("--include-exercises", action="store_true", help="Also extract exercise content after the exercise heading.")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress logs.")
    parser.add_argument(
        "--no-response-format",
        action="store_true",
        help="Disable OpenAI response_format={type:json_object}. It is enabled by default per DeepSeek docs.",
    )
    parser.add_argument(
        "--no-proxy",
        action="store_true",
        help="Ignore HTTP(S)_PROXY environment variables when calling DeepSeek. Useful for SSL EOF proxy issues.",
    )
    parser.add_argument("--check-config", action="store_true", help="Check API environment variables without calling the API.")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    if args.check_config:
        raise SystemExit(check_config())

    extractor = ConceptFormulaExtractor(
        input_path=args.input,
        output_path=args.output,
        model=args.model,
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
        extraction_thinking=args.thinking,
        extraction_reasoning_effort=args.reasoning_effort,
        relation_model=args.relation_model,
        relation_thinking=args.relation_thinking,
        relation_reasoning_effort=args.relation_reasoning_effort,
        normalizer_model=args.normalizer_model,
        normalizer_thinking=args.normalizer_thinking,
        normalizer_reasoning_effort=args.normalizer_reasoning_effort,
        verification_batch_size=args.verification_batch_size,
        verification_model=args.verification_model,
        verification_reasoning_effort=args.verification_reasoning_effort,
        verification_thinking=args.verification_thinking,
        resume=not args.no_resume,
        include_exercises=args.include_exercises,
        verbose=not args.quiet,
    )
    try:
        result = extractor.run()
    except (RuntimeError, ValidationError, json.JSONDecodeError) as exc:
        print(f"ConceptFormulaExtractor failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(result.diagnostics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
