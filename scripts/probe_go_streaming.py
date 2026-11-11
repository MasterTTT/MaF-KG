"""Inspect native SSE transport with a tiny non-textbook request."""
import argparse
import json
import os
import time
import urllib.request
import uuid
from pathlib import Path

from prospective_native_client import MODELS, payload, write


def consume(lines, responses_api=False, deadline=None):
    events = []
    content, reasoning, finish, usage = [], [], None, {}
    result = {"choices": []}
    completed = None
    for raw in lines:
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError("Stream exceeded request wall-clock deadline")
        line = raw.decode("utf-8").strip() if isinstance(raw, bytes) else raw.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        event = json.loads(data)
        events.append(event)
        if responses_api:
            if event.get("type") in {"response.completed", "response.incomplete", "response.failed"}:
                completed = event.get("response")
            if event.get("type") == "error":
                raise ValueError("Responses stream returned error")
            continue
        if event.get("error"):
            raise ValueError("Chat stream returned error")
        for key in ("id", "model", "created", "system_fingerprint"):
            if event.get(key) is not None:
                result[key] = event[key]
        if event.get("usage"):
            usage = event["usage"]
        for choice in event.get("choices", []):
            if choice.get("index", 0) != 0:
                continue
            delta = choice.get("delta", {})
            if isinstance(delta.get("content"), str):
                content.append(delta["content"])
            if isinstance(delta.get("reasoning_content"), str):
                reasoning.append(delta["reasoning_content"])
            if choice.get("finish_reason"):
                finish = choice["finish_reason"]
    if responses_api:
        if not isinstance(completed, dict):
            raise ValueError("Responses stream ended without terminal response")
        return completed, events
    if not finish:
        raise ValueError("Chat stream ended without finish_reason")
    result.update(usage=usage, choices=[{"finish_reason":finish,"message":{
        "role":"assistant", "content":"".join(content), "reasoning_content":"".join(reasoning)}}])
    return result, events


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output",type=Path)
    parser.add_argument("--models",nargs="+",choices=MODELS,required=True)
    args = parser.parse_args()
    key = os.environ["OPENCODE_API_KEY"]
    args.output.mkdir(parents=True,exist_ok=False)
    results, session = [], str(uuid.uuid4())
    for model in args.models:
        route, body = payload(model, "Return only the requested short answer.", "Return exactly OK.", 256)
        body["stream"] = True
        if route == "chat/completions":
            body["stream_options"] = {"include_usage":True}
        write(args.output / f"{model}.request.json", body)
        request = urllib.request.Request("https://opencode.ai/zen/go/v1/"+route,
            data=json.dumps(body).encode("utf-8"),headers={"Authorization":"Bearer "+key,
            "Content-Type":"application/json","User-Agent":"MaFKG-stream-probe/1.0",
            "x-opencode-session":session},method="POST")
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request,timeout=45) as response:
                native, events = consume(response,route == "responses")
            if key in json.dumps(events):
                raise ValueError("Credential echo; not persisted")
            write(args.output / f"{model}.response.json", native)
            write(args.output / f"{model}.events.json", events)
            text = ("".join(p.get("text", "") for item in native.get("output",[]) for p in item.get("content",[])
                           if p.get("type") == "output_text") if route == "responses" else
                    native["choices"][0]["message"]["content"])
            row = {"model":model,"output":text,"ok":text.strip()=="OK","usage":native.get("usage",{}),"attempts":1}
        except Exception as exc:
            row = {"model":model,"ok":False,"error_type":type(exc).__name__,"attempts":1}
        row["elapsed_seconds"] = round(time.monotonic()-started,3)
        results.append(row)
        print(json.dumps(row),flush=True)
    write(args.output / "results.json", results)


if __name__ == "__main__":
    main()
