import sys
import json
import shutil
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_direct_extraction_baseline import (
    numbered_lines, shared_evidence, reference_reachability, direct_chunk, install_hooks, audit_job,
)
from run_memory_ablation import COMMON, load_module, write_json


class DirectBaselineTests(unittest.TestCase):
    def test_union_preserves_gaps_and_source(self):
        result = shared_evidence([{"text": "1: a\n3: c"}, {"text": "3: c\n4: d"}], ["a", "b", "c", "d"])
        self.assertEqual(result, {1: "a", 3: "c", 4: "d"})

    def test_unmapped_source_rejected(self):
        with self.assertRaises(ValueError):
            shared_evidence([{"text": "1: altered"}], ["original"])

    def test_duplicate_line_rejected(self):
        with self.assertRaises(ValueError):
            numbered_lines("1: a\n1: a")

    def test_unnumbered_text_rejected(self):
        with self.assertRaises(ValueError):
            numbered_lines("1: a\nexplanation")

    def test_reachability_uses_original_line_mapping(self):
        reference = {"nodes": [{"id": "n", "section": "s", "status": "M", "evidence": [10, 12]}]}
        section = {"id": "s", "start_line": 10}
        result = reference_reachability(reference, section, {1: "a"}, ["a", "", "c"])
        self.assertEqual(result[0]["input_status"], "partial")
        self.assertEqual(result[0]["missing_nonempty_original_lines"], [12])

    def test_frozen_direct_worker_roundtrip_without_network(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp, patch.dict("os.environ", {"DEEPSEEK_API_KEY": "offline-test"}):
            folder = Path(temp)
            runtime = folder / "rt"
            shutil.copytree(root / "agents", runtime / "agents", ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copytree(root / "prompts", runtime / "prompts")
            source = folder / "input.md"
            source.write_text("# Topic\n\nA paragraph.\n", encoding="utf-8")
            module = load_module(runtime / "agents/Extractor.py")
            extractor = module.Extractor(input_path=source, verbose=False)
            state = extractor._chunk_document(extractor._load_document(
                {**COMMON, "input_path": str(source), "diagnostics": {}}))
            chunks = [asdict(c) for c in state["chunks"]]
            once = direct_chunk(module, chunks, state["raw_lines"])
            plan = folder / "plan"
            plan.mkdir()
            write_json(plan / "chunks.json", chunks)
            write_json(plan / "direct_chunk.json", asdict(once))
            write_json(plan / "templates.json", {"direct": [json.loads(
                extractor._chunk_extraction_prompt(once, str(source), [], []))]})
            section = {"id": "test", "plan_dir": str(plan), "input": str(source)}
            job = {"name": "direct", "section": "test", "directory": str(folder)}
            for name in ("requests", "responses", "raw"):
                (folder / name).mkdir()
            module.ProviderChatClient.complete_json = lambda *args, **kwargs: {
                "node_actions": [], "relations": [], "expressions": [], "notes": []}
            install_hooks(module, section, job, folder)
            settings = {**COMMON, "request_delay": 0}
            extractor = module.Extractor(input_path=source, output_path=folder / "graph.json",
                                        resume=False, no_proxy=True, verbose=False, **settings)
            result = extractor.run()
            self.assertEqual(result.nodes, [])
            report = audit_job({"sections": [section], "snapshot": str(root)}, job)
            self.assertTrue(report["protocol_passed"])
            self.assertEqual(report["chunks"], 1)


if __name__ == "__main__":
    unittest.main()
