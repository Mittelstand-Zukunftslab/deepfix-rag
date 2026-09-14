import pytest

from deepfix_rag.config.schema import RAGConfig
from deepfix_rag.pipeline import execute_retrieval


@pytest.mark.parametrize("backend", ["pyterrier", "bm25", "graph", "vectorless"])
def test_execute_retrieval_raises_for_unimplemented_backends(backend):
    config = RAGConfig.model_validate({"retrieval": {"backend": backend}})
    with pytest.raises(NotImplementedError):
        execute_retrieval("any query", config, index_dir="unused")


def test_execute_retrieval_requires_index_dir():
    config = RAGConfig()
    with pytest.raises(ValueError):
        execute_retrieval("any query", config, index_dir=None)
