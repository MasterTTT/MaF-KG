import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "evaluation/human_quality_review_2026-10-04/run01"


class ReviewPacketTests(unittest.TestCase):
    def test_complete_prediction_coverage(self):
        mapping = json.loads((PACK / "private/mapping.json").read_text(encoding="utf-8"))
        self.assertEqual(len(mapping["packets"]), 12)
        counts = [0, 0]
        for row in mapping["packets"]:
            packet = json.loads((PACK / (row["packet_id"] + ".json")).read_text(encoding="utf-8"))
            graph = json.loads(Path(row["graph_path"]).read_text(encoding="utf-8"))
            self.assertEqual(len(packet["nodes"]), len(graph["nodes"]))
            self.assertEqual(len(packet["relations"]), len(graph["relations"]))
            counts[0] += len(packet["nodes"])
            counts[1] += len(packet["relations"])
        self.assertEqual(counts, [93, 106])

    def test_labels_and_old_verdicts_absent(self):
        for path in PACK.glob("P*.json"):
            packet = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("arm", packet)
            self.assertNotIn("repeat", packet)
            for item in packet["nodes"] + packet["relations"]:
                self.assertNotIn("id", item["prediction"])
                self.assertNotIn("verdict", item)
                self.assertTrue(all(v in (None, "", []) for v in item["decision"].values()))

    def test_endpoint_and_source_mapping(self):
        for path in PACK.glob("P*.json"):
            packet = json.loads(path.read_text(encoding="utf-8"))
            ids = {n["review_id"] for n in packet["nodes"]}
            for relation in packet["relations"]:
                self.assertIn(relation["source"], ids)
                self.assertIn(relation["target"], ids)
            for item in packet["nodes"] + packet["relations"]:
                for evidence in item["evidence"]:
                    local = evidence["local_lines"]
                    chapter = evidence["chapter_lines"]
                    self.assertEqual(chapter[1] - chapter[0], local[1] - local[0])
                    self.assertIsInstance(evidence["actual_source"], str)


if __name__ == "__main__":
    unittest.main()
