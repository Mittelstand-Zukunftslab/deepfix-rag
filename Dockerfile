# Product API (FastAPI in src/deepfix_rag/api).
# Build context is the repository root: docker build -t deepfix-api .

FROM ghcr.io/astral-sh/uv:python3.12-bookworm

WORKDIR /app

# scripts/openhands_runner.py forces the subprocess terminal, which starts an
# interactive bash in a PTY and errors if bash is missing from PATH. tmux is the
# backend the same tool auto-selects on Unix when the terminal type is unset;
# the OpenHands agent-server image installs it for that path.
RUN apt-get update \
    && apt-get install -y --no-install-recommends bash git curl ca-certificates tmux \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    VIRTUAL_ENV=/app/.venv \
    UV_TORCH_BACKEND=cpu \
    PATH="/app/.venv/bin:${PATH}"

# Pinned OpenHands interpreter, separate from the API venv so deepfix_rag is not
# installed here. Copy the requirements file before this RUN so a pin change
# invalidates the layer. App source is copied later, so app edits do not rebuild it.
COPY docker/openhands-agent-requirements.txt /tmp/openhands-agent-requirements.txt
RUN uv venv --python 3.12 /opt/openhands-agent/.venv \
    && uv pip install --python /opt/openhands-agent/.venv/bin/python \
        -r /tmp/openhands-agent-requirements.txt

# uv sync --frozen installs the lockfile's PyPI torch, which on Linux pulls the
# CUDA stack. uv 0.9 only accepts --torch-backend on pip/tool installs, so drop
# those packages from the export and install the CPU wheel instead.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv venv "$VIRTUAL_ENV" \
    && uv export --frozen --no-emit-project --no-hashes -o /tmp/requirements.txt \
    && grep -Ev '^(torch|triton|cuda-bindings|cuda-toolkit|nvidia-)' /tmp/requirements.txt > /tmp/requirements.cpu.txt \
    && uv pip install -r /tmp/requirements.cpu.txt \
    && uv pip install --torch-backend cpu 'torch==2.14.0+cpu'

COPY src ./src
RUN uv pip install --no-deps -e .
COPY configs ./configs
COPY scripts ./scripts

ENV PYTHONUNBUFFERED=1 \
    PRODUCT_DATA_DIR=/data/product \
    WORKSPACE_DIR=/data/workspace \
    OPENHANDS_PYTHON=/opt/openhands-agent/.venv/bin/python

RUN mkdir -p /data/product /data/workspace

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=90s --retries=5 \
    CMD curl -f http://127.0.0.1:8000/health

# One worker: SQLite, the index writer, and the scheduler assume a single process.
CMD ["uvicorn", "deepfix_rag.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
