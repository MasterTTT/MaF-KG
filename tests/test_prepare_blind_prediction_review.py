import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "blind_review", ROOT / "scripts/prepare_blind_prediction_review.py")
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)
ACTUAL = ROOT / "evaluation/prospective_2026-10-04/blind_prediction_review_run06"
FINAL = ROOT / "evaluation/prospective_2026-10-04/blind_prediction_review_ode5"


class BlindPredictionReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "output"
        self.source = self.root / "book.md"
        self.input = self.root / "section.md"
        self.source.write_text("outside\noriginal premise\noriginal result\noutside\n", encoding="utf-8", newline="\n")
        self.input.write_text("original premise\noriginal result\n", encoding="utf-8", newline="\n")
        self.graph = {"model": "secret-model", "source_path": "secret-path",
                      "nodes": [
                          {"id": "original-a", "type": "Claim", "name": "Formula",
                           "description": "A stated result", "logical_form": {"premises": ["P"]},
                           "evidence": [{"start_line": 1, "end_line": 2,
                                         "text": "FABRICATED QUOTE", "source_path": "secret-path",
                                         "chunk_id": "direct_001"}], "verdict": "old assistant label"},
                          {"id": "original-b", "type": "Method", "name": "Method",
                           "description": "Use the result", "evidence": []}],
                      "relations": [{"id": "edge-a", "type": "uses", "source_id": "original-b",
                                     "target_id": "original-a", "evidence": [
                                         {"start_line": 2, "end_line": 2, "text": "FALSE"}]}]}
        self.jobs = []
        for index, (model, arm) in enumerate((('secret-model', 'direct'), ('other-model', 'sequential'))):
            directory = self.root / f"graph{index}"
            directory.mkdir()
            path = directory / "graph.json"
            path.write_text(review.json_text(self.graph), encoding="utf-8", newline="\n")
            self.jobs.append({"model": model, "name": arm, "section": "s1", "repeat": 1,
                              "status": "completed", "directory": directory.name,
                              "graph_sha256": review.digest(path.read_bytes()), "nodes": 2, "relations": 1})
        self.manifest = {"status": "partial", "sections": [
            {"id": "s1", "title": "Section", "source": self.source.name,
             "source_sha256": review.digest(self.source.read_bytes()),
             "input": self.input.name, "start_line": 2, "end_line": 3}],
            "artifacts": [{"path": self.input.name, "sha256": review.digest(self.input.read_bytes())}],
            "jobs": self.jobs + [{"model": "running-model", "name": "direct", "section": "s1",
                                  "status": "running", "directory": "never-open-this"}]}
        self.manifest_path = self.root / "manifest.json"
        self.save_manifest()

    def save_manifest(self):
        self.manifest_path.write_text(review.json_text(self.manifest), encoding="utf-8", newline="\n")

    def save_graph(self, graph=None):
        path = self.root / self.jobs[0]["directory"] / "graph.json"
        path.write_text(review.json_text(graph or self.graph), encoding="utf-8", newline="\n")
        self.jobs[0]["graph_sha256"] = review.digest(path.read_bytes())
        self.save_manifest()

    def packets(self, output=None):
        return [json.loads(path.read_text(encoding="utf-8"))
                for path in sorted((output or self.output).glob("G*.json"))]

    def test_coverage_source_offsets_empty_ratings_and_privacy(self):
        result = review.prepare(self.manifest_path, self.output, 42)
        self.assertEqual(result, {"packets": 2, "nodes": 4, "relations": 2, "skipped_noncompleted_jobs": 1})
        for packet in self.packets():
            self.assertEqual(len(packet["nodes"]), 2)
            self.assertEqual(len(packet["relations"]), 1)
            span = packet["nodes"][0]["evidence"][0]
            self.assertEqual(span["local_lines"], [1, 2])
            self.assertEqual(span["chapter_lines"], [2, 3])
            self.assertEqual(span["authoritative_source"], "original premise\noriginal result")
            self.assertEqual(packet["relations"][0]["target"]["name"], "Formula")
            self.assertEqual(packet["relations"][0]["source"]["description"], "Use the result")
            self.assertEqual(packet["nodes"][0]["prediction"]["logical_form"], {"premises": ["P"]})
            self.assertIsNone(packet["nodes"][1]["prediction"]["logical_form"])
            for item in packet["nodes"] + packet["relations"]:
                self.assertTrue(all(v is None or v == "" or v == [] for v in item["ratings"].values()))
        for path in self.output.iterdir():
            if path.is_file():
                data = path.read_bytes()
                self.assertFalse(data.startswith(b"\xef\xbb\xbf"))
                self.assertNotIn(b"\r", data)
                text = data.decode("utf-8")
                for token in ("secret-model", "other-model", "running-model", "secret-path",
                              "FABRICATED", "old assistant label", "original-a", str(self.root)):
                    self.assertNotIn(token, text)
        private = json.loads((self.output / "private/unblinding_map.json").read_text(encoding="utf-8"))
        self.assertEqual({row["model"] for row in private["packets"]}, {"secret-model", "other-model"})
        self.assertEqual(private["manifest_sha256"], review.digest(self.manifest_path.read_bytes()))

    def test_seed_reproducible_independent_of_manifest_order(self):
        review.prepare(self.manifest_path, self.output, 17)
        self.manifest["jobs"].reverse()
        self.save_manifest()
        second = self.root / "second"
        review.prepare(self.manifest_path, second, 17)
        for path in self.output.glob("G*.*"):
            self.assertEqual(path.read_bytes(), (second / path.name).read_bytes())

    def test_section_input_hash_and_no_completed_jobs(self):
        self.manifest["sections"][0]["input_sha256"] = self.manifest["artifacts"][0]["sha256"]
        self.manifest["artifacts"] = []
        self.save_manifest()
        review.prepare(self.manifest_path, self.output)
        self.manifest["jobs"] = [self.manifest["jobs"][-1]]
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "No completed graphs"):
            review.prepare(self.manifest_path, self.root / "empty")
        self.assertFalse((self.root / "empty").exists())

    def test_scope_filter_does_not_inspect_other_graphs_or_select_models(self):
        self.manifest["jobs"].append({"model": "excluded-model", "name": "sequential",
                                       "section": "s2", "status": "completed",
                                       "directory": "missing-outside-scope", "graph_sha256": "0" * 64})
        self.manifest["jobs"][0]["directory"] = "missing-other-arm"
        self.save_manifest()
        result = review.prepare(self.manifest_path, self.output, section="s1", arm="sequential")
        self.assertEqual(result["packets"], 1)
        self.assertEqual(result["skipped_noncompleted_jobs"], 0)
        private = json.loads((self.output / "private/unblinding_map.json").read_text(encoding="utf-8"))
        self.assertEqual(private["filters"], {"section": "s1", "arm": "sequential"})
        self.assertEqual(private["excluded_by_scope_jobs"], 3)
        self.assertEqual(private["packets"][0]["model"], "other-model")
        with self.assertRaisesRegex(ValueError, "No completed graphs"):
            review.prepare(self.manifest_path, self.root / "no-match", section="absent")

    def test_explicit_attempt_filter_excludes_old_outputs(self):
        self.jobs[1]["attempt_number"] = 2
        self.jobs[0]["directory"] = "must-not-open-old-output"
        self.save_manifest()
        result = review.prepare(self.manifest_path, self.output, attempt_number=2)
        self.assertEqual(result["packets"], 1)
        private = json.loads((self.output / "private/unblinding_map.json").read_text(encoding="utf-8"))
        self.assertEqual(private["filters"]["attempt_number"], 2)
        self.assertEqual(private["packets"][0]["model"], "other-model")
        with self.assertRaisesRegex(ValueError, "positive"):
            review.prepare(self.manifest_path, self.root / "invalid-attempt", attempt_number=0)

    def test_locked_review_graph_hash_exclusion(self):
        excluded = self.jobs[0]["graph_sha256"]
        # The fixture graphs initially have the same bytes; give the second a
        # distinct description so only the intended prior graph is excluded.
        second = self.root / self.jobs[1]["directory"] / "graph.json"
        data = json.loads(second.read_text(encoding="utf-8"))
        data["nodes"][0]["description"] = "Another stated result"
        second.write_text(review.json_text(data), encoding="utf-8", newline="\n")
        self.jobs[1]["graph_sha256"] = review.digest(second.read_bytes())
        self.jobs[0]["directory"] = "must-not-open-reviewed"
        self.save_manifest()
        result = review.prepare(self.manifest_path, self.output, exclude_graph_hashes=(excluded,))
        self.assertEqual(result["packets"], 1)
        private = json.loads((self.output / "private/unblinding_map.json").read_text(encoding="utf-8"))
        self.assertEqual(private["filters"]["exclude_graph_hashes"], [excluded])
        with self.assertRaisesRegex(ValueError, "Invalid excluded"):
            review.prepare(self.manifest_path, self.root / "bad-hash", exclude_graph_hashes=("bad",))

    def frozen_materials(self):
        snapshot = self.root / "snapshot"
        (snapshot / "prompts").mkdir(parents=True)
        prompt = snapshot / "prompts/extraction_system.txt"
        prompt.write_bytes(b"Shared sequential pipeline.\r\nConcept Claim Method.\r\n")
        protocol = self.root / "frozen_protocol.md"
        protocol.write_text("# Frozen\n\n\u8303\u56f4\u56fa\u5b9a\uff1asecret-model other-model sequential\n\n"
                            "source_support: supported / unsupported / uncertain\n"
                            "ontology_admission: eligible / ineligible / uncertain\n"
                            "condition_completeness: complete / incomplete / not_applicable / uncertain\n"
                            "direction_correctness: correct / incorrect / uncertain\n"
                            "schema_signature: valid / invalid\n",
                            encoding="utf-8", newline="\n")
        self.manifest["snapshot"] = str(snapshot)
        self.manifest["artifacts"].append({"path": str(prompt), "sha256": review.digest(prompt.read_bytes())})
        self.save_manifest()
        return protocol, prompt

    def test_frozen_ratings_prompt_copy_and_masked_protocol(self):
        protocol, prompt = self.frozen_materials()
        review.prepare(self.manifest_path, self.output, section="s1", arm="sequential", protocol=protocol)
        packet = self.packets()[0]
        self.assertEqual(packet["status"], "pending_assistant_blind_review")
        self.assertIn("ontology_admission", packet["nodes"][0]["ratings"])
        self.assertIn("condition_completeness", packet["nodes"][0]["ratings"])
        self.assertIn("schema_signature", packet["relations"][0]["ratings"])
        self.assertNotIn("ontology_type_correctness", packet["nodes"][0]["ratings"])
        copied = (self.output / "extraction_system.txt").read_bytes()
        self.assertEqual(copied, prompt.read_bytes().replace(b"\r\n", b"\n"))
        materials = packet["review_materials"]
        self.assertEqual(materials["extractor_prompt_original_sha256"], review.digest(prompt.read_bytes()))
        self.assertEqual(materials["extractor_prompt_copy_sha256"], review.digest(copied))
        self.assertEqual(materials["protocol_original_sha256"], review.digest(protocol.read_bytes()))
        public_protocol = (self.output / "review_protocol.md").read_text(encoding="utf-8")
        self.assertNotIn("secret-model", public_protocol)
        self.assertNotIn("other-model", public_protocol)
        for line in protocol.read_text(encoding="utf-8").splitlines():
            if not line.startswith("\u8303\u56f4\u56fa\u5b9a\uff1a"):
                self.assertIn(line, public_protocol)
        self.assertEqual((self.output / "private/protocol_original.md").read_bytes(), protocol.read_bytes())

    def test_frozen_prompt_hash_failure_before_output(self):
        protocol, prompt = self.frozen_materials()
        prompt.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "extractor prompt hash mismatch"):
            review.prepare(self.manifest_path, self.output, protocol=protocol)
        self.assertFalse(self.output.exists())

    def test_hash_failures_before_output(self):
        for role in ("source", "input", "graph"):
            with self.subTest(role=role):
                manifest = copy.deepcopy(self.manifest)
                if role == "source":
                    self.manifest["sections"][0]["source_sha256"] = "0" * 64
                elif role == "input":
                    self.manifest["artifacts"][0]["sha256"] = "0" * 64
                else:
                    self.manifest["jobs"][0]["graph_sha256"] = "0" * 64
                self.save_manifest()
                with self.assertRaisesRegex(ValueError, "hash mismatch"):
                    review.prepare(self.manifest_path, self.output)
                self.assertFalse(self.output.exists())
                self.manifest = manifest
        self.save_manifest()

    def test_missing_input_hash_is_rejected(self):
        self.manifest["artifacts"] = []
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "input SHA-256"):
            review.prepare(self.manifest_path, self.output)
        self.assertFalse(self.output.exists())

    def test_hash_valid_but_wrong_source_slice_rejected(self):
        self.input.write_text("changed input\noriginal result\n", encoding="utf-8", newline="\n")
        self.manifest["artifacts"][0]["sha256"] = review.digest(self.input.read_bytes())
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "does not match"):
            review.prepare(self.manifest_path, self.output)
        self.assertFalse(self.output.exists())

    def test_bad_evidence_bounds_and_non_integer_rejected(self):
        for start, end in ((0, 1), (2, 1), (1, 3), (True, 1), ("1", 2)):
            with self.subTest(start=start, end=end):
                graph = copy.deepcopy(self.graph)
                graph["relations"][0]["evidence"][0].update(start_line=start, end_line=end)
                self.save_graph(graph)
                with self.assertRaises(ValueError):
                    review.prepare(self.manifest_path, self.output)
                self.assertFalse(self.output.exists())

    def test_missing_endpoint_and_duplicate_node_ids_rejected(self):
        for kind in ("endpoint", "duplicate"):
            with self.subTest(kind=kind):
                graph = copy.deepcopy(self.graph)
                if kind == "endpoint":
                    graph["relations"][0]["target_id"] = "missing"
                else:
                    graph["nodes"][1]["id"] = graph["nodes"][0]["id"]
                self.save_graph(graph)
                with self.assertRaises(ValueError):
                    review.prepare(self.manifest_path, self.output)
                self.assertFalse(self.output.exists())

    def test_duplicate_edges_preserved(self):
        graph = copy.deepcopy(self.graph)
        graph["relations"].append(copy.deepcopy(graph["relations"][0]))
        self.jobs[0]["relations"] = 2
        self.save_graph(graph)
        review.prepare(self.manifest_path, self.output)
        self.assertEqual(sorted(len(p["relations"]) for p in self.packets()), [1, 2])

    def test_existing_output_and_embedded_identity_fail_closed(self):
        graph = copy.deepcopy(self.graph)
        graph["nodes"][0]["description"] = "secret-model prediction"
        self.save_graph(graph)
        with self.assertRaisesRegex(ValueError, "Identity label"):
            review.prepare(self.manifest_path, self.output)
        self.assertFalse(self.output.exists())
        self.save_graph()
        review.prepare(self.manifest_path, self.output)
        before = (self.output / "G01.json").read_bytes()
        with self.assertRaises(FileExistsError):
            review.prepare(self.manifest_path, self.output)
        self.assertEqual(before, (self.output / "G01.json").read_bytes())

    def test_embedded_graph_path_fails_closed(self):
        graph = copy.deepcopy(self.graph)
        graph["nodes"][0]["description"] = str(self.root / self.jobs[0]["directory"] / "graph.json")
        self.save_graph(graph)
        with self.assertRaisesRegex(ValueError, "Private input/graph path"):
            review.prepare(self.manifest_path, self.output)
        self.assertFalse(self.output.exists())

    def test_section_bounds_and_manifest_count_rejected(self):
        self.manifest["sections"][0]["end_line"] = 99
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "Section line bounds"):
            review.prepare(self.manifest_path, self.output)
        self.manifest["sections"][0]["end_line"] = 3
        self.jobs[0]["nodes"] = 99
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "count mismatch"):
            review.prepare(self.manifest_path, self.output)
        self.assertFalse(self.output.exists())

    def test_markdown_fences_cannot_be_closed_by_prediction(self):
        graph = copy.deepcopy(self.graph)
        graph["nodes"][0]["description"] = "```\ncontent\n```"
        self.save_graph(graph)
        review.prepare(self.manifest_path, self.output)
        markdown = (self.output / "G01.md").read_text(encoding="utf-8")
        self.assertIn("````json", markdown)


