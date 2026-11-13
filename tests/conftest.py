"""Global pytest fixtures and configuration for deepfix_rag tests."""

import asyncio
import json
from pathlib import Path
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jwt.algorithms import OKPAlgorithm
import pytest
from typer.testing import CliRunner

from deepfix_rag.config.schema import DataLoaderConfig, RAGConfig
from deepfix_rag.config.settings import settings
from deepfix_rag.pipeline import RAGWorkflow
from tests.identity import IdentityIssuer

# Fixture model ids. Tests never read the deployment model names, so the suite
# behaves the same with and without a repository .env.
FIXTURE_EMBEDDING_MODEL = "fixture/embedding-test"
FIXTURE_RERANKER_MODEL = "fixture/reranker-test"
FIXTURE_LLM_MODEL = "fixture/generation-test"
FIXTURE_JUDGE_MODEL = "fixture/judge-test"
FIXTURE_HERMES_MODEL = "fixture/hermes-test"


@pytest.fixture(autouse=True, scope="session")
def _fixture_model_env() -> None:
    """Fill in a model id only where the environment left one empty.

    Model ids come from the environment in production, and no default is in the
    package. CI has no ``.env``, so the ids are pinned here to keep the suite
    reproducible. A developer's ``.env`` still wins, which matters for the
    integration tests that call a live embedding endpoint.
    """
    defaults = {
        "LLM_MODEL": FIXTURE_LLM_MODEL,
        "LLM_JUDGE_MODEL": FIXTURE_JUDGE_MODEL,
        "EMBEDDING_MODEL": FIXTURE_EMBEDDING_MODEL,
        "COHERE_MODEL": FIXTURE_RERANKER_MODEL,
        "HERMES_MODEL": FIXTURE_HERMES_MODEL,
    }
    previous = {name: getattr(settings, name) for name in defaults}
    for name, value in defaults.items():
        if not getattr(settings, name):
            setattr(settings, name, value)
    yield
    for name, value in previous.items():
        setattr(settings, name, value)


@pytest.fixture(scope="session")
def identity() -> IdentityIssuer:
    """Fixture providing a signing key and the matching verifier for API tests."""
    private_key = Ed25519PrivateKey.generate()
    kid = uuid.uuid4().hex
    public_jwk = json.loads(OKPAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": kid, "alg": "EdDSA", "use": "sig"})
    return IdentityIssuer(private_key=private_key, kid=kid, jwks={"keys": [public_jwk]})


@pytest.fixture
def cli_runner() -> CliRunner:
    """Fixture providing an isolated Typer CliRunner instance."""
    return CliRunner()


@pytest.fixture(scope="session")
def tests_dir() -> Path:
    """Fixture providing the absolute path to the tests directory."""
    return Path(__file__).parent.resolve()


@pytest.fixture(scope="session")
def test_data_dir(tests_dir: Path) -> Path:
    """Fixture providing the path to the tests/data directory."""
    return tests_dir / "data"


@pytest.fixture(scope="session")
def sample_corpus_path(test_data_dir: Path) -> Path:
    """Fixture providing the path to the sample corpus JSONL file."""
    path = test_data_dir / "sample_corpus.jsonl"
    assert path.exists(), f"Sample corpus file does not exist at {path}"
    return path


@pytest.fixture(scope="session")
def sample_qa_path(test_data_dir: Path) -> Path:
    """Fixture providing the path to the sample QA JSONL file."""
    path = test_data_dir / "sample_qa.jsonl"
    assert path.exists(), f"Sample QA file does not exist at {path}"
    return path


@pytest.fixture(scope="session")
def default_config_path() -> Path:
    """Fixture providing the path to the root configs/default.yaml."""
    path = Path("configs/default.yaml").resolve()
    assert path.exists(), f"Default config file does not exist at {path}"
    return path


@pytest.fixture
def default_rag_config() -> RAGConfig:
    """Fixture providing a fresh default RAGConfig instance."""
    return RAGConfig()


@pytest.fixture
def sample_rag_config(sample_corpus_path: Path) -> RAGConfig:
    """Fixture providing a RAGConfig instance pre-configured for local testing with sample data."""
    cfg = RAGConfig()
    cfg.retrieval.language = "german"
    cfg.retrieval.top_k = 3
    cfg.rerank.enabled = False
    cfg.data = DataLoaderConfig(path_or_repo=str(sample_corpus_path))
    return cfg


@pytest.fixture
def populated_sample_index_dir(sample_corpus_path: Path, tmp_path: Path) -> Path:
    """Fixture providing a temporary LanceDB directory pre-populated with sample corpus."""

    index_dir = tmp_path / "sample_lancedb_index"
    cfg = RAGConfig()
    cfg.retrieval.lancedb_index_dir = str(index_dir)
    cfg.data = DataLoaderConfig(path_or_repo=str(sample_corpus_path))
    cfg.retrieval.load_if_exists = False

    async def _build():
        workflow = RAGWorkflow(config=cfg)
        return await workflow.run(ingest=True)

    asyncio.run(_build())
    return index_dir
