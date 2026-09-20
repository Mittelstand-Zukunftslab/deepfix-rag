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

from deepfix_rag.config.schema import DataLoaderConfig, RAGConfig
from deepfix_rag.config.settings import LOGGER, settings
from deepfix_rag.data import DataLoader


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

    effective_limit = limit if limit is not None else resolved_config.data.limit
    if effective_limit is None:
        effective_limit = 50
    resolved_config.data.limit = effective_limit

    # 2. Resolve dataset & QA pairs
    if dataset is None:
        dataset = resolved_config.data.path_or_repo
    dataset_path_str = str(dataset)

    dataset_name = Path(dataset_path_str).stem or "rag_benchmark"
    qa_cfg = DataLoaderConfig(path_or_repo=dataset_path_str, limit=effective_limit)
    qa_data = DataLoader().load_qa(qa_cfg)
    if not qa_data:
        raise ValueError(f"Dataset '{dataset_path_str}' returned no QA pairs to evaluate.")

    # 3. Resolve corpus
    corpus_docs = DataLoader().load_corpus(resolved_config.data)

    def corpus_iter():
        for doc in corpus_docs:
            yield {"docno": doc.id_, "text": doc.text}

    # 4. Prepare topics and qrels DataFrames (with positive and negative samples)
    qrels_df: pd.DataFrame = DataLoader.to_qrels_df(qa_data)
    topics_df = qrels_df[["qid", "query"]].drop_duplicates().reset_index(drop=True)
    rng = random.Random(42)

    # Sample negative documents per query from corpus
    all_corpus_ids = sorted({doc.id_ for doc in corpus_docs})
    pos_by_qid: dict[str, set[str]] = qrels_df.groupby("qid")["docno"].apply(set).to_dict()
    negatives_per_query = 2
    neg_rows: list[dict[str, Any]] = []

    for _, row in topics_df.iterrows():
        qid = row["qid"]
        query = row["query"]
        pos_ids = pos_by_qid.get(qid, set())
        candidates = [did for did in all_corpus_ids if did not in pos_ids]
        if candidates:
            sampled = rng.sample(candidates, min(negatives_per_query, len(candidates)))
            for neg_id in sampled:
                neg_rows.append(
                    {"qid": qid, "docno": neg_id, "label": 0, "rank": 0, "query": query}
                )

    # Fallback: ensure at least 2 negatives exist for stratified split
    if len(neg_rows) < 2:
        for i in range(2 - len(neg_rows)):
            fallback_id = f"neg_fallback_{i}"
            corpus_docs.append(Document(text="Irrelevant baseline document.", id_=fallback_id))
            neg_rows.append(
                {
                    "qid": topics_df.iloc[0]["qid"],
                    "docno": fallback_id,
                    "label": 0,
                    "rank": 0,
                    "query": topics_df.iloc[0]["query"],
                }
            )

    qrels_df = pd.concat([qrels_df, pd.DataFrame(neg_rows)], ignore_index=True)

    # 5. Build InformationRetrievalDataset and split into train and test splits
    LOGGER.info(
        f"Building InformationRetrievalDataset for '{dataset_name}' ({len(topics_df)} topics, {len(qrels_df)} qrels)..."
    )
    ir_ds = InformationRetrievalDataset(
        dataset_name=dataset_name,
        topics=topics_df,
        enable_embedding_pca=False,
        qrels=qrels_df,
        corpus_iter=corpus_iter,
    )

    try:
        train_ir_ds, test_ir_ds = ir_ds.split(train_size=0.7, random_state=42)
    except Exception as exc:
        raise ValueError(f"Failed to split dataset into train and test sets: {exc}")

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

    LOGGER.info("Generating predictions and embeddings across train and test partitions...")
    train_ir_ds.set_predictions(model.retrieve_dataframe(train_ir_ds))
    test_ir_ds.set_predictions(model.retrieve_dataframe(test_ir_ds))

    train_ir_ds.set_embeddings(model.get_embedding)
    test_ir_ds.set_embeddings(model.get_embedding)

    # 7. Connect to DeepFix server and run diagnosis
    target_api_url = server_url or settings.DEEPFIX_SERVER_URL
    LOGGER.info(f"Connecting to DeepFix analysis server at {target_api_url}...")

    tracking_uri = settings.MLFLOW_TRACKING_URI
    mlflow_config = MLflowConfig()
    if tracking_uri and tracking_uri.startswith(("http://", "https://", "file://")):
        mlflow_config.tracking_uri = tracking_uri

    client = DeepFixClient(
        api_url=target_api_url,
        timeout=300,
        mlflow_config=mlflow_config,
    )

    try:
        response: APIResponse = client.get_diagnosis(
            train_data=train_ir_ds,
            test_data=test_ir_ds,
            model=model,
            model_name="llamaindex",
            language="english",
        )
    except Exception as err:
        LOGGER.error(f"DeepFix server connection/diagnostic failure: {err}")
        raise ConnectionError(
            f"Failed to communicate with DeepFix server at '{target_api_url}': {err}"
        ) from err

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
