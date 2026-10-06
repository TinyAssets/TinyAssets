# Dockerfile for the TinyAssets daemon (MCP server).
#
# Per docs/exec-plans/active/2026-04-20-selfhost-uptime-migration.md Row A:
# provider-agnostic container image that ships to Fly.io, Hetzner, or any
# Linux host. No Fly-specific config baked in; that lives in Row D.
#
# Build:
#     docker build -t tinyassets-daemon .
#
# Run (local smoke):
#     docker run -p 8001:8001 \
#       -v $(pwd)/data:/data \
#       -e TINYASSETS_DATA_DIR=/data \
#       tinyassets-daemon
#
# MCP initialize probe (after run):
#     curl -sS -X POST http://localhost:8001/mcp \
#       -H "Content-Type: application/json" \
#       -H "Accept: application/json, text/event-stream" \
#       -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
#            "params":{"protocolVersion":"2024-11-05","capabilities":{},
#                      "clientInfo":{"name":"probe","version":"1.0"}}}'
#
# Image is multi-stage: builder layer installs native-compilation deps,
# final layer stays slim (no build-essential).

# ---------- Stage 1: builder ----------

FROM python:3.11-slim@sha256:a3ab0b966bc4e91546a033e22093cb840908979487a9fc0e6e38295747e49ac0 AS builder

ARG TARGETARCH
ARG NODEJS_VERSION=22.23.3-1nodesource1
ARG CODEX_CLI_VERSION=0.153.4
ARG CLAUDE_CODE_CLI_VERSION=2.1.288
ARG NODESOURCE_REPO_CHECKSUM=b42e0321dabdc24e892115da705cf061167eac12a317f23d329862d0aa0a271d
ARG RUSTUP_VERSION=1.28.2
ARG RUSTUP_SHA256_AMD64=20a06e644b0d9bd2fbdbfd52d42540bdde820ea7df86e92e533c073da0cdd43c
ARG RUSTUP_SHA256_ARM64=e3853c5a252fca15252d07cb23a1bdd9377a8c6f3efa01531109281ae47f841c
ARG RUST_TOOLCHAIN=1.85.1

# Native build-deps for lancedb (rust), clingo (cmake), spacy (cython),
# and general C extensions. Removed from the final image.
# Node.js 22 LTS via nodesource — Debian's default apt nodejs is too old
# (Node 12/18) for either CLI. The floor is now @anthropic-ai/claude-code,
# whose published metadata moved from engines.node >=18.0.0 at 2.1.183 to
# >=22.0.0 at 2.1.288; @openai/codex asks only for >=16, so 22 serves both.
# 22 is the current LTS line, and the smallest one that satisfies that floor.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        cmake \
        curl \
        git \
        gnupg \
        pkg-config \
    && mkdir -p -m 755 /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
        -o /tmp/nodesource-repo.gpg.key \
    && echo "${NODESOURCE_REPO_CHECKSUM}  /tmp/nodesource-repo.gpg.key" | sha256sum -c - \
    && gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg /tmp/nodesource-repo.gpg.key \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" \
        > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends nodejs="${NODEJS_VERSION}" \
    && rm -f /tmp/nodesource-repo.gpg.key \
    && rm -rf /var/lib/apt/lists/*

