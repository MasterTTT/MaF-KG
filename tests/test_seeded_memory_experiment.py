import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_seeded_memory_experiment import audit_artifacts, audit_seed, write


class SeededMemoryTests(unittest.TestCase):
    def seed(self):
        return {"nodes": [{"id": "a", "name": "原函数", "evidence": [
            {"source_path": "E:\\MaF-KG\\data\\raw\\第四章_全章_无习题.md"}]}], "relations": []}

    def test_seed_accepts_prefix(self):
        self.assertEqual(audit_seed(self.seed())["nodes"], 1)

    def test_future_relation_evidence_rejected(self):
        graph = self.seed()
        graph["relations"] = [{"source_id": "a", "target_id": "a", "evidence": [
            {"source_path": "E:/MaF-KG/data/raw/第五章_全章_无习题.md"}]}]
        with self.assertRaisesRegex(ValueError, "non-prefix"):
            audit_seed(graph)

    def test_missing_endpoint_rejected(self):
        graph = self.seed()
        graph["relations"] = [{"source_id": "a", "target_id": "absent"}]
        with self.assertRaisesRegex(ValueError, "endpoint"):
            audit_seed(graph)

    def test_frozen_change_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "中文.json"
            write(path, {"name": "原函数"})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["name"], "原函数")
            with self.assertRaisesRegex(ValueError, "changed"):
                audit_artifacts({"artifacts": [{"snapshot": str(path), "sha256": "incorrect"}]})


if __name__ == "__main__":
    unittest.main()
