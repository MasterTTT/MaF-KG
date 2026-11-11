# MaF-KG

Formula-aware knowledge graph construction from higher-mathematics textbooks.
The Python pipeline extracts source-grounded Concept, Claim and Method records,
preserves their mathematical expressions, and organizes them into teaching-oriented
KnowledgeUnits. A separate stage proposes and reviews prerequisite relationships.

## Components

- `agents/Extractor.py`: source parsing, bounded graph memory, validated incremental
  merging, expression ownership and chunk caching.
- `scripts/build_knowledge_units.py`: heading/card grouping, constrained member
  routing and fingerprinted section checkpoints.
- `scripts/build_ku_depends_candidates.py` and prerequisite review scripts:
  candidate generation, scoped review and evaluation.
- `scripts/import_kg_to_neo4j.py`: batched, idempotent graph import with ID constraints.
- `prompts/`: versioned extraction and organization prompts.
- `tests/`: core regression tests and research-specific checks.
- `analysis/`: research evaluation and audit utilities, some tied to local artifacts.

## Setup

Use Python 3.11 or later in a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Provide provider credentials using environment variables such as `OPENCODE_API_KEY`
or `DEEPSEEK_API_KEY`. Neo4j import uses `NEO4J_URI`, `NEO4J_USER` and
`NEO4J_PASSWORD`. Never commit credential files. Some historical scripts contain
local Windows defaults; pass explicit source and output paths when reproducing them.
Model-backed commands can incur provider charges.

Inspect command options before running:

```powershell
python agents/Extractor.py --help
python scripts/build_knowledge_units.py --help
python scripts/import_kg_to_neo4j.py --help
```

The default KU pipeline uses textbook subheading boundaries. Older planner prompts
remain available as experimental alternatives; they are not the default pipeline.

## Core regression tests

```powershell
python -m pytest tests/test_extractor_core.py tests/test_build_knowledge_units.py tests/test_evaluate_kg.py -q
```

The full research test collection also reads frozen run manifests, third-party
baseline snapshots and evaluation packets. Those local artifacts are intentionally
not included in this source-only repository. Historical audit assertions describe
their original environments and should not be treated as portable core unit tests.
Third-party baseline setup is handled separately by `scripts/prepare_external_vendors.py`.

## Data and release scope

Textbook source files, generated graphs, model responses, API ledgers, manuscript
drafts and local worktree archives are excluded from synchronization. Existing
small reference annotations remain versioned for continuity; their distribution
rights must be reviewed before a public release.

This synchronization does not publish a dataset, change repository visibility or
grant an open-source license. Choose a license and review third-party/data rights
before making the repository public. Experimental graph quality is not a claim
of validated student-learning or personalized-recommendation outcomes.
