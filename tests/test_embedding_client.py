import os
from deepfix_rag.retrieval.models import EmbeddingClient, _resolve_provider_endpoints


def test_resolve_provider_endpoints_aisc():
    base_url, api_key = _resolve_provider_endpoints("aisc", "octen-embedding-8b")
    assert "aisc.hpi.de" in base_url
    assert api_key == "sk-5lWnPeBK4omoI8l9UHOjhw"


def test_resolve_provider_endpoints_octen_model():
    base_url, api_key = _resolve_provider_endpoints("hpi", "octen-embedding-8b")
    assert "aisc.hpi.de" in base_url
    assert api_key == "sk-5lWnPeBK4omoI8l9UHOjhw"


def test_embedding_client_init():
    client = EmbeddingClient(model="octen-embedding-8b", provider="aisc")
    assert client.model == "octen-embedding-8b"
    assert "aisc.hpi.de" in client.api_base
    assert client._normalize_model_name() == "openai/octen-embedding-8b"


def test_embedding_client_live_octen():
    client = EmbeddingClient(model="octen-embedding-8b", provider="aisc")
    vector = client.embed_query("Legal passage query for Octen embedding test")
    assert isinstance(vector, list)
    assert len(vector) == 4096
    assert all(isinstance(x, float) for x in vector)
