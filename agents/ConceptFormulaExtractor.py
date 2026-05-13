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
from pydantic import BaseModel, Field, ValidationError
from typing_extensions import TypedDict


ROOT = Path(__file__).resolve().parents[1]
PROMPT_DIR = ROOT / "prompts"
DEFAULT_INPUT = ROOT / "data" / "raw" / "第一章第一节_映射与函数.md"
DEFAULT_OUTPUT = ROOT / "KG" / "第一章第一节_映射与函数_concept_formula_kg_agent.json"

NODE_TYPES = [
    "Section",
    "Concept",
    "Definition",
    "Formula",
    "Theorem",
    "Condition",
    "Method",
    "Example",
]

RELATIONSHIP_TYPES = [
    "belongs_to",
    "contains",
    "has_definition",
    "has_formula",
    "requires",
    "has_condition",
    "derived_from",
    "equivalent_to",
    "applies_to",
    "uses_method",
    "involves_concept",
    "example_of",
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
        "Section",
        "Concept",
        "Definition",
        "Formula",
        "Theorem",
        "Condition",
        "Method",
        "Example",
    ]
    name: str
    aliases: list[str] = Field(default_factory=list)
    description: str = ""
    latex: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float = Field(default=0.75, ge=0, le=1)


class KGRelationship(BaseModel):
    id: str = ""
    type: Literal[
        "belongs_to",
        "contains",
        "has_definition",
        "has_formula",
        "requires",
        "has_condition",
        "derived_from",
        "equivalent_to",
        "applies_to",
        "uses_method",
        "involves_concept",
        "example_of",
    ]
    source: str
    target: str
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float = Field(default=0.75, ge=0, le=1)
    properties: dict[str, Any] = Field(default_factory=dict)


class AgentGraphPayload(BaseModel):
    nodes: list[KGNode] = Field(default_factory=list)
    relationships: list[KGRelationship] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    schema_version: str = "maf-kg.agent.v1"
    source_path: str
    title: str
    model: str
    node_types: list[str] = Field(default_factory=lambda: NODE_TYPES)
    relationship_types: list[str] = Field(default_factory=lambda: RELATIONSHIP_TYPES)
    nodes: list[KGNode]
    relationships: list[KGRelationship]
    diagnostics: dict[str, Any] = Field(default_factory=dict)


@dataclass
class DocumentChunk:
    id: str
    start_line: int
    end_line: int
    heading_path: list[str]
    text: str


class ExtractionState(TypedDict, total=False):
    # Shared LangGraph state passed between workflow nodes.
    input_path: str
    output_path: str
    model: str
    temperature: float
    max_chunk_chars: int
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
    relation_payload: dict[str, Any]
    normalized_payload: dict[str, Any]
    verified_payload: dict[str, Any]
    verification_batch_size: int
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
        for match in re.finditer(r"(?<!\\)\$(?!\$)(.*?)(?<!\\)\$", text, flags=re.S)
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


def _best_effort_agent_payload(text: str) -> dict[str, Any]:
    # DeepSeek can occasionally return truncated JSON. Recover complete objects
    # locally before spending another API call on JSON repair.
    body = text.split("\n\n", 1)[1] if text.startswith("JSONDecodeError:") and "\n\n" in text else text
    body = body.strip()
    if body.startswith("```"):
        body = re.sub(r"^```(?:json)?\s*", "", body)
        body = re.sub(r"\s*```$", "", body)
    try:
        return _extract_json_object(body)
    except json.JSONDecodeError:
        pass

    nodes_match = re.search(r'"nodes"\s*:\s*\[', body)
    if not nodes_match:
        raise
    nodes_start = body.find("[", nodes_match.end() - 1)
    try:
        nodes_text, nodes_end = _extract_balanced_array(body, nodes_start)
        nodes = json.loads(nodes_text)
    except json.JSONDecodeError:
        nodes = _extract_complete_json_objects(body[nodes_start + 1 :])
        return {
            "nodes": nodes,
            "relationships": [],
            "notes": ["Recovered complete node objects from an incomplete JSON response."],
        }

    relationships: list[dict[str, Any]] = []
    rel_match = re.search(r'"relationships"\s*:\s*\[', body[nodes_end:])
    if rel_match:
        rel_start = nodes_end + body[nodes_end:].find("[", rel_match.end() - 1)
        try:
            rel_text, _ = _extract_balanced_array(body, rel_start)
            relationships = json.loads(rel_text)
        except json.JSONDecodeError:
            rel_prefix = body[rel_start + 1 :]
            relationships = _extract_complete_json_objects(rel_prefix)
    return {"nodes": nodes, "relationships": relationships, "notes": ["Recovered from incomplete JSON response."]}