# SQLite >= 3.51.3, built from the pinned sqlite.org amalgamation. Debian
# trixie ships 3.46.1, which predates the WAL-reset corruption fix in 3.51.3;
# Litestream replicates the WAL, so the floor comes first (target-architecture
# S1a.1, docs/concerns/2026-10-02-sqlite-predates-wal-reset-fix.md). The
# compile options mirror the Debian build the platform already ran on
# (`pragma compile_options` on prod, 2026-10-02), so behaviour is unchanged:
# FTS3/4/5 (daemon_brain uses fts5), RTREE, recursive triggers on by default,
# MAX_VARIABLE_NUMBER=250000, and the rest. Bump all three ARGs together; the
# SHA-256 is of the tarball sqlite.org lists (its SHA3-256 was checked too).
ARG SQLITE_AUTOCONF_YEAR=2026
ARG SQLITE_AUTOCONF_VERSION=3530400
ARG SQLITE_AUTOCONF_SHA256=0e9483900e92cd5de8fd48d16bf9200145a61f7fd5be542a5ac81d8a9516eb9c
RUN set -eu; \
    curl --proto '=https' --tlsv1.2 -fsSL \
        "https://sqlite.org/${SQLITE_AUTOCONF_YEAR}/sqlite-autoconf-${SQLITE_AUTOCONF_VERSION}.tar.gz" \
        -o /tmp/sqlite.tar.gz; \
    echo "${SQLITE_AUTOCONF_SHA256}  /tmp/sqlite.tar.gz" | sha256sum -c -; \
    mkdir /tmp/sqlite-src; \
    tar -xzf /tmp/sqlite.tar.gz -C /tmp/sqlite-src --strip-components=1; \
    cd /tmp/sqlite-src; \
    CFLAGS="-O2 -DSQLITE_ENABLE_COLUMN_METADATA -DSQLITE_ENABLE_DBSTAT_VTAB \
      -DSQLITE_ENABLE_DBPAGE_VTAB -DSQLITE_ENABLE_FTS3 -DSQLITE_ENABLE_FTS3_PARENTHESIS \
      -DSQLITE_ENABLE_FTS3_TOKENIZER -DSQLITE_ENABLE_FTS4 -DSQLITE_ENABLE_FTS5 \
      -DSQLITE_ENABLE_RTREE -DSQLITE_ENABLE_MATH_FUNCTIONS -DSQLITE_ENABLE_UNLOCK_NOTIFY \
      -DSQLITE_ENABLE_UPDATE_DELETE_LIMIT -DSQLITE_ENABLE_PREUPDATE_HOOK \
      -DSQLITE_ENABLE_SESSION -DSQLITE_ENABLE_STMTVTAB -DSQLITE_SECURE_DELETE \
      -DSQLITE_SOUNDEX -DSQLITE_MAX_VARIABLE_NUMBER=250000 \
      -DSQLITE_LIKE_DOESNT_MATCH_BLOBS -DSQLITE_ALLOW_ROWID_IN_VIEW \
      -DSQLITE_DEFAULT_RECURSIVE_TRIGGERS=1 -DSQLITE_USE_URI=1 \
      -DSQLITE_ENABLE_LOAD_EXTENSION -DSQLITE_MAX_DEFAULT_PAGE_SIZE=32768 \
      -DSQLITE_MAX_SCHEMA_RETRY=25" \
      ./configure --prefix=/opt/sqlite --disable-static; \
    make -j"$(nproc)"; \
    make install; \
    rm -rf /tmp/sqlite-src /tmp/sqlite.tar.gz

# Install rust toolchain for lancedb wheels that lack pre-built linux
# binaries. Pinned to known-good rustup + toolchain versions; bump when
# lancedb upgrades.
ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH=/usr/local/cargo/bin:$PATH
RUN set -e; \
    case "${TARGETARCH}" in \
      amd64) rustup_arch="x86_64-unknown-linux-gnu"; rustup_sha="${RUSTUP_SHA256_AMD64}" ;; \
      arm64) rustup_arch="aarch64-unknown-linux-gnu"; rustup_sha="${RUSTUP_SHA256_ARM64}" ;; \
      *) echo "unsupported TARGETARCH for pinned rustup: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl --proto '=https' --tlsv1.2 -fsSL \
        "https://static.rust-lang.org/rustup/archive/${RUSTUP_VERSION}/${rustup_arch}/rustup-init" \
        -o /tmp/rustup-init; \
    echo "${rustup_sha}  /tmp/rustup-init" | sha256sum -c -; \
    chmod 0755 /tmp/rustup-init; \
    /tmp/rustup-init -y --default-toolchain "${RUST_TOOLCHAIN}" --profile minimal; \
    rm -f /tmp/rustup-init

