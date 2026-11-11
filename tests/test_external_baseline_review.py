import sys
import json
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prepare_external_baseline_review import native_items, prepare
from external_baseline_runtime import sha256


def test_native_triples_are_not_claim_generated():
    graph = [[["A", "special_case_of", "B"], None], [["C", "uses", "D"]]]
    items, drops = native_items(graph)
    assert [i["item_id"] for i in items] == ["T001_001", "T002_001"]
    assert items[0]["native"] == graph[0][0]
    assert drops == [{"section": 1, "index": 2, "value": None}]


def test_preserves_description_not_vectors():
    graph = {"nodes": [{"name": "X", "description": "under C, X implies Y", "embedding": [1]}],
             "relations": []}
    items, drops = native_items(graph)
    assert items[0]["native"]["description"] == "under C, X implies Y"
    assert "embedding" not in items[0]["native"]
    assert not drops


def experiment_fixture(tmp_path):
    run, output = tmp_path / "run", tmp_path / "review"
    snapshot, jobdir = run / "snapshot", run / "job"
    snapshot.mkdir(parents=True)
    jobdir.mkdir()
    (snapshot / "semantic_reference.json").write_text(json.dumps({"nodes": [], "relations": []}), encoding="utf-8")
    manifest = {"snapshot": str(snapshot), "jobs": [{"method": "maf", "section": "S", "repeat": 1,
                 "directory": str(jobdir), "status": "pending"}]}
    path = run / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return run, output, manifest, path, jobdir


def test_refresh_adds_completed_packet_and_preserves_judgment_bytes(tmp_path):
    run, output, manifest, path, jobdir = experiment_fixture(tmp_path)
    prepare(run, output)
    graph = jobdir / "graph.json"
    graph.write_text(json.dumps({"nodes": [], "relations": []}), encoding="utf-8")
    manifest["jobs"][0].update(status="completed", graph_sha256=sha256(graph))
    path.write_text(json.dumps(manifest), encoding="utf-8")
    prepare(run, output, refresh=True)
    review = output / "P01_review.json"
    marker = b'{"label":"P01","reviewer_provenance":"reviewed marker"}\n'
    review.write_bytes(marker)
    prepare(run, output, refresh=True)
    assert review.read_bytes() == marker
    manifest["jobs"][0]["status"] = "failed"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="terminal"):
        prepare(run, output, refresh=True)


def test_refresh_rejects_mapping_change(tmp_path):
    run, output, manifest, path, _ = experiment_fixture(tmp_path)
    prepare(run, output)
    manifest["jobs"][0]["method"] = "direct"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        prepare(run, output, refresh=True)
