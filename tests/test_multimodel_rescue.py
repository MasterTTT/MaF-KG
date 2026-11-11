import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import run_multimodel_rescue as rescue


class RescueTests(unittest.TestCase):
    def test_native_payload_preserves_input(self):
        for model in rescue.MODELS:
            route, body = rescue.rescue_payload(model, 'system', 'source', 24000)
            self.assertEqual(route, 'chat/completions')
            if model == 'glm-5.3-flash':
                self.assertEqual(body['reasoning_effort'], 'none')
                self.assertNotIn('thinking', body)
            else:
                self.assertEqual(body['thinking'], {'type': 'disabled'})
                self.assertNotIn('reasoning_effort', body)
            self.assertEqual(body['max_tokens'], 24000)
            self.assertEqual(body['messages'][1]['content'], 'source')

    def test_unregistered_model_is_rejected(self):
        with self.assertRaises(ValueError):
            rescue.rescue_payload('deepseek-v4-flash', 'system', 'source', 24000)

    def test_changed_artifact_is_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source'
            path.write_text('original', encoding='utf-8')
            manifest = {'artifacts': [{'path': str(path), 'sha256': rescue.digest(path)}],
                        'frozen_source_artifacts': []}
            rescue.check(manifest)
            path.write_text('changed', encoding='utf-8')
            with self.assertRaises(ValueError):
                rescue.check(manifest)


if __name__ == '__main__':
    unittest.main()
