import json
import importlib.util
import math
import subprocess
import sys
import tempfile
import textwrap
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import external_itext2kg as adapter


ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "evaluation/external_baselines_2026-10-08/vendor/itext2kg"
REQUIRED_REAL_MODULES = ("numpy", "sklearn", "pydantic", "langchain_core")


def _has_module(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


HAS_REAL_RUNTIME = all(_has_module(name) for name in REQUIRED_REAL_MODULES)


class _Array:
    def __init__(self, values):
        self.values = values.tolist() if hasattr(values, "tolist") else list(values)

    def reshape(self, rows, columns):
        if (rows, columns) != (1, -1):
            raise AssertionError("Unexpected array reshape in original matcher")
        return self

    def tolist(self):
        return list(self.values)

    def __iter__(self):
        return iter(self.values)


def _cosine_similarity(left, right):
    a, b = left.values, right.values
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    score = dot / (norm_a * norm_b) if norm_a and norm_b else 0.0
    return [[score]]


def _dependency_modules():
    numpy = types.ModuleType("numpy")
    numpy.array = _Array
    sklearn = types.ModuleType("sklearn")
    sklearn.__path__ = []
    metrics = types.ModuleType("sklearn.metrics")
    metrics.__path__ = []
    pairwise = types.ModuleType("sklearn.metrics.pairwise")
    pairwise.cosine_similarity = _cosine_similarity
    return {
        "numpy": numpy,
        "sklearn": sklearn,
        "sklearn.metrics": metrics,
        "sklearn.metrics.pairwise": pairwise,
    }


def _embed(texts):
    return [[1.0, 0.0] for _ in texts]


class TestExternalIText2KG(unittest.TestCase):
    def test_explicit_guard_retains_isolated_entities_and_processes_next_section(self):
        calls = []

        def client(messages, label):
            calls.append(label)
            if ".entities." in label:
                name = "A" if sum(".entities." in c for c in calls) == 1 else "B"
                return json.dumps({"entities": [{"label": "Concept", "name": name}]})
            return '{"relationships":[]}'

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "guarded"
            result = adapter.run(VENDOR, ["A", "B"], client, _embed, output, no_progress_limit=3)
            trace = json.loads((output / "call_trace.json").read_text(encoding="utf-8"))
            self.assertEqual(sum(".entities." in c for c in calls), 2)
            self.assertEqual(len(calls), 10)
            self.assertEqual(result["relationships"], [])
            self.assertTrue(result["entities"])
            self.assertIn("progress-bound-3", result["method"])
            self.assertEqual([e["section_index"] for e in trace["no_progress_exits"]], [0, 1])

    def test_source_latex_braces_survive_template_interpolation(self):
        sent = []

        def client(messages, label):
            sent.append(messages[0]["content"])
            if ".entities." in label:
                return '{"entities":[{"label":"Concept","name":"A"},{"label":"Concept","name":"B"}]}'
            return '{"relationships":[{"startNode":"a","endNode":"b","name":"uses"}]}'

        with tempfile.TemporaryDirectory() as temporary:
            adapter.run(VENDOR, [r"x_{0} \\frac{a}{b}: A uses B."], client, _embed, Path(temporary) / "braces")
        assert r"x_{0} \\frac{a}{b}" in sent[0]

    def setUp(self):
        dependency_patch = patch.dict(sys.modules, _dependency_modules())
        dependency_patch.start()
        self.addCleanup(dependency_patch.stop)

    def test_runs_pinned_graph_with_text_only_injected_backends(self):
        sent = []

        def client(messages, label, **kwargs):
            self.assertEqual(kwargs, {})
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0]["role"], "user")
            sent.append((messages, label))
            if ".entities." in label:
                return json.dumps({"entities": [{"label": "Concept", "name": "Convergent sequence"}]})
            return json.dumps({"relationships": [{
                "startNode": "convergent sequence", "endNode": "bounded sequence", "name": "implies"
            }]})

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "run"
            result = adapter.run(
                VENDOR,
                ["A convergent sequence is bounded."],
                client,
                _embed,
                output,
                embedding_model_id="unit-test-vector-backend",
            )

            self.assertEqual(len(sent), 2)
            self.assertIn("Context: A convergent sequence is bounded.", sent[0][0][0]["content"])
            self.assertIn("Format_instructions", sent[0][0][0]["content"])
            self.assertTrue(sent[0][1].startswith("itext2kg.entities.call-"))
            self.assertEqual(result["upstream_commit"], adapter.PINNED_COMMIT)
            self.assertEqual(result["entities"][0]["name"], "convergent sequence")
            self.assertEqual(result["relationships"], [{
                "startNode": "convergent sequence",
                "endNode": "convergent sequence",
                "name": "implies",
                "properties": {"embeddings": [1.0, 0.0]},
            }])
            json.dumps(result)

            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            trace = json.loads((output / "call_trace.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "succeeded")
            self.assertEqual(manifest["entity_threshold"], 0.7)
            self.assertEqual(manifest["relationship_threshold"], 0.7)
            self.assertEqual(manifest["isolated_entity_match_threshold"], 0.5)
            self.assertEqual(manifest["embedding_model_id"], "unit-test-vector-backend")
            self.assertEqual(len(trace["chat_calls"]), 2)
            self.assertTrue(any(call["texts"] == ["bounded sequence"] for call in trace["embedding_calls"]))

    def test_original_isolated_entity_loop_stops_at_explicit_call_ceiling(self):
        def client(messages, label):
            if ".entities." in label:
                return '{"entities":[{"label":"Concept","name":"Unlinked concept"}]}'
            return '{"relationships":[]}'

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "loop"
            with self.assertRaises(adapter.ChatCallLimitReached):
                adapter.run(
                    VENDOR,
                    ["A standalone concept."],
                    client,
                    _embed,
                    output,
                    max_chat_calls=3,
                )

            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            trace = json.loads((output / "call_trace.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "failed")
            self.assertEqual(manifest["error_type"], "ChatCallLimitReached")
            self.assertEqual(len(trace["chat_calls"]), 3)
            self.assertEqual(trace["gate"]["status"], "max_chat_calls_reached")
            self.assertTrue(any(call.get("skipped_empty_batch") for call in trace["embedding_calls"]))

    def test_invalid_json_contract_fails_without_recursive_repair(self):
        calls = []

        def client(messages, label):
            calls.append(label)
            return '{"unexpected":[]}'

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "invalid"
            with self.assertRaises(adapter.OutputContractError):
                adapter.run(VENDOR, ["Text."], client, _embed, output)

            self.assertEqual(len(calls), 1)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["error_type"], "OutputContractError")

    def test_transport_gate_is_propagated_without_retry(self):
        class TransportGate(Exception):
            pass

        calls = []

        def client(messages, label):
            calls.append(label)
            raise TransportGate("budget reached")

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "gated"
            with self.assertRaisesRegex(TransportGate, "budget reached"):
                adapter.run(VENDOR, ["Text."], client, _embed, output)

            self.assertEqual(len(calls), 1)
            trace = json.loads((output / "call_trace.json").read_text(encoding="utf-8"))
            self.assertEqual(trace["chat_calls"][0]["status"], "failed")
            self.assertEqual(trace["chat_calls"][0]["error_type"], "TransportGate")

    def test_rejects_empty_sections_and_existing_artifacts(self):
        with self.assertRaisesRegex(ValueError, "non-empty list"):
            adapter.run(VENDOR, [], lambda messages, label: "{}", _embed, Path("unused"))

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "existing"
            output.mkdir()
            target = output / "result.json"
            target.write_text("preserve", encoding="utf-8")
            with self.assertRaisesRegex(FileExistsError, "Refusing to overwrite"):
                adapter.run(VENDOR, ["Text."], lambda messages, label: "{}", _embed, output)
            self.assertEqual(target.read_text(encoding="utf-8"), "preserve")


@unittest.skipUnless(HAS_REAL_RUNTIME, "requires real NumPy, scikit-learn, Pydantic, and langchain-core")
class TestExternalIText2KGRealDependencies(unittest.TestCase):
    def test_original_matcher_runs_in_fresh_process_with_real_dependencies(self):
        fixture = textwrap.dedent(
            r"""
            import sys
            from pathlib import Path

            root, output = Path(sys.argv[1]), Path(sys.argv[2])
            sys.path.insert(0, str(root / "scripts"))
            from external_itext2kg import run

            vendor = root / "evaluation/external_baselines_2026-10-08/vendor/itext2kg"
            calls = []

            def client(messages, label):
                calls.append(label)
                if ".entities." in label:
                    return '{"entities":[{"label":"Concept","name":"Convergent sequence"}]}'
                return ('{"relationships":[{"startNode":"convergent sequence",'
                        '"endNode":"bounded sequence","name":"implies"}]}')

            def embed(texts):
                return [[1.0, 0.0] for _ in texts]

            graph = run(vendor, ["A convergent sequence is bounded."], client, embed, output)
            assert len(calls) == 2, calls
            assert graph["relationships"][0]["endNode"] == "convergent sequence", graph
            assert graph["relationships"][0]["properties"]["embeddings"] == [1.0, 0.0], graph
            print("REAL_MATCHER_OK")
            """
        )
        with tempfile.TemporaryDirectory() as temporary:
            completed = subprocess.run(
                [sys.executable, "-X", "utf8", "-c", fixture, str(ROOT), str(Path(temporary) / "run")],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=90,
            )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("REAL_MATCHER_OK", completed.stdout)


if __name__ == "__main__":
    unittest.main()
