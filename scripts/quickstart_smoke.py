"""Run the README's Path A quick start, then assert the stack actually serves.

The README cannot go stale silently because every command here is lifted
verbatim from a fenced ``bash`` block in ``README.md`` that sits between
``<!-- quickstart:NAME -->`` and ``<!-- /quickstart:NAME -->`` markers. Editing
the documentation changes what CI runs. Only the ``clone`` block is rewritten,
to point at the commit under test instead of GitHub.

What this adds on top of the README is what a newcomer would check by hand: the
five services healthy within a timeout, the web sign-in page, the API
``/health``, MLflow, and a real registration plus sign-in through the web app's
own ``/api/auth/*`` routes. It writes no real secret and calls no model
endpoint: the four required keys come from the README's own ``sed`` block, and
the stack boots with placeholder model ids.

Usage, from the repository root::

    python3 scripts/quickstart_smoke.py --work-dir /tmp/quickstart
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
import uuid

MARKER = re.compile(
    r"<!--\s*quickstart:(?P<name>[\w-]+)\s*-->\n```bash\n(?P<body>.*?)\n```\n"
    r"<!--\s*/quickstart:(?P=name)\s*-->",
    re.DOTALL,
)
SERVICES = ("rustfs", "mlflow", "backend-api", "hermes", "web")
REQUIRED_BLOCKS = ("clone", "keys", "network-and-ports", "up")
REQUIRED_KEYS = (
    "API_SERVER_KEY",
    "HERMES_S3_ACCESS_KEY_ID",
    "HERMES_S3_SECRET_ACCESS_KEY",
    "BETTER_AUTH_SECRET",
)
HEALTH_TIMEOUT_SECONDS = 900
HTTP_TIMEOUT_SECONDS = 60
BASE_URL = "http://localhost:8080"


class SmokeFailure(RuntimeError):
    """A documented step, or one of the assertions after it, failed."""


def read_blocks(readme: Path) -> dict[str, str]:
    """Return the fenced command blocks by marker name."""
    blocks: dict[str, str] = {}
    text = readme.read_text(encoding="utf-8")
    for match in MARKER.finditer(text):
        blocks[match["name"]] = match["body"]
    if not blocks:
        raise SmokeFailure(
            f"no <!-- quickstart:NAME --> fences in {readme}. The quick start has to "
            "stay machine-checkable: wrap each block in those markers."
        )
    missing = [name for name in REQUIRED_BLOCKS if name not in blocks]
    if missing:
        raise SmokeFailure(f"{readme} is missing the quickstart blocks: {', '.join(missing)}")
    return blocks


def clone_block(body: str, work_dir: Path) -> str:
    """Point the README's clone at the commit under test, and run the rest as written."""
    lines: list[str] = []
    for line in body.splitlines():
        if line.startswith("git clone "):
            lines.append(f"git clone file://{work_dir / 'upstream'} deepfix-rag")
        elif line.strip() in {"cd deepfix-rag", "cp example.env .env", ""}:
            lines.append(line)
        elif line.lstrip().startswith("#"):
            lines.append(line)
        else:
            raise SmokeFailure(
                f"the quickstart:clone block gained a line this script does not know: "
                f"{line!r}. Teach scripts/quickstart_smoke.py to run it."
            )
    return "\n".join(lines)


def run_block(body: str, cwd: Path, *, label: str) -> None:
    """Run one README block unchanged, so the documented command is the command run."""
    print(f"::group::README quickstart:{label}\n{body}\n::endgroup::", flush=True)
    completed = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", body],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.stdout.strip():
        print(completed.stdout, flush=True)
    if completed.stderr.strip():
        print(completed.stderr, file=sys.stderr, flush=True)
    if completed.returncode != 0:
        raise SmokeFailure(f"the README's quickstart:{label} block exited {completed.returncode}")


def compose(work_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "-f", "docker-compose.yml", "-f", "docker-compose.local.yml", *args],
        cwd=work_dir,
        capture_output=True,
        text=True,
        check=False,
    )


