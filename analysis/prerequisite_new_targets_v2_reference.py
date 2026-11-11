# -*- coding: utf-8 -*-
"""P1-B 5.2 step 2: build and freeze the reference pairs for the four new targets.

Annotation standard (declared here, applied once, never adjusted after seeing predictions):

  required_direct    the target's own statement, definition or procedure uses the prerequisite's
                     content with no other catalogue unit in between;
  required_indirect  the prerequisite is needed, but only through another catalogue unit;
  proof_support      an in-catalogue unit is actually used in a proof of the target;
  principle_support  a principle that justifies or motivates the target without being a learning
                     prerequisite;
  related_or_none    same chapter/section or same notation family, and NOT a prerequisite. Reverse
                     direction (a unit that is proved FROM the target) is recorded here as well and
                     flagged, because a reversed edge is the most common way a prerequisite graph goes
                     wrong.

Every pair carries the reason, the required scope, and the source spans of both endpoints, so a reader
can check the judgement against the frozen text. Pairs are written from the catalogue entries and their
Card spans (`worksheet.md`); nothing here is derived from any prediction.

Usage: python analysis/prerequisite_new_targets_v2_reference.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(r'E:\MaF-KG\.worktrees\stable-main')
FROZEN = ROOT / 'evaluation' / 'prerequisite_new_targets_2026-10-07' / 'run02' / 'frozen_inputs01'
OUT = ROOT / 'evaluation' / 'paper_closeout_2026-10-10' / 'prerequisite_confirmation'
sys.stdout.reconfigure(encoding='utf-8')

# (pair_id, target, prerequisite, role, directness_or_None, reason, required_scope)
PAIRS = [
    # ---------------------------------------------------------------- 原函数与不定积分的概念（定义型）
    ('N01', 'ku_fb8130a0dde2', 'ku_8dfb5403bb52', 'required', 'direct',
     '原函数定义为 F′(x)=f(x)；判断一个函数是否为原函数、以及由导数公式反推原函数，都要直接对基本初等函数求导。',
     '要求基本初等函数的导数公式，不要求其推导过程'),
    ('N02', 'ku_fb8130a0dde2', 'ku_cdb2413d68e1', 'required', 'direct',
     '由 (u±v)′=u′±v′、(Cu)′=Cu′ 直接得到不定积分的线性性质，是该单元自身的运算依据。',
     '要求和、差、常数倍的求导法则，不要求证明'),
    ('N03', 'ku_fb8130a0dde2', 'ku_09206fe29015', 'required', 'indirect',
     '原函数以导数为定义，而导数由极限定义，因此经"导数概念"间接依赖函数极限；本单元本身不直接使用极限运算。',
     '只要求理解极限概念，不要求 ε-δ 论证'),
    ('N04', 'ku_fb8130a0dde2', 'ku_423b096c02ca', 'required', 'indirect',
     '复合函数求导法则不参与"原函数"的定义，只在后续换元积分（微分形式不变性）中间接用到。',
     '只要求会用链式法则，不要求证明'),
    ('N05', 'ku_fb8130a0dde2', 'ku_55349e5dafb7', 'related_or_none', None,
     '同属第二章的求导技术，但理解不定积分概念不需要高阶导数。',
     None),
    ('N06', 'ku_fb8130a0dde2', 'ku_29e511730e46', 'related_or_none', None,
     '基本积分表是本单元的下游产物（先有原函数概念才有积分表），方向相反，不构成先修。',
     None),
    ('N07', 'ku_fb8130a0dde2', 'ku_c9aad5c266bb', 'related_or_none', None,
     '反函数求导法则只在求反三角函数类原函数时用到，属后续方法，不是概念的先修。',
     None),

    # ---------------------------------------------------------------- 罗尔定理（定理/证明型）
    ('N08', 'ku_da56c3fdccf2', 'ku_09206fe29015', 'required', 'indirect',
     '定理条件中的"可导"由极限定义，因此经导数概念间接依赖函数极限；定理陈述本身不出现极限运算。',
     '只要求理解极限与可导的关系，不要求 ε-δ 论证'),
    ('N09', 'ku_da56c3fdccf2', 'ku_8dfb5403bb52', 'required', 'indirect',
     '定理结论是 f′(ξ)=0，求导公式用于实例中验证驻点，但定理的陈述与证明本身不依赖导数公式表。',
     '只要求会对多项式与基本初等函数求导'),
    ('N10', 'ku_da56c3fdccf2', 'ku_cdb2413d68e1', 'required', 'indirect',
     '证明中若需讨论辅助函数或推广形式才用求导法则，定理本身不直接使用。',
     '不要求复杂函数的求导技巧'),
    ('N11', 'ku_da56c3fdccf2', 'ku_d43d41d9a5e8', 'related_or_none', None,
     '方向相反：拉格朗日中值定理是由罗尔定理推出的下游结论，把它当作罗尔的先修会把依赖图整体反向。',
     None),
    ('N12', 'ku_da56c3fdccf2', 'ku_6f096c74ce38', 'related_or_none', None,
     '同为反向：柯西中值定理由罗尔定理（或拉格朗日）推出，属下游推广。',
     None),
    ('N13', 'ku_da56c3fdccf2', 'ku_975a894dced2', 'related_or_none', None,
     '洛必达法则以柯西中值定理为依据，是本章下游的应用单元，与罗尔定理无先修关系。',
     None),
    ('N14', 'ku_da56c3fdccf2', 'ku_55349e5dafb7', 'related_or_none', None,
     '高阶导数与罗尔定理同属导数应用，但定理只需一阶导数。',
     None),

    # ---------------------------------------------------------------- 分部积分法及其应用（方法型）
    ('N15', 'ku_e9f8c9c2740e', 'ku_cdb2413d68e1', 'required', 'direct',
     '分部积分公式由乘积求导法则 (uv)′=u′v+uv′ 两边积分移项直接得到，是本方法的唯一推导来源。',
     '要求和、差、积、商的求导法则，尤其是乘积法则'),
    ('N16', 'ku_e9f8c9c2740e', 'ku_fb8130a0dde2', 'required', 'direct',
     '公式两端都是不定积分，使用该方法必须先有原函数与不定积分的概念与记号。',
     '要求理解原函数与不定积分的定义与记号，不要求其历史来源'),
    ('N17', 'ku_e9f8c9c2740e', 'ku_29e511730e46', 'required', 'direct',
     '公式把积分转化为 ∫v du，最终仍要用基本积分表把 ∫v du 积出来，否则方法无法收尾。',
     '要求基本积分表中的常用公式，不要求逐条推导'),
    ('N18', 'ku_e9f8c9c2740e', 'ku_294ed87693ea', 'required', 'direct',
     '分项处理与常数因子外提是分部积分法中反复使用的运算依据。',
     '要求不定积分的线性性质'),
    ('N19', 'ku_e9f8c9c2740e', 'ku_8dfb5403bb52', 'required', 'indirect',
     '通过乘积求导法则与原函数概念间接依赖基本导数公式；分部积分本身不再直接查导数表。',
     '只要求会用导数公式'),
    ('N20', 'ku_e9f8c9c2740e', 'ku_423b096c02ca', 'required', 'indirect',
     '对复合形式（如 ∫ln x dx 之外的类型、换元后再分部）才需要链式法则，方法的推导不需要。',
     '只要求会用链式法则'),
    ('N21', 'ku_e9f8c9c2740e', 'ku_55349e5dafb7', 'related_or_none', None,
     '同章相邻的求导技术，分部积分法不需要高阶导数。',
     None),

    # ---------------------------------------------------------------- 牛顿-莱布尼茨公式（定理应用型）
    ('N22', 'ku_99bdf15cb98e', 'ku_ffbe6652bb63', 'required', 'direct',
     '公式的对象就是定积分，必须先有定积分的定义与可积性概念才能谈它的计算。',
     '要求定积分的定义与可积的充分条件，不要求可积性证明'),
    ('N23', 'ku_99bdf15cb98e', 'ku_f10444319956', 'required', 'direct',
     '定理的证明正是构造 Φ(x)=∫_a^x f(t)dt 并证明 Φ′(x)=f(x)，再得 Φ(x)=F(x)−F(a)；没有这一单元就无法证明公式。',
     '要求积分上限函数的导数结论，不要求其完整证明'),
    ('N24', 'ku_99bdf15cb98e', 'ku_fb8130a0dde2', 'required', 'direct',
     '公式用"原函数"这一概念表述（F 是 f 的原函数），不理解原函数就无法理解公式在说什么。',
     '要求原函数与不定积分的概念'),
    ('N25', 'ku_99bdf15cb98e', 'ku_29e511730e46', 'required', 'indirect',
     '计算具体定积分时要查积分表求原函数，公式本身不要求；因此是间接使用。',
     '要求常用积分公式'),
    ('N26', 'ku_99bdf15cb98e', 'ku_09206fe29015', 'required', 'indirect',
     '定积分定义本身是极限，故经定积分定义间接依赖函数极限；公式陈述不直接出现极限。',
     '只要求理解极限概念'),
    ('N27', 'ku_99bdf15cb98e', 'ku_cdb2413d68e1', 'required', 'indirect',
     '验证或求出原函数 F 时需要求导运算，公式本身不直接使用求导法则。',
     '只要求会用基本求导法则'),
    ('N28', 'ku_99bdf15cb98e', 'ku_4ca81bb2e29f', 'related_or_none', None,
     '方向相反：无穷限反常积分以本公式为计算工具，是把本公式当作先修的下游单元。',
     None),
]


def spans(unit):
    return [{'source': Path(card['source_path']).name, 'start_line': card['start_line'],
             'end_line': card['end_line'], 'kind': card.get('teaching_unit_kind')}
            for card in unit.get('cards', [])]


def main() -> int:
    selection = json.loads((OUT / 'selection.json').read_text(encoding='utf-8'))
    units = {u['id']: u for u in json.loads((FROZEN / 'catalog.json').read_text(encoding='utf-8'))['knowledge_units']}
    target_ids = [row['unit_id'] for row in selection['targets']]
    if sorted({row[1] for row in PAIRS}) != sorted(target_ids):
        raise SystemExit('pairs do not cover exactly the frozen targets')
    for row in PAIRS:
        if row[2] not in units or row[1] not in units:
            raise SystemExit('unknown unit in %s' % row[0])
        if row[3] == 'required' and row[4] not in ('direct', 'indirect'):
            raise SystemExit('required pair without directness: %s' % row[0])
        if row[3] != 'required' and row[4] is not None:
            raise SystemExit('non-required pair carries directness: %s' % row[0])

    annotations = []
    for pair_id, target_id, prereq_id, role, directness, reason, scope in PAIRS:
        annotations.append({
            'id': pair_id, 'target_ku': target_id, 'prerequisite_ku': prereq_id,
            'target_name': units[target_id]['name'], 'prerequisite_name': units[prereq_id]['name'],
            'role': role, 'directness': directness, 'reason': reason, 'required_scope': scope,
            'provenance': 'author_side_source_review_before_prediction',
            'target_spans': spans(units[target_id]), 'prerequisite_spans': spans(units[prereq_id]),
            'evidence_note': ('spans are the Cards of both endpoints in the frozen catalogue; the judgement was made '
                              'on the catalogue descriptions and these spans, before any prediction existed'),
        })

    roles = {}
    for row in annotations:
        roles[row['role']] = roles.get(row['role'], 0) + 1
    directness_counts = {'direct': 0, 'indirect': 0}
    for row in annotations:
        if row['directness']:
            directness_counts[row['directness']] += 1

    payload = {
        'experiment': 'prerequisite-new-target-confirmation-v2',
        'status': 'reference_frozen_before_prediction',
        'annotation_standard': ('required_direct = used by the target itself with no catalogue unit in between; '
                                'required_indirect = needed only through another catalogue unit; proof_support = an '
                                'in-catalogue unit used in a proof of the target; principle_support = motivates but '
                                'is not a learning prerequisite; related_or_none = same family and NOT a prerequisite, '
                                'including reversed-direction pairs, which are flagged in the reason.'),
        'role_coverage_note': ('No proof_support or principle_support pair could be assigned inside this catalogue: '
                               'the ingredients of the Rolle and Newton-Leibniz proofs (the extreme-value theorem, '
                               'Fermat lemma and the continuous-function properties) are either aggregated inside the '
                               'target unit itself or absent from the catalogue. That gap is recorded rather than '
                               'filled with a guessed support edge.'),
        'targets': selection['targets'],
        'pairs': annotations,
        'counts': {'pairs': len(annotations), 'by_role': roles, 'by_directness': directness_counts,
                   'targets': len(target_ids)},
        'negative_pair_types': {
            'reversed_direction': [row['id'] for row in annotations
                                   if row['role'] == 'related_or_none'
                                   and ('reversed' in row['reason'] or '方向相反' in row['reason'])],
            'same_family_only': [row['id'] for row in annotations
                                 if row['role'] == 'related_or_none'
                                 and 'reversed' not in row['reason'] and '方向相反' not in row['reason']],
        },
        'frozen_before_prediction': True,
        'next_steps': ['run candidate generation for the four targets',
                       'run the fixed-pool review on the locked context arm from 5.1',
                       'score end-to-end and fixed-pool universes separately'],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'reference_v2.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('frozen pairs: %d over %d targets | roles %s | directness %s'
          % (len(annotations), len(target_ids), roles, directness_counts))
    print('reversed-direction negatives: %s' % payload['negative_pair_types']['reversed_direction'])
    for row in annotations:
        print('  %s %-22s <- %-24s %s%s' % (row['id'], row['target_name'], row['prerequisite_name'], row['role'],
                                            '/' + row['directness'] if row['directness'] else ''))
    print('wrote ->', OUT / 'reference_v2.json')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
