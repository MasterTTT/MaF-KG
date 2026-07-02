# MaF-KG Schema v5

Note: this file name is kept for repo continuity, but the active extractor schema is now `maf-kg.agent.v5`.

## Top-Level Object

```json
{
  "schema_version": "maf-kg.agent.v5",
  "source_path": "data/raw/...",
  "title": "...",
  "model": "deepseek-v4-flash",
  "node_types": ["Concept", "Formula", "Property", "Theorem", "Example", "Proof", "Figure"],
  "relationship_types": ["has_formula", "has_property", "example_of", "illustrated_by", "prerequisite_of", "similar_to", "proved_by"],
  "nodes": [],
  "relationships": [],
  "diagnostics": {}
}
```

## Node Schema

Each published node is compact:

```json
{
  "id": "concept_...",
  "type": "Concept",
  "name": "映射",
  "aliases": ["函数对应"],
  "description": "设A、B是两个非空集合，如果按某种对应法则f...",
  "content": {}
}
```

Type-specific `content`:

- `Formula`: `{"latex": "..."}`
- `Example`: optional `{"approach": "...", "steps": ["...", "..."]}`
- `Proof`: optional `{"approach": "...", "steps": ["...", "..."]}`
- `Figure`: optional `{"label": "图1-3", "url": "..."}`
- `Concept`, `Property`, `Theorem`: usually empty

## Relationship Schema

Each published relationship is compact and keeps score plus evidence:

```json
{
  "id": "rel_...",
  "type": "has_formula",
  "source": "concept_...",
  "target": "formula_...",
  "score": 0.91,
  "evidence": []
}
```

## Node Type Boundaries

- `Concept`: a genuine mathematical concept, object, or term
- `Formula`: a general explanatory/definitional/theorem-level formula, with exact source LaTeX
- `Property`: a reusable property, fact, or conclusion about a concept or theorem
- `Theorem`: a formal theorem-level assertion in the textbook
- `Example`: a textbook example or worked example
- `Proof`: only the formal proof that directly belongs to a theorem
- `Figure`: an explicit figure label or image node

## Relationship Types

- `has_formula`
- `has_property`
- `example_of`
- `illustrated_by`
- `prerequisite_of`
- `similar_to`
- `proved_by`

## Pipeline Summary

The v5 extractor follows this flow:

1. `ExtractionAgent`: chunk-local candidate nodes and local candidate relationships
2. `NodeResolver`: programmatic cross-chunk node aggregation
3. `RelationAgent`: cross-chunk relationship completion
4. `NormalizerAgent`: graph cleanup on the resolved global graph
5. `VerificationAgent`: strict semantic relation validation
6. `finalize_graph`: compact published graph output
