#!/usr/bin/env python3
"""Reverse-import ontology and mapping metadata from Oracle 26ai node/edge DDL.

The script parses an existing ONTO_NODE_* / ONTO_EDGE_* / PROPERTY GRAPH DDL file
and reconstructs platform metadata rows for:

- SYS_DOMAIN
- SYS_ONTOLOGY_ENTITY
- SYS_ONTOLOGY_PROPERTY
- SYS_ONTOLOGY_RELATION
- SYS_ENTITY_MAPPING
- SYS_PROPERTY_MAPPING
- SYS_RELATION_MAPPING

By default it only prints a summary.  Pass --apply to write the derived metadata
into the current platform database configured by backend/.env or .env.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


VIEW_RE = re.compile(r"CREATE\s+OR\s+REPLACE\s+VIEW\s+([A-Z0-9_]+)\s+AS\s*(.*?)\n;", re.IGNORECASE | re.DOTALL)
COMMENT_RE = re.compile(r"COMMENT\s+ON\s+TABLE\s+([A-Z0-9_]+)\s+IS\s+'((?:''|[^'])*)';", re.IGNORECASE)
GRAPH_RE = re.compile(r"CREATE\s+OR\s+REPLACE\s+PROPERTY\s+GRAPH\s+([A-Z0-9_]+)", re.IGNORECASE)
TOKEN_RE = re.compile(r"[A-Z][A-Z0-9_]*")


@dataclass
class VertexDefinition:
    table_name: str
    graph_label: str
    key_columns: list[str]
    property_columns: list[str]


@dataclass
class EdgeDefinition:
    table_name: str
    graph_label: str
    key_columns: list[str]
    source_label: str
    source_key: str
    target_label: str
    target_key: str
    property_columns: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ddl-file", required=True, help="Path to ontology PGQ DDL file.")
    parser.add_argument("--domain-name", required=True, help="Platform business domain name to update.")
    parser.add_argument("--domain-id", help="Optional fixed domain id. If omitted, existing domain is reused or a new one is created.")
    parser.add_argument("--created-by", default="oracle_pgq_ddl_importer", help="Audit username stored in created_by/mapped_by.")
    parser.add_argument("--apply", action="store_true", help="Write parsed metadata into the platform database.")
    parser.add_argument("--replace-domain-metadata", action="store_true", help="Delete existing ontology/mapping metadata in the domain before import.")
    parser.add_argument("--summary-json", help="Optional path for writing the parsed summary as JSON.")
    return parser.parse_args()


def split_top_level(text: str, delimiter: str = ",") -> list[str]:
    items: list[str] = []
    current: list[str] = []
    depth = 0
    quote = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "'" and (i == 0 or text[i - 1] != "\\"):
            quote = not quote
            current.append(ch)
            i += 1
            continue
        if not quote:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth = max(0, depth - 1)
            elif ch == delimiter and depth == 0:
                item = "".join(current).strip()
                if item:
                    items.append(item)
                current = []
                i += 1
                continue
        current.append(ch)
        i += 1
    tail = "".join(current).strip()
    if tail:
        items.append(tail)
    return items


def extract_balanced_section(text: str, start_token: str) -> tuple[str, int]:
    start = text.upper().find(start_token.upper())
    if start < 0:
        raise ValueError(f"Missing section token: {start_token}")
    open_paren = text.find("(", start)
    if open_paren < 0:
        raise ValueError(f"Missing opening parenthesis after {start_token}")
    depth = 0
    quote = False
    for idx in range(open_paren, len(text)):
        ch = text[idx]
        if ch == "'" and (idx == 0 or text[idx - 1] != "\\"):
            quote = not quote
        if quote:
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[open_paren + 1:idx], idx + 1
    raise ValueError(f"Unbalanced section starting at {start_token}")


def parse_comment_map(sql_text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for table_name, raw_comment in COMMENT_RE.findall(sql_text):
        result[table_name.upper()] = raw_comment.replace("''", "'").strip()
    return result


def parse_view_map(sql_text: str) -> dict[str, str]:
    return {name.upper(): body.strip() for name, body in VIEW_RE.findall(sql_text)}


def parse_graph_metadata(sql_text: str) -> tuple[str, list[VertexDefinition], list[EdgeDefinition]]:
    graph_match = GRAPH_RE.search(sql_text)
    if not graph_match:
        raise ValueError("DDL file does not contain CREATE OR REPLACE PROPERTY GRAPH")
    graph_name = graph_match.group(1).upper()
    graph_sql = sql_text[graph_match.start():]
    vertex_section, vertex_end = extract_balanced_section(graph_sql, "VERTEX TABLES")
    edge_section, _edge_end = extract_balanced_section(graph_sql[vertex_end:], "EDGE TABLES")
    vertices = parse_vertex_entries(vertex_section)
    edges = parse_edge_entries(edge_section)
    return graph_name, vertices, edges


def parse_vertex_entries(section: str) -> list[VertexDefinition]:
    entries = split_top_level(section)
    result: list[VertexDefinition] = []
    for item in entries:
        token = " ".join(item.split())
        match = re.match(
            r"([A-Z0-9_]+)\s+AS\s+([A-Z0-9_]+)\s+KEY\s*\(([^)]*)\)\s+(?:PROPERTIES\s*\((.*?)\)|NO\s+PROPERTIES)\s*$",
            token,
            re.IGNORECASE,
        )
        if not match:
            raise ValueError(f"Unable to parse vertex table entry: {item[:120]}")
        table_name, graph_label, key_columns, props = match.groups()
        result.append(
            VertexDefinition(
                table_name=table_name.upper(),
                graph_label=graph_label.upper(),
                key_columns=[part.strip().upper() for part in split_top_level(key_columns)],
                property_columns=[part.strip().upper() for part in split_top_level(props or "") if part.strip()],
            )
        )
    return result


def parse_edge_entries(section: str) -> list[EdgeDefinition]:
    entries = split_top_level(section)
    result: list[EdgeDefinition] = []
    pattern = re.compile(
        r"([A-Z0-9_]+)\s+AS\s+([A-Z0-9_]+)\s+"
        r"KEY\s*\(([^)]*)\)\s+"
        r"SOURCE\s+KEY\s*\(([^)]*)\)\s+REFERENCES\s+([A-Z0-9_]+)\s*\(([^)]*)\)\s+"
        r"DESTINATION\s+KEY\s*\(([^)]*)\)\s+REFERENCES\s+([A-Z0-9_]+)\s*\(([^)]*)\)\s+"
        r"(?:PROPERTIES\s*\((.*?)\)|NO\s+PROPERTIES)\s*$",
        re.IGNORECASE,
    )
    for item in entries:
        token = " ".join(item.split())
        match = pattern.match(token)
        if not match:
            raise ValueError(f"Unable to parse edge table entry: {item[:120]}")
        (
            table_name,
            graph_label,
            key_columns,
            source_key,
            source_label,
            source_ref_key,
            target_key,
            target_label,
            target_ref_key,
            props,
        ) = match.groups()
        result.append(
            EdgeDefinition(
                table_name=table_name.upper(),
                graph_label=graph_label.upper(),
                key_columns=[part.strip().upper() for part in split_top_level(key_columns)],
                source_label=source_label.upper(),
                source_key=source_ref_key.strip().upper(),
                target_label=target_label.upper(),
                target_key=target_ref_key.strip().upper(),
                property_columns=[part.strip().upper() for part in split_top_level(props or "") if part.strip()],
            )
        )
    return result


def _display_name_from_comment(comment: str, fallback: str) -> str:
    text = (comment or "").strip()
    for marker in ("顶点", "边", "("):
        if marker in text:
            left = text.split(marker, 1)[0].strip(" -—_")
            if left:
                return left
    return fallback


def _pascal_name(label: str) -> str:
    parts = [item for item in label.strip().split("_") if item]
    return "".join(part.capitalize() for part in parts) or label.title()


def _guess_relation_cardinality(edge_label: str) -> str:
    token = edge_label.upper()
    if token in {"INSTANCE_OF", "USES", "USES_EQUIPMENT", "BELONGS_TO", "SOURCED_FROM"}:
        return "MANY_TO_ONE"
    if token in {"HAS_DEFECT", "INSPECTED_BY"}:
        return "ONE_TO_MANY"
    if token in {"PRECEDES", "SUCCEEDS", "FOLLOWS", "PERFORMED_BY"}:
        return "MANY_TO_MANY"
    return "MANY_TO_MANY"


def _column_token(name: str) -> str:
    return str(name or "").strip().upper()


def _property_name(column_name: str) -> str:
    return str(column_name or "").strip().lower()


def _infer_data_type(view_sql: str, column_name: str) -> str:
    token = _column_token(column_name)
    normalized = " ".join(view_sql.upper().split())
    if re.search(rf"\bAS\s+{re.escape(token)}\b", normalized):
        item_match = re.search(rf"(.+?)\bAS\s+{re.escape(token)}\b", normalized)
        if item_match:
            expr = item_match.group(1)
            if "CAST(" in expr:
                cast_match = re.search(r"\bAS\s+(VARCHAR2|CHAR|NUMBER|DATE|TIMESTAMP)(?:\([^)]*\))?\)", expr)
                if cast_match:
                    return cast_match.group(1)
            if "COUNT(" in expr or "SUM(" in expr or "AVG(" in expr or "MIN(" in expr or "MAX(" in expr:
                return "NUMBER"
            if "DATE" in expr or "_TIME" in token:
                return "DATE"
            if re.search(r"\b[0-9]+(?:\.[0-9]+)?\b", expr):
                return "NUMBER"
    if token.endswith("_TIME") or token.endswith("_DATE"):
        return "DATE"
    if token.endswith("_COUNT") or token.endswith("_QTY") or token.endswith("_WEIGHT") or token.endswith("_PRES") or token.endswith("_DAYS"):
        return "NUMBER"
    return "VARCHAR2"


def build_import_summary(sql_text: str, ddl_path: Path) -> dict[str, Any]:
    comments = parse_comment_map(sql_text)
    view_map = parse_view_map(sql_text)
    graph_name, vertices, edges = parse_graph_metadata(sql_text)
    entity_by_label: dict[str, dict[str, Any]] = {}

    for vertex in vertices:
        view_sql = view_map.get(vertex.table_name)
        if not view_sql:
            raise ValueError(f"Missing CREATE VIEW definition for {vertex.table_name}")
        entity_name = _pascal_name(vertex.graph_label)
        display_name = _display_name_from_comment(comments.get(vertex.table_name, ""), entity_name)
        property_columns = list(dict.fromkeys(vertex.key_columns + vertex.property_columns))
        properties = []
        for order_num, column_name in enumerate(property_columns, start=1):
            properties.append({
                "property_name": _property_name(column_name),
                "property_display_name": column_name,
                "data_type": _infer_data_type(view_sql, column_name),
                "is_primary_key": "Y" if column_name in vertex.key_columns else "N",
                "is_nullable": "Y",
                "property_desc": f"来源列 {column_name}",
                "order_num": order_num,
                "source_mark": "MAPPED",
                "mapping": {
                    "source_table": vertex.table_name,
                    "source_column": column_name,
                    "mapping_type": "DIRECT",
                    "confidence": "HIGH",
                    "mapping_status": "CONFIRMED",
                },
            })
        entity = {
            "graph_label": vertex.graph_label,
            "entity_name": entity_name,
            "entity_display_name": display_name,
            "entity_desc": comments.get(vertex.table_name) or f"根据 {ddl_path.name} 反向导入的节点定义",
            "build_type": "VIEW",
            "table_name": vertex.table_name,
            "status": "DEPLOYED",
            "object_type": "DIMENSION",
            "properties": properties,
            "entity_mapping": {
                "build_type": "VIEW",
                "view_sql": view_sql.rstrip(";"),
                "mapping_status": "CONFIRMED",
            },
        }
        entity_by_label[vertex.graph_label] = entity

    relations = []
    for edge in edges:
        view_sql = view_map.get(edge.table_name)
        if not view_sql:
            raise ValueError(f"Missing CREATE VIEW definition for {edge.table_name}")
        source_entity = entity_by_label.get(edge.source_label)
        target_entity = entity_by_label.get(edge.target_label)
        if not source_entity or not target_entity:
            raise ValueError(f"Edge {edge.table_name} references unknown labels {edge.source_label}->{edge.target_label}")
        relations.append({
            "graph_label": edge.graph_label,
            "relation_name": _pascal_name(edge.graph_label),
            "relation_desc": comments.get(edge.table_name) or f"根据 {ddl_path.name} 反向导入的边定义",
            "relation_type": "ASSOCIATION",
            "relation_table_name": edge.table_name,
            "source_entity_name": source_entity["entity_name"],
            "target_entity_name": target_entity["entity_name"],
            "mapping": {
                "source_table": source_entity["table_name"],
                "target_table": target_entity["table_name"],
                "relation_table": edge.table_name,
                "join_condition": "",
                "edge_sql": view_sql.rstrip(";"),
                "mapping_mode": "DIRECT",
                "relation_cardinality": _guess_relation_cardinality(edge.graph_label),
                "mapping_status": "CONFIRMED",
            },
            "properties": edge.property_columns,
        })

    return {
        "ddl_file": str(ddl_path),
        "graph_name": graph_name,
        "entity_count": len(entity_by_label),
        "relation_count": len(relations),
        "entities": list(entity_by_label.values()),
        "relations": relations,
    }


def _import_db_modules():
    repo_root = Path(__file__).resolve().parents[2]
    backend_root = repo_root / "backend"
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
    from app.core.database import SessionLocal
    from app.models.models import (
        SysDomain,
        SysEntityMapping,
        SysOntologyEntity,
        SysOntologyProperty,
        SysOntologyRelation,
        SysPropertyMapping,
        SysRelationMapping,
        generate_id,
    )
    return {
        "SessionLocal": SessionLocal,
        "SysDomain": SysDomain,
        "SysOntologyEntity": SysOntologyEntity,
        "SysOntologyProperty": SysOntologyProperty,
        "SysOntologyRelation": SysOntologyRelation,
        "SysEntityMapping": SysEntityMapping,
        "SysPropertyMapping": SysPropertyMapping,
        "SysRelationMapping": SysRelationMapping,
        "generate_id": generate_id,
    }


def _delete_domain_metadata(db: Any, models: dict[str, Any], domain_id: str) -> None:
    SysOntologyEntity = models["SysOntologyEntity"]
    SysOntologyRelation = models["SysOntologyRelation"]
    SysEntityMapping = models["SysEntityMapping"]
    SysPropertyMapping = models["SysPropertyMapping"]
    SysRelationMapping = models["SysRelationMapping"]
    SysOntologyProperty = models["SysOntologyProperty"]

    db.query(SysPropertyMapping).filter(
        SysPropertyMapping.property_id.in_(
            db.query(SysOntologyProperty.property_id).join(
                SysOntologyEntity, SysOntologyEntity.entity_id == SysOntologyProperty.entity_id
            ).filter(SysOntologyEntity.domain_id == domain_id)
        )
    ).delete(synchronize_session=False)
    db.query(SysEntityMapping).filter(
        SysEntityMapping.entity_id.in_(
            db.query(SysOntologyEntity.entity_id).filter(SysOntologyEntity.domain_id == domain_id)
        )
    ).delete(synchronize_session=False)
    db.query(SysRelationMapping).filter(
        SysRelationMapping.relation_id.in_(
            db.query(SysOntologyRelation.relation_id).filter(SysOntologyRelation.domain_id == domain_id)
        )
    ).delete(synchronize_session=False)
    db.query(SysOntologyProperty).filter(
        SysOntologyProperty.entity_id.in_(
            db.query(SysOntologyEntity.entity_id).filter(SysOntologyEntity.domain_id == domain_id)
        )
    ).delete(synchronize_session=False)
    db.query(SysOntologyRelation).filter(SysOntologyRelation.domain_id == domain_id).delete(synchronize_session=False)
    db.query(SysOntologyEntity).filter(SysOntologyEntity.domain_id == domain_id).delete(synchronize_session=False)


def apply_import(summary: dict[str, Any], domain_name: str, domain_id: str | None, created_by: str, replace_domain_metadata: bool) -> dict[str, Any]:
    models = _import_db_modules()
    SessionLocal = models["SessionLocal"]
    SysDomain = models["SysDomain"]
    SysOntologyEntity = models["SysOntologyEntity"]
    SysOntologyProperty = models["SysOntologyProperty"]
    SysOntologyRelation = models["SysOntologyRelation"]
    SysEntityMapping = models["SysEntityMapping"]
    SysPropertyMapping = models["SysPropertyMapping"]
    SysRelationMapping = models["SysRelationMapping"]
    generate_id = models["generate_id"]

    db = SessionLocal()
    now = datetime.utcnow()
    try:
        domain = None
        if domain_id:
            domain = db.query(SysDomain).filter(SysDomain.domain_id == domain_id).first()
        if not domain:
            domain = db.query(SysDomain).filter(SysDomain.domain_name == domain_name).first()
        if not domain:
            domain = SysDomain(
                domain_id=domain_id or generate_id("dm"),
                domain_name=domain_name,
                domain_desc=f"根据 {Path(summary['ddl_file']).name} 反向导入的业务分析域",
                status="ACTIVE",
                created_by=created_by,
            )
            db.add(domain)
            db.flush()
        else:
            domain.domain_name = domain_name
            domain.updated_at = now

        if replace_domain_metadata:
            _delete_domain_metadata(db, models, domain.domain_id)
            db.flush()

        existing_entities = {
            item.table_name.upper(): item
            for item in db.query(SysOntologyEntity).filter(SysOntologyEntity.domain_id == domain.domain_id).all()
            if (item.table_name or "").strip()
        }
        entity_by_name = {
            item.entity_name.upper(): item
            for item in db.query(SysOntologyEntity).filter(SysOntologyEntity.domain_id == domain.domain_id).all()
        }

        persisted_entities: dict[str, Any] = {}
        for entity_data in summary["entities"]:
            table_name = entity_data["table_name"].upper()
            entity = existing_entities.get(table_name) or entity_by_name.get(entity_data["entity_name"].upper())
            if not entity:
                entity = SysOntologyEntity(
                    entity_id=generate_id("ent"),
                    domain_id=domain.domain_id,
                    entity_name=entity_data["entity_name"],
                    entity_display_name=entity_data["entity_display_name"],
                    entity_desc=entity_data["entity_desc"],
                    object_type=entity_data["object_type"],
                    build_type=entity_data["build_type"],
                    table_name=entity_data["table_name"],
                    status=entity_data["status"],
                    created_by=created_by,
                    created_at=now,
                    updated_at=now,
                )
                db.add(entity)
            entity.entity_name = entity_data["entity_name"]
            entity.entity_display_name = entity_data["entity_display_name"]
            entity.entity_desc = entity_data["entity_desc"]
            entity.object_type = entity_data["object_type"]
            entity.build_type = entity_data["build_type"]
            entity.table_name = entity_data["table_name"]
            entity.status = entity_data["status"]
            entity.updated_at = now

            existing_props = {item.property_name.lower(): item for item in entity.properties or []}
            seen_props: set[str] = set()
            for prop_data in entity_data["properties"]:
                property_name = prop_data["property_name"].lower()
                seen_props.add(property_name)
                prop = existing_props.get(property_name)
                if not prop:
                    prop = SysOntologyProperty(
                        property_id=generate_id("prop"),
                        entity_id=entity.entity_id,
                        property_name=property_name,
                        property_display_name=prop_data["property_display_name"],
                        data_type=prop_data["data_type"],
                        is_primary_key=prop_data["is_primary_key"],
                        is_nullable=prop_data["is_nullable"],
                        property_desc=prop_data["property_desc"],
                        order_num=prop_data["order_num"],
                        source_mark=prop_data["source_mark"],
                        created_at=now,
                        updated_at=now,
                    )
                    db.add(prop)
                prop.property_name = property_name
                prop.property_display_name = prop_data["property_display_name"]
                prop.data_type = prop_data["data_type"]
                prop.is_primary_key = prop_data["is_primary_key"]
                prop.is_nullable = prop_data["is_nullable"]
                prop.property_desc = prop_data["property_desc"]
                prop.order_num = prop_data["order_num"]
                prop.source_mark = prop_data["source_mark"]
                prop.updated_at = now

                mapping = prop.mapping
                if not mapping:
                    mapping = SysPropertyMapping(
                        mapping_id=generate_id("pmap"),
                        property_id=prop.property_id,
                        mapped_at=now,
                    )
                    db.add(mapping)
                mapping.source_table = prop_data["mapping"]["source_table"]
                mapping.source_column = prop_data["mapping"]["source_column"]
                mapping.mapping_type = prop_data["mapping"]["mapping_type"]
                mapping.confidence = prop_data["mapping"]["confidence"]
                mapping.mapping_status = prop_data["mapping"]["mapping_status"]
                mapping.mapped_by = created_by
                mapping.mapped_at = now

            for stale_name, stale_prop in existing_props.items():
                if stale_name not in seen_props:
                    if stale_prop.mapping:
                        db.delete(stale_prop.mapping)
                    db.delete(stale_prop)

            entity_mapping = entity.entity_mapping
            if not entity_mapping:
                entity_mapping = SysEntityMapping(
                    mapping_id=generate_id("emap"),
                    entity_id=entity.entity_id,
                )
                db.add(entity_mapping)
            entity_mapping.build_type = entity_data["entity_mapping"]["build_type"]
            entity_mapping.view_sql = entity_data["entity_mapping"]["view_sql"]
            entity_mapping.mapping_status = entity_data["entity_mapping"]["mapping_status"]
            entity_mapping.mapped_by = created_by
            entity_mapping.mapped_at = now
            persisted_entities[entity_data["entity_name"]] = entity

        existing_relations = {
            item.relation_table_name.upper(): item
            for item in db.query(SysOntologyRelation).filter(SysOntologyRelation.domain_id == domain.domain_id).all()
            if (item.relation_table_name or "").strip()
        }
        relation_keys = {
            ((item.relation_name or "").upper(), item.source_entity_id, item.target_entity_id): item
            for item in db.query(SysOntologyRelation).filter(SysOntologyRelation.domain_id == domain.domain_id).all()
        }
        seen_relation_ids: set[str] = set()
        for relation_data in summary["relations"]:
            source_entity = persisted_entities[relation_data["source_entity_name"]]
            target_entity = persisted_entities[relation_data["target_entity_name"]]
            relation = existing_relations.get(relation_data["relation_table_name"].upper()) or relation_keys.get(
                (relation_data["relation_name"].upper(), source_entity.entity_id, target_entity.entity_id)
            )
            if not relation:
                relation = SysOntologyRelation(
                    relation_id=generate_id("rel"),
                    domain_id=domain.domain_id,
                    source_entity_id=source_entity.entity_id,
                    target_entity_id=target_entity.entity_id,
                    relation_name=relation_data["relation_name"],
                    relation_type=relation_data["relation_type"],
                    relation_desc=relation_data["relation_desc"],
                    relation_table_name=relation_data["relation_table_name"],
                    created_at=now,
                    updated_at=now,
                )
                db.add(relation)
            relation.source_entity_id = source_entity.entity_id
            relation.target_entity_id = target_entity.entity_id
            relation.relation_name = relation_data["relation_name"]
            relation.relation_type = relation_data["relation_type"]
            relation.relation_desc = relation_data["relation_desc"]
            relation.relation_table_name = relation_data["relation_table_name"]
            relation.updated_at = now
            seen_relation_ids.add(relation.relation_id)

            relation_mapping = relation.relation_mapping
            if not relation_mapping:
                relation_mapping = SysRelationMapping(
                    mapping_id=generate_id("rmap"),
                    relation_id=relation.relation_id,
                )
                db.add(relation_mapping)
            relation_mapping.source_table = relation_data["mapping"]["source_table"]
            relation_mapping.target_table = relation_data["mapping"]["target_table"]
            relation_mapping.relation_table = relation_data["mapping"]["relation_table"]
            relation_mapping.join_condition = relation_data["mapping"]["join_condition"]
            relation_mapping.edge_sql = relation_data["mapping"]["edge_sql"]
            relation_mapping.mapping_mode = relation_data["mapping"]["mapping_mode"]
            relation_mapping.relation_cardinality = relation_data["mapping"]["relation_cardinality"]
            relation_mapping.mapping_status = relation_data["mapping"]["mapping_status"]
            relation_mapping.mapped_by = created_by
            relation_mapping.mapped_at = now

        if replace_domain_metadata:
            for relation in db.query(SysOntologyRelation).filter(SysOntologyRelation.domain_id == domain.domain_id).all():
                if relation.relation_id not in seen_relation_ids:
                    if relation.relation_mapping:
                        db.delete(relation.relation_mapping)
                    db.delete(relation)

        db.commit()
        return {
            "domain_id": domain.domain_id,
            "domain_name": domain.domain_name,
            "entity_count": len(summary["entities"]),
            "relation_count": len(summary["relations"]),
            "graph_name": summary["graph_name"],
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> int:
    args = parse_args()
    ddl_path = Path(args.ddl_file).expanduser().resolve()
    if not ddl_path.is_file():
        raise SystemExit(f"DDL file not found: {ddl_path}")
    sql_text = ddl_path.read_text(encoding="utf-8")
    summary = build_import_summary(sql_text, ddl_path)
    if args.summary_json:
        Path(args.summary_json).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ddl_file": summary["ddl_file"],
                "graph_name": summary["graph_name"],
                "entity_count": summary["entity_count"],
                "relation_count": summary["relation_count"],
                "entity_names": [item["entity_name"] for item in summary["entities"]],
                "relation_names": [item["relation_name"] for item in summary["relations"]],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if not args.apply:
        print("Dry run completed. No database changes were made.")
        return 0
    result = apply_import(
        summary=summary,
        domain_name=args.domain_name,
        domain_id=args.domain_id,
        created_by=args.created_by,
        replace_domain_metadata=args.replace_domain_metadata,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