def assert_keys_set(repo: Path) -> None:
    """All four keys must carry a value; Compose interpolates every service regardless."""
    values: dict[str, str] = {}
    for line in (repo / ".env").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    empty = [key for key in REQUIRED_KEYS if not values.get(key)]
    if empty:
        raise SmokeFailure(f"the README's keys block left these empty in .env: {', '.join(empty)}")


def wait_for_health(work_dir: Path) -> None:
    """Poll ``docker compose ps`` until every named service reports healthy."""
    deadline = time.monotonic() + HEALTH_TIMEOUT_SECONDS
    statuses = ""
    while time.monotonic() < deadline:
        by_service = compose_rows(work_dir, "ps", "--all", "--format", "json")
        waiting = [
            f"{name}={by_service.get(name, {}).get('Status', 'missing')}"
            for name in SERVICES
            if by_service.get(name, {}).get("Health") != "healthy"
        ]
        if not waiting:
            print(f"all five services healthy: {', '.join(SERVICES)}", flush=True)
            return
        statuses = "; ".join(waiting)
        print(f"waiting for health: {statuses}", flush=True)
        time.sleep(10)
    raise SmokeFailure(f"no healthy stack within {HEALTH_TIMEOUT_SECONDS}s: {statuses}")


def compose_rows(work_dir: Path, *args: str) -> dict[str, dict[str, str]]:
    """``compose ps`` rows by service. Compose emits one object per line, or an array."""
    stdout = compose(work_dir, *args).stdout.strip()
    rows: list[dict[str, str]] = []
    if stdout.startswith("["):
        rows = json.loads(stdout)
    else:
        for line in stdout.splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return {row.get("Service", ""): row for row in rows}