@unittest.skipUnless(ACTUAL.exists(), "Local generated run06 packets not installed")
class ActualRun06PacketTests(unittest.TestCase):
    def test_every_actual_prediction_hash_endpoint_and_source_span(self):
        private = json.loads((ACTUAL / "private/unblinding_map.json").read_text(encoding="utf-8"))
        manifest_path = Path(private["manifest_path"])
        manifest_bytes = manifest_path.read_bytes()
        self.assertEqual(review.digest(manifest_bytes), private["manifest_sha256"])
        # The original packet retains its generator version across later script edits.
        self.assertRegex(private["generator_sha256"], r"^[0-9a-f]{64}$")
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        completed = [row for row in manifest["jobs"] if row["status"] == "completed"]
        self.assertEqual(len(private["packets"]), len(completed))
        graph_paths = {str((Path(row["directory"]) / "graph.json").resolve()) for row in completed}
        self.assertEqual({row["graph_path"] for row in private["packets"]}, graph_paths)
        totals = [0, 0]
        for row in private["packets"]:
            graph_bytes = Path(row["graph_path"]).read_bytes()
            self.assertEqual(review.digest(graph_bytes), row["graph_sha256"])
            graph = json.loads(graph_bytes.decode("utf-8"))
            packet = json.loads((ACTUAL / (row["packet_id"] + ".json")).read_text(encoding="utf-8"))
            self.assertEqual(len(packet["nodes"]), len(graph["nodes"]))
            self.assertEqual(len(packet["relations"]), len(graph["relations"]))
            totals[0] += len(graph["nodes"])
            totals[1] += len(graph["relations"])
            original_bytes = Path(row["source_path"]).read_bytes()
            self.assertEqual(review.digest(original_bytes), row["source_sha256"])
            self.assertEqual(review.digest(Path(row["input_path"]).read_bytes()), row["input_sha256"])
            original = original_bytes.decode("utf-8").splitlines()
            node_ids = {n["review_id"] for n in packet["nodes"]}
            by_original = {n["id"]: n for n in graph["nodes"]}
            for predicted, blinded in zip(graph["nodes"], packet["nodes"]):
                for field in review.NODE_FIELDS:
                    self.assertEqual(predicted.get(field), blinded["prediction"][field])
            for predicted, blinded in zip(graph["relations"], packet["relations"]):
                self.assertEqual(predicted["type"], blinded["type"])
                for side in ("source", "target"):
                    self.assertIn(blinded[side]["review_id"], node_ids)
                    endpoint = by_original[predicted[side + "_id"]]
                    for field in ("type", "name", "description"):
                        self.assertEqual(endpoint.get(field), blinded[side][field])
            for item in packet["nodes"] + packet["relations"]:
                self.assertTrue(all(v is None or v == "" or v == [] for v in item["ratings"].values()))
                for span in item["evidence"]:
                    first, last = span["chapter_lines"]
                    self.assertEqual(span["authoritative_source"], "\n".join(original[first - 1:last]))
                    self.assertEqual(first, span["local_lines"][0] + row["chapter_lines"][0] - 1)
            labels = {job["model"] for job in manifest["jobs"]} | {job["name"] for job in manifest["jobs"]}
            paths = {r["graph_path"] for r in private["packets"]}
            review.assert_blinded(packet, labels, paths)
        self.assertEqual(totals, [164, 124])

    def test_actual_output_encoding_and_protocol(self):
        for path in ACTUAL.rglob("*"):
            if path.is_file():
                data = path.read_bytes()
                text = data.decode("utf-8", errors="strict")
                self.assertFalse(data.startswith(b"\xef\xbb\xbf"))
                self.assertNotIn("\r", text)
                self.assertNotIn("\ufffd", text)
        protocol = (ACTUAL / "review_protocol.md").read_text(encoding="utf-8")
        self.assertIn("Do not compute F1", protocol)
        self.assertIn("pending human review", protocol)
        self.assertIn("type uncertain", " ".join(protocol.split()))


