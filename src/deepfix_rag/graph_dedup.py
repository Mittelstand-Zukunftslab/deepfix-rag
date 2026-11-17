"""Merge duplicate property-graph entities in Python and log every merge.

FalkorDB has no APOC, so the deduplication in LlamaIndex's property-graph guide
(one Cypher statement using ``apoc.text.distance`` and ``apoc.coll.*``) cannot
run here. This module does the same job in three steps: read the entities of each
label, decide with the embedding model and ``rapidfuzz``, then rewrite the graph
through the store's ``structured_query``.

Two entities merge only when both signals agree — their name embeddings are close
*and* their names are close — because either signal alone is wrong on this
corpus: embeddings collapse ``BT-Drucksache 20/123`` onto ``BT-Drucksache 20/124``,
and an edit distance alone never joins ``Ausschuss für auswärtige Angelegenheiten``
to ``Auswärtiger Ausschuss``. Names that carry different identifier tokens are
never merged at all, whatever the two signals say, so two different Drucksachen
stay two nodes.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Iterable

from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.graph_stores.types import PropertyGraphStore
import numpy as np
from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

from deepfix_rag.config.settings import LOGGER

MERGE_LOG_NAME = "graph_merges.jsonl"
BASE_ENTITY_LABEL = "__Entity__"


@dataclass(frozen=True)
class GraphEntity:
    """One entity node as the merge step reads it."""

    node_id: str
    name: str
    label: str
    aliases: tuple[str, ...]

    @property
    def lowered(self) -> str:
        return self.name.lower().strip()


@dataclass(frozen=True)
class EntityMerge:
    """One decided merge, and the evidence that decided it."""

    label: str
    canonical: str
    merged: str
    similarity: float
    name_ratio: float
    edit_distance: int
    reason: str

    def as_log_record(self) -> dict[str, Any]:
        """The JSON object appended to the merge log."""
        return {
            "label": self.label,
            "canonical": self.canonical,
            "merged": self.merged,
            "similarity": round(self.similarity, 6),
            "name_ratio": round(self.name_ratio, 4),
            "edit_distance": self.edit_distance,
            "reason": self.reason,
        }


@dataclass
class MergePlan:
    """One cluster to collapse, with the log records it produces."""

    canonical: GraphEntity
    duplicates: list[GraphEntity] = field(default_factory=list)
    records: list[EntityMerge] = field(default_factory=list)


def identifier_tokens(name: str) -> frozenset[str]:
    """The whitespace-separated tokens of a name that carry a number.

    ``"BT-Drucksache 20/123"`` gives ``{"20/123"}`` and ``"§ 315c StGB"`` gives
    ``{"315c"}``. Two names whose tokens differ describe different things, which
    keeps consecutive Drucksachen and neighbouring paragraphs apart even when
    their embeddings are nearly identical.
    """
    return frozenset(
        token for token in name.lower().split() if any(char.isdigit() for char in token)
    )


def names_are_close(
    left: str,
    right: str,
    *,
    ratio_threshold: float,
    max_edit_distance: int,
) -> tuple[bool, float, int]:
    """Whether two already-lower-cased names are close, with the evidence.

    Close means one name contains the other and their ``rapidfuzz`` partial ratio
    reaches the threshold, or their Levenshtein distance is within the allowed
    distance.
    """
    ratio = fuzz.partial_ratio(left, right) / 100.0
    distance = Levenshtein.distance(left, right)
    contained = left in right or right in left
    close = (contained and ratio >= ratio_threshold) or distance <= max_edit_distance
    return close, ratio, distance


def _cosine_similarities(vectors: np.ndarray) -> np.ndarray:
    """Pairwise cosine similarity, one row per entity."""
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    unit = vectors / norms
    return unit @ unit.T


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def read_entities(graph_store: PropertyGraphStore) -> list[GraphEntity]:
    """Every entity node, with the entity type that lives on its labels."""
    rows = graph_store.structured_query(
        f"MATCH (e:{BASE_ENTITY_LABEL}) "
        "RETURN e.id AS id, e.name AS name, e.aliases AS aliases, "
        f"[l IN labels(e) WHERE l <> '{BASE_ENTITY_LABEL}'] AS labels"
    )
    entities: list[GraphEntity] = []
    for row in rows or []:
        node_id = row.get("id")
        if not node_id:
            continue
        labels = _as_list(row.get("labels"))
        entities.append(
            GraphEntity(
                node_id=str(node_id),
                name=str(row.get("name") or node_id),
                label=labels[0] if labels else "entity",
                aliases=tuple(_as_list(row.get("aliases"))),
            )
        )
    return entities


class _UnionFind:
    """Disjoint sets over entity ids, so a chain of agreeing pairs becomes one cluster."""

    def __init__(self, ids: Iterable[str]) -> None:
        self._parent = {node_id: node_id for node_id in ids}

    def find(self, node_id: str) -> str:
        root = node_id
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[node_id] != root:
            self._parent[node_id], node_id = root, self._parent[node_id]
        return root

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self._parent[right_root] = left_root

    def clusters(self) -> list[list[str]]:
        grouped: dict[str, list[str]] = defaultdict(list)
        for node_id in self._parent:
            grouped[self.find(node_id)].append(node_id)
        return [members for members in grouped.values() if len(members) > 1]


def plan_merges(
    entities: list[GraphEntity],
    embed_model: BaseEmbedding,
    *,
    similarity_threshold: float,
    ratio_threshold: float,
    max_edit_distance: int,
) -> list[MergePlan]:
    """Decide which entities of one label name the same thing.

    Three passes, cheapest first: embedding similarity over the whole label group,
    the identifier guard, then the name test on the pairs that survive.
    """
    if len(entities) < 2:
        return []
    by_id = {entity.node_id: entity for entity in entities}
    vectors = np.asarray(
        embed_model.get_text_embedding_batch([entity.name for entity in entities]), dtype=float
    )
    if vectors.ndim != 2 or vectors.shape[0] != len(entities):
        LOGGER.warning("Skipped entity deduplication: the embedding batch came back malformed.")
        return []
    similarities = _cosine_similarities(vectors)

    union = _UnionFind(by_id)
    evidence: dict[frozenset[str], tuple[float, float, int, str]] = {}
    for left_at in range(len(entities)):
        for right_at in range(left_at + 1, len(entities)):
            left, right = entities[left_at], entities[right_at]
            if left.lowered == right.lowered:
                continue
            similarity = float(similarities[left_at, right_at])
            if similarity < similarity_threshold:
                continue
            if identifier_tokens(left.name) != identifier_tokens(right.name):
                continue
            close, ratio, distance = names_are_close(
                left.lowered,
                right.lowered,
                ratio_threshold=ratio_threshold,
                max_edit_distance=max_edit_distance,
            )
            if not close:
                continue
            union.union(left.node_id, right.node_id)
            contained = left.lowered in right.lowered or right.lowered in left.lowered
            evidence[frozenset((left.node_id, right.node_id))] = (
                similarity,
                ratio,
                distance,
                "contained" if contained else "edit_distance",
            )

    plans: list[MergePlan] = []
    for cluster in union.clusters():
        members = [by_id[node_id] for node_id in cluster]
        canonical = sorted(members, key=lambda entity: (-len(entity.name), entity.lowered))[0]
        plan = MergePlan(canonical=canonical)
        for member in members:
            if member.node_id == canonical.node_id:
                continue
            similarity, ratio, distance, reason = evidence.get(
                frozenset((member.node_id, canonical.node_id)),
                (0.0, 0.0, 0, "cluster"),
            )
            plan.duplicates.append(member)
            plan.records.append(
                EntityMerge(
                    label=member.label,
                    canonical=canonical.name,
                    merged=member.name,
                    similarity=similarity,
                    name_ratio=ratio,
                    edit_distance=distance,
                    reason=reason,
                )
            )
        plans.append(plan)
    return plans


def _entity_edges(graph_store: PropertyGraphStore, node_id: str) -> list[tuple[str, str, str]]:
    """Every edge touching one node, as ``(direction, relation type, other id)``."""
    rows = graph_store.structured_query(
        "MATCH (a)-[r]->(b) WHERE a.id = $node OR b.id = $node "
        "RETURN a.id AS source, type(r) AS type, b.id AS target",
        param_map={"node": node_id},
    )
    edges: list[tuple[str, str, str]] = []
    for row in rows or []:
        source, target = str(row["source"]), str(row["target"])
        if source == node_id:
            edges.append(("out", str(row["type"]), target))
        if target == node_id:
            edges.append(("in", str(row["type"]), source))
    return edges


def _apply_plan(graph_store: PropertyGraphStore, plan: MergePlan) -> None:
    """Re-point every edge of a duplicate onto the canonical node, then delete it.

    Relationship types cannot be parameterised, so each distinct type is one
    statement. ``MERGE`` rather than ``CREATE`` keeps one edge when the duplicate
    and the canonical node both pointed at the same node.
    """
    canonical = plan.canonical
    alias_names = sorted(
        {
            *canonical.aliases,
            *(name for duplicate in plan.duplicates for name in duplicate.aliases),
        }
        | {duplicate.name for duplicate in plan.duplicates}
    )
    graph_store.structured_query(
        f"MATCH (c:{BASE_ENTITY_LABEL} {{id: $canonical}}) SET c.aliases = $aliases",
        param_map={"canonical": canonical.node_id, "aliases": alias_names},
    )
    for duplicate in plan.duplicates:
        for direction, relation_type, other_id in _entity_edges(graph_store, duplicate.node_id):
            if other_id == canonical.node_id:
                # Both ends collapse onto the canonical node; the self-relation is
                # dropped with the duplicate's own edge below.
                continue
            label = f"`{relation_type.replace('`', '')}`"
            if direction == "out":
                statement = (
                    f"MATCH (d:{BASE_ENTITY_LABEL} {{id: $duplicate}})-[r:{label}]->(o {{id: $other}}) "
                    f"MATCH (c:{BASE_ENTITY_LABEL} {{id: $canonical}}) "
                    f"MERGE (c)-[n:{label}]->(o) SET n += properties(r) DELETE r"
                )
            else:
                statement = (
                    f"MATCH (o {{id: $other}})-[r:{label}]->(d:{BASE_ENTITY_LABEL} {{id: $duplicate}}) "
                    f"MATCH (c:{BASE_ENTITY_LABEL} {{id: $canonical}}) "
                    f"MERGE (o)-[n:{label}]->(c) SET n += properties(r) DELETE r"
                )
            graph_store.structured_query(
                statement,
                param_map={
                    "canonical": canonical.node_id,
                    "duplicate": duplicate.node_id,
                    "other": other_id,
                },
            )
        graph_store.structured_query(
            f"MATCH (d:{BASE_ENTITY_LABEL} {{id: $duplicate}}) DETACH DELETE d",
            param_map={"duplicate": duplicate.node_id},
        )
    graph_store.structured_query(
        f"MATCH (c:{BASE_ENTITY_LABEL} {{id: $canonical}})-[r]->(c) DELETE r",
        param_map={"canonical": canonical.node_id},
    )


def deduplicate_property_graph(
    *,
    graph_store: PropertyGraphStore,
    embed_model: BaseEmbedding,
    log_path: Path | str,
    similarity_threshold: float,
    ratio_threshold: float,
    max_edit_distance: int,
) -> int:
    """Merge duplicate entities and return how many were merged away."""
    entities = read_entities(graph_store)
    by_label: dict[str, list[GraphEntity]] = defaultdict(list)
    for entity in entities:
        by_label[entity.label].append(entity)

    log = Path(log_path)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("", encoding="utf-8")

    merged_total = 0
    for label in sorted(by_label):
        plans = plan_merges(
            by_label[label],
            embed_model,
            similarity_threshold=similarity_threshold,
            ratio_threshold=ratio_threshold,
            max_edit_distance=max_edit_distance,
        )
        if not plans:
            continue
        for plan in plans:
            _apply_plan(graph_store, plan)
        with log.open("a", encoding="utf-8") as handle:
            for plan in plans:
                for record in plan.records:
                    handle.write(json.dumps(record.as_log_record(), ensure_ascii=False) + "\n")
        label_total = sum(len(plan.records) for plan in plans)
        merged_total += label_total
        LOGGER.info("Merged %d duplicate %s entities.", label_total, label)

    return merged_total
