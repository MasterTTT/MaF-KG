# -*- coding: utf-8 -*-
"""P1-C C1: a KST-style behavioural probe of the prerequisite structure we extracted.

What this measures (Cui/Do/Sachan, *Do LLMs Exhibit Coherent Knowledge Structures in Mathematical
Reasoning?*, arXiv 2609.05245, NB1): for every target item the model answers correctly, are its
prerequisite items also answered correctly? Formally

    PSR(t) = |{p in Pre(t) : probe(p) correct}| / |Pre(t)|,  macro-PSR = mean over correct targets,

with the prerequisite order taken from the frozen reference graph and two controls: a
textbook-order order (the nearest preceding core item) and a seeded random order.

Design decisions that keep this honest and cheap:

* Probes are closed-book: the model is asked to state an item by name, with no source text. Nothing
  in the prompt reveals the prerequisite structure or the reference.
* Keys are extracted mechanically from the frozen `meaning` field (the name plus clauses that contain a
  mathematical relation), frozen before any probe is sent. Scoring is surface-form substring presence,
  which is auditable but brittle: it is a key-hit test, not a semantic judgement. The raw hit data is
  reported so a reader can see exactly what failed.
* The prerequisite orders are declared here, before the run, and never adjusted afterwards.

Usage:
  python analysis/kst_probe_prepare.py prepare
  python analysis/kst_probe_prepare.py run
  python analysis/kst_probe_prepare.py score
"""
import argparse
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
MERGED = ROOT / 'evaluation' / 'external_baselines_merged_2026-10-09'
OUT = ROOT / 'evaluation' / 'paper_closeout_2026-10-10' / 'kst_probe'
LEDGER = OUT / 'budgets' / 'probe.json'
sys.path.insert(0, str(MERGED / 'snapshot' / 'scripts'))
sys.stdout.reconfigure(encoding='utf-8')

from prospective_native_client import Budget, payload, write  # noqa: E402

MODEL = 'deepseek-v4-flash'
ENDPOINT = 'https://opencode.ai/zen/go/v1/chat/completions'
USD_LIMIT = 0.30
TOKEN_LIMIT = 400_000
MAX_TOKENS = 4000
REPEATS = 2
PROBE_COUNT = 24
SEED = 20261010
PREREQ_TYPES = ('uses', 'derived_from', 'defined_in_terms_of', 'special_case_of')
MATH_OPERATORS = ('=', '≤', '≥', '≠', '<', '>', '→', '≈', '∈', '⊂')
# Formula-level keys: maximal runs without Chinese characters that carry a relation or an arrow. The first
# key rule (whole Chinese clauses) scored 0 correct targets in every order, i.e. it measured the rule rather
# than the model; both rules are reported and the raw answers are unchanged.
FORMULA = re.compile(r'[^，。；、,;：:！？!?\u4e00-\u9fff\s]{3,60}')
CN = re.compile(r'[\u4e00-\u9fff]')
SYSTEM = ('你是数学教材知识测评的答题者。只依据你自己的数学知识作答，不要索要原文，不要输出与题目无关的内容。'
          '用简洁的中文与公式陈述，控制在 200 字以内。')


def norm(text: str) -> str:
    """Canonical form for the key-hit test: LaTeX and plain notation are mapped to one shape.

    The reference `meaning` text writes mathematics in Unicode (`Δx`, `x0`) while the model answers in
    LaTeX (`\\Delta x`, `x_0`). Comparing the raw strings measures the notation system, not the content —
    the first two scoring passes returned 0 correct targets for exactly that reason. Both sides are
    therefore canonicalised: LaTeX commands become Unicode symbols, fractions become `(a)/(b)`, braces and
    subscripts are flattened, and spaces plus punctuation are removed. The rule is frozen before it is
    applied and no answer text is edited.
    """
    value = str(text)
    for command, symbol in sorted(LATEX.items(), key=lambda item: -len(item[0])):
        value = value.replace(command, symbol)
    value = re.sub(r'\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}', r'(\1)/(\2)', value)
    value = value.replace('\\', '')
    value = re.sub(r'_\{([^{}]*)\}', r'\1', value)
    value = re.sub(r'_([A-Za-z0-9])', r'\1', value)
    value = re.sub(r'[\s${}\[\]()，,。;；:：、]+', '', value)
    return value.replace('（', '').replace('）', '')


