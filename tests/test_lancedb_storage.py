"""LanceDB S3 connection options."""

import pytest

from deepfix_rag.pipeline import lancedb_storage_options

pytestmark = [pytest.mark.unit]


def test_http_endpoint_is_allowed_and_https_is_unchanged() -> None:
    http = lancedb_storage_options(
        "s3://deepfix-index/dev",
        endpoint="http://rustfs:9000",
        access_key="key",
        secret_key="secret",
        region="us-east-1",
    )
    assert http["endpoint"] == "http://rustfs:9000"
    assert http["allow_http"] == "true"
    assert http["aws_access_key_id"] == "key"
    assert "secret" in http["aws_secret_access_key"]

    https = lancedb_storage_options(
        "s3://deepfix-index/dev",
        endpoint="https://s3.example",
        access_key=None,
        secret_key=None,
        region=None,
    )
    assert https == {"endpoint": "https://s3.example"}
    assert "allow_http" not in https


def test_local_index_has_no_storage_options() -> None:
    assert (
        lancedb_storage_options(
            "indexes/current",
            endpoint="http://rustfs:9000",
            access_key="key",
            secret_key="secret",
            region="us-east-1",
        )
        == {}
    )