def fetch(url: str, opener: urllib.request.OpenerDirector | None = None) -> tuple[int, str]:
    """GET with a bounded timeout, so a hung service fails instead of stalling the job."""
    request = urllib.request.Request(url)
    open_request = opener.open if opener else urllib.request.urlopen
    try:
        with open_request(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return response.status, response.read().decode(errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")


def post_json(url: str, payload: dict[str, str]) -> tuple[int, str, http.cookiejar.CookieJar]:
    """POST JSON the way the web app's own form does, keeping the session cookie."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Origin": BASE_URL},
    )
    try:
        with opener.open(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return response.status, response.read().decode(errors="replace"), jar
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace"), jar


def expect(label: str, status: int, body: str = "", *, code: int = 200, prefix: str = "") -> None:
    print(f"{label} -> {status} {body[:200]}", flush=True)
    if status != code:
        raise SmokeFailure(f"{label} answered {status}, expected {code}")
    if prefix and not body.strip().startswith(prefix):
        raise SmokeFailure(
            f"{label} answered {body[:200]!r}, expected JSON starting with {prefix!r}"
        )


def assert_serving() -> None:
    """Check what a newcomer opens: the sign-in page, the health routes, and a real login."""
    status, body = fetch(f"{BASE_URL}/sign-in")
    expect("web /sign-in", status, body)

    status, body = fetch("http://localhost:8000/health")
    expect("API /health", status, body, prefix="{")
    if json.loads(body).get("status") != "ok":
        raise SmokeFailure(f"API /health answered {body!r}, expected status ok")

    status, body = fetch("http://localhost:5000/health")
    expect("MLflow /health", status, body)
    status, body = fetch("http://localhost:5000/")
    expect("MLflow UI", status, body)

    email = f"quickstart-{uuid.uuid4().hex[:12]}@example.com"
    password = "quickstart-smoke-password"
    status, body, jar = post_json(
        f"{BASE_URL}/api/auth/sign-up/email",
        {"email": email, "password": password, "name": "quickstart"},
    )
    expect("web /api/auth/sign-up/email", status, body, prefix="{")
    status, body, jar = post_json(
        f"{BASE_URL}/api/auth/sign-in/email",
        {"email": email, "password": password},
    )
    expect("web /api/auth/sign-in/email", status, body, prefix="{")
    if not any(cookie.name == "better-auth.session_token" for cookie in jar):
        raise SmokeFailure(f"sign-in set no session cookie: {body[:200]!r}")

    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    status, body = fetch(f"{BASE_URL}/api/auth/token", opener)
    expect("web /api/auth/token (identity token)", status, body, prefix="{")
    status, body = fetch(f"{BASE_URL}/api/documents", opener)
    expect("web /api/documents (FastAPI through the web proxy)", status, body, prefix="[")
    print(f"registered and signed in as {email}", flush=True)


def dump_diagnostics(work_dir: Path) -> None:
    """Print what an operator needs after a failure: state first, then logs."""
    print("::group::docker compose ps", flush=True)
    print(compose(work_dir, "ps", "--all").stdout, flush=True)
    print("::endgroup::", flush=True)
    for service in SERVICES:
        print(f"::group::docker compose logs {service}", flush=True)
        printed = compose(work_dir, "logs", "--no-color", "--tail", "300", service)
        print(printed.stdout, printed.stderr, flush=True)
        print("::endgroup::", flush=True)


def prepare_upstream(work_dir: Path) -> None:
    """A local remote holding this commit, so the README's clone has somewhere to clone.

    ``actions/checkout`` fetches with ``--depth=1``, and a shallow history cannot be
    pushed into a new bare repository, so the checkout is unshallowed first. The
    clone the README documents then really happens, against this local copy.
    """
    for args in (
        ["git", "fetch", "--quiet", "--unshallow", "--tags"],
        ["git", "fetch", "--quiet", "--tags"],
    ):
        if subprocess.run(args, capture_output=True, text=True, check=False).returncode == 0:
            break
    else:
        raise SmokeFailure("could not fetch this checkout's history to clone from")
    upstream = work_dir / "upstream"
    if upstream.exists():
        shutil.rmtree(upstream)
    git = ["git", "-c", "advice.detachedHead=false"]
    for args in (
        [*git, "init", "--quiet", "--bare", "--initial-branch=main", str(upstream)],
        [*git, "push", "--quiet", str(upstream), "HEAD:refs/heads/main"],
    ):
        completed = subprocess.run(args, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            raise SmokeFailure(f"{' '.join(args)} failed: {completed.stderr.strip()}")


def teardown(compose_dir: Path, elapsed: float) -> None:
    """Always end with ``down -v``, so a rerun starts from nothing."""
    print(f"::group::docker compose down -v after {elapsed:.0f}s", flush=True)
    if (compose_dir / "docker-compose.local.yml").is_file():
        result = compose(compose_dir, "down", "-v", "--remove-orphans")
        print(result.stdout, result.stderr, flush=True)
    else:
        print(f"no compose override in {compose_dir}; nothing to stop", flush=True)
    print("::endgroup::", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=Path("/tmp/deepfix-quickstart"))
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
    parser.add_argument(
        "--skip-clone",
        action="store_true",
        help="run the blocks in the current checkout instead of cloning the README way",
    )
    args = parser.parse_args()

    work_dir = args.work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    compose_dir = Path.cwd().resolve()
    # Diagnostics only make sense once `up` has been attempted; before that there
    # is nothing to inspect and `compose logs` would only report a missing file.
    stack_started = False
    started = time.monotonic()
    try:
        blocks = read_blocks(args.readme)
        clone = clone_block(blocks["clone"], work_dir)
        if not args.skip_clone:
            prepare_upstream(work_dir)
            run_block(clone, work_dir, label="clone")
            compose_dir = work_dir / "deepfix-rag"
        run_block(blocks["keys"], compose_dir, label="keys")
        assert_keys_set(compose_dir)
        run_block(blocks["network-and-ports"], compose_dir, label="network-and-ports")
        stack_started = True
        run_block(blocks["up"], compose_dir, label="up")
        wait_for_health(compose_dir)
        assert_serving()
    except SmokeFailure as exc:
        print(f"FAILED: {exc}", file=sys.stderr, flush=True)
        if stack_started:
            dump_diagnostics(compose_dir)
        return 1
    except Exception:
        # Anything unexpected is still a failure of the documented path, so print
        # the same evidence rather than only a traceback.
        traceback.print_exc()
        if stack_started:
            dump_diagnostics(compose_dir)
        return 1
    finally:
        elapsed = time.monotonic() - started
        teardown(compose_dir, elapsed)
    print(f"quickstart smoke passed in {elapsed:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