@unittest.skipUnless(FINAL.exists(), "Local final ODE packet not installed")
class FinalOdePacketTests(unittest.TestCase):
    def test_final_scope_counts_frozen_materials_and_empty_ratings(self):
        private = json.loads((FINAL / "private/unblinding_map.json").read_text(encoding="utf-8"))
        manifest_bytes = Path(private["manifest_path"]).read_bytes()
        self.assertEqual(private["manifest_sha256"], review.digest(manifest_bytes))
        self.assertEqual(private["filters"], {"section": "ch7_s5", "arm": "sequential"})
        self.assertEqual((private["manifest_attempted_jobs"], private["manifest_completed_jobs"],
                          private["manifest_failed_jobs"]), (36, 22, 14))
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        selected = [job for job in manifest["jobs"] if job["section"] == "ch7_s5"
                    and job["name"] == "sequential" and job["status"] == "completed"]
        self.assertEqual(len(private["packets"]), len(selected))
        self.assertEqual(len(selected), 5)
        self.assertEqual({p["graph_path"] for p in private["packets"]},
                         {str((Path(job["directory"]) / "graph.json").resolve()) for job in selected})
        protocol_path = ROOT / "evaluation/prospective_2026-10-04/prediction_validity_protocol_v1.md"
        self.assertEqual(private["review_materials"]["protocol_sha256"], review.digest(protocol_path.read_bytes()))
        self.assertEqual((FINAL / "private/protocol_original.md").read_bytes(), protocol_path.read_bytes())
        prompt = Path(private["review_materials"]["extractor_prompt_path"]).read_bytes()
        copied = (FINAL / "extraction_system.txt").read_bytes()
        self.assertEqual(copied, prompt.replace(b"\r\n", b"\n"))
        self.assertEqual(private["review_materials"]["extractor_prompt_sha256"], review.digest(prompt))
        # This historical packet locks the generator version at creation, not
        # the evolving current source used for later review batches.
        self.assertEqual(private["generator_sha256"],
                         "91dd6a73b3c89355cc85e9ea0eb7e95ac5e280e8a12a1ddfed9ae514e2efa414")
        total_nodes, total_relations = 0, 0
        labels = {job["model"] for job in manifest["jobs"]} | {job["name"] for job in manifest["jobs"]}
        paths = {p["graph_path"] for p in private["packets"]}
        for row in private["packets"]:
            graph_bytes = Path(row["graph_path"]).read_bytes()
            self.assertEqual(review.digest(graph_bytes), row["graph_sha256"])
            graph = json.loads(graph_bytes.decode("utf-8"))
            packet = json.loads((FINAL / (row["packet_id"] + ".json")).read_text(encoding="utf-8"))
            total_nodes += len(packet["nodes"])
            total_relations += len(packet["relations"])
            self.assertEqual((len(packet["nodes"]), len(packet["relations"])),
                             (len(graph["nodes"]), len(graph["relations"])))
            review.assert_blinded(packet, labels, paths)
            original_bytes = Path(row["source_path"]).read_bytes()
            original = original_bytes.decode("utf-8").splitlines()
            self.assertEqual(review.digest(original_bytes), row["source_sha256"])
            self.assertEqual(review.digest(Path(row["input_path"]).read_bytes()), row["input_sha256"])
            for item in packet["nodes"] + packet["relations"]:
                self.assertTrue(all(v is None or v == [] or v == "" for v in item["ratings"].values()))
                for span in item["evidence"]:
                    start, end = span["chapter_lines"]
                    self.assertEqual(span["authoritative_source"], "\n".join(original[start - 1:end]))
            self.assertEqual(packet["review_materials"]["protocol_original_sha256"], review.digest(protocol_path.read_bytes()))
            self.assertEqual(packet["review_materials"]["extractor_prompt_copy_sha256"], review.digest(copied))
        self.assertEqual((total_nodes, total_relations), (44, 30))
        for path in FINAL.rglob("*"):
            if path.is_file():
                data = path.read_bytes()
                text = data.decode("utf-8", errors="strict")
                self.assertNotIn("\r", text)
                self.assertNotIn("\ufffd", text)
                self.assertFalse(data.startswith(b"\xef\xbb\xbf"))


if __name__ == "__main__":
    unittest.main()
