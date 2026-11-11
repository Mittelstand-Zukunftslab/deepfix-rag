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
# The command `deepfix` on PATH is /opt/deepfix/bin/deepfix, a shim in front
# of /opt/deepfix/.venv/bin/deepfix. `which deepfix` resolves to the shim
# because /opt/deepfix/bin is first on PATH, and /usr/local/bin/deepfix
# points at the same shim. The virtualenv binary stays the real CLI.
# While the real CLI runs, the shim rewrites output/heartbeat.json every
# 30 seconds and writes one deepfix-heartbeat line to its own stderr.
#
# On this pin the local terminal captures a command with stdout=PIPE and
# stderr=STDOUT (tools/environments/local.py). Those bytes are not written
# to agent.log and are not the stream s6 forwards. The shim appends a
# redacted copy to /opt/data/logs/deepfix-cli.log. The s6 longrun
# deepfix-cli-log tails that file onto the container stderr, which is
# what `docker logs` shows.
#
# Native OTLP on this pin is content-free gateway monitoring. It does not
# emit an agent span or an LLM span. Traces come from briancaffey/hermes-otel
# v1.32.6, commit ef0c925a145b6d6455beb70cce5c611a2b316440. The package is
# installed under /opt/deepfix/otel, not into /opt/hermes/.venv. That venv is
# sealed to uv.lock; pip-installing the plugin there, or enabling a plugin
# whose manifest declares python_dependencies, makes startup report
# "venv: out of sync with uv.lock". The installed plugin.yaml is marked
# python_runtime: external so it is not a workspace member, and a .pth file
# adds /opt/deepfix/otel to the Hermes interpreter only. Boot writes
# $HERMES_HOME/hermes_otel.yaml with type: mlflow, content_capture off, and
# the deepfix_hermes experiment id. The chat profile does not get that file
# or the plugin. A small deepfix-run-tag plugin stamps deepfix.run_id on the
# spans. The OTEL exporter env vars are not set; hermes-otel owns the MLflow
# header.
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
    DEEPFIX_REAL_BIN=/opt/deepfix/.venv/bin/deepfix \
    DEEPFIX_PYTHON=/opt/deepfix/.venv/bin/python \
    DEEPFIX_CONTAINER_LOG=/opt/data/logs/deepfix-cli.log \
    DEEPFIX_TRACE_EXPERIMENT_ATTEMPTS=5 \
    DEEPFIX_TRACE_EXPERIMENT_DELAY_SECONDS=2 \
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

# hermes-otel v1.32.6, outside the sealed Hermes venv. --target does not
# change that environment's installed distributions, so the uv.lock stamp
# stays the one the base image recorded. python_runtime: external keeps the
# enabled plugin out of the workspace-member stamp. The .pth is read only by
# the Hermes interpreter; the deepfix 3.12 venv does not see it.
RUN set -eu; \
    uv pip install --python /opt/hermes/.venv/bin/python \
        --target /opt/deepfix/otel \
        "hermes-otel @ git+https://github.com/briancaffey/hermes-otel.git@ef0c925a145b6d6455beb70cce5c611a2b316440"; \
    PYTHONPATH=/opt/deepfix/otel /opt/hermes/.venv/bin/python -c "from pathlib import Path; plugin = Path('/opt/deepfix/otel/hermes_otel/plugin.yaml'); text = plugin.read_text(encoding='utf-8'); plugin.write_text(text if 'python_runtime:' in text else 'python_runtime: external\n' + text, encoding='utf-8'); Path('/opt/deepfix/hermes-otel-package').write_text('/opt/deepfix/otel/hermes_otel\n', encoding='utf-8')"; \
    site="$(/opt/hermes/.venv/bin/python -c 'import site; print(site.getsitepackages()[0])')"; \
    printf '%s\n' /opt/deepfix/otel > "$site/deepfix-otel.pth"; \
    find /opt/deepfix/otel -type d -exec chmod 0755 {} +; \
    find /opt/deepfix/otel -type f -exec chmod 0644 {} +; \
    chown -R root:root /opt/deepfix/otel /opt/deepfix/hermes-otel-package "$site/deepfix-otel.pth"

COPY .agents/skills /opt/deepfix/skills
COPY docker/hermes/sandbox_boot.py /opt/deepfix/sandbox_boot.py
COPY docker/hermes/bin/deepfix /opt/deepfix/bin/deepfix
COPY docker/hermes/deepfix_run_tag /opt/deepfix/plugins/deepfix-run-tag
COPY docker/hermes/s6/deepfix-cli-log /etc/s6-overlay/s6-rc.d/deepfix-cli-log
COPY docker/hermes/seal_volume.py /opt/deepfix/seal_volume.py
COPY docker/hermes/00-deepfix-volume /etc/cont-init.d/00-deepfix-volume
COPY docker/hermes/01b-deepfix-sandbox /etc/cont-init.d/01b-deepfix-sandbox

# Skills are root-owned. Files are 0444. Directories stay traversable and
# not writable by group or other, so the hermes user cannot edit them.
# The venv is already root-owned and world-executable; do not chmod it.
RUN find /opt/deepfix/skills /opt/deepfix/plugins -type d -exec chmod 0755 {} + \
    && find /opt/deepfix/skills /opt/deepfix/plugins -type f -exec chmod 0444 {} + \
    && chown -R root:root /opt/deepfix/skills /opt/deepfix/plugins /opt/deepfix/sandbox_boot.py /opt/deepfix/seal_volume.py /opt/deepfix/bin \
    && chmod 0444 /opt/deepfix/sandbox_boot.py /opt/deepfix/seal_volume.py \
    && chmod 0555 /opt/deepfix/bin/deepfix /etc/cont-init.d/00-deepfix-volume /etc/cont-init.d/01b-deepfix-sandbox \
    && chmod 0555 /etc/s6-overlay/s6-rc.d/deepfix-cli-log/run \
    && touch /etc/s6-overlay/s6-rc.d/user/contents.d/deepfix-cli-log \
    && ln -sfn /opt/deepfix/bin/deepfix /usr/local/bin/deepfix \
    && ln -sfn /opt/deepfix/.venv/bin/deepfix-rag /usr/local/bin/deepfix-rag \
    && ln -sfn /opt/deepfix/.venv/bin/deepfix-sync /usr/local/bin/deepfix-sync \
    && chmod -R a+rX /opt/deepfix/app

# The shim directory is first, so `which deepfix` is the shim. The virtualenv
# binary remains the real CLI. Hermes' own interpreter stays ahead of it.
ENV PATH="/opt/deepfix/bin:/opt/hermes/bin:/opt/hermes/.venv/bin:/opt/deepfix/.venv/bin:/usr/local/bin:${PATH}"

# s6 continues after a failing cont-init script unless this is 2. The sandbox
# boot script exits 1 when API_SERVER_KEY is missing or LLM_API_KEY is set,
# and the container must stop instead of serving without that check.
ENV S6_BEHAVIOUR_IF_STAGE2_FAILS=2

WORKDIR /opt/data
