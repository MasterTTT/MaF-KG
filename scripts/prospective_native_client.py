"""Single-attempt experimental Go client with persistent conservative budgets."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

MODELS = ("deepseek-v4-flash", "glm-5.3-flash", "gpt-6-luna", "mimo-v2.6-flash", "kimi-k2.6", "longcat-2.0")
STREAM_MODELS = ("glm-5.3-flash", "mimo-v2.6-flash", "kimi-k2.6", "longcat-2.0")
# Use peak DS rates and no cache discount for conservative accounting.
RATES = {MODELS[0]: (0.30, 1.20), MODELS[1]: (0.15, 0.50), MODELS[2]: (0.10, 0.50),
         MODELS[3]: (0.14, 0.28), MODELS[4]: (0.95, 4.00), MODELS[5]: (0.30, 1.20)}


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def payload(model: str, system: str, user: str, limit: int) -> tuple[str, dict]:
    if model not in MODELS:
        raise ValueError("Model not in frozen experiment")
    if model == "gpt-6-luna":
        return "responses", {"model": model, "instructions": system, "input": user,
                             "max_output_tokens": limit, "reasoning": {"effort": "low"},
                             "store": False, "stream": False}
    body = {"model": model, "messages": [{"role": "system", "content": system},
                                           {"role": "user", "content": user}],
            "max_tokens": limit, "stream": False}
    if model == "deepseek-v4-flash":
        body.update(thinking={"type": "enabled"}, reasoning_effort="low")
    # GLM's native gateway default is recorded, not relabeled as DS low.
    return "chat/completions", body


def normalize(model: str, response: dict) -> dict:
    if model != "gpt-6-luna":
        return response
    if response.get("status") != "completed":
        raise ValueError(f"Responses status is not completed: {response.get('status')}")
    texts = [part["text"] for item in response.get("output", [])
             if item.get("type") == "message" and item.get("role") == "assistant"
             for part in item.get("content", []) if part.get("type") == "output_text"]
    if not texts:
        raise ValueError("Responses contained no assistant output_text")
    usage = response.get("usage", {})
    return {"id": response.get("id"), "model": response.get("model"),
            "choices": [{"finish_reason": "stop", "message": {"content": "".join(texts)}}],
            "usage": {"prompt_tokens": usage.get("input_tokens"),
                      "completion_tokens": usage.get("output_tokens"),
                      "total_tokens": usage.get("total_tokens"),
                      "completion_tokens_details": usage.get("output_tokens_details", {}),
                      "prompt_tokens_details": usage.get("input_tokens_details", {})}}


class Budget:
    def __init__(self, path: Path, model: str, usd: float = 0.50, tokens: int = 1_000_000):
        self.path, self.model, self.usd, self.tokens = path, model, usd, tokens
        self.data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
            "model": model, "usd_limit": usd, "token_limit": tokens,
            "accounting_usd": 0.0, "accounting_tokens": 0, "requests": []}
        if (self.data["model"], self.data["usd_limit"], self.data["token_limit"]) != (model, usd, tokens):
            raise ValueError("Budget configuration changed")
        if any(r["status"] == "reserved" for r in self.data["requests"]):
            raise ValueError("Unsettled request: inspect before sending anything else")

    def reserve(self, body: dict, output_limit: int, label: str) -> int:
        # Bytes overestimate ordinary text tokenization; margin covers framing.
        # This is a local estimate, not a provider quota/billing guarantee.
        input_bound = len(json.dumps(body, ensure_ascii=False).encode("utf-8")) + 4096
        token_bound = input_bound + output_limit
        a, b = RATES[self.model]
        usd_bound = (input_bound * a + output_limit * b) / 1_000_000
        if self.data["accounting_usd"] + usd_bound > self.usd:
            raise ValueError("Pre-request USD budget gate reached")
        if self.data["accounting_tokens"] + token_bound > self.tokens:
            raise ValueError("Pre-request token budget gate reached")
        self.data["requests"].append({"label": label, "status": "reserved",
                                      "bound_usd": usd_bound, "bound_tokens": token_bound})
        self.data["accounting_usd"] += usd_bound
        self.data["accounting_tokens"] += token_bound
        write(self.path, self.data)
        return len(self.data["requests"]) - 1

    def settle(self, index: int, usage: dict | None) -> None:
        row = self.data["requests"][index]
        if row["status"] != "reserved":
            raise ValueError("Request already settled")
        incoming = (usage or {}).get("prompt_tokens", (usage or {}).get("input_tokens"))
        outgoing = (usage or {}).get("completion_tokens", (usage or {}).get("output_tokens"))
        if type(incoming) is int and type(outgoing) is int and incoming >= 0 and outgoing >= 0:
            a, b = RATES[self.model]
            cost, tokens = (incoming * a + outgoing * b) / 1_000_000, incoming + outgoing
            self.data["accounting_usd"] += cost - row["bound_usd"]
            self.data["accounting_tokens"] += tokens - row["bound_tokens"]
            row.update(status="reported_usage", usage=usage, estimated_usd=cost)
        else:
            row["status"] = "unknown_usage_keep_full_reservation"
        write(self.path, self.data)
        if self.data["accounting_usd"] > self.usd or self.data["accounting_tokens"] > self.tokens:
            raise ValueError("Reported usage exceeded budget: stop")


def install(module, model: str, directory: Path, budget_path: Path, session: str,
            usd_limit: float = 0.50, token_limit: int = 1_000_000) -> None:
    def complete(self, system_prompt, user_prompt, label="request"):
        route, body = payload(model, system_prompt, user_prompt, self.max_tokens)
        if model in STREAM_MODELS:
            body.update(stream=True, stream_options={"include_usage":True})
        endpoint = "https://opencode.ai/zen/go/v1/" + route
        budget = Budget(budget_path, model, usd=usd_limit, tokens=token_limit)
        index = budget.reserve(body, self.max_tokens, str(directory / label))
        write(directory / "native_requests" / f"{label}.json", {"endpoint": endpoint, "body": body})
        key = os.environ.get("OPENCODE_API_KEY")
        if not key:
            budget.settle(index, None)
            raise RuntimeError("OPENCODE_API_KEY missing")
        request = urllib.request.Request(endpoint, data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                     "User-Agent": "MaFKG-prospective-experiment/1.0", "x-opencode-session": session}, method="POST")
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                http_status = response.status
                if body["stream"]:
                    from probe_go_streaming import consume
                    native, events = consume(response, deadline=started + self.timeout)
                    raw = json.dumps(events, ensure_ascii=False)
                else:
                    raw = response.read().decode("utf-8")
                    native = json.loads(raw)
            if key in raw:
                raise RuntimeError("Credential echo; response not persisted")
            write(directory / "native_responses" / f"{label}.json", native)
            write(directory / "native_transport" / f"{label}.json", {
                "http_status":http_status, "elapsed_seconds":round(time.monotonic()-started,4),
                "stream":body["stream"], "attempts":1})
            if body["stream"]:
                write(directory / "native_events" / f"{label}.json", events)
        except Exception as exc:
            error = {"type": type(exc).__name__, "attempts": 1}
            if isinstance(exc, urllib.error.HTTPError):
                error.update(http_status=exc.code,
                             body=exc.read().decode("utf-8", errors="replace").replace(key, "[REDACTED]")[:4000])
            write(directory / "native_errors" / f"{label}.json", error)
            budget.settle(index, None)
            raise RuntimeError(f"Native request failed; no retry: {error['type']}") from None
        # Account before parsing: malformed/truncated replies still consumed tokens.
        budget.settle(index, native.get("usage"))
        normalized = normalize(model, native)
        return self._execute_request(lambda: normalized, "native-single-attempt", 1, label)
    module.ProviderChatClient.complete_json = complete
