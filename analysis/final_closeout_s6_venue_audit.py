#!/usr/bin/env python
"""S6: venue and release-risk audit.

Records what can be verified from the official conference call and what cannot, so that no deadline
or track is guessed.  The WWW 2026 research-track call is the most recent official statement; the
WWW 2027 call is not published at the time of writing, so its dates and tracks are recorded as an
author input rather than inferred.

Read-only.  Writes only under
evaluation/paper_closeout_2026-10-10/final_closeout_v1/audits/.

Usage:
    python analysis/final_closeout_s6_venue_audit.py
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "evaluation/paper_closeout_2026-10-10/final_closeout_v1/audits"

VENUE_SOURCE = "https://www2026.thewebconf.org/calls/research-tracks.html"

PAYLOAD = {
    "schema": "dsh.final_closeout.s6_venue_and_release_audit.v1",
    "venue": {
        "target": "The Web Conference (WWW) research tracks",
        "verified_from": VENUE_SOURCE,
        "verified_edition": "The Web Conference 2026",
        "verified_at": "2026-10-10",
        "format_requirements": {
            "language": "English",
            "layout": "double-column, ACM template",
            "latex": "\\documentclass[sigconf, anonymous, review]{acmart}",
            "page_budget": "8 pages main paper, references and optional appendix up to 12 pages total",
            "self_containment": "the first 8 pages must be self-contained",
            "review": "double-blind; omit names, affiliations and identifying self-citations",
            "single_pdf": True,
        },
        "relevance_requirement_verbatim": (
            "Every submission must clearly state how the work is relevant to the Web and to the "
            "track in the first page. Submissions that merely use a Web artifact---e.g., a dataset "
            "or a Web Application Programmer Interface (API) or a social network---rather than "
            "answering a specific Web-related scientific research challenge, are out of scope and "
            "will be desk-rejected."
        ),
        "tracks_that_fit": [
            {
                "track": "Semantics and Knowledge",
                "why": (
                    "The track covers methods and applications for creating, curating and consuming "
                    "interlinked structured data on the Web, including knowledge graphs, ontologies "
                    "and the synergy between semantic models and LLMs, plus provenance and trust. "
                    "This paper builds a two-layer semantic graph with explicit provenance."
                ),
            },
            {
                "track": "Graph Algorithms and Modeling for the Web",
                "why": (
                    "Relevant if the contribution is framed around graph construction and modeling "
                    "rather than semantics; the prerequisite-order part also fits here."
                ),
            },
        ],
        "unverifiable": {
            "ww2027_dates": (
                "The 2027 call for papers was not retrievable from the official site at the time of "
                "writing, and the 2026 deadlines have passed. No deadline is inferred from last "
                "year's rules or from any earlier verbal estimate."
            ),
            "ww2027_tracks": "Track list for 2027 not published; the 2026 track list is used only as a baseline.",
            "author_input_required": [
                "Target edition (2027 or later) and its exact abstract/full-paper deadlines.",
                "Which track to submit to, and therefore which relevance paragraph to lead with.",
                "Whether the anonymous artifact repository will be released at submission or after acceptance.",
            ],
        },
        "publishing_note": (
            "ACM moved to fully open access from January 2026, with a temporary 2026 subsidy; the "
            "2027 policy is not yet confirmed. APC eligibility depends on the institution and is an "
            "author input."
        ),
    },
    "release_risks": [
        {
            "risk_id": "R1",
            "item": "textbook licensing",
            "detail": (
                "The source is a copyrighted Chinese calculus textbook. The paper may quote short "
                "passages for scientific purposes, but the full chapter Markdown must not be "
                "redistributed in the artifact."
            ),
            "mitigation": (
                "Publish the reference set, the graphs, the review packets and the scoring code; "
                "state that the source text is obtainable by lawful purchase and describe how to "
                "reconstruct the input from it."
            ),
            "status": "author_decision_required",
        },
        {
            "risk_id": "R2",
            "item": "what may be shared publicly",
            "detail": "Frozen reference sets, review packets and per-item judgements are derived works of the source.",
            "mitigation": "Share identifiers, line ranges and short excerpts; keep long excerpts out of the public artifact.",
            "status": "author_decision_required",
        },
        {
            "risk_id": "R3",
            "item": "model and API version pinning",
            "detail": (
                "All runs used DeepSeek-V4-Flash through the OpenCode Go endpoint with thinking "
                "enabled and low reasoning effort, temperature never sent, max_tokens 64000 for "
                "extraction and 12000 for review, single attempt, 600 s timeout."
            ),
            "mitigation": "State the exact wire configuration; note that the provider may retire the model.",
            "status": "disclosed",
        },
        {
            "risk_id": "R4",
            "item": "AI-assisted evaluation disclosure",
            "detail": (
                "Reviews were produced by AI-assisted source review (DSH review agents, and four "
                "earlier packets by a different model). There is no second human annotator."
            ),
            "mitigation": (
                "The paper must not claim independent human annotation, expert Gold, or "
                "inter-annotator agreement; it must state the provenance of every judgement."
            ),
            "status": "disclosed",
        },
        {
            "risk_id": "R5",
            "item": "failures and adaptations",
            "detail": (
                "EDC's empty graph is a parser-compatibility fault; iText2KG's names were rewritten "
                "by the pinned upstream sanitiser; both are reported as adapted configurations, not "
                "as reproductions of the published methods."
            ),
            "mitigation": "Report adapted configurations explicitly and never present them as the original papers' settings.",
            "status": "disclosed",
        },
        {
            "risk_id": "R6",
            "item": "anonymity and keys",
            "detail": "The repository contains no key material in the tracked artifacts; API keys are read from the environment at call time.",
            "mitigation": "Run the secret scan before any release and keep the anonymous repository free of absolute local paths.",
            "status": "check_required_before_release",
        },
        {
            "risk_id": "R7",
            "item": "reproducibility claim",
            "detail": (
                "Because the textbook cannot be redistributed, the study cannot be claimed to be "
                "fully publicly reproducible."
            ),
            "mitigation": "Say precisely what is released and what a reader must obtain themselves.",
            "status": "disclosed",
        },
    ],
    "citation_verification": {
        "status": "verified_for_the_two_external_methods",
        "verified": [
            {
                "key": "EDC",
                "authors": ["Bowen Zhang", "Harold Soh"],
                "title": "Extract, Define, Canonicalize: An LLM-based Framework for Knowledge Graph Construction",
                "venue": "Proceedings of the 2024 Conference on Empirical Methods in Natural Language Processing (EMNLP 2024)",
                "pages": "9820-9836",
                "address": "Miami, Florida, USA",
                "publisher": "Association for Computational Linguistics",
                "doi": "10.18653/v1/2024.emnlp-main.548",
                "url": "https://aclanthology.org/2024.emnlp-main.548/",
                "code": "https://github.com/clear-nus/edc",
                "checked_from": "ACL Anthology record (primary)",
                "note": "The three-phase design named in this paper is exactly the design our adapted configuration runs.",
            },
            {
                "key": "iText2KG",
                "authors": [
                    "Yassir Lairgi",
                    "Ludovic Moncla",
                    "Remy Cazabet",
                    "Khalid Benabdeslem",
                    "Pierre Cleau",
                ],
                "title": "iText2KG: Incremental Knowledge Graphs Construction Using Large Language Models",
                "venue": "Accepted at the International Conference on Web Information Systems Engineering (WISE) 2024",
                "arxiv": "arXiv:2409.03284",
                "doi": "10.48550/arXiv.2409.03284",
                "url": "https://arxiv.org/abs/2409.03284",
                "code_commit_used_here": "43bc581c99a3a35ace99dc4699acdabb0f1a98f7",
                "checked_from": "arXiv abstract record (primary); the commit is the one pinned in our snapshot manifests",
                "note": (
                    "Four modules: Document Distiller, Incremental Entity Extractor, Incremental "
                    "Relation Extractor, Graph Integrator. Our runs pin this release and patch only "
                    "the name handling, which is why the paper must call it an adapted configuration."
                ),
            },
        ],
        "pending": [
            {
                "key": "KST source paper",
                "status": "pending",
                "why": "The probe was withdrawn from the main evidence, so its citation is not on the critical path.",
            },
            {
                "key": "mathematics-textbook knowledge graph prior work",
                "status": "lead_found_not_verified",
                "lead": "AutoMathKG: The automated mathematical knowledge graph based on LLM and vector database",
                "why": "Needs a primary-source check before it can be cited or compared against.",
            },
        ],
        "rule": (
            "A reference is only written into the paper once its authors, title, year, venue and "
            "DOI/arXiv have been checked against a primary source; unread or unverified comparisons "
            "are marked pending rather than guessed."
        ),
    },
    "what_must_appear_on_page_one": [
        "An explicit statement of Web relevance tied to the Semantics and Knowledge track: a "
        "knowledge-graph construction and provenance problem for structured educational content on "
        "the Web, not a mere use of a Web API.",
        "The two-layer representation (independently citable L1 statements plus L2 teaching units).",
        "The scope-dependence finding, stated as the headline result rather than buried.",
    ],
}


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "venue_and_release_audit.json"
    out.write_text(json.dumps(PAYLOAD, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 投稿要求与发布风险核查（S6）",
        "",
        f"来源：{VENUE_SOURCE}（The Web Conference 2026 研究 track 官方征稿页，核查于 2026-10-10）",
        "",
        "## 可核实的硬要求",
        "",
        "| 项 | 要求 |",
        "|---|---|",
        "| 语言/版式 | 英文、双栏、ACM 模板 |",
        "| LaTeX | `\\documentclass[sigconf, anonymous, review]{acmart}` |",
        "| 页数 | 正文 8 页；参考文献 + 附录至多 12 页；前 8 页必须自洽 |",
        "| 评审 | 双盲；去掉姓名、单位与可识别的自引 |",
        "| 相关性 | **第一页必须写明与 Web 及所选 track 的相关性** |",
        "",
        "> 原文红线：只使用 Web 产物（数据集、API、社交网络）而不回答具体 Web 科研问题的投稿属于 out of scope，将被 desk-reject。",
        "",
        "## 不可核实、必须由作者确认",
        "",
        "- **WWW 2027 的截稿日期**：官网当时不可得，2026 的截止已过；**不按往年规则或旧口述猜测**。",
        "- **WWW 2027 的 track 列表**：未发布；2026 的 track 只作基线。",
        "- 目标年份、track 选择、匿名仓库的发布时点。",
        "",
        "## 最贴合的 track",
        "",
        "- **Semantics and Knowledge**：覆盖 Web 上结构化语义数据的创建、组织、消费，知识图谱与 LLM 的协同，以及来源与可信度。",
        "- **Graph Algorithms and Modeling for the Web**：若把贡献框定为图构造与建模，先修序部分也可落在此。",
        "",
        "## 发布风险清单",
        "",
        "| ID | 项 | 状态 |",
        "|---|---|---|",
    ]
    for risk in PAYLOAD["release_risks"]:
        lines.append(f"| {risk['risk_id']} | {risk['item']} | {risk['status']} |")
    lines += [
        "",
        "## 第一页必须出现的内容",
        "",
    ]
    for item in PAYLOAD["what_must_appear_on_page_one"]:
        lines.append(f"- {item}")
    lines += [
        "",
        "## 引文核对",
        "",
    ]
    for item in PAYLOAD["citation_verification"]["verified"]:
        lines += [
            f"### {item['key']} —— 已核实（一手来源：{item['checked_from']}）",
            "",
            f"- 作者：{', '.join(item['authors'])}",
            f"- 题目：{item['title']}",
            f"- 发表：{item['venue']}"
            + (f"，{item.get('pages')}" if item.get("pages") else "")
            + (f"，{item.get('address')}" if item.get("address") else ""),
            f"- DOI：{item['doi']}",
            f"- 链接：{item['url']}",
        ]
        if item.get("code"):
            lines.append(f"- 代码：{item['code']}")
        if item.get("code_commit_used_here"):
            lines.append(f"- 本实验锁定的 commit：`{item['code_commit_used_here']}`")
        lines.append(f"- 说明：{item['note']}")
        lines.append("")
    lines.append("### 待核实")
    lines.append("")
    for item in PAYLOAD["citation_verification"]["pending"]:
        lines.append(f"- **{item['key']}**（{item['status']}）：{item.get('why', '')}"
                     + (f" 线索：{item['lead']}" if item.get("lead") else ""))
    lines += ["", f"规则：{PAYLOAD['citation_verification']['rule']}"]
    (OUT_DIR / "venue_and_release_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"wrote {out.relative_to(REPO).as_posix()}")
    print(f"  verified edition: {PAYLOAD['venue']['verified_edition']}")
    print(f"  release risks: {len(PAYLOAD['release_risks'])}")
    print(f"  unverifiable: {list(PAYLOAD['venue']['unverifiable'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