# Install codex CLI to an explicit prefix so the path is deterministic
# across distros. npm global prefix under nodesource Debian may be
# /usr/lib/node_modules (not /usr/local/lib), so we pin to /opt/codex-install
# — then COPY --from=builder targets a known path.
#
# Smoke-test the install via the absolute path. The final-stage image
# does NOT symlink the bare codex bin to /usr/local/bin; it copies the
# flock wrapper there instead (see below). Adding the same wrapper in
# the builder stage would just be dead weight, so we run the binary
# directly here.
COPY scripts/codex_cli_smoke.py /tmp/codex_cli_smoke.py
RUN mkdir -p /opt/codex-install && \
    npm install --prefix /opt/codex-install "@openai/codex@${CODEX_CLI_VERSION}" && \
    /opt/codex-install/node_modules/.bin/codex --version && \
    python /tmp/codex_cli_smoke.py /opt/codex-install/node_modules/.bin/codex

# Install Claude Code CLI next to Codex. It runs only as a universe's provider
# child, on that universe's own credentials; the image holds no model login
# (AGENTS.md Hard Rule 15).
RUN mkdir -p /opt/claude-code-install && \
    npm install --prefix /opt/claude-code-install "@anthropic-ai/claude-code@${CLAUDE_CODE_CLI_VERSION}" && \
    /opt/claude-code-install/node_modules/.bin/claude --version

WORKDIR /build

# Copy project metadata + source so editable install works. PLAN.md is NOT
# copied: nothing in the runtime reads it (#3967 removed the daemon's PLAN
# serving, and provider_jail never binds /app), and shipping it made every
# PLAN.md edit a runtime change that rebuilt the image and killed in-flight
# turns. Adding it back is what re-arms that -- see tests/test_runtime_paths.py.
COPY pyproject.toml ./
COPY tinyassets/ ./tinyassets/
COPY domains/ ./domains/
# fantasy_daemon is the node-execution runtime invoked by
# tinyassets.cloud_worker. Without it in the image, the cloud worker
# supervisor crash-loops with `No module named fantasy_daemon`.
COPY fantasy_daemon/ ./fantasy_daemon/

# Drop-first operational exec wrapper. Compiled HERE, in the builder stage
# that already carries build-essential (see the apt block above); the final
# stage stays free of a compiler. Fully static because the wrapper must not
# depend on the dynamic loader or NSS: it uses only prctl/capset/setres*/
# exec and never resolves a user or group by name.
#
# -Werror is deliberate: a warning in a binary that runs a privilege
# retirement is a defect, not a note. The `ldd` line asserts the artifact
# really is static — a dynamic build would load /lib from a path the future
# root branch must not touch.
COPY deploy/native/ta_op.c /tmp/ta_op.c
RUN gcc -static -O2 -Wall -Wextra -Werror -o /tmp/ta-op /tmp/ta_op.c \
    && ldd /tmp/ta-op 2>&1 | grep -q 'not a dynamic executable' \
    && { /tmp/ta-op nosuchmode; [ $? -eq 78 ]; }

# Install into a venv that we'll copy to the final stage. Keeps the
# final image free of pip metadata + build tools.
RUN python -m venv --copies /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip && \
    /opt/venv/bin/pip install --no-cache-dir -e ".[mcp,browser]"

# ---------- Stage 2: final ----------

FROM python:3.11-slim@sha256:a3ab0b966bc4e91546a033e22093cb840908979487a9fc0e6e38295747e49ac0

ARG TARGETARCH
ARG NODEJS_VERSION=22.23.3-1nodesource1
ARG GH_VERSION=2.100.0
ARG GH_DEB_SHA256_AMD64=698c8d88cc19cc92bfe96bad58d10b2a5b274c52433d6dc57799c81f6139d5fc
ARG GH_DEB_SHA256_ARM64=33ccd2ad7ce639c927e1cb209e36555b0e1fbb89f7a38239c0568040ec758612
ARG NODESOURCE_REPO_CHECKSUM=b42e0321dabdc24e892115da705cf061167eac12a317f23d329862d0aa0a271d

