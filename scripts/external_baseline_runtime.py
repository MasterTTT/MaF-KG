"""Bounded text transport and audited CPU embeddings for external KG methods."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from prospective_native_client import Budget, payload, write

MODEL = "deepseek-v4-flash"
ENDPOINT = "https://opencode.ai/zen/go/v1/chat/completions"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class GateReached(RuntimeError):
    pass


class TextClient:
    def __init__(self, directory: Path, ledger: Path, session: str, *,
                 max_tokens=24000, timeout=600, max_calls=40, max_seconds=5400,
                 usd_limit=0.50, token_limit=1_000_000, domain_instruction="", stream=True):
        self.directory, self.ledger, self.session = directory, ledger, session
        self.model_name = MODEL
        self.max_tokens, self.timeout = max_tokens, timeout
        self.max_calls, self.max_seconds = max_calls, max_seconds
        self.usd_limit, self.token_limit = usd_limit, token_limit
        self.domain_instruction = domain_instruction
        self.stream = stream
        self.started, self.calls = time.monotonic(), 0

    def __call__(self, messages: list[dict], label="request", **kwargs) -> str:
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.ledger.with_suffix(".lock")
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise GateReached("Concurrent or interrupted method ledger; audit before calling") from None
        try:
            os.write(descriptor, str(os.getpid()).encode("ascii"))
            return self._complete(messages, label, **kwargs)
        finally:
            os.close(descriptor)
            lock_path.unlink()

    def _complete(self, messages: list[dict], label="request", **kwargs) -> str:
        if self.calls >= self.max_calls or time.monotonic() - self.started >= self.max_seconds:
            raise GateReached("Task request/time gate reached")
        key = os.environ.get("OPENCODE_API_KEY")
        if not key:
            raise RuntimeError("OPENCODE_API_KEY missing")
        if not messages or any(m.get("role") not in {"system", "user", "assistant"}
                               or not isinstance(m.get("content"), str) for m in messages):
            raise ValueError("Text messages only")
        _, body = payload(MODEL, "", "", self.max_tokens)
        if self.stream:
            body.update(stream=True, stream_options={"include_usage": True})
        body["messages"] = [dict(m) for m in messages]
        if self.domain_instruction:
            body["messages"].insert(0, {"role": "system", "content": self.domain_instruction})
        # Native wrappers may request JSON schema, but text output is intentionally
        # preserved for the method's own parser. Never masquerade as a GPT model.
        self.calls += 1
        safe_label = re.sub(r"[^A-Za-z0-9_.-]", "_", str(label))[:100]
        stem = f"{self.calls:04d}_{safe_label}"
        budget = Budget(self.ledger, MODEL, self.usd_limit, self.token_limit)
        index = budget.reserve(body, self.max_tokens, str(self.directory / stem))
        write(self.directory / "requests" / f"{stem}.json", {
            "endpoint": ENDPOINT, "body": body, "wrapper_options": kwargs,
            "schema_transport": "native text; original method parses output"})
        request = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                     "User-Agent": "MaFKG-external-baselines/1.0",
                     "x-opencode-session": self.session}, method="POST")
        started = time.monotonic()
        settled = False
        stream_lines = []
        try:
            remaining = self.max_seconds - (started - self.started)
            with urllib.request.urlopen(request, timeout=min(self.timeout, remaining)) as response:
                status = response.status
                if self.stream:
                    from probe_go_streaming import consume

                    def observed_lines():
                        for line in response:
                            stream_lines.append(line.decode("utf-8"))
                            yield line

                    native, _ = consume(observed_lines(), deadline=started + min(self.timeout, remaining))
                    raw = json.dumps(native, ensure_ascii=False)
                else:
                    raw = response.read().decode("utf-8")
            if key in raw:
                raise RuntimeError("Credential echo: not persisted")
            target = self.directory / "responses" / f"{stem}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(raw, encoding="utf-8", newline="\n")
            native = json.loads(raw)
            budget.settle(index, native.get("usage"))
            settled = True
            choices = native.get("choices", [])
            if len(choices) != 1 or choices[0].get("finish_reason") != "stop":
                raise RuntimeError("Non-complete native response; no parse repair or retry")
            content = choices[0].get("message", {}).get("content")
            if not isinstance(content, str) or not content.strip():
                raise RuntimeError("Empty native response")
            write(self.directory / "transport" / f"{stem}.json", {
                "status": status, "attempts": 1, "elapsed_seconds": time.monotonic()-started,
                "model_returned": native.get("model"), "usage": native.get("usage"),
                "stream": self.stream,
                "reasoning_effort_sent": body["reasoning_effort"], "finish_reason": "stop"})
            return content
        except Exception as exc:
            if not settled and budget.data["requests"][index]["status"] == "reserved":
                budget.settle(index, None)
            error = {"error_type": type(exc).__name__, "attempts": 1,
                     "elapsed_seconds": time.monotonic()-started}
            if isinstance(exc, urllib.error.HTTPError):
                error["http_status"] = exc.code
                error["body"] = exc.read().decode("utf-8", errors="replace").replace(key, "[REDACTED]")[:4000]
            write(self.directory / "errors" / f"{stem}.json", error)
            raise RuntimeError(f"Single-attempt request failed: {error['error_type']}") from None
        finally:
            if stream_lines and key not in "".join(stream_lines):
                write(self.directory / "events_raw" / f"{stem}.json", stream_lines)


class Embeddings:
    """FastEmbed ONNX, with a vector and resource log; no graph-aware cache."""
    def __init__(self, directory: Path, model_name: str, cache_dir: Path, model_path: Path):
        from fastembed import TextEmbedding
        self.directory, self.model_name = directory, model_name
        self.engine = TextEmbedding(model_name=model_name, cache_dir=str(cache_dir),
                                    threads=2, providers=["CPUExecutionProvider"],
                                    specific_model_path=str(model_path), local_files_only=True)
        self.cache = {}
        self.calls = 0

    def __call__(self, texts: list[str]) -> list[list[float]]:
        texts = list(texts)
        if not texts or any(not isinstance(t, str) or not t.strip() for t in texts):
            raise ValueError("Non-empty embedding texts required")
        self.calls += 1
        missing = list(dict.fromkeys(t for t in texts if t not in self.cache))
        started = time.monotonic()
        if missing:
            vectors = list(self.engine.embed(missing))
            if len(vectors) != len(missing):
                raise ValueError("Embedding batch length mismatch")
            import numpy as np
            for t, v in zip(missing, vectors):
                norm = float(np.linalg.norm(v))
                if not np.isfinite(norm) or norm <= 0:
                    raise ValueError("Invalid embedding vector")
                self.cache[t] = (v / norm).tolist()
        result = [self.cache[t] for t in texts]
        write(self.directory / "embeddings" / f"{self.calls:04d}.json", {
            "model": self.model_name, "texts": texts, "vectors": result,
            "normalization": "L2 unit vectors; mean pooling in fastembed 0.7.3",
            "new_vectors": len(missing), "elapsed_seconds": time.monotonic()-started})
        return result
