"""Prepare an explicitly source-reviewed permutation, without modifying source KG."""
import argparse
import hashlib
import json
from pathlib import Path


def reorder(lines, spans):
    order = []
    for start, end in spans:
        if not 1 <= start <= end <= len(lines):
            raise ValueError("Invalid source span")
        order.extend(range(start, end + 1))
    if sorted(order) != list(range(1, len(lines) + 1)):
        raise ValueError("Permutation must contain every line exactly once")
    return [lines[i-1] for i in order], order


def preview(source, output):
    original = source.read_bytes()
    lines = original.splitlines(keepends=True)
    # Printed pages 7-8 followed 9-10 in the original scan. This is a reviewed
    # local correction, not a generic page-order detector or a production patch.
    spans = [(1, 240), (333, 420), (241, 332), (421, len(lines))]
    corrected, order = reorder(lines, spans)
    data = b"".join(corrected)
    data.decode("utf-8-sig", errors="strict")
    output.mkdir(parents=True, exist_ok=False)
    target = output / "第一章第一节_页序校正预览.md"
    target.write_bytes(data)
    inverse = {old: new for new, old in enumerate(order, 1)}
    restored = b"".join(corrected[inverse[i]-1] for i in range(1, len(lines)+1))
    if restored != original:
        raise ValueError("Correction is not byte-reversible")
    manifest = {"status": "offline_preview_not_deployed", "source": str(source), "output": str(target),
        "source_sha256": hashlib.sha256(original).hexdigest(), "output_sha256": hashlib.sha256(data).hexdigest(),
        "line_count": len(lines), "source_bom": original.startswith(b"\xef\xbb\xbf"),
        "source_crlf_count": original.count(b"\r\n"), "source_lf_count": original.count(b"\n"),
        "source_spans_in_corrected_order": spans, "byte_reversible": True,
        "text_changed": False, "line_order_changed": True, "graphs_changed": False,
        "new_to_old": order, "old_to_new": inverse,
        "warning": "Old evidence line numbers and persisted chunk spans must not be used on the corrected preview."}
    (output / "line_mapping.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if source.read_bytes() != original:
        raise ValueError("Original source changed")
    print("Preview", target, "lines", len(lines), "byte-reversible; original unchanged")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source", type=Path)
    p.add_argument("output", type=Path)
    a = p.parse_args()
    preview(a.source.resolve(), a.output.resolve())