LATEX = {
    r'\Delta': 'Δ', r'\delta': 'δ', r'\alpha': 'α', r'\beta': 'β', r'\gamma': 'γ', r'\theta': 'θ',
    r'\varphi': 'φ', r'\phi': 'φ', r'\psi': 'ψ', r'\omega': 'ω', r'\lambda': 'λ', r'\mu': 'μ',
    r'\pi': 'π', r'\sigma': 'σ', r'\xi': 'ξ', r'\eta': 'η', r'\epsilon': 'ε', r'\varepsilon': 'ε',
    r'\to': '→', r'\rightarrow': '→', r'\le': '≤', r'\leq': '≤', r'\ge': '≥', r'\geq': '≥',
    r'\ne': '≠', r'\neq': '≠', r'\approx': '≈', r'\cdot': '·', r'\times': '×', r'\infty': '∞',
    r'\pm': '±', r'\in': '∈', r'\subset': '⊂', r'\prime': "'", r'\lim': 'lim', r'\quad': '',
    r'\qquad': '', r'\mathrm': '', r'\left': '', r'\right': '', r'\displaystyle': '',
    r'\ln': 'ln', r'\sin': 'sin', r'\cos': 'cos', r'\tan': 'tan', r'\sec': 'sec', r'\cot': 'cot',
    r'\arcsin': 'arcsin', r'\arccos': 'arccos', r'\arctan': 'arctan', r'\log': 'log', r'\exp': 'exp',
}


def extract_keys(node):
    """Formula-level content keys from the frozen `meaning`, frozen before the run.

    The node name is deliberately NOT a key: it appears in the question, so echoing it would make every
    probe trivially "correct". Keys are the Chinese-free runs of the meaning that carry a relation
    operator or an arrow (e.g. `Δy=AΔx+o(Δx)`), plus, when a probe has none, the longest Chinese-free run.
    A probe with no usable key is marked unscorable and excluded from the PSR denominators.
    """
    name = str(node['name'])
    meaning = str(node.get('meaning') or '')
    candidates = []
    for match in FORMULA.finditer(meaning):
        token = match.group(0).strip(' ,.;:()[]')
        if len(token) < 3 or token in name or CN.search(token):
            continue
        if any(op in token for op in MATH_OPERATORS):
            candidates.append(token)
    keys = []
    for token in candidates:
        if token not in keys:
            keys.append(token)
        if len(keys) >= 4:
            break
    if not keys:
        fallback = [m.group(0).strip(' ,.;:()[]') for m in FORMULA.finditer(meaning)
                    if len(m.group(0).strip(' ,.;:()[]')) >= 3 and not CN.search(m.group(0))]
        fallback = [t for t in fallback if t not in name and name not in t]
        if fallback:
            keys.append(max(fallback, key=len)[:60])
    return keys


def orders(reference, nodes):
    """Pre-declared prerequisite orders: extracted graph, textbook order, seeded random."""
    by_id = {n['id']: n for n in nodes}
    extracted = {}
    for rel in reference['relations']:
        if rel['type'] not in PREREQ_TYPES:
            continue
        target, prereq = rel['start'], rel['end']
        if target in by_id and prereq in by_id:
            extracted.setdefault(target, set()).add(prereq)
    for rel in reference['cross_section_relations']:
        if rel['type'] not in PREREQ_TYPES:
            continue
        if rel['start'] in by_id and rel['end'] in by_id:
            extracted.setdefault(rel['start'], set()).add(rel['end'])

    ordered = sorted(nodes, key=lambda n: n['merged_evidence'][0])
    textbook = {}
    for index, node in enumerate(ordered):
        if index == 0:
            continue
        textbook[node['id']] = {ordered[index - 1]['id']}

    rng = random.Random(SEED)
    random_order = {}
    for node in nodes:
        others = [n['id'] for n in nodes if n['id'] != node['id']]
        if others:
            random_order[node['id']] = {rng.choice(others)}

    return {'extracted': {k: sorted(v) for k, v in extracted.items()},
            'textbook_order': {k: sorted(v) for k, v in textbook.items()},
            'random_seeded': {k: sorted(v) for k, v in random_order.items()}}


