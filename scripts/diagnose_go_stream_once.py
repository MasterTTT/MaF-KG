"""One bounded diagnostic request; never a quality-experiment retry."""
import argparse
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from prospective_native_client import Budget, write


def run(source: Path, output: Path):
    record = json.loads(source.read_text(encoding="utf-8"))
    body = record["body"]
    if body["model"] != "glm-5.3-flash":
        raise ValueError("Diagnostic is scoped to the recorded GLM stream failure")
    body.update(max_tokens=512, stream=True, stream_options={"include_usage": True})
    key = os.environ["OPENCODE_API_KEY"]
    output.mkdir(parents=True, exist_ok=False)
    budget = Budget(output / "budget.json", body["model"], usd=0.02, tokens=150000)
    index = budget.reserve(body, 512, "glm-diagnostic-only")
    write(output / "request.json", {"endpoint": record["endpoint"], "body": body,
          "source_request": str(source.resolve()), "scope": "max output reduced to 512, timeout 90; not quality scoring"})
    trace = {"scope": "diagnostic only; not a rerun admitted to quality matrix", "events": []}
    started = time.monotonic()
    usage = None
    request = urllib.request.Request(record["endpoint"], data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                 "User-Agent": "MaFKG-go-diagnostic/1.0", "x-opencode-session": str(uuid.uuid4())}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            trace.update(http_status=response.status, content_type=response.headers.get("Content-Type"))
            for raw in response:
                if time.monotonic() - started > 90:
                    raise TimeoutError("Diagnostic stream wall allowance reached")
                line = raw.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    trace["done_received"] = True
                    break
                event = json.loads(data)
                if event.get("usage"):
                    usage = event["usage"]
                trace["events"].append({"elapsed_seconds": round(time.monotonic()-started, 3), "event": event})
                if event.get("error"):
                    trace["provider_error"] = event["error"]
                    break
    except Exception as exc:
        trace.update(error_type=type(exc).__name__, error_message=str(exc))
        if isinstance(exc, urllib.error.HTTPError):
            trace.update(http_status=exc.code, error_body=exc.read().decode("utf-8", errors="replace"))
    finally:
        budget.settle(index, usage)
    trace["elapsed_seconds"] = round(time.monotonic()-started, 3)
    trace["usage"] = usage
    sanitized = json.loads(json.dumps(trace, ensure_ascii=False).replace(key, "[REDACTED]"))
    write(output / "trace.json", sanitized)
    print(json.dumps({k:v for k,v in sanitized.items() if k != "events"}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    run(args.source, args.output)
