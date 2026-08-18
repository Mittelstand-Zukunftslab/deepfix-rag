"""PyTerrier index management."""

from pathlib import Path

from deepfix_rag.config.schema import RAGConfig


def build_index(
    dataset_path: Path,
    index_dir: Path,
    config: RAGConfig,
    force: bool = False,
) -> Path:
    """Build PyTerrier inverted/dense index from dataset according to RAGConfig."""

    raise NotImplementedError("Not implemented yet")



def build_index_cmd(dataset: str, config: str, index_dir: str) -> None:
    """CLI anchor for index generation."""

    raise NotImplementedError("Not implemented yet")