def _extract_balanced_array(text: str, start: int) -> tuple[str, int]:
    if start < 0 or text[start] != "[":
        raise json.JSONDecodeError("array start not found", text, max(start, 0))
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return text[start : index + 1], index + 1
    raise json.JSONDecodeError("unterminated array", text, start)


def _extract_complete_json_objects(text: str) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    index = 0
    while index < len(text):
        brace = text.find("{", index)
        if brace == -1:
            break
        try:
            object_text, end = _extract_balanced_object(text, brace)
            objects.append(json.loads(object_text))
            index = end
        except json.JSONDecodeError:
            break
    return objects


def _extract_balanced_object(text: str, start: int) -> tuple[str, int]:
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1], index + 1
    raise json.JSONDecodeError("unterminated object", text, start)


class JsonParseFailure(RuntimeError):
    def __init__(self, message: str, raw_content: str) -> None:
        super().__init__(message)
        self.raw_content = raw_content


class DeepSeekChatClient:
    """OpenAI-compatible DeepSeek client with retries and JSON recovery."""

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
        # Strict parse -> local best-effort recovery -> model-based JSON repair.
        try:
            return _extract_json_object(content)
        except json.JSONDecodeError as exc:
            debug_path = self._write_debug_response(content, label, exc)
            try:
                return _best_effort_agent_payload(content)
            except Exception:
                pass
            repaired = self._repair_json_with_model(content, str(exc))
            try:
                return _extract_json_object(repaired)
            except json.JSONDecodeError as repair_exc:
                try:
                    return _best_effort_agent_payload(content)
                except Exception as recovery_exc:
                    raise JsonParseFailure(
                        f"JSON 解析失败，原始响应已保存到 {debug_path}；修复后仍失败: {repair_exc}",
                        content,
                    ) from recovery_exc

    def _repair_json_with_model(self, invalid_json: str, error: str) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "max_tokens": self.max_tokens,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a JSON repair agent. Return only valid JSON. "
                        "Do not summarize, do not add markdown. Preserve the original keys "
                        "nodes, relationships, notes when present."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": "Repair this invalid JSON into one valid JSON object.",
                            "parse_error": error,
                            "invalid_json": invalid_json,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        }
        return self._post_with_powershell(payload, "json_repair")

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
        max_retries: int = 2,
        request_timeout: float = 180,
        max_tokens: int = 16000,
        save_output: bool = True,
        use_response_format: bool = True,
        no_proxy: bool = False,
        request_delay: float = 3.0,
        verification_batch_size: int = 35,
        resume: bool = True,
        include_exercises: bool = False,
        verbose: bool = True,
    ) -> None:
        self.input_path = Path(input_path)
        self.output_path = Path(output_path)
        self.model = model or os.getenv("DEEPSEEK_MODEL") or os.getenv("DS_MODEL") or "deepseek-v4-flash"
        self.temperature = temperature
        self.max_chunk_chars = max_chunk_chars
        self.max_retries = max_retries
        self.request_timeout = request_timeout
        self.max_tokens = max_tokens
        self.save_output = save_output
        self.use_response_format = use_response_format
        self.no_proxy = no_proxy
        self.request_delay = request_delay
        self.verification_batch_size = verification_batch_size
        self.resume = resume
        self.include_exercises = include_exercises
        self.verbose = verbose
        self.client: DeepSeekChatClient | None = None
        self.graph = self._build_graph()

    def _build_graph(self):
        # LangGraph pipeline:
        # markdown -> chunks -> local extraction -> cross-chunk relations
        # -> normalization -> verification -> deterministic ids -> JSON output.
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
        workflow.add_node("infer_cross_chunk_relationships", self._infer_cross_chunk_relationships, retry_policy=api_retry)
        workflow.add_node("normalize_with_agent", self._normalize_with_agent, retry_policy=api_retry)
        workflow.add_node("verify_with_agent", self._verify_with_agent, retry_policy=api_retry)
        workflow.add_node("finalize_graph", self._finalize_graph)
        workflow.add_node("save_result", self._save_result)

        workflow.set_entry_point("load_document")
        workflow.add_edge("load_document", "chunk_document")
        workflow.add_edge("chunk_document", "extract_with_agents")
        workflow.add_edge("extract_with_agents", "infer_cross_chunk_relationships")
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
            "max_retries": self.max_retries,
            "request_timeout": self.request_timeout,
            "max_tokens": self.max_tokens,
            "save_output": self.save_output,
            "use_response_format": self.use_response_format,
            "no_proxy": self.no_proxy,
            "request_delay": self.request_delay,
            "verification_batch_size": self.verification_batch_size,
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
            )
        return self.client

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
        self._log(state, "Preparing line-numbered chunks")
        lines = state["raw_lines"]
        max_chars = state["max_chunk_chars"]
        chunks: list[DocumentChunk] = []
        heading_stack: list[tuple[int, str]] = []
        current: list[tuple[int, str, list[str]]] = []
        current_chars = 0

        def heading_level(line: str) -> int:
            marker = line.split(" ", 1)[0]
            return marker.count("#") if marker.startswith("#") else 99

        def flush() -> None:
            # Keep line numbers and heading context in every chunk for evidence tracing.
            nonlocal current, current_chars
            if not current:
                return
            start = current[0][0]
            end = current[-1][0]
            heading_path = current[0][2]
            text = "\n".join(f"{line_no}: {line}" for line_no, line, _ in current)
            chunk_id = f"chunk_{len(chunks) + 1:03d}_{start}_{end}"
            chunks.append(
                DocumentChunk(
                    id=chunk_id,
                    start_line=start,
                    end_line=end,
                    heading_path=heading_path,
                    text=text,
                )
            )
            current = []
            current_chars = 0

        for line_no, line in enumerate(lines, start=1):
            if line.startswith("#"):
                level = heading_level(line)
                title = line.lstrip("#").strip()
                heading_stack = [item for item in heading_stack if item[0] < level]
                heading_stack.append((level, title))
            heading_path = [title for _, title in heading_stack]
            line_payload = f"{line_no}: {line}"
            if current and current_chars + len(line_payload) > max_chars:
                flush()
            current.append((line_no, line, heading_path))
            current_chars += len(line_payload) + 1
        flush()

        diagnostics = dict(state.get("diagnostics", {}))
        diagnostics["chunk_count"] = len(chunks)
        diagnostics["chunk_spans"] = [
            {"id": chunk.id, "start_line": chunk.start_line, "end_line": chunk.end_line}
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
                chunk_payloads.append(validated.model_dump(mode="json"))
                self._log(
                    state,
                    f"ExtractionAgent chunk {index}/{len(chunks)} resume: "
                    f"{len(validated.nodes)} nodes, {len(validated.relationships)} relationships",
                )
                continue
            if chunk_payloads and state.get("request_delay", 0) > 0:
                time.sleep(float(state["request_delay"]))
            self._log(
                state,
                f"ExtractionAgent chunk {index}/{len(chunks)} start: {chunk.id} L{chunk.start_line}-L{chunk.end_line}",
            )
            payload = client.complete_json(
                EXTRACTION_SYSTEM_PROMPT,
                self._chunk_extraction_prompt(chunk, state["input_path"]),
                label=chunk.id,
            )
            validated = AgentGraphPayload.model_validate(payload)
            self._write_stage_cache("extract", chunk.id, validated.model_dump(mode="json"), state)
            chunk_payloads.append(validated.model_dump(mode="json"))
            self._log(
                state,
                f"ExtractionAgent chunk {index}/{len(chunks)} done: "
                f"{len(validated.nodes)} nodes, {len(validated.relationships)} relationships",
            )
        diagnostics = dict(state.get("diagnostics", {}))
        diagnostics["chunk_payload_count"] = len(chunk_payloads)
        return {**state, "chunk_payloads": chunk_payloads, "diagnostics": diagnostics}

    def _infer_cross_chunk_relationships(self, state: ExtractionState) -> ExtractionState:
        # RelationAgent adds relationships that require context across chunks.
        client = self._client(state)
        self._log(state, "RelationAgent start: inferring cross-chunk relationships")
        cached = self._read_stage_cache("relation", "cross_chunk", state)
        if cached and state.get("resume", True):
            validated = AgentGraphPayload.model_validate(cached)
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
        compact = self._compact_payloads(state["chunk_payloads"])
        payload = client.complete_json(
            RELATION_SYSTEM_PROMPT,
            json.dumps(
                {
                    "source_path": state["input_path"],
                    "allowed_relationship_types": RELATIONSHIP_TYPES,
                    "candidate_graph": compact,
                },
                ensure_ascii=False,
            ),
            label="cross_chunk_relationships",
        )
        validated = AgentGraphPayload.model_validate(payload)
        self._write_stage_cache("relation", "cross_chunk", validated.model_dump(mode="json"), state)
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
        # NormalizerAgent deduplicates nodes, standardizes names, and removes weak relations.
        client = self._client(state)
        self._log(state, "NormalizerAgent start: deduplicating and cleaning candidate graph")
        cached = self._read_stage_cache("normalize", "normalized", state)
        if cached and state.get("resume", True):
            validated = AgentGraphPayload.model_validate(cached)
            diagnostics = dict(state.get("diagnostics", {}))
            diagnostics["normalized_node_count_from_agent"] = len(validated.nodes)
            diagnostics["normalized_relationship_count_from_agent"] = len(validated.relationships)
            self._log(
                state,
                f"NormalizerAgent resume: {len(validated.nodes)} nodes, {len(validated.relationships)} relationships",
            )
            return {
                **state,
                "normalized_payload": validated.model_dump(mode="json"),
                "diagnostics": diagnostics,
            }
        if state.get("request_delay", 0) > 0:
            time.sleep(float(state["request_delay"]))
        merged = {
            "chunk_payloads": state["chunk_payloads"],
            "cross_chunk_payload": state["relation_payload"],
        }
        payload = client.complete_json(
            NORMALIZER_SYSTEM_PROMPT,
            json.dumps(
                {
                    "source_path": state["input_path"],
                    "allowed_node_types": NODE_TYPES,
                    "allowed_relationship_types": RELATIONSHIP_TYPES,
                    "candidate_graph": merged,
                },
                ensure_ascii=False,
            ),
            label="normalizer",
        )
        validated = AgentGraphPayload.model_validate(payload)
        fallback_payload = self._candidate_graph_as_payload(state)
        recovered_from_incomplete_json = any(
            "incomplete JSON" in note or "incomplete json" in note.lower() for note in validated.notes
        )
        if recovered_from_incomplete_json and not validated.relationships and fallback_payload.relationships:
            diagnostics = dict(state.get("diagnostics", {}))
            diagnostics["normalizer_fallback"] = "kept_agent_candidate_graph_after_incomplete_normalizer_json"
            diagnostics["normalized_node_count_from_agent"] = len(validated.nodes)
            diagnostics["normalized_relationship_count_from_agent"] = len(validated.relationships)
            diagnostics["fallback_node_count"] = len(fallback_payload.nodes)
            diagnostics["fallback_relationship_count"] = len(fallback_payload.relationships)
            self._log(
                state,
                "NormalizerAgent produced incomplete JSON; keeping candidate graph before normalization",
            )
            self._write_stage_cache("normalize", "normalized", fallback_payload.model_dump(mode="json"), state)
            return {
                **state,
                "normalized_payload": fallback_payload.model_dump(mode="json"),
                "diagnostics": diagnostics,
            }
        self._write_stage_cache("normalize", "normalized", validated.model_dump(mode="json"), state)
        diagnostics = dict(state.get("diagnostics", {}))
        diagnostics["normalized_node_count_from_agent"] = len(validated.nodes)
        diagnostics["normalized_relationship_count_from_agent"] = len(validated.relationships)
        self._log(
            state,
            f"NormalizerAgent done: {len(validated.nodes)} nodes, {len(validated.relationships)} relationships",
        )
        return {
            **state,
            "normalized_payload": validated.model_dump(mode="json"),
            "diagnostics": diagnostics,
        }

    def _candidate_graph_as_payload(self, state: ExtractionState) -> AgentGraphPayload:
        # Fallback path: preserve agent-produced candidates if normalization is truncated.
        nodes: list[KGNode] = []
        relationships: list[KGRelationship] = []
        for raw_payload in [*state.get("chunk_payloads", []), state.get("relation_payload", {})]:
            if not raw_payload:
                continue
            payload = AgentGraphPayload.model_validate(raw_payload)
            nodes.extend(payload.nodes)
            relationships.extend(payload.relationships)
        return AgentGraphPayload(
            nodes=nodes,
            relationships=relationships,
            notes=["Normalizer fallback: preserved agent-produced candidate graph."],
        )

    def _verify_with_agent(self, state: ExtractionState) -> ExtractionState:
        # VerificationAgent reviews relationships in batches to avoid large JSON responses.
        client = self._client(state)
        self._log(state, "VerificationAgent start: validating relationship evidence and direction")
        cached = self._read_stage_cache("verify", "verified", state)
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
        node_summaries = [
            {
                "id": node.id,
                "type": node.type,
                "name": node.name,
                "aliases": node.aliases[:5],
                "description": node.description[:240],
                "latex": node.latex,
                "evidence": [ev.model_dump(mode="json") for ev in node.evidence[:1]],
            }
            for node in payload.nodes
        ]

        verified_relationships: list[KGRelationship] = []
        notes: list[str] = []
        batches = [relationships[index : index + batch_size] for index in range(0, len(relationships), batch_size)]
        for index, batch in enumerate(batches, start=1):
            label = f"batch_{index:03d}_of_{len(batches):03d}_{len(relationships)}rels"
            cached_batch = self._read_stage_cache("verify", label, state) if state.get("resume", True) else None
            if cached_batch:
                batch_payload = AgentGraphPayload.model_validate(cached_batch)
                self._log(
                    state,
                    f"VerificationAgent batch {index}/{len(batches)} resume: "
                    f"{len(batch_payload.relationships)} relationships",
                )
            else:
                if index > 1 and state.get("request_delay", 0) > 0:
                    time.sleep(float(state["request_delay"]))
                self._log(state, f"VerificationAgent batch {index}/{len(batches)} start")
                response = client.complete_json(
                    VERIFICATION_SYSTEM_PROMPT,
                    json.dumps(
                        {
                            "source_path": state["input_path"],
                            "allowed_node_types": NODE_TYPES,
                            "allowed_relationship_types": RELATIONSHIP_TYPES,
                            "output_contract": {
                                "nodes": [],
                                "relationships": OUTPUT_CONTRACT["relationships"],
                                "notes": OUTPUT_CONTRACT["notes"],
                            },
                            "candidate_nodes": node_summaries,
                            "relationship_batch": [rel.model_dump(mode="json") for rel in batch],
                        },
                        ensure_ascii=False,
                    ),
                    label=f"verification_{label}",
                )
                batch_payload = AgentGraphPayload.model_validate(response)
                self._write_stage_cache("verify", label, batch_payload.model_dump(mode="json"), state)
                self._log(
                    state,
                    f"VerificationAgent batch {index}/{len(batches)} done: "
                    f"{len(batch_payload.relationships)} relationships",
                )
            verified_relationships.extend(batch_payload.relationships)
            notes.extend(batch_payload.notes)

        verified = AgentGraphPayload(nodes=payload.nodes, relationships=verified_relationships, notes=notes)
        self._write_stage_cache("verify", "verified", verified.model_dump(mode="json"), state)
        diagnostics = dict(state.get("diagnostics", {}))
        diagnostics["verification_batch_count"] = len(batches)
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

    def _finalize_graph(self, state: ExtractionState) -> ExtractionState:
        # Local finalization stabilizes ids and drops relationships with invalid endpoints.
        self._log(state, "Finalizing graph ids and validating references")
        payload = AgentGraphPayload.model_validate(state.get("verified_payload", state["normalized_payload"]))
        node_map: dict[str, KGNode] = {}
        tmp_to_final: dict[str, str] = {}

        for node in payload.nodes:
            final_id = _node_id(node.type, node.name)
            if node.id:
                tmp_to_final[node.id] = final_id
            node.id = final_id
            self._repair_evidence(node.evidence, state["input_path"])
            self._preserve_node_latex(node, state["raw_lines"])
            key = node.id
            if key in node_map:
                node_map[key].aliases = sorted(set(node_map[key].aliases + node.aliases))
                node_map[key].evidence.extend(node.evidence)
                node_map[key].confidence = max(node_map[key].confidence, node.confidence)
                if not node_map[key].description and node.description:
                    node_map[key].description = node.description
                if not node_map[key].latex and node.latex:
                    node_map[key].latex = node.latex
                node_map[key].properties.update(node.properties)
            else:
                node_map[key] = node

        relationships: dict[str, KGRelationship] = {}
        for rel in payload.relationships:
            rel.source = tmp_to_final.get(rel.source, rel.source)
            rel.target = tmp_to_final.get(rel.target, rel.target)
            if rel.source not in node_map or rel.target not in node_map or rel.source == rel.target:
                continue
            self._repair_evidence(rel.evidence, state["input_path"])
            self._preserve_relationship_latex(rel, state["raw_lines"])
            rel.id = _rel_id(rel.type, rel.source, rel.target)
            if rel.id in relationships:
                relationships[rel.id].evidence.extend(rel.evidence)
                relationships[rel.id].confidence = max(relationships[rel.id].confidence, rel.confidence)
                relationships[rel.id].properties.update(rel.properties)
            else:
                relationships[rel.id] = rel

        nodes = sorted(
            node_map.values(),
            key=lambda item: (
                item.evidence[0].start_line if item.evidence else 10**9,
                NODE_TYPES.index(item.type),
                item.name,
            ),
        )
        rels = sorted(relationships.values(), key=lambda item: (item.type, item.source, item.target))
        diagnostics = dict(state.get("diagnostics", {}))
        diagnostics["node_count"] = len(nodes)
        diagnostics["relationship_count"] = len(rels)
        diagnostics["node_count_by_type"] = self._count_by_type(nodes)
        diagnostics["relationship_count_by_type"] = self._count_by_type(rels)
        diagnostics["formula_latex_from_evidence_count"] = sum(
            1
            for node in nodes
            if node.type == "Formula" and node.properties.get("latex_source") in {"source_lines", "evidence"}
        )
        diagnostics["node_source_latex_count"] = sum(1 for node in nodes if node.properties.get("source_latex"))
        diagnostics["relationship_source_latex_count"] = sum(1 for rel in rels if rel.properties.get("source_latex"))

        result = ExtractionResult(
            source_path=state["input_path"],
            title=self._title_from_lines(state["raw_lines"]),
            model=state["model"],
            nodes=nodes,
            relationships=rels,
            diagnostics=diagnostics,
        )
        self._log(state, f"Finalized graph: {len(nodes)} nodes, {len(rels)} relationships")
        return {**state, "result": result, "diagnostics": diagnostics}

    def _save_result(self, state: ExtractionState) -> ExtractionState:
        result = state["result"]
        if state.get("save_output", True):
            output_path = Path(state["output_path"])
            self._log(state, f"Saving result: {output_path}")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            result.diagnostics["output_path"] = str(output_path)
            content = json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2)
            try:
                output_path.write_text(content, encoding="utf-8")
            except OSError as exc:
                fallback_root = Path("C:/tmp/maf_kg_outputs")
                fallback_path = fallback_root / output_path.name
                try:
                    fallback_path.parent.mkdir(parents=True, exist_ok=True)
                    fallback_path.write_text(content, encoding="utf-8")
                except OSError:
                    fallback_path = Path(tempfile.gettempdir()) / "maf_kg_outputs" / output_path.name
                    fallback_path.parent.mkdir(parents=True, exist_ok=True)
                    fallback_path.write_text(content, encoding="utf-8")
                result.diagnostics["output_path"] = str(fallback_path)
                result.diagnostics["output_fallback_reason"] = str(exc)
                self._log(state, f"Primary output failed; saved fallback result: {fallback_path}")
            self._log(state, "Saved result")
        return state

    def _chunk_extraction_prompt(self, chunk: DocumentChunk, source_path: str) -> str:
        payload = {
            "task": "Extract candidate knowledge graph nodes and relationships from the given higher mathematics textbook chunk.",
            "source_path": source_path,
            "chunk_id": chunk.id,
            "line_span": [chunk.start_line, chunk.end_line],
            "heading_path": chunk.heading_path,
            "allowed_node_types": NODE_TYPES,
            "allowed_relationship_types": RELATIONSHIP_TYPES,
            "output_contract": OUTPUT_CONTRACT,
            "limits": {
                "max_nodes": 35,
                "max_relationships": 60,
                "instruction": "Prioritize facts about mathematical definitions, formulas, conditions, theorems, methods, and examples. Do not over-extract just to cover every term.",
            },
            "text_with_line_numbers": chunk.text,
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
        raw = f"{source}:{state['model']}:{state['max_chunk_chars']}:{stage}:{label}"
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
        node.properties["source_latex"] = latex_items
        node.properties["source_latex_source"] = latex_source
        if node.latex:
            raw_latex = _best_latex_candidate(latex_items)
            if raw_latex and node.latex != raw_latex:
                node.properties.setdefault("latex_agent", node.latex)
                node.latex = raw_latex
            node.properties["latex_source"] = latex_source
        elif node.type == "Formula":
            node.latex = _best_latex_candidate(latex_items)
            node.properties["latex_source"] = latex_source

    def _preserve_relationship_latex(self, rel: KGRelationship, raw_lines: list[str]) -> None:
        latex_items, latex_source = self._source_latex_from_evidence(rel.evidence, raw_lines)
        if not latex_items:
            return
        rel.properties["source_latex"] = latex_items
        rel.properties["source_latex_source"] = latex_source

    def _count_by_type(self, items: list[KGNode] | list[KGRelationship]) -> dict[str, int]:
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
            "id": "Optional temporary id, such as c1, f1, or d1. Relationships may refer to it.",
            "type": "One allowed node type.",
            "name": "Canonical mathematical name, preferably faithful to the Chinese textbook wording.",
            "aliases": ["Optional aliases or equivalent names."],
            "description": "Concise mathematical meaning of the node.",
            "latex": "Required for Formula nodes; null for non-formula nodes when not applicable.",
            "properties": {
                "section": "Heading path or local section name.",
                "conditions": "Applicability conditions, domains, assumptions, or variable constraints.",
            },
            "evidence": [
                {
                    "source_path": "Original markdown source path.",
                    "start_line": "Evidence start line number.",
                    "end_line": "Evidence end line number.",
                    "text": "Original evidence snippet. Do not rewrite it.",
                    "chunk_id": "Chunk id.",
                }
            ],
            "confidence": "Number from 0 to 1.",
        }
    ],
    "relationships": [
        {
            "id": "Optional temporary id.",
            "type": "One allowed relationship type.",
            "source": "Temporary or final source node id.",
            "target": "Temporary or final target node id.",
            "evidence": ["Evidence objects with the same format as node evidence."],
            "confidence": "Number from 0 to 1.",
            "properties": {"reason": "Why this edge should exist."},
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
    print(
        json.dumps(
            {
                "api_key_env": key_name,
                "api_key_found": bool(key_name),
                "base_url": base_url,
                "model": model,
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
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--request-timeout", type=float, default=180)
    parser.add_argument("--max-tokens", type=int, default=16000)
    parser.add_argument("--request-delay", type=float, default=3.0, help="Seconds to sleep between DeepSeek calls.")
    parser.add_argument("--verification-batch-size", type=int, default=35, help="Relationships per VerificationAgent call.")
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
        max_retries=args.max_retries,
        request_timeout=args.request_timeout,
        max_tokens=args.max_tokens,
        save_output=not args.no_save,
        use_response_format=not args.no_response_format,
        no_proxy=args.no_proxy,
        request_delay=args.request_delay,
        verification_batch_size=args.verification_batch_size,
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