# Runtime-only deps. No build-essential here.
# libgomp1 is a common transitive native dep for numpy/scipy-backed
# packages (spacy, lancedb); include it proactively.
# Node.js 22 LTS via nodesource — same version as builder so the copied
# codex and claude-code native addons are ABI-compatible. No npm needed at
# runtime; both module trees are COPY'd from the builder.
#
# GitHub CLI (gh) — the github_pull_request effector shells out to
# `gh pr create` (tinyassets/effectors/github_pr.py). Without gh on the
# runtime PATH the effector fails with error_kind=gh_not_installed
# (BUG-110), which blocks every real PR open from the patch-request loop.
# Installed from GitHub's immutable release asset with a per-architecture
# checksum. The cli.github.com apt repository retains only its newest version,
# so an exact apt pin made every upstream release break all future deploys.
#
# git and ripgrep are the universe agent's own toolchain (harness W3, design
# #4172 §4.3 "its own computer"): its tool jail binds /usr read-only, so what
# is installed here is what `bash` in the agent's workspace can run. git was
# present only as a transitive dependency; it is named so it cannot vanish.
RUN set -e; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        acl \
        bubblewrap \
        ca-certificates \
        curl \
        git \
        gnupg \
        libgomp1 \
        ffmpeg \
        ripgrep \
        tini \
        util-linux; \
    mkdir -p -m 755 /etc/apt/keyrings; \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
        -o /tmp/nodesource-repo.gpg.key; \
    echo "${NODESOURCE_REPO_CHECKSUM}  /tmp/nodesource-repo.gpg.key" | sha256sum -c -; \
    gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg /tmp/nodesource-repo.gpg.key; \
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" \
        > /etc/apt/sources.list.d/nodesource.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends nodejs="${NODEJS_VERSION}"; \
    case "${TARGETARCH}" in \
      amd64) gh_sha="${GH_DEB_SHA256_AMD64}" ;; \
      arm64) gh_sha="${GH_DEB_SHA256_ARM64}" ;; \
      *) echo "unsupported TARGETARCH for pinned GitHub CLI: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl --proto '=https' --tlsv1.2 -fsSL \
        "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${TARGETARCH}.deb" \
        -o /tmp/gh.deb; \
    echo "${gh_sha}  /tmp/gh.deb" | sha256sum -c -; \
    apt-get install -y --no-install-recommends /tmp/gh.deb; \
    apt-get purge -y curl gnupg; \
    rm -f /tmp/nodesource-repo.gpg.key /tmp/gh.deb; \
    rm -rf /var/lib/apt/lists/*; \
    groupadd --system --gid 1001 tinyassets; \
    useradd --system --uid 1001 --gid tinyassets --home /home/tinyassets --shell /bin/bash tinyassets; \
    groupadd --system --gid 1002 ta-broker; \
    groupadd --system --gid 1003 ta-engine; \
    groupadd --system --gid 1100 ta-work; \
    groupadd --system --gid 1101 ta-brk; \
    groupadd --system --gid 1102 ta-vault; \
    useradd --system --uid 1002 --gid ta-broker --home /var/lib/ta-broker --shell /usr/sbin/nologin ta-broker; \
    useradd --system --uid 1003 --gid ta-engine --home /nonexistent --shell /usr/sbin/nologin ta-engine

# The jail binds /usr, not the daemon's /opt/venv. Install the basic test
# runner on its Python so a checkout can run tests without platform imports.
RUN /usr/local/bin/python -m pip install --no-cache-dir "pytest==8.4.2" && \
    /usr/local/bin/python -m pytest --version

# Copy the codex install tree from builder and install the flock
# wrapper as /usr/local/bin/codex. The wrapper takes an exclusive
# flock on a sentinel in /app/.codex before exec'ing the real codex
# binary — required because PR #965 binds the codex auth directory
# across the daemon + worker containers, and Codex's official CI/CD
# auth guide forbids sharing one auth.json across concurrent runners
# without serialization (concurrent refresh attempts race rotation
# and trigger `refresh_token_reused`). See deploy/codex-flock-wrapper.sh.
COPY --from=builder /opt/codex-install /opt/codex-install
COPY --from=builder /opt/claude-code-install /opt/claude-code-install
COPY deploy/codex-flock-wrapper.sh /usr/local/bin/codex
RUN chmod 0755 /usr/local/bin/codex && \
    ln -s /opt/claude-code-install/node_modules/.bin/claude /usr/local/bin/claude && \
    /usr/local/bin/codex --version && \
    /usr/local/bin/claude --version && \
    git --version && rg --version && node --version && python3 --version

# Install the drop-first wrapper root-owned 0555 under /usr/local/libexec —
# OUTSIDE /app and /data; /app also remains immutable and root-owned.
# A binary that root may one day exec must not live in a tree its target
# user can write. Not setuid, not setgid: it grants nothing, it retires.
# The pinned SQLite (see the builder). /usr/local/lib precedes the Debian lib
# directory in the loader's search order, so after ldconfig Python's _sqlite3
# loads this libsqlite3.so.0. The build FAILS here if it does not, which is the
# point: a silent fall-back to 3.46.1 would replicate a WAL the fix is for.
COPY --from=builder /opt/sqlite/lib/ /tmp/sqlite-lib/
RUN set -eu; \
    cp -a /tmp/sqlite-lib/libsqlite3.so* /usr/local/lib/; \
    rm -rf /tmp/sqlite-lib; \
    ldconfig; \
    python3 -c "import sqlite3, sys; v = sqlite3.sqlite_version_info; print('sqlite', sqlite3.sqlite_version); sys.exit(0 if v >= (3, 51, 3) else 1)"; \
    python3 -c "import sqlite3; sqlite3.connect(':memory:').execute('create virtual table t using fts5(x)')"

COPY --from=builder /tmp/ta-op /usr/local/libexec/ta-op
RUN chown root:root /usr/local/libexec/ta-op \
    && chmod 0555 /usr/local/libexec/ta-op \
    && { /usr/local/libexec/ta-op nosuchmode; [ $? -eq 78 ]; }

WORKDIR /app

# Copy the populated venv + source from the builder.
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /build/tinyassets /app/tinyassets
COPY --from=builder /build/domains /app/domains
COPY --from=builder /build/fantasy_daemon /app/fantasy_daemon
COPY --from=builder /build/pyproject.toml /app/pyproject.toml

# Headless Chromium for the custom-UI preview (openspec custom-ui-assets D6:
# `read_graph target="app_ui_preview"` renders a person's own UI so the agent
# that built it can see it). INTERIM PLACEMENT: in the target architecture
# (#4263) the renderer belongs inside the command center's sealed box image,
# not this shared daemon image; move this layer there when the box image exists.
#
# --only-shell: the headless shell, not the full browser. --with-deps installs
# its shared libraries with apt (root, here, before USER). The browser runs as
# uid 1001 with Chromium's OWN sandbox on (ui_preview passes chromium_sandbox=
# True), which needs unprivileged user namespaces -- the same thing bubblewrap
# needs, and compose's seccomp=unconfined already allows. Proven 2026-10-02 in a
# python:3.11-slim + playwright 1.58 container as uid 1001: Chromium 145, WebGL
# via SwiftShader. One render at a time per process (ui_preview._SLOT).
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN /opt/venv/bin/playwright install --with-deps --only-shell chromium &&\
    rm -rf /var/lib/apt/lists/* &&\
    chmod -R a+rX /opt/ms-playwright &&\
    /opt/venv/bin/python -c "from playwright.sync_api import sync_playwright" &&\
    ls -d /opt/ms-playwright/chromium_headless_shell-*

# Static data files required at runtime.
# world_rules.lp is the ASP constraint program; asp_engine.py resolves it
# relative to the package root (parents[2]/data/). The *.db files in data/
# are runtime state and live in TINYASSETS_DATA_DIR, not here.
COPY data/world_rules.lp /app/data/world_rules.lp

# Public model lists, one file per source kind. REVIEWED DATA the runtime reads, not
# state: `public_model_lists.lists_directory()` resolves `models/` beside the package,
# so without this COPY every source kind reads as unlisted and the feature silently
# does nothing in production (Codex on #4028 — it never reached the image).
COPY models/ /app/models/

# Stdlib-only MCP canary — reused across Layer-1 (local), tier-3 GHA,
# docker-build CI, cloud canary, and the compose.yml container-health
# healthcheck. Single definition of "healthy MCP" across every probe
# surface. Copied directly (not via the builder stage) because the
# script is pure stdlib — no compilation needed.
COPY scripts/mcp_public_canary.py /app/scripts/mcp_public_canary.py
COPY scripts/_canary_common.py /app/scripts/_canary_common.py

# The workspace jail's acceptance proofs. They assert Linux-only, bwrap-only
# behaviour that no developer box and no CI runner can answer, so they run
# INSIDE the deployed container after a deploy:
#
#     docker exec <daemon> python /app/scripts/workspace_bwrap_oracle.py
#
# Shipped rather than copied in on the day: an acceptance check you have to
# `docker cp` before you can run it is one that gets skipped. Stdlib-only and
# read-only against a temp root under /tmp; it never touches /data.
COPY scripts/workspace_bwrap_oracle.py /app/scripts/workspace_bwrap_oracle.py
COPY deploy/docker-entrypoint.sh /usr/local/libexec/ta-entry.sh
COPY scripts/check_privileged_chain.py /usr/local/libexec/ta-chain.py
COPY deploy/role_egress_migration.py /usr/local/libexec/ta-egress-migration.py
COPY deploy/role_launcher.py /usr/local/libexec/ta-launch.py
COPY deploy/role_owner_launcher.py /usr/local/libexec/ta-owner-launch.py
COPY deploy/role_decoder.py /usr/local/libexec/ta-decoder.py
COPY deploy/role_git.py /usr/local/libexec/ta-git.py
COPY deploy/broker_main.py /app/broker_main.py
COPY scripts/role_image_oracle.py /app/scripts/role_image_oracle.py
COPY scripts/role_launcher_oracle.py scripts/role_account_erasure_oracle.py /app/scripts/
COPY scripts/role_stream_oracle.py /app/scripts/role_stream_oracle.py

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Data directory — Row B will wire TINYASSETS_DATA_DIR through all
# on-disk state. For now, /data is the expected bind-mount target;
# operators supply it via `-v /host/path:/data` + the env var below.
ENV TINYASSETS_DATA_DIR=/data HOME=/home/tinyassets
RUN mkdir -p /data /home/tinyassets /var/lib/ta-broker && \
    chown tinyassets:tinyassets /data /home/tinyassets && \
    chown ta-broker:ta-broker /var/lib/ta-broker && \
    chmod 0700 /home/tinyassets /var/lib/ta-broker && \
    chmod -R a-w,a+rX /app && \
    chmod 0555 /app/broker_main.py /usr/local/libexec/ta-entry.sh /usr/local/libexec/ta-chain.py /usr/local/libexec/ta-egress-migration.py /usr/local/libexec/ta-launch.py /usr/local/libexec/ta-owner-launch.py /usr/local/libexec/ta-decoder.py /usr/local/libexec/ta-git.py && \
    /opt/venv/bin/python -I -S -B /usr/local/libexec/ta-chain.py

USER tinyassets

EXPOSE 8001

# tini as PID 1 handles signal forwarding + zombie reaping.
# docker-entrypoint.sh enforces cloud-daemon subscription-only auth,
# optionally installs a subscription Codex auth bundle, then execs the CMD.
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/libexec/ta-entry.sh"]

# Default command — the FastMCP streamable-http server on 0.0.0.0:8001.
# Through a launcher whose import is empty: every broker/workspace child is a
# multiprocessing spawn child, which re-imports __main__ by name first, and the
# server as __main__ cost each child ~5 s (tinyassets/serve.py).
CMD ["python", "-m", "tinyassets.serve"]
