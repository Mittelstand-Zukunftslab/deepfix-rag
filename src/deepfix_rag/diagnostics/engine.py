"""DeepFix diagnostics engine wrapping DeepFixClient for multi-agent RAG analysis."""

import asyncio
from pathlib import Path
import random
from typing import Any

from deepfix_core.models.api import APIResponse
from deepfix_sdk import DeepFixClient, MLflowConfig
from deepfix_sdk.ir import InformationRetrievalDataset, LlamaindexModel
from llama_index.core.schema import Document
import pandas as pd

from deepfix_rag.config.schema import ChunkingConfig, DataLoaderConfig, RAGConfig
from deepfix_rag.config.settings import LOGGER, settings
from deepfix_rag.data import DataLoader


def prepare_ir_dataset(
    dataset_name: str, chunking_config: ChunkingConfig, data_loader_config: DataLoaderConfig
) -> InformationRetrievalDataset:

    loader = DataLoader(data_loader_config)
    qa_data = loader.load_qa()
    if not qa_data:
        raise ValueError(
            f"Dataset '{data_loader_config.path_or_repo}' returned no QA pairs to evaluate."
        )

    gold_ids = list(dict.fromkeys(r.doc_id for r in qa_data))

    # 3. Resolve corpus and chunk for embed-size, keeping parent doc_id on Document.id_
    corpus_docs = DataLoader.chunk_docs(
        loader.load_corpus(doc_ids=gold_ids),
        chunking_config,
    )

    def _chunk_id(doc: Document) -> str:
        return str(doc.metadata.get("chunk_id") or f"{doc.id_}::chunk_0")

    def _parent_id(doc: Document) -> str:
        return DataLoader.parent_doc_id(str(doc.metadata.get("doc_id") or doc.id_))

    def corpus_iter():
        for doc in corpus_docs:
            yield {"docno": _chunk_id(doc), "text": doc.text}

    # 4. Prepare topics and qrels DataFrames (positives at parent doc_id, IR rows at chunk_id)
    gold_qrels_df: pd.DataFrame = DataLoader.to_qrels_df(qa_data)
    topics_df = gold_qrels_df[["qid", "query"]].drop_duplicates().reset_index(drop=True)
    rng = random.Random(42)

    chunk_to_parent: dict[str, str] = {_chunk_id(doc): _parent_id(doc) for doc in corpus_docs}
    parent_to_chunks: dict[str, list[str]] = {}
    for cid, pid in chunk_to_parent.items():
        parent_to_chunks.setdefault(pid, []).append(cid)

    expanded_rows: list[dict[str, Any]] = []
    for row in gold_qrels_df.itertuples(index=False):
        parent_docno = str(row.docno)
        for cid in parent_to_chunks.get(parent_docno, []):
            expanded_rows.append(
                {
                    "qid": row.qid,
                    "docno": cid,
                    "label": 1,
                    "rank": row.rank,
                    "query": row.query,
                }
            )
    qrels_df = pd.DataFrame(expanded_rows, columns=["qid", "docno", "label", "rank", "query"])

    pos_parents_by_qid: dict[str, set[str]] = (
        gold_qrels_df.groupby("qid")["docno"].apply(set).to_dict()
    )
    all_chunk_ids = sorted(chunk_to_parent)
    negatives_per_query = 2
    neg_rows: list[dict[str, Any]] = []

    for _, row in topics_df.iterrows():
        qid = row["qid"]
        query = row["query"]
        pos_parents = pos_parents_by_qid.get(qid, set())
        candidates = [cid for cid in all_chunk_ids if chunk_to_parent[cid] not in pos_parents]
        if candidates:
            sampled = rng.sample(candidates, min(negatives_per_query, len(candidates)))
            for neg_id in sampled:
                neg_rows.append(
                    {"qid": qid, "docno": neg_id, "label": 0, "rank": 0, "query": query}
                )

    # Fallback: ensure at least 2 negatives exist for stratified split
    if len(neg_rows) < 2:
        for i in range(2 - len(neg_rows)):
            fallback_parent = f"neg_fallback_{i}"
            fallback_chunk = f"{fallback_parent}::chunk_0"
            corpus_docs.append(
                Document(
                    text="Irrelevant baseline document.",
                    id_=fallback_parent,
                    metadata={"doc_id": fallback_parent, "chunk_id": fallback_chunk},
                )
            )
            chunk_to_parent[fallback_chunk] = fallback_parent
            neg_rows.append(
                {
                    "qid": topics_df.iloc[0]["qid"],
                    "docno": fallback_chunk,
                    "label": 0,
                    "rank": 0,
                    "query": topics_df.iloc[0]["query"],
                }
            )

    if neg_rows:
        LOGGER.info(f"Adding {len(neg_rows)} negative rows to qrels DataFrame.")
        qrels_df = pd.concat([qrels_df, pd.DataFrame(neg_rows)], ignore_index=True)

    assert set(qrels_df["label"]) == {0, 1}, (
        f"Qrels DataFrame must contain only 0 and 1 labels. Found {set(qrels_df['label'])}."
    )

    # 5. Build InformationRetrievalDataset and split into train and test splits
    LOGGER.info(
        f"Building InformationRetrievalDataset for '{dataset_name}' ({len(topics_df)} topics, {len(qrels_df)} qrels)..."
    )
    return InformationRetrievalDataset(
        dataset_name=dataset_name,
        topics=topics_df,
        enable_embedding_pca=data_loader_config.enable_embedding_pca,
        embedding_pca_components=data_loader_config.embedding_pca_components,
        qrels=qrels_df,
        corpus_iter=corpus_iter,
    )


