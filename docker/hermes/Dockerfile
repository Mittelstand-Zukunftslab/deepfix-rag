# Hermes optimization sandbox.
#
# Pin: nousresearch/hermes-agent v0.22.7, linux/amd64 repo digest
# sha256:e6d81ae52ebf6a43efa529701405509f20054ba818f179ddd748bed4f8af584c
# Chosen after GET /v1/capabilities on that image returned
# run_submission, run_status, run_stop, and run_events_sse. The older
# v2026.6.19 tag is not this pin.
#
# The image Python is 3.14 and /opt/hermes is read-only to the hermes user.
# deepfix is installed into /opt/deepfix/.venv (Python 3.12), which is not
# /opt/hermes/.venv. Only the deepfix console scripts are placed on PATH
# ahead of nothing that would shadow the Hermes interpreter: the venv bin
# is appended after /opt/hermes/.venv/bin, and /usr/local/bin/deepfix is a
# symlink into the venv.
#
# terminal.timeout is 7200 seconds. Confirmed on this pin before the digest
# was committed:
#   - terminal.timeout default in cli-config.yaml.example is 180
#   - TERMINAL_MAX_FOREGROUND_TIMEOUT (FOREGROUND_MAX_TIMEOUT) is 600
#   - timeouts.tools.sequential_call inherits the 420-second tool deadline
#   - agent.deadline.MAX_SAFE_TIMEOUT_S is 365 days on Linux, so 7200 is
#     under the platform clamp
# The boot script sets terminal.timeout and the tool deadlines to 7200/7500
# and exports TERMINAL_MAX_FOREGROUND_TIMEOUT=7200. Without those, a
# deepfix index or deepfix eval would be backgrounded at 600s or abandoned
# at 420s.
#
# The optimization profile's api_server toolsets are terminal, file, and
# skills (docker/hermes/sandbox_boot.py). Recorded from nousresearch/hermes-agent
# v0.22.7 hermes_cli/tools_config.py CONFIGURABLE_TOOLSETS, which is what
# GET /v1/toolsets lists with an enabled flag:
#   on:  terminal, file, skills
#   off: web, browser, delegation, cronjob (this pin has no toolset named
#        cron), discord, discord_admin (this pin has no toolset named
#        messaging), code_execution, vision, image_gen, todo, memory,
#        session_search, clarify, kanban, computer_use, connections
# agent.max_turns is 40. The chat profile (port 8643, not started) has no
# terminal, file, or code_execution toolset, so it cannot run deepfix.
# Skills are root 0444 under /opt/deepfix/skills. skill_manage on this pin
# edits a skill wherever it lives, including a writable external_dirs entry;
# the mode is what blocks that. A same-name skill in the profile skills
# dir would shadow this tree (local precedence).
#
# US-007's deepfix shim (heartbeat.json every 30s) and the hermes-otel
# plugin are not installed here. A long silent deepfix index still has
# nothing writing output/heartbeat.json.
# deepfix-sync is the fixed bundle command (download input/, upload output/).
# It is not a script the model writes. The optimization profile's S3 key is
# the hermes-optimizer prefix key, injected at boot, not baked in.

FROM nousresearch/hermes-agent@sha256:e6d81ae52ebf6a43efa529701405509f20054ba818f179ddd748bed4f8af584c

USER root

# Native wheels cover most of the lockfile. A compiler is here for the
# packages that still build from source (the API image hits the same set).
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential git \
    && rm -rf /var/lib/apt/lists/*

ENV UV_PYTHON_INSTALL_DIR=/opt/deepfix/python \
    UV_PROJECT_ENVIRONMENT=/opt/deepfix/.venv \
    VIRTUAL_ENV=/opt/deepfix/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_TORCH_BACKEND=cpu \
    DEEPFIX_SKILLS_DIR=/opt/deepfix/skills \
    TERMINAL_MAX_FOREGROUND_TIMEOUT=7200 \
    HERMES_GATEWAY_BOOTSTRAP_STATE=running \
    HERMES_DASHBOARD=0 \
    API_SERVER_ENABLED=true \
    API_SERVER_HOST=0.0.0.0 \
    PYTHONDONTWRITEBYTECODE=1

# Secrets are not set here. API_SERVER_KEY and OPENROUTER_API_KEY come from
# the container environment at boot. LLM_API_KEY is rejected if present.

RUN set -eu; \
    uv_bin="$(find /opt/hermes/tools -type f -name uv -print -quit)"; \
    test -n "$uv_bin"; \
    ln -sfn "$uv_bin" /usr/local/bin/uv; \
    uv python install 3.12

WORKDIR /opt/deepfix/app

COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv venv --python 3.12 /opt/deepfix/.venv \
    && uv export --frozen --no-emit-project --no-hashes -o /tmp/requirements.txt \
    && grep -Ev '^(torch|triton|cuda-bindings|cuda-toolkit|nvidia-)' /tmp/requirements.txt > /tmp/requirements.cpu.txt \
    && uv pip install --python /opt/deepfix/.venv/bin/python -r /tmp/requirements.cpu.txt \
    && uv pip install --python /opt/deepfix/.venv/bin/python --torch-backend cpu 'torch==2.14.0+cpu'

COPY src ./src
RUN uv pip install --python /opt/deepfix/.venv/bin/python --no-deps -e .

COPY .agents/skills /opt/deepfix/skills
COPY docker/hermes/sandbox_boot.py /opt/deepfix/sandbox_boot.py
COPY docker/hermes/01b-deepfix-sandbox /etc/cont-init.d/01b-deepfix-sandbox

# Skills are root-owned. Files are 0444. Directories stay traversable and
# not writable by group or other, so the hermes user cannot edit them.
# The venv is already root-owned and world-executable; do not chmod it.
RUN find /opt/deepfix/skills -type d -exec chmod 0755 {} + \
    && find /opt/deepfix/skills -type f -exec chmod 0444 {} + \
    && chown -R root:root /opt/deepfix/skills /opt/deepfix/sandbox_boot.py \
    && chmod 0444 /opt/deepfix/sandbox_boot.py \
    && chmod 0555 /etc/cont-init.d/01b-deepfix-sandbox \
    && ln -sfn /opt/deepfix/.venv/bin/deepfix /usr/local/bin/deepfix \
    && ln -sfn /opt/deepfix/.venv/bin/deepfix-rag /usr/local/bin/deepfix-rag \
    && ln -sfn /opt/deepfix/.venv/bin/deepfix-sync /usr/local/bin/deepfix-sync \
    && chmod -R a+rX /opt/deepfix/app

# Hermes' own interpreter stays first. deepfix resolves via the venv bin
# and the /usr/local/bin symlink.
ENV PATH="/opt/hermes/bin:/opt/hermes/.venv/bin:/opt/deepfix/.venv/bin:/usr/local/bin:${PATH}"

# s6 continues after a failing cont-init script unless this is 2. The sandbox
# boot script exits 1 when API_SERVER_KEY is missing or LLM_API_KEY is set,
# and the container must stop instead of serving without that check.
ENV S6_BEHAVIOUR_IF_STAGE2_FAILS=2

WORKDIR /opt/data
