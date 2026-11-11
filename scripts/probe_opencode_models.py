"""One tiny request per model; no source text, retries, or provider fallback."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--models", nargs="+", choices=("deepseek-v4-flash", "glm-5.3-flash", "gpt-6-luna", "mimo-v2.6-flash", "kimi-k2.6"))
    parser.add_argument("--glm-native-payload", action="store_true")
    args = parser.parse_args()
    key = os.environ.get("OPENCODE_API_KEY")
    if not key:
        raise RuntimeError("OPENCODE_API_KEY missing")
    args.output.mkdir(parents=True, exist_ok=False)
    session = str(uuid.uuid4())
    results = []
    for model in args.models or ("deepseek-v4-flash", "glm-5.3-flash", "gpt-6-luna"):
        responses_api = model == "gpt-6-luna"
        endpoint = "https://opencode.ai/zen/go/v1/" + ("responses" if responses_api else "chat/completions")
        if responses_api:
            payload = {"model": model, "input": "Return exactly OK.", "max_output_tokens": 256,
                       "reasoning": {"effort": "low"}, "store": False}
        else:
            payload = {"model": model, "messages": [{"role": "user", "content": "Return exactly OK."}],
                       "max_tokens": 64, "stream": False, "thinking": {"type": "disabled"}}
            if model == "glm-5.3-flash" and args.glm_native_payload:
                del payload["thinking"]
            if model in ("mimo-v2.6-flash", "kimi-k2.6"):
                del payload["thinking"]
                payload["max_tokens"] = 256
        (args.output / (model + ".request.json")).write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        request = urllib.request.Request(endpoint, data=json.dumps(payload).encode("utf-8"),
                                         headers={"Authorization": "Bearer " + key,
                                                  "Content-Type": "application/json",
                                                  "User-Agent": "MaFKG-connectivity-probe/1.0",
                                                  "x-opencode-session": session}, method="POST")
        started = time.monotonic()
        row = {"model": model, "endpoint": endpoint, "attempts": 1}
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                raw = response.read().decode("utf-8")
                row["http_status"] = response.status
            if key in raw:
                raise RuntimeError("Response contained credential; refused to persist")
            parsed = json.loads(raw)
            (args.output / (model + ".response.json")).write_text(raw, encoding="utf-8")
            if responses_api:
                content = "".join(c.get("text", "") for item in parsed.get("output", [])
                                  for c in item.get("content", []) if c.get("type") == "output_text")
                row["finish_status"] = parsed.get("status")
                stopped = row["finish_status"] == "completed"
            else:
                choice = parsed.get("choices", [{}])[0]
                content = choice.get("message", {}).get("content", "") or ""
                row["finish_status"] = choice.get("finish_reason")
                stopped = row["finish_status"] == "stop"
            row.update(response_model=parsed.get("model"), usage=parsed.get("usage", {}),
                       output=content[:200], connectivity_ok=True,
                       minimal_output_ok=content.strip() == "OK" and stopped)
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace").replace(key, "[REDACTED]")
            row.update(http_status=exc.code, connectivity_ok=False, minimal_output_ok=False,
                       error="HTTP error; no retry or fallback", error_body=error_body[:1200])
        except Exception as exc:
            row.update(connectivity_ok=False, minimal_output_ok=False, error=type(exc).__name__)
        row["elapsed_seconds"] = round(time.monotonic() - started, 3)
        results.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    report = {"date": datetime.now(timezone.utc).isoformat(), "results": results,
              "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "limits": ["No textbook sent", "No retry or fallback", "Not a quota balance check",
                         "DS and GLM smoke tests use thinking disabled, not production reasoning settings",
                         "Does not establish long-input JSON extraction compatibility"]}
    (args.output / "results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
