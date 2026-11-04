"""Prefix policy for the Hermes RustFS user.

The optimization sandbox gets one long-lived IAM user, ``hermes-optimizer``.
The policy below is attached to that user. It is not an STS session policy.
Root credentials (``RUSTFS_ACCESS_KEY`` / ``S3_ACCESS_KEY_ID`` on the API)
stay out of the Hermes container. They write the run bundle and read outputs.

Operators apply the same document once with ``rc`` against the internal
endpoint, then store the new key as ``HERMES_S3_ACCESS_KEY_ID`` and
``HERMES_S3_SECRET_ACCESS_KEY`` on the Hermes service only:

    rc admin policy create <alias> hermes-optimizer-prefix policy.json
    rc admin user add <alias> hermes-optimizer
    rc admin policy attach <alias> hermes-optimizer-prefix --user hermes-optimizer

``provision_hermes_user`` sends those three admin calls. It does not print
the secret.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import fnmatch
import hashlib
import json
from typing import Any
import urllib.parse

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
import httpx

HERMES_IAM_USER = "hermes-optimizer"
HERMES_POLICY_NAME = "hermes-optimizer-prefix"
RUN_KEY_PREFIX = "optimization/runs"
_ADMIN_PREFIX = "/rustfs/admin/v3"


class HermesIamError(RuntimeError):
    """RustFS rejected an IAM admin call, or the request was not safe to send."""


def hermes_prefix_policy(bucket: str) -> dict[str, Any]:
    """IAM document that allows object access only under ``optimization/runs/*``."""
    resource_bucket = f"arn:aws:s3:::{bucket}"
    resource_objects = f"arn:aws:s3:::{bucket}/{RUN_KEY_PREFIX}/*"
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:GetBucketLocation"],
                "Resource": [resource_bucket],
            },
            {
                "Effect": "Allow",
                "Action": ["s3:ListBucket"],
                "Resource": [resource_bucket],
                "Condition": {"StringLike": {"s3:prefix": [f"{RUN_KEY_PREFIX}/*"]}},
            },
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                "Resource": [resource_objects],
            },
        ],
    }


def authorize(
    policy: Mapping[str, Any],
    *,
    action: str,
    resource: str,
    prefix: str | None = None,
) -> str:
    """Return ``Allow`` or ``AccessDenied`` for one request against this document.

    This is the allowlist the prefix policy states. An explicit Deny wins.
    Anything the statements do not allow is ``AccessDenied``.
    """
    statements = policy.get("Statement", [])
    if not isinstance(statements, list):
        return "AccessDenied"
    denied = False
    allowed = False
    for statement in statements:
        if not isinstance(statement, Mapping) or not _statement_matches(
            statement, action=action, resource=resource, prefix=prefix
        ):
            continue
        effect = statement.get("Effect")
        if effect == "Deny":
            denied = True
        elif effect == "Allow":
            allowed = True
    if denied or not allowed:
        return "AccessDenied"
    return "Allow"


def _statement_matches(
    statement: Mapping[str, Any],
    *,
    action: str,
    resource: str,
    prefix: str | None,
) -> bool:
    if not _any_match(_as_list(statement.get("Action")), action):
        return False
    if not _any_match(_as_list(statement.get("Resource")), resource):
        return False
    condition = statement.get("Condition")
    if condition is None:
        return True
    if not isinstance(condition, Mapping):
        return False
    string_like = condition.get("StringLike")
    if not isinstance(string_like, Mapping) or set(condition) != {"StringLike"}:
        return False
    patterns = string_like.get("s3:prefix")
    if set(string_like) != {"s3:prefix"} or patterns is None:
        return False
    if prefix is None:
        return False
    return _any_match(_as_list(patterns), prefix)


def _as_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [item for item in value if isinstance(item, str)]
    return []


def _any_match(patterns: Sequence[str], value: str) -> bool:
    return any(fnmatch.fnmatchcase(value, pattern) for pattern in patterns)


@dataclass(frozen=True)
class AdminCall:
    """One signed admin call, without the IAM secret."""

    method: str
    path: str
    query: tuple[tuple[str, str], ...]
    body_kind: str


def admin_call_plan() -> tuple[AdminCall, ...]:
    """Create the policy, create the user, and attach the policy to that user.

    No call is ``AssumeRole``. The user body is built at send time so this
    plan does not carry the secret.
    """
    return (
        AdminCall(
            "PUT",
            f"{_ADMIN_PREFIX}/add-canned-policy",
            (("name", HERMES_POLICY_NAME),),
            "policy",
        ),
        AdminCall(
            "PUT",
            f"{_ADMIN_PREFIX}/add-user",
            (("accessKey", HERMES_IAM_USER),),
            "user",
        ),
        AdminCall(
            "PUT",
            f"{_ADMIN_PREFIX}/set-user-or-group-policy",
            (
                ("policyName", HERMES_POLICY_NAME),
                ("userOrGroup", HERMES_IAM_USER),
                ("isGroup", "false"),
            ),
            "empty",
        ),
    )


def provision_hermes_user(
    *,
    endpoint_url: str,
    root_access_key: str,
    root_secret_key: str,
    bucket: str,
    secret_key: str,
    region: str = "us-east-1",
) -> str:
    """Create ``hermes-optimizer`` and attach the prefix policy.

    Returns the access key id. ``secret_key`` is the secret the caller chose
    and is not logged. The root key is used only to sign these admin calls.
    """
    if not endpoint_url.strip():
        raise HermesIamError("endpoint URL is required")
    if not root_access_key.strip() or not root_secret_key:
        raise HermesIamError("root credentials are required to provision the IAM user")
    if root_access_key == HERMES_IAM_USER:
        raise HermesIamError("the Hermes IAM user must not be the root access key")
    policy_body = json.dumps(hermes_prefix_policy(bucket), separators=(",", ":")).encode()
    user_body = _user_document(secret_key)
    bodies = {"policy": policy_body, "user": user_body, "empty": b""}
    for call in admin_call_plan():
        response = _signed_admin(
            method=call.method,
            endpoint_url=endpoint_url,
            path=call.path,
            query=dict(call.query),
            body=bodies[call.body_kind],
            access_key=root_access_key,
            secret_key=root_secret_key,
            region=region,
        )
        if response.status_code >= 300:
            raise HermesIamError(
                f"RustFS admin {call.method} {call.path} failed: HTTP {response.status_code}"
            )
    return HERMES_IAM_USER


def _user_document(secret_key: str) -> bytes:
    if not secret_key or any(char in secret_key for char in "\n\r\x00"):
        raise HermesIamError("IAM secret key is empty or contains a newline")
    payload = {"secretKey": secret_key, "status": "enabled"}
    return json.dumps(payload, separators=(",", ":")).encode()


def _signed_admin(
    *,
    method: str,
    endpoint_url: str,
    path: str,
    query: dict[str, str],
    body: bytes,
    access_key: str,
    secret_key: str,
    region: str,
) -> httpx.Response:
    query_string = urllib.parse.urlencode(query, quote_via=urllib.parse.quote)
    url = endpoint_url.rstrip("/") + path
    if query_string:
        url = f"{url}?{query_string}"
    # RustFS rejects a signed admin call that omits this header (HTTP 400).
    payload_hash = hashlib.sha256(body).hexdigest()
    aws_request = AWSRequest(
        method=method,
        url=url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-amz-content-sha256": payload_hash,
        },
    )
    SigV4Auth(Credentials(access_key, secret_key), "s3", region).add_auth(aws_request)
    signed = {key: value for key, value in aws_request.headers.items()}
    return httpx.request(method, url, content=body, headers=signed, timeout=30.0)
