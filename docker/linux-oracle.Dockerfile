# The Linux oracle: what CI runs, on the dev box.
#
# A local Windows run is not an oracle on its own (AGENTS.md). Six CI rounds on
# the workspace change were spent on failures that could only exist on Linux:
# a test that skips on Windows keeps asserting a contract the code has already
# left, and the local suite stays green while doing so. This image is the same
# Python CI uses (3.11), plus the two things the sandbox actually needs and a
# Windows host cannot provide at all: bubblewrap, and POSIX descriptor
# semantics.
#
# Built by scripts/linux_oracle.py, which tags it with a hash of this file plus
# pyproject.toml, so a dependency change rebuilds it and nothing else does.
FROM python:3.11-slim

# git: the workspace sink shells to it, and several suites need a real repo.
# bubblewrap: the node sandbox jail - the two proofs that skip everywhere else.
# nodejs/npm: the provisioning grammar's fixtures, and the codex CLI below.
# build-essential: source-only wheels in the dependency tree.
RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends \
        git bubblewrap nodejs npm build-essential ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# The codex CLI, at production's version and production's path.
#
# tests/test_provider_jail_codex_nested.py resolves `codex` on
# /opt/codex-install/node_modules/.bin -- exactly where the daemon image puts
# it -- and its `_codex_binary()` reaches past the npm shim for the vendored
# NATIVE binary, because the proof runs codex's own filesystem sandbox helper.
# Without both, those cases skip, and linux-jail-proof fails on any skip.
#
# No login and no model call: the proofs drive `codex sandbox`, which stands in
# for what `codex exec` does per tool call. The image holds no model
# credential (AGENTS.md Hard Rule 15).
#
# Keep CODEX_CLI_VERSION equal to the daemon image's ARG of the same name --
# the jail's behaviour is the thing under proof, so a different codex here
# would prove it for a version we do not ship.
# tests/test_linux_oracle.py::test_the_oracle_pins_the_image_s_codex asserts it.
ARG CODEX_CLI_VERSION=0.153.4
RUN mkdir -p /opt/codex-install \
    && npm install --prefix /opt/codex-install "@openai/codex@${CODEX_CLI_VERSION}" \
    && /opt/codex-install/node_modules/.bin/codex --version \
    && test -n "$(find /opt/codex-install -path '*/vendor/*/bin/codex' -type f -print -quit)"

# Dependencies are baked into a layer keyed on pyproject.toml. The package
# itself is NOT installed here: the run mounts the working tree (uncommitted
# changes included, which is the point of a local oracle) and pytest imports it
# from the rootdir. Installing a stub here would shadow the tree under test.
COPY pyproject.toml /tmp/oracle/pyproject.toml
# One normal shell RUN works with both classic builders and BuildKit. A Docker
# heredoc is silently skipped by some classic builders, leaving an empty file.
RUN python -c "import tomllib; p = tomllib.load(open('/tmp/oracle/pyproject.toml', 'rb')).get('project', {}); print('\n'.join(p.get('dependencies', []) + p.get('optional-dependencies', {}).get('dev', [])))" > /tmp/oracle/requirements.txt \
    && test -s /tmp/oracle/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /tmp/oracle/requirements.txt \
    && python -m pytest --version

# The suite refuses a temp root inside the repo (tests/conftest.py), so give it
# one outside and make it explicit rather than inherited.
ENV TMPDIR=/tmp/oracle-tmp
RUN mkdir -p /tmp/oracle-tmp

WORKDIR /work