def prepare(rekey: bool = False) -> int:
    reference = json.loads((MERGED / 'reference' / 'reference_merged_v1.json').read_text(encoding='utf-8'))
    required = [n for n in reference['nodes'] if n['status'] == 'M']
    core = [n for n in required if n.get('tier') == 'core']
    detail = [n for n in required if n.get('tier') != 'core']
    selected = (core + detail[:max(0, PROBE_COUNT - len(core))])[:PROBE_COUNT]
    selected.sort(key=lambda n: n['merged_evidence'][0])
    if OUT.exists():
        if not rekey:
            raise SystemExit('exists: %s (use `prepare --rekey` to recompute keys for the same probes)' % OUT)
        existing = json.loads((OUT / 'probes.json').read_text(encoding='utf-8'))
        if [p['node_id'] for p in existing['probes']] != [n['id'] for n in selected]:
            raise SystemExit('probe selection changed; refusing to rekey')
        fixed = []
        for probe, node in zip(existing['probes'], selected):
            probe = dict(probe)
            probe['keys'] = extract_keys(node)
            probe['scorable'] = bool(probe['keys'])
            fixed.append(probe)
        existing['probes'] = fixed
        existing['key_rule'] = ('formula-level: Chinese-free runs of the frozen `meaning` carrying a relation '
                                'operator or an arrow, up to four per probe; the whole-clause rule that scored '
                                '0 correct targets in every order is recorded as superseded')
        write(OUT / 'probes.json', existing)
        print('rekeyed %d probes | scorable %d' % (len(fixed), sum(1 for p in fixed if p['scorable'])))
        return 0
    probes = [{'probe_id': 'K%02d' % (i + 1), 'node_id': n['id'], 'name': n['name'], 'type': n['type'],
               'tier': n.get('tier'), 'merged_evidence': n['merged_evidence'], 'keys': extract_keys(n)}
              for i, n in enumerate(selected)]
    for probe in probes:
        probe['scorable'] = bool(probe['keys'])
    order = orders(reference, selected)
    all_orders = {'extracted': order['extracted'], 'textbook_order': order['textbook_order'],
                  'random_seeded': order['random_seeded']}
    covered = {name: sum(1 for p in probes if p['node_id'] in table) for name, table in all_orders.items()}
    OUT.mkdir(parents=True)
    write(OUT / 'probes.json', {'probes': probes, 'orders': all_orders,
                                'key_rule': ('node name plus up to three clauses of the frozen `meaning` that '
                                             'contain a mathematical relation, clipped to 60 characters'),
                                'source': 'reference_merged_v1.json (frozen; nothing edited)'})
    print('probes: %d (core %d / detail %d) | scorable %d | keys per probe: %s'
          % (len(probes), sum(1 for p in probes if p['tier'] == 'core'), sum(1 for p in probes if p['tier'] != 'core'),
             sum(1 for p in probes if p['scorable']), sorted({len(p['keys']) for p in probes})))
    for name, table in all_orders.items():
        edges = sum(len(v) for v in table.values())
        print('  order %-16s targets with prerequisites: %d | edges: %d' % (name, covered[name], edges))
    print('wrote ->', OUT / 'probes.json')
    return 0


def probe_keys(probe, variant='full'):
    """`full` uses every extracted key; `sharp` uses only the longest one (the decisive expression).

    The full-key pass turned out to have little power: statement probes are paraphrases, so a binary
    "all keys present" test is degenerate and a graded one is noisy. The sharp variant asks directly for
    the core expression and scores that single decisive key, which is a much cleaner binary signal.
    """
    if variant == 'sharp':
        return [max(probe['keys'], key=len)] if probe['keys'] else []
    return list(probe['keys'])


