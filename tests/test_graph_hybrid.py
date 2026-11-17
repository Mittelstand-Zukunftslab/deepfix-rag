"""The graph path end to end: ontology extraction, deduplication, and `graph_hybrid`.

These cases need a real FalkorDB instance, because the parts they cover are the
ones a stub cannot tell the truth about: whether strict extraction drops a pair
the ontology forbids, whether a merge re-points the edges a traversal walks, and
whether a retriever returns citable chunks rather than triplet text.

The embedding model is a `MockEmbedding`, so nothing reaches OpenRouter.
`FALKORDB_HOST`/`FALKORDB_PORT` point at the host; `DEEPFIX_FALKORDB_REQUIRED=1`
turns "no host, skipped" into a failure, which is how CI runs this module.
"""

import json
import os
from pathlib import Path
from typing import Any

from llama_index.core.embeddings import MockEmbedding
from llama_index.graph_stores.falkordb import FalkorDBPropertyGraphStore
import pytest

from deepfix_rag.config.settings import settings

pytestmark = [pytest.mark.integration]

EMBED_DIM = 16


@pytest.fixture(scope="module")
def falkordb_store() -> FalkorDBPropertyGraphStore:
    """A reachable FalkorDB graph for this module, or a skip (or a failure in CI).

    The graph is a scratch database, not the deployment's: this module deletes
    every node in it, and pointing it at `FALKORDB_DATABASE` would wipe a
    developer's real graph.
    """
    url = f"redis://{settings.FALKORDB_HOST}:{settings.FALKORDB_PORT}"
    database = "deepfix_graph_hybrid_test"
    try:
        store = FalkorDBPropertyGraphStore(url=url, database=database)
        store.structured_query("RETURN 1")
        store.structured_query("MATCH (n) DETACH DELETE n")
    except Exception as exc:
        if os.environ.get("DEEPFIX_FALKORDB_REQUIRED") == "1":
            pytest.fail(
                f"Graph retrieval needs a reachable FalkorDB at {url} because "
                f"DEEPFIX_FALKORDB_REQUIRED=1, and the connection failed: {type(exc).__name__}"
            )
        pytest.skip(
            f"No FalkorDB at {url} ({type(exc).__name__}). Start one with "
            "`docker compose -f compose.graphdb.yml up -d falkordb`, or set "
            "FALKORDB_HOST/FALKORDB_PORT. DEEPFIX_FALKORDB_REQUIRED=1 makes this "
            "a failure instead of a skip."
        )
    previous = settings.FALKORDB_DATABASE
    settings.FALKORDB_DATABASE = database
    try:
        yield store
    finally:
        settings.FALKORDB_DATABASE = previous
        store.structured_query("MATCH (n) DETACH DELETE n")


def test_deduplication_never_merges_identifier_pairs(
    falkordb_store: FalkorDBPropertyGraphStore,
) -> None:
    """Two printed papers whose numbers differ stay two nodes, whatever their text."""
    from deepfix_rag.graph_dedup import plan_merges, read_entities

    falkordb_store.structured_query("MATCH (n) DETACH DELETE n")
    falkordb_store.structured_query(
        "CREATE (a:__Entity__:DRUCKSACHE {id: 'BT-Drucksache 20/123', "
        "name: 'BT-Drucksache 20/123'}), "
        "(b:__Entity__:DRUCKSACHE {id: 'BT-Drucksache 20/124', "
        "name: 'BT-Drucksache 20/124'}), "
        "(c:__Entity__:FRAKTION {id: 'SPD-Fraktion', name: 'SPD-Fraktion'}), "
        "(d:__Entity__:FRAKTION {id: 'SPD Fraktion', name: 'SPD Fraktion'})"
    )
    by_label: dict[str, list[Any]] = {}
    for entity in read_entities(falkordb_store):
        by_label.setdefault(entity.label, []).append(entity)
    embed_model = MockEmbedding(embed_dim=EMBED_DIM)

    papers = plan_merges(
        by_label["DRUCKSACHE"],
        embed_model,
        similarity_threshold=0.0,
        ratio_threshold=0.0,
        max_edit_distance=10,
    )
    assert papers == [], "two different Drucksachen were merged"

    groups = plan_merges(
        by_label["FRAKTION"],
        embed_model,
        similarity_threshold=0.0,
        ratio_threshold=0.0,
        max_edit_distance=10,
    )
    assert len(groups) == 1
    names = {groups[0].canonical.name, *(duplicate.name for duplicate in groups[0].duplicates)}
    assert names == {"SPD-Fraktion", "SPD Fraktion"}
    assert len(groups[0].duplicates) == 1
    falkordb_store.structured_query("MATCH (n) DETACH DELETE n")


def test_a_merge_repoints_edges_and_logs_itself(
    falkordb_store: FalkorDBPropertyGraphStore,
    tmp_path: Path,
) -> None:
    """Merging a duplicate moves its edges to the survivor and appends one log line."""
    from deepfix_rag.graph_dedup import deduplicate_property_graph

    falkordb_store.structured_query("MATCH (n) DETACH DELETE n")
    falkordb_store.structured_query(
        "CREATE (a:__Entity__:FRAKTION {id: 'SPD-Fraktion', name: 'SPD-Fraktion'}), "
        "(b:__Entity__:FRAKTION {id: 'SPD Fraktion', name: 'SPD Fraktion'}), "
        "(c:__Entity__:PERSON {id: 'Alice Berger', name: 'Alice Berger'}), "
        "(c)-[:MEMBER_OF]->(b)"
    )
    log_path = tmp_path / "graph_merges.jsonl"
    merged = deduplicate_property_graph(
        graph_store=falkordb_store,
        embed_model=MockEmbedding(embed_dim=EMBED_DIM),
        log_path=log_path,
        similarity_threshold=0.0,
        ratio_threshold=0.0,
        max_edit_distance=10,
    )

    assert merged == 1
    entities = {
        row["id"]
        for row in falkordb_store.structured_query("MATCH (e:__Entity__) RETURN e.id AS id")
    }
    # Exactly one of the two spellings survives, and the survivor keeps the edges.
    survivors = entities & {"SPD-Fraktion", "SPD Fraktion"}
    assert len(survivors) == 1
    survivor = survivors.pop()
    assert "Alice Berger" in entities
    edges = falkordb_store.structured_query(
        "MATCH (a:__Entity__ {id: 'Alice Berger'})-[r]->(b) RETURN type(r) AS type, b.id AS target"
    )
    assert {"type": "MEMBER_OF", "target": survivor} in edges

    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert set(records[0]) == {
        "label",
        "canonical",
        "merged",
        "similarity",
        "name_ratio",
        "edit_distance",
        "reason",
    }
    assert records[0]["label"] == "FRAKTION"
    assert records[0]["canonical"] == survivor
    assert records[0]["merged"] in {"SPD-Fraktion", "SPD Fraktion"}
    assert records[0]["merged"] != survivor
    falkordb_store.structured_query("MATCH (n) DETACH DELETE n")