def run_diagnostics(
    dataset: str | Path | None = None,
    config: RAGConfig | Path | str | None = None,
    server_url: str | None = None,
    limit: int | None = None,
) -> APIResponse:
    """Synchronously run RAG retrieval evaluation and diagnose failures via DeepFixClient following IR tutorial."""

    # 1. Resolve configuration
    if isinstance(config, (str, Path)):
        resolved_config = RAGConfig.from_yaml(config)
    elif config is None:
        resolved_config = RAGConfig()
    else:
        resolved_config = config.model_copy(deep=True)

    data_updates: dict[str, Any] = {}
    if limit is not None:
        data_updates["limit"] = limit
    if dataset is not None:
        data_updates["path_or_repo"] = str(dataset)
        data_updates["source_type"] = None
    if data_updates:
        resolved_config.data = DataLoaderConfig.model_validate(
            {**resolved_config.data.model_dump(), **data_updates}
        )

    ir_ds: InformationRetrievalDataset = prepare_ir_dataset(
        dataset_name=Path(resolved_config.data.path_or_repo).stem or "rag_benchmark",
        chunking_config=resolved_config.chunking,
        data_loader_config=resolved_config.data,
    )

    # 6. Initialize and fit LlamaindexModel
    backend_val = (
        resolved_config.retrieval.backend.value
        if hasattr(resolved_config.retrieval.backend, "value")
        else str(resolved_config.retrieval.backend)
    )
    LOGGER.info(
        f"Initializing LlamaindexModel (backend={backend_val}, index_dir={resolved_config.retrieval.lancedb_index_dir})..."
    )
    model = LlamaindexModel(
        dataset=ir_ds,
        load_if_exists=resolved_config.retrieval.load_if_exists,
        lancedb_index_dir=resolved_config.retrieval.lancedb_index_dir,
        top_k=5,
        retrieval_mode=backend_val,
        dense_weight=resolved_config.retrieval.dense_weight,
        bm25_weight=resolved_config.retrieval.bm25_weight,
        language=resolved_config.retrieval.language,
        enable_reranking=resolved_config.rerank.enabled,
    )
    model.fit()

    LOGGER.info("Generating predictions and embeddings...")
    ir_ds.set_predictions(model.retrieve_dataframe(ir_ds))
    ir_ds.set_embeddings(model.get_embedding)

    # 7. Connect to DeepFix server and run diagnosis
    target_api_url = server_url or settings.DEEPFIX_SERVER_URL
    LOGGER.info(f"Connecting to DeepFix analysis server at {target_api_url}...")

    client = DeepFixClient(
        api_url=target_api_url,
        timeout=360,
        mlflow_config=MLflowConfig(tracking_uri=settings.MLFLOW_TRACKING_URI),
    )

    try:
        response: APIResponse = client.get_diagnosis(
            train_data=ir_ds,
            model=model,
            model_name="llamaindex",
            language="english",
        )
    except Exception as err:
        raise err

    return response


async def run_diagnostics_async(
    dataset: str | Path | None = None,
    config: RAGConfig | Path | str | None = None,
    server_url: str | None = None,
    limit: int | None = None,
) -> APIResponse:
    """Asynchronously run RAG retrieval evaluation and diagnose failures via DeepFixClient."""
    return await asyncio.to_thread(
        run_diagnostics,
        dataset=dataset,
        config=config,
        server_url=server_url,
        limit=limit,
    )