def run(variant='full') -> int:
    data = json.loads((OUT / 'probes.json').read_text(encoding='utf-8'))
    folder = 'responses' if variant == 'full' else 'responses_sharp'
    key = os.environ.get('OPENCODE_API_KEY')
    if not key:
        raise SystemExit('OPENCODE_API_KEY missing; nothing sent')
    session = 'kst-probe-c1'
    for repeat in range(1, REPEATS + 1):
        for probe in data['probes']:
            if not probe.get('scorable'):
                continue
            label = 'r%d_%s' % (repeat, probe['probe_id'])
            if (OUT / folder / ('%s.json' % label)).exists():
                print('skip (already answered):', label)
                continue
            if variant == 'sharp':
                question = ('请只写出下面的数学条目中**最核心的那个表达式或关键条件**（一行公式即可，'
                            '不要解释、不要铺垫）：\n%s' % probe['name'])
            else:
                question = ('请陈述下面的数学条目，写出它的完整内容，包括成立所必需的条件：\n%s' % probe['name'])
            route, body = payload(MODEL, SYSTEM, question, MAX_TOKENS)
            body.update(stream=False)
            budget = Budget(LEDGER, MODEL, USD_LIMIT, TOKEN_LIMIT)
            index = budget.reserve(body, MAX_TOKENS, label)
            write(OUT / 'requests' / ('%s.json' % label), {'endpoint': ENDPOINT, 'body': body})
            request = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode('utf-8'),
                                             headers={'Authorization': 'Bearer ' + key,
                                                      'Content-Type': 'application/json',
                                                      'User-Agent': 'MaFKG-kst-probe/1.0',
                                                      'x-opencode-session': session}, method='POST')
            started = time.monotonic()
            try:
                with urllib.request.urlopen(request, timeout=600) as response:
                    raw = response.read().decode('utf-8')
                if key in raw:
                    raise RuntimeError('credential echo; response not persisted')
                native = json.loads(raw)
                write(OUT / folder / ('%s.json' % label), native)
                write(OUT / 'transport' / ('%s.json' % label), {
                    'http_status': 200, 'elapsed_seconds': round(time.monotonic() - started, 2),
                    'usage': native.get('usage'), 'attempts': 1})
                budget.settle(index, native.get('usage'))
            except Exception as exc:  # noqa: BLE001
                error = {'type': type(exc).__name__, 'attempts': 1}
                if isinstance(exc, urllib.error.HTTPError):
                    error.update(http_status=exc.code,
                                 body=exc.read().decode('utf-8', errors='replace').replace(key, '[REDACTED]')[:2000])
                write(OUT / 'errors' / ('%s.json' % label), error)
                budget.settle(index, None)
                raise SystemExit('call failed, no retry: %s' % error['type'])
            ledger = json.loads(LEDGER.read_text(encoding='utf-8'))
            print('%-9s answered | ledger %.4f USD / %d tokens'
                  % (label, ledger['accounting_usd'], ledger['accounting_tokens']), flush=True)
    return 0


