"""Import a MaF-KG semantic-layer JSON artifact into Neo4j.

Supports the ``maf-kg.semantic.*`` schema family (nodes ``Concept`` / ``Claim`` /
``Method``). The default import is intentionally compact: it keeps graph-query
fields and source trace lines, but does not import bulky experimental payloads
such as full evidence arrays or diagnostics. Use ``--profile full`` only when
debugging extraction artifacts inside Neo4j.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KG_DIR = ROOT / "KG"

NODE_TYPES = {"Concept", "Claim", "Method"}
RELATIONSHIP_TYPES = {
    "involves",
    "uses",
    "establishes",
    "derived_from",
    "equivalent_to",
    "special_case_of",
    "defined_in_terms_of",
}


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    try:
        from neo4j import GraphDatabase
    except ImportError:
        print(
            "Missing dependency: neo4j. Install it with `.venv\\Scripts\\python.exe -m pip install neo4j`.",
            file=sys.stderr,
        )
        return 2

    kg_path = resolve_kg_path(args.json)
    data = json.loads(kg_path.read_text(encoding="utf-8"))
    validate_schema(data, kg_path)

    nodes = [prepare_node(node, data, kg_path, args.profile) for node in data.get("nodes", [])]
    relationships = [
        prepare_relationship(rel, args.profile) for rel in data.get("relations", [])
    ]

    if args.dry_run:
        print(import_summary(kg_path, args, nodes, relationships))
        return 0

    uri = args.uri or os.getenv("NEO4J_URI") or "bolt://localhost:7687"
    user = args.user or os.getenv("NEO4J_USER") or "neo4j"
    password = args.password or os.getenv("NEO4J_PASSWORD")
    if not password:
        print("Missing Neo4j password. Pass --password or set NEO4J_PASSWORD.", file=sys.stderr)
        return 2

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        with driver.session(database=args.database) as session:
            if args.clear:
                session.execute_write(clear_existing)
            session.execute_write(create_constraints)
            session.execute_write(import_nodes, nodes, args.batch_size)
            session.execute_write(import_relationships, relationships, args.batch_size)
    finally:
        driver.close()

    print(
        json.dumps(
            {
                "kg_path": str(kg_path),
                "uri": uri,
                "database": args.database,
                "profile": args.profile,
                "nodes": len(nodes),
                "relationships": len(relationships),
                "cleared_existing_kg_nodes": bool(args.clear),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import a MaF-KG semantic KG JSON into Neo4j.")
    parser.add_argument(
        "--json",
        default=None,
        help="KG JSON path. Defaults to the latest semantic JSON under KG/.",
    )
    parser.add_argument("--uri", default=None, help="Neo4j URI. Defaults to NEO4J_URI or bolt://localhost:7687.")
    parser.add_argument("--user", default=None, help="Neo4j user. Defaults to NEO4J_USER or neo4j.")
    parser.add_argument("--password", default=None, help="Neo4j password. Defaults to NEO4J_PASSWORD.")
    parser.add_argument("--database", default=None, help="Neo4j database name. Omit for default database.")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument(
        "--profile",
        choices=["compact", "full"],
        default="compact",
        help="compact imports only key graph fields; full imports nested JSON debug payloads.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Prepare import payload and print a summary without connecting to Neo4j.",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Delete existing nodes labeled KGNode before import. This is destructive for prior MaF-KG imports.",
    )
    return parser


def resolve_kg_path(raw_path: str | None) -> Path:
    if raw_path:
        path = Path(raw_path)
        if not path.is_absolute():
            path = ROOT / path
        if not path.exists():
            raise SystemExit(f"KG JSON not found: {path}")
        return path

    candidates = sorted(
        {
            *DEFAULT_KG_DIR.glob("*_语义图_*.json"),
            *DEFAULT_KG_DIR.glob("*_semantic_*.json"),
        },
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise SystemExit(f"No semantic KG JSON files found under {DEFAULT_KG_DIR}")
    return candidates[0]


def validate_schema(data: dict[str, Any], kg_path: Path) -> None:
    schema_version = str(data.get("schema_version") or "")
    if not schema_version.startswith("maf-kg.semantic."):
        raise SystemExit(f"Unsupported schema_version: {schema_version}")
    node_ids = {node.get("id") for node in data.get("nodes", [])}
    for node in data.get("nodes", []):
        if node.get("type") not in NODE_TYPES:
            raise SystemExit(f"Unsupported node type: {node.get('type')}")
    for rel in data.get("relations", []):
        if rel.get("type") not in RELATIONSHIP_TYPES:
            raise SystemExit(f"Unsupported relationship type: {rel.get('type')}")
        src, tgt = rel.get("source_id"), rel.get("target_id")
        if src not in node_ids or tgt not in node_ids:
            raise SystemExit(f"Relationship has invalid reference: {rel.get('id')}")


def prepare_node(node: dict[str, Any], data: dict[str, Any], kg_path: Path, profile: str) -> dict[str, Any]:
    evidence = node.get("evidence") or []
    first_ev = evidence[0] if evidence else {}
    props = {
        "id": node["id"],
        "type": node["type"],
        "name": node.get("name", ""),
        "aliases": node.get("aliases") or [],
        "description": node.get("description", ""),
        "claim_kind": node.get("claim_kind"),
        "source_path": first_ev.get("source_path") or data.get("source_path", ""),
        "start_line": first_ev.get("start_line"),
        "end_line": first_ev.get("end_line"),
        "chunk_id": first_ev.get("chunk_id"),
    }
    if profile == "full":
        props.update(
            {
                "source_json": str(kg_path),
                "evidence_json": stable_json(evidence),
            }
        )
    return {"id": node["id"], "type": node["type"], "props": drop_empty_values(props)}


def prepare_relationship(rel: dict[str, Any], profile: str) -> dict[str, Any]:
    content = rel.get("content") or {}
    properties = rel.get("properties") or {}
    quality = rel.get("quality") or {}
    evidence = rel.get("evidence") or []
    first_ev = evidence[0] if evidence else {}
    rel_type = rel["type"]
    rel_id = rel.get("id") or stable_relationship_id(rel)
    props = {
        "id": rel_id,
        "type": rel_type,
        "source_path": first_ev.get("source_path", ""),
        "start_line": first_ev.get("start_line"),
        "end_line": first_ev.get("end_line"),
        "chunk_id": first_ev.get("chunk_id"),
    }
    if profile == "full":
        props.update(
            {
                "content_json": stable_json(content),
                "properties_json": stable_json(properties),
                "quality_json": stable_json(quality),
                "evidence_json": stable_json(evidence),
            }
        )
    return {
        "id": rel_id,
        "type": rel_type,
        "neo4j_type": to_relationship_type(rel_type),
        "source": rel["source_id"],
        "target": rel["target_id"],
        "props": drop_empty_values(props),
    }


def drop_empty_values(props: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in props.items():
        if value is None:
            continue
        if value == "":
            continue
        if value == []:
            continue
        cleaned[key] = value
    return cleaned


def stable_json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def stable_relationship_id(rel: dict[str, Any]) -> str:
    src, tgt = rel.get("source_id"), rel.get("target_id")
    raw = f"{rel.get('type')}|{src}|{tgt}|{stable_json(rel.get('evidence'))}"
    return "rel_" + re.sub(r"[^a-zA-Z0-9_]", "_", raw)[:80]


def to_relationship_type(rel_type: str) -> str:
    if rel_type not in RELATIONSHIP_TYPES:
        raise ValueError(f"Unsupported relationship type: {rel_type}")
    return rel_type.upper()


def create_constraints(tx) -> None:
    tx.run("CREATE CONSTRAINT maf_kg_node_id IF NOT EXISTS FOR (n:KGNode) REQUIRE n.id IS UNIQUE")


def clear_existing(tx) -> None:
    tx.run("MATCH (n:KGNode) DETACH DELETE n")


def import_nodes(tx, nodes: list[dict[str, Any]], batch_size: int) -> None:
    for batch in batched(nodes, batch_size):
        tx.run(
            """
            UNWIND $rows AS row
            MERGE (n:KGNode {id: row.id})
            SET n = row.props
            """,
            rows=batch,
        )

    labels_by_type: dict[str, list[str]] = {}
    for node in nodes:
        labels_by_type.setdefault(node["type"], []).append(node["id"])

    for node_type, ids in labels_by_type.items():
        label = sanitize_label(node_type)
        for batch in batched(ids, batch_size):
            tx.run(
                f"""
                MATCH (n:KGNode)
                WHERE n.id IN $ids
                SET n:{label}
                """,
                ids=batch,
            )


def import_relationships(tx, relationships: list[dict[str, Any]], batch_size: int) -> None:
    rels_by_type: dict[str, list[dict[str, Any]]] = {}
    for rel in relationships:
        rels_by_type.setdefault(rel["neo4j_type"], []).append(rel)

    for neo4j_type, rows in rels_by_type.items():
        rel_type = sanitize_relationship_type(neo4j_type)
        for batch in batched(rows, batch_size):
            tx.run(
                f"""
                UNWIND $rows AS row
                MATCH (source:KGNode {{id: row.source}})
                MATCH (target:KGNode {{id: row.target}})
                MERGE (source)-[r:{rel_type} {{id: row.id}}]->(target)
                SET r = row.props
                """,
                rows=batch,
            )


def import_summary(kg_path: Path, args: argparse.Namespace, nodes: list[dict[str, Any]], relationships: list[dict[str, Any]]) -> str:
    node_props = sorted({key for node in nodes for key in node.get("props", {})})
    rel_props = sorted({key for rel in relationships for key in rel.get("props", {})})
    payload = {
        "kg_path": str(kg_path),
        "profile": args.profile,
        "nodes": len(nodes),
        "relationships": len(relationships),
        "node_property_keys": node_props,
        "relationship_property_keys": rel_props,
        "sample_node": nodes[0].get("props", {}) if nodes else {},
        "sample_relationship": relationships[0].get("props", {}) if relationships else {},
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def sanitize_label(label: str) -> str:
    if label not in NODE_TYPES:
        raise ValueError(f"Unsupported label: {label}")
    return label


def sanitize_relationship_type(rel_type: str) -> str:
    allowed = {to_relationship_type(item) for item in RELATIONSHIP_TYPES}
    if rel_type not in allowed:
        raise ValueError(f"Unsupported relationship type: {rel_type}")
    return rel_type


def batched(items: list[Any], batch_size: int) -> list[list[Any]]:
    size = max(1, batch_size)
    return [items[index : index + size] for index in range(0, len(items), size)]


if __name__ == "__main__":
    raise SystemExit(main())
