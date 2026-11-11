import copy
import json
import shutil
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from extraction_context_state import ContextState, SCHEMA
from run_context_state_experiment import (ARMS, PROTOCOL_FILES, install_hooks,
                                          validate_cache_paths, validate_protocol)
from run_memory_ablation import COMMON, digest, load_module
from score_context_state_experiment import score


class ContextProtocolTests(unittest.TestCase):
    def test_real_extractor_cache_write_uses_short_fingerprint(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / ("r" * 40) / "differential/structured_state/runtime"
            (runtime / "agents").mkdir(parents=True)
            shutil.copy2(root / "agents/Extractor.py", runtime / "agents/Extractor.py")
            shutil.copytree(root / "prompts", runtime / "prompts")
            module = load_module(runtime / "agents/Extractor.py")
            current = module.DocumentChunk(id="chunk_001_1_34", start_line=1, end_line=34,
                                           heading_path=["Topic"], text="1: A source statement.")
            chunks = json.loads(json.dumps([asdict(current)]))
            install_hooks(module, chunks, "structured_state", lambda row: None, lambda row: None)
            extractor = module.Extractor(input_path=Path("differential.md"), verbose=False)
            state = {**COMMON, "input_path": "differential.md", "include_exercises": False,
                     "diagnostics": {}, "raw_lines": [], "content_lines": []}
            extractor._experimental_context = ContextState(chunks)
            fingerprint = extractor._cache_fingerprint(current, state, [], [])
            self.assertEqual(len(fingerprint), 20)
            validate_cache_paths(extractor, chunks, state)
            extractor._write_chunk_cache(current.id, fingerprint, {"status": "offline_probe"}, state)
            self.assertEqual(extractor._read_chunk_cache(current.id, fingerprint, state),
                             {"status": "offline_probe"})
            self.assertLess(len(str(extractor._cache_path(current.id, fingerprint, state))), 260)

    def fixture(self, directory):
        artifacts = []
        for name in PROTOCOL_FILES:
            source = Path(__file__).resolve().parents[1] / "scripts" / name
            target = directory / "snapshot/protocol" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            artifacts.append({"snapshot": str(target), "sha256": digest(target)})
        paths = (directory / "snapshot/input/topic.md", directory / "snapshot/protocol/gold.json",
                 directory / "chunks.json", directory / "offline_templates.json",
                 directory / "structured_system_prompt.txt", directory / "snapshot/prompts/extraction_system.txt")
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
            artifacts.append({"snapshot": str(path), "sha256": digest(path)})
        arms = [{"name": name, "memory_node_bytes": 12000, "memory_relation_bytes": 6000,
                 "system_prompt": str(paths[4] if name == "structured_state" else paths[5]),
                 "system_prompt_sha256": digest(paths[4] if name == "structured_state" else paths[5]),
                 "status": "pending"} for name in ARMS]
        return {"experiment": "structured_extraction_context_state", "context_schema": SCHEMA, "context_mode": "package",
                "common": copy.deepcopy(COMMON), "input": str(paths[0]), "gold_snapshot": str(paths[1]),
                "gold_sha256": digest(paths[1]), "artifacts": artifacts,
                "arms": arms, "reading_note_enabled": False}

    def test_valid_offline_protocol_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            validate_protocol(directory, self.fixture(directory))

    def test_configuration_and_budget_drift_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            original = self.fixture(directory)
            for field, value in (("reasoning_effort", "high"), ("max_chunk_chars", 6400)):
                changed = copy.deepcopy(original)
                changed["common"][field] = value
                with self.assertRaisesRegex(ValueError, "frozen extraction configuration"):
                    validate_protocol(directory, changed)
            changed = copy.deepcopy(original)
            changed["arms"][1]["memory_node_bytes"] = 24000
            with self.assertRaisesRegex(ValueError, "Memory budget"):
                validate_protocol(directory, changed)

    def test_unbound_input_path_and_prompt_arm_swap_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            original = self.fixture(directory)
            changed = copy.deepcopy(original)
            changed["input"] = str(directory / "other.md")
            with self.assertRaisesRegex(ValueError, "not bound"):
                validate_protocol(directory, changed)
            changed = copy.deepcopy(original)
            changed["arms"][1]["system_prompt"] = changed["arms"][0]["system_prompt"]
            with self.assertRaisesRegex(ValueError, "intended context arm"):
                validate_protocol(directory, changed)

    def test_generated_notes_and_duplicate_artifacts_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            original = self.fixture(directory)
            changed = copy.deepcopy(original)
            changed["reading_note_enabled"] = True
            with self.assertRaisesRegex(ValueError, "reading notes"):
                validate_protocol(directory, changed)
            changed = copy.deepcopy(original)
            changed["artifacts"].append(copy.deepcopy(changed["artifacts"][0]))
            with self.assertRaisesRegex(ValueError, "Duplicate artifact"):
                validate_protocol(directory, changed)

    def test_scorer_refuses_unexecuted_experiment(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            manifest = self.fixture(directory)
            (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must be completed"):
                score(directory, directory / "missing_review.json")


if __name__ == "__main__":
    unittest.main()