def score(variant='full') -> int:
    data = json.loads((OUT / 'probes.json').read_text(encoding='utf-8'))
    probes = {p['probe_id']: p for p in data['probes']}
    node_of = {p['node_id']: p['probe_id'] for p in data['probes']}
    folder = 'responses' if variant == 'full' else 'responses_sharp'
    results = {}
    for repeat in range(1, REPEATS + 1):
        for probe_id, probe in probes.items():
            if not probe.get('scorable'):
                continue
            keys = probe_keys(probe, variant)
            if not keys:
                continue
            path = OUT / folder / ('r%d_%s.json' % (repeat, probe_id))
            if not path.is_file():
                continue
            native = json.loads(path.read_text(encoding='utf-8'))
            text = native['choices'][0]['message']['content']
            body = norm(text)
            hits = {key: norm(key) in body for key in keys}
            results.setdefault(probe_id, {})['r%d' % repeat] = {
                'hits': hits, 'hit_count': sum(hits.values()), 'key_count': len(hits),
                'all_keys': all(hits.values()), 'text_chars': len(text)}
    payload = {
        'purpose': ('KST NB1 applied to our extracted prerequisite order: when the model states a target '
                    'correctly, does it also state its prerequisites correctly?'),
        'key_rule': data['key_rule'],
        'scoring_rules': {
            'canonicalisation': ('LaTeX commands are mapped to Unicode, fractions to (a)/(b), braces and '
                                 'subscripts flattened, spaces and punctuation removed, on BOTH sides; no answer '
                                 'text is edited'),
            'primary': ('threshold-free: for every prerequisite edge the target score and the prerequisite score '
                        'are reported as key-hit ratios, so the reader sees the gap without a cut-off'),
            'secondary': 'PSR at two declared cut-offs: all keys present (1.0) and at least half the keys (0.5)',
        },
        'probes': {}, 'orders': {},
        'limitations': [
            'Key-hit scoring is a surface-form test after notation canonicalisation, not a semantic judgement.',
            'One model, one textbook excerpt, two repeats; repeats are not independent samples.',
            'Closed-book statement probes measure recall of stated results, not mathematical reasoning.',
            'Three scoring passes were needed: the whole-clause rule and the raw-LaTeX comparison each returned '
            '0 correct targets, which measured the rule rather than the model. All three are reported.',
        ],
    }
    for probe_id, per_repeat in results.items():
        payload['probes'][probe_id] = {'name': probes[probe_id]['name'], 'tier': probes[probe_id]['tier'],
                                       'keys': probes[probe_id]['keys'], 'repeats': per_repeat}

    for order_name, table in data['orders'].items():
        edges, by_target = [], {}
        for target, prereqs in table.items():
            probe_id = node_of.get(target)
            if probe_id is None or probe_id not in results:
                continue
            available = [node_of[p] for p in prereqs if p in node_of and node_of[p] in results]
            if not available:
                continue
            for repeat in results[probe_id]:
                target_row = results[probe_id][repeat]
                target_score = target_row['hit_count'] / target_row['key_count']
                prereq_scores = [results[q][repeat]['hit_count'] / results[q][repeat]['key_count']
                                 for q in available]
                for q, score in zip(available, prereq_scores):
                    edges.append({'target': probes[probe_id]['name'], 'prerequisite': probes[q]['name'],
                                  'repeat': repeat, 'target_score': round(target_score, 3),
                                  'prerequisite_score': round(score, 3),
                                  'gap': round(target_score - score, 3)})
                by_target.setdefault(probe_id, []).append((target_score, prereq_scores))
        gaps = [e['gap'] for e in edges]
        order_stats = {
            'edges': len(edges),
            'targets_with_a_probed_prerequisite': len(by_target),
            'mean_target_score': round(sum(e['target_score'] for e in edges) / len(edges), 4) if edges else None,
            'mean_prerequisite_score': round(sum(e['prerequisite_score'] for e in edges) / len(edges), 4) if edges else None,
            'mean_gap_target_minus_prerequisite': round(sum(gaps) / len(gaps), 4) if gaps else None,
            'edges_where_prerequisite_scores_lower': sum(1 for g in gaps if g > 0),
            'edges_where_they_tie': sum(1 for g in gaps if g == 0),
            'edge_sample': sorted(edges, key=lambda e: -abs(e['gap']))[:12],
        }
        for cut in (1.0, 0.5):
            macro, micro_hit, micro_n, scored = 0.0, 0, 0, 0
            for probe_id, rows in by_target.items():
                for target_score, prereq_scores in rows:
                    if target_score + 1e-9 < cut:
                        continue
                    hits = [1 if s + 1e-9 >= cut else 0 for s in prereq_scores]
                    macro += sum(hits) / len(hits)
                    micro_hit += sum(hits)
                    micro_n += len(hits)
                    scored += 1
            order_stats['psr_cut_%s' % cut] = {
                'targets_scoring_at_or_above_cut': scored,
                'psr_macro': round(macro / scored, 4) if scored else None,
                'psr_micro': round(micro_hit / micro_n, 4) if micro_n else None,
            }
        payload['orders'][order_name] = order_stats
        print('%-16s edges=%d | mean target=%s prereq=%s gap=%s | lower-when-prerequisite=%d | PSR@1.0=%s PSR@0.5=%s'
              % (order_name, order_stats['edges'], order_stats['mean_target_score'],
                 order_stats['mean_prerequisite_score'], order_stats['mean_gap_target_minus_prerequisite'],
                 order_stats['edges_where_prerequisite_scores_lower'],
                 order_stats['psr_cut_1.0']['psr_macro'], order_stats['psr_cut_0.5']['psr_macro']))

    write(OUT / ('psr_report_%s.json' % variant), payload)
    print('wrote ->', OUT / ('psr_report_%s.json' % variant))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=('prepare', 'run', 'score'))
    ap.add_argument('--rekey', action='store_true', help='recompute probe keys for the same selected probes')
    ap.add_argument('--variant', choices=('full', 'sharp'), default='full',
                    help='full = state-the-item probes with every key; sharp = decisive-expression probes')
    args = ap.parse_args()
    if args.action == 'prepare':
        return prepare(rekey=args.rekey)
    return {'run': run, 'score': score}[args.action](variant=args.variant)


if __name__ == '__main__':
    raise SystemExit(main())
