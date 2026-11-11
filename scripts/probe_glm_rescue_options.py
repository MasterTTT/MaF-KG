"""Bounded option probes; preserve native evidence, never log credentials."""
import argparse
from pathlib import Path
from types import SimpleNamespace
import prospective_native_client as native
from run_multimodel_rescue import credential

parser = argparse.ArgumentParser()
parser.add_argument('--extended', action='store_true')
args = parser.parse_args()
credential()
root = Path(__file__).resolve().parents[1] / ('evaluation/prospective_2026-10-04/glm_rescue_option_probes2'
                                             if args.extended else 'evaluation/prospective_2026-10-04/glm_rescue_option_probes')
original = native.payload
options = ({'chat_template_kwargs': {'enable_thinking': False}},
           {'reasoning': {'enabled': False}}, {'response_format': {'type': 'json_object'}}) if args.extended else (
               {'reasoning_effort': 'none'}, {'reasoning_effort': 'low'}, {'enable_thinking': False})
for index, option in enumerate(options, 1):
    folder = root / f'probe{index}'
    folder.mkdir(parents=True, exist_ok=False)
    def payload(model, system, user, limit):
        route, body = original(model, system, user, limit)
        body.update(option)
        return route, body
    native.payload = payload
    class Client:
        max_tokens = 512
        timeout = 90
        def _execute_request(self, request, *args):
            return request()
    module = SimpleNamespace(ProviderChatClient=Client)
    native.install(module, 'glm-5.3-flash', folder, root / 'budget.json',
                   'glm-rescue-options', usd_limit=0.05, token_limit=30000)
    try:
        result = Client().complete_json('Reply concisely in valid JSON.', 'Reply only {"ok":true}.', 'probe')
        choice = result['choices'][0]
        print(option, choice['finish_reason'], 'content_chars', len(choice['message'].get('content') or ''), flush=True)
    except Exception as error:
        print(option, type(error).__name__, str(error), flush=True)
