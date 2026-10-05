"""Tests for Dockerfile shape and env wiring.

Verifies:
- codex CLI install layer is present in the Dockerfile
- nodejs runtime is included in the final stage
- the host env template carries no platform model credential (Hard Rule 15)
- compose.yml env_file passes /etc/tinyassets/env to the daemon service
- The codex module copy layer is present

These are static text-parse tests — they don't require Docker to be
installed and run in < 0.1s.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
GITIGNORE = REPO_ROOT / ".gitignore"
COMPOSE = REPO_ROOT / "deploy" / "compose.yml"
ENV_TEMPLATE = REPO_ROOT / "deploy" / "tinyassets-env.template"
ENTRYPOINT = REPO_ROOT / "deploy" / "docker-entrypoint.sh"
CODEX_PROVIDER = REPO_ROOT / "tinyassets" / "providers" / "codex_provider.py"


# ---------------------------------------------------------------------------
# Dockerfile — codex CLI presence
# ---------------------------------------------------------------------------


def test_dockerfile_installs_codex_npm():
    """Builder stage must install @openai/codex via npm."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "@openai/codex" in text, (
        "Dockerfile must install @openai/codex — required by codex_provider.py"
    )
    assert "CODEX_CLI_VERSION=0." in text, (
        "Dockerfile must pin the Codex CLI package version for reproducible builds"
    )


def test_dockerfile_installs_claude_code_npm():
    """Builder stage must install Claude Code CLI via npm."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "@anthropic-ai/claude-code" in text, (
        "Dockerfile must install Claude Code CLI for claude_provider.py"
    )
    assert "CLAUDE_CODE_CLI_VERSION=2." in text, (
        "Dockerfile must pin Claude Code CLI package version"
    )


def test_dockerfile_builder_has_nodejs_for_npm():
    """Builder stage must include nodejs + npm to run npm install."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "nodesource" in text, (
        "Dockerfile must use nodesource to install Node.js 22 "
        "(Debian default nodejs is too old for either vendored CLI)"
    )
    # Node 22, not 20: @anthropic-ai/claude-code declares engines.node >=22.0.0
    # from 2.1.288 (it was >=18.0.0 at 2.1.183). @openai/codex asks >=16, so 22
    # satisfies both. Both stages must agree or the copied native addons break.
    assert text.count("NODEJS_VERSION=22.") == 2, (
        "both Dockerfile stages must pin the exact NodeSource nodejs 22 package "
        "version; claude-code 2.1.288 requires Node >= 22"
    )
    assert "node_20.x" not in text, (
        "the nodesource apt repo must be node_22.x in every stage, or apt "
        "installs a Node 20 package that cannot satisfy the pin"
    )
    assert text.count("deb.nodesource.com/node_22.x") == 2, (
        "both stages must point at the node_22.x nodesource repo"
    )


def test_dockerfile_final_stage_has_nodejs_runtime():
    """Final stage must ship nodejs so the codex CLI (Node.js binary) can run."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    # The final stage starts at 'FROM python:3.11-slim' (second FROM).
    # Assert nodejs appears after the second FROM.
    froms = [i for i, line in enumerate(text.splitlines()) if line.startswith("FROM ")]
    assert len(froms) >= 2, "Expected at least 2 FROM stages"
    final_stage_text = "\n".join(text.splitlines()[froms[1]:])
    assert "nodejs" in final_stage_text, (
        "Final image stage must install nodejs runtime for codex CLI"
    )
    assert "GH_VERSION=2." in text, (
        "Final image stage must pin the GitHub CLI package version"
    )


def test_the_final_stage_ships_the_agents_own_toolchain():
    """Harness W3: the universe agent's tool jail binds /usr read-only, so the
    runtime image IS its computer's toolchain. git and ripgrep are named (git
    was only transitive), and the build fails if any of the four is missing."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    froms = [i for i, line in enumerate(text.splitlines()) if line.startswith("FROM ")]
    final_stage_text = "\n".join(text.splitlines()[froms[1]:])
    install = final_stage_text[final_stage_text.index("apt-get install"):]
    install = install[:install.index(";")]
    for package in ("git", "ripgrep", "bubblewrap"):
        assert re.search(rf"^\s+{package} \\$", install, re.M), package
    assert ("git --version && rg --version && node --version && python3 --version"
            in final_stage_text)


def test_dockerfile_installs_immutable_github_cli_release_asset():
    """The gh pin must remain available after the apt repository advances."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "ARG TARGETARCH" in text
    assert "ARG GH_VERSION=2.100.0" in text
    assert (
        "ARG GH_DEB_SHA256_AMD64="
        "698c8d88cc19cc92bfe96bad58d10b2a5b274c52433d6dc57799c81f6139d5fc"
    ) in text
    assert (
        "ARG GH_DEB_SHA256_ARM64="
        "33ccd2ad7ce639c927e1cb209e36555b0e1fbb89f7a38239c0568040ec758612"
    ) in text
    assert "github.com/cli/cli/releases/download/v${GH_VERSION}" in text
    assert 'echo "${gh_sha}  /tmp/gh.deb" | sha256sum -c -' in text
    assert "apt-get install -y --no-install-recommends /tmp/gh.deb" in text
    assert "cli.github.com/packages" not in text
    assert 'apt-get install -y --no-install-recommends gh="${GH_VERSION}"' not in text


def test_dockerfile_copies_complete_healthcheck_canary():
    """The in-container canary must include its shared import dependency."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "COPY scripts/mcp_public_canary.py /app/scripts/mcp_public_canary.py" in text
    assert "COPY scripts/_canary_common.py /app/scripts/_canary_common.py" in text


def test_dockerfile_base_images_are_digest_pinned():
    """Both stages must use an immutable python base image digest."""
    from_lines = [
        line for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()
        if line.startswith("FROM ")
    ]
    assert from_lines, "Dockerfile must contain FROM lines"
    assert all("@sha256:" in line for line in from_lines), (
        "Dockerfile FROM images must be pinned by digest, not mutable tags"
    )


def test_dockerfile_does_not_pipe_remote_installers_to_shell():
    """Build-time installers must be fetched/verified explicitly."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    executable_text = "\n".join(
        line for line in text.splitlines()
        if not line.lstrip().startswith("#")
    )
    assert re.search(r"\|\s*(?:ba)?sh\b", executable_text) is None
    assert "RUSTUP_SHA256" in text
    assert "NODESOURCE_REPO_CHECKSUM" in text


def test_dockerfile_copies_codex_binary():
    """Final stage must COPY codex install tree from builder."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "COPY --from=builder" in text, (
        "Dockerfile must COPY codex from builder to final stage"
    )
    # Codex is installed to /opt/codex-install in the builder; that dir is COPY'd.
    copy_lines = [
        line for line in text.splitlines()
        if line.strip().startswith("COPY --from=builder") and "codex-install" in line
    ]
    assert copy_lines, (
        "Expected a 'COPY --from=builder /opt/codex-install ...' line in final stage"
    )


def test_dockerfile_copies_claude_binary():
    """Final stage must COPY Claude Code install tree from builder."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "COPY --from=builder /opt/claude-code-install /opt/claude-code-install" in text
    assert "ln -s /opt/claude-code-install/node_modules/.bin/claude /usr/local/bin/claude" in text


def test_dockerfile_codex_version_smoke():
    """Builder stage must run 'codex --version' to verify install."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "codex --version" in text, (
        "Dockerfile must run 'codex --version' after install to catch broken installs"
    )


def test_dockerfile_claude_version_smoke():
    """Builder/final stage must run 'claude --version' to verify install."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "claude --version" in text, (
        "Dockerfile must run 'claude --version' after install to catch broken installs"
    )


# ---------------------------------------------------------------------------
# Dockerfile — codex flock wrapper (PR #965 Codex round-2 Finding 1)
# ---------------------------------------------------------------------------


def test_dockerfile_installs_codex_flock_wrapper():
    """Final stage must install deploy/codex-flock-wrapper.sh as /usr/local/bin/codex.

    Every codex launch is a universe's provider child with that universe's own
    CODEX_HOME; the wrapper serializes concurrent launches against one home.
    Codex's official CI/CD auth guide forbids sharing one
    auth.json across concurrent runners; the wrapper serializes
    invocations via an exclusive flock on CODEX_HOME/.lock.

    Round-1 of PR #965 used a bare symlink (`ln -s ... /usr/local/bin/codex`),
    which is the exact pattern Codex Issue #10332 calls out as broken
    when the auth dir is shared. The wrapper replaces it.
    """
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "COPY deploy/codex-flock-wrapper.sh /usr/local/bin/codex" in text, (
        "Dockerfile must COPY deploy/codex-flock-wrapper.sh to /usr/local/bin/codex "
        "so every `codex` invocation goes through the cross-container flock"
    )
    # The legacy bare symlink must be gone. Regression guard.
    assert "ln -s /opt/codex-install/node_modules/.bin/codex /usr/local/bin/codex" not in text, (
        "Dockerfile must not install codex via bare symlink; concurrent "
        "containers sharing CODEX_HOME would race the OAuth refresh "
        "(Codex Issue #10332). Use the flock wrapper instead."
    )


def test_dockerfile_installs_util_linux_for_flock():
    """flock(1) lives in util-linux. The final stage apt layer must include it
    explicitly so the wrapper works even if the base image trims util-linux
    in a future python-slim revision.
    """
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "util-linux" in text, (
        "Final stage must apt-install util-linux; flock(1) is part of "
        "that package and codex-flock-wrapper.sh exec's flock on every call"
    )


def test_codex_flock_wrapper_script_present():
    """The wrapper script itself must exist + be executable + lock the shared dir."""
    wrapper = REPO_ROOT / "deploy" / "codex-flock-wrapper.sh"
    assert wrapper.exists(), "deploy/codex-flock-wrapper.sh must exist"
    text = wrapper.read_text(encoding="utf-8")
    # Bash strict-mode header.
    assert text.startswith("#!/usr/bin/env bash"), (
        "wrapper must declare #!/usr/bin/env bash shebang"
    )
    assert "set -euo pipefail" in text, "wrapper must use strict bash"
    # Lock target must live next to auth.json so the shared CODEX_HOME
    # makes the lock visible across containers.
    assert "CODEX_HOME" in text and ".lock" in text, (
        "wrapper must lock a sentinel inside the codex auth directory"
    )
    assert "flock -x" in text, (
        "wrapper must use exclusive flock (`flock -x`) — shared locks "
        "would not serialize refresh writes"
    )
    # The actual codex binary should be the exec target.
    assert "/opt/codex-install/node_modules/.bin/codex" in text, (
        "wrapper must exec the real codex bin under flock"
    )
    assert text.rstrip().endswith('exec flock -x "${LOCK_FILE}" "${CODEX_BIN}" "$@"'), (
        "wrapper's final line must `exec flock -x ... codex \"$@\"` so the "
        "wrapper process is replaced (no double-fork) and signals + exit codes "
        "propagate from codex to the daemon caller"
    )


def test_local_git_credentials_stay_out_of_git_and_docker_context():
    """Local Git credential helpers must not be stageable or sent to Docker."""
    gitignore = GITIGNORE.read_text(encoding="utf-8")
    dockerignore = DOCKERIGNORE.read_text(encoding="utf-8")

    assert "*git-credentials*" in gitignore, (
        ".gitignore must catch local Git credential helper files before staging"
    )
    assert "*credentials*" in dockerignore, (
        ".dockerignore must catch credential files even when the basename has "
        "a host/tool prefix"
    )


# ---------------------------------------------------------------------------
# tinyassets-env.template — no platform model credential (Hard Rule 15)
# ---------------------------------------------------------------------------


def test_env_template_carries_no_platform_model_credential():
    """The host env template once offered a Codex auth bundle, a Claude config
    dir, a Claude OAuth token, API keys and an opt-in switch. The platform has
    no LLM, so it offers none of them (full guard:
    tests/test_no_platform_llm_credentials.py)."""
    text = ENV_TEMPLATE.read_text(encoding="utf-8")
    for name in (
        "TINYASSETS_CODEX_AUTH_JSON_B64",
        "CLAUDE_CONFIG_DIR",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "OPENAI_API_KEY",
        "TINYASSETS_ALLOW_API_KEY_PROVIDERS",
    ):
        assert name not in text, f"tinyassets-env.template still offers {name}"


# ---------------------------------------------------------------------------
# compose.yml — env_file wiring
# ---------------------------------------------------------------------------


def test_compose_daemon_uses_env_file():
    """daemon service in compose.yml must load /etc/tinyassets/env."""
    text = COMPOSE.read_text(encoding="utf-8")
    assert "/etc/tinyassets/env" in text, (
        "compose.yml daemon service must reference /etc/tinyassets/env as env_file "
        "so subscription auth material and other secrets are passed to the container"
    )


def test_compose_requires_explicit_workflow_image_without_latest_default():
    """Every TinyAssets-image service must not silently pull mutable :latest."""
    yaml = __import__("yaml")
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))

    # The four `worker*` services were deleted on 2026-08-29 with the host-run
    # fleet: nothing runs outside a user's universe (PLAN.md). Derived rather
    # than listed, so a NEW service on the TinyAssets image inherits the pin
    # requirement instead of silently escaping this test.
    tinyassets_services = [
        name
        for name, service in data["services"].items()
        if "TINYASSETS_IMAGE" in str(service.get("image", ""))
    ]
    assert set(tinyassets_services) == {"daemon", "slack-agent"}, (
        "unexpected TinyAssets-image service set: " f"{sorted(tinyassets_services)}"
    )

    for service_name in tinyassets_services:
        image = data["services"][service_name].get("image", "")
        assert "${TINYASSETS_IMAGE:?" in image, (
            f"{service_name} image must require TINYASSETS_IMAGE instead of "
            "defaulting to ghcr.io/tinyassets/tinyassets-daemon:latest"
        )
        assert ":latest" not in image


def test_compose_sidecar_images_are_digest_pinned():
    yaml = __import__("yaml")
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))

    for service_name in ("cloudflared", "logs"):
        image = data["services"][service_name].get("image", "")
        assert "@sha256:" in image, (
            f"{service_name} image must pin the version tag by digest"
        )


def test_compose_env_file_covers_daemon_service():
    """The env_file stanza must be in the daemon service block, not just cloudflared."""
    yaml = __import__("yaml")
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    daemon_env_files = data["services"]["daemon"].get("env_file", [])
    env_file_values = [
        ef if isinstance(ef, str) else ef.get("path", "")
        for ef in daemon_env_files
    ]
    # The daemon loads the rendered copy without the platform's own secrets,
    # never the host secret store itself
    # (docs/concerns/2026-10-02-platform-secrets-in-daemon-env.md).
    assert "/etc/tinyassets/daemon.env" in env_file_values, (
        "daemon service env_file must include /etc/tinyassets/daemon.env"
    )
    assert "/etc/tinyassets/env" not in env_file_values, (
        "daemon service env_file must not load the host secret store"
    )


def test_compose_daemon_has_no_platform_login_home():
    """No compose service carries a platform Codex or Claude login home.

    Until 2026-09-24 the daemon set CODEX_HOME=/data/.codex and
    CLAUDE_CONFIG_DIR=/data/.claude so a platform login survived redeploys. The
    platform has no LLM (Hard Rule 15): a universe's provider child gets its own
    CLI home from that universe's credentials, never from the daemon.
    """
    yaml = __import__("yaml")
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))

    for service_name, service in data["services"].items():
        environment = service.get("environment") or {}
        assert "CODEX_HOME" not in environment, service_name
        assert "CLAUDE_CONFIG_DIR" not in environment, service_name
    daemon_volumes = data["services"]["daemon"].get("volumes") or []
    assert "tinyassets-data:/data" in daemon_volumes
    assert "/var/lib/tinyassets-codex:/app/.codex" not in daemon_volumes


# The former `test_compose_declares_four_pinned_cloud_workers_with_goal_pool_off`
# asserted the host-run fleet MUST exist. Deleted 2026-08-29: nothing runs
# unless it lives inside a user's universe under that user's control (PLAN.md),
# and the platform never runs an actor of its own. Its inverse is below.


def test_compose_declares_no_host_run_cloud_worker_service():
    """No compose service may run the host-owned fleet supervisor.

    This is the founder principle made executable: the platform never runs an
    actor of its own, so no service command may invoke `tinyassets.cloud_worker`
    and no service may carry the fleet-only worker identity env vars.
    """
    yaml = __import__("yaml")
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))

    raw = COMPOSE.read_text(encoding="utf-8")
    assert "cloud_worker" not in raw, (
        "deploy/compose.yml still names cloud_worker (service, anchor, or "
        "healthcheck) — the host-run fleet is retired"
    )

    for name, service in data["services"].items():
        command = " ".join(service.get("command") or [])
        assert "tinyassets.cloud_worker" not in command, (
            f"service {name} runs the retired host fleet supervisor"
        )
        healthcheck = " ".join((service.get("healthcheck") or {}).get("test") or [])
        assert "cloud_worker" not in healthcheck, (
            f"service {name} healthchecks the retired host fleet supervisor"
        )
        environment = service.get("environment") or {}
        for fleet_only in ("TINYASSETS_WORKER_ID", "TINYASSETS_WORKER_MODEL"):
            assert fleet_only not in environment, (
                f"service {name} carries fleet-only env {fleet_only}"
            )


def test_no_cloud_worker_module_ships_in_the_package():
    """`tinyassets.cloud_worker` must not exist, and must not be importable.

    Deleting the compose services without deleting the module would leave the
    host fleet one `docker run` away. Asserted on the filesystem AND through the
    import system, because a stale .pyc or an installed copy would satisfy only
    one of the two.
    """
    import importlib.util

    package_root = REPO_ROOT / "tinyassets"
    for name in ("cloud_worker.py", "cloud_worker_healthcheck.py"):
        assert not (package_root / name).exists(), (
            f"tinyassets/{name} is back — the host-run fleet is retired"
        )

    for module in ("tinyassets.cloud_worker", "tinyassets.cloud_worker_healthcheck"):
        assert importlib.util.find_spec(module) is None, (
            f"{module} is importable — the host-run fleet is retired"
        )


# ---------------------------------------------------------------------------
# codex_provider.py — --skip-git-repo-check flag (BUG-004 fix A)
# ---------------------------------------------------------------------------


def test_codex_provider_has_skip_git_repo_check():
    """codex exec must pass --skip-git-repo-check so it works outside a git repo."""
    text = CODEX_PROVIDER.read_text(encoding="utf-8")
    assert "--skip-git-repo-check" in text, (
        "codex_provider.py must pass --skip-git-repo-check to 'codex exec'; "
        "without it codex v0.122+ refuses to run in /app (not a git repo)"
    )


def test_codex_provider_flag_is_on_exec_command():
    """--skip-git-repo-check must be on the exec invocation, not a separate call."""
    text = CODEX_PROVIDER.read_text(encoding="utf-8")
    start = text.index("cmd = [")
    end = text.index("]", start)
    cmd_block = text[start:end]
    assert '"exec"' in cmd_block
    assert "--skip-git-repo-check" in cmd_block
    assert "*sandbox_args" in cmd_block
    assert '"--sandbox", "workspace-write"' in text
    assert '"--full-auto"' not in text
    assert "--dangerously-bypass-approvals-and-sandbox" in text


# ---------------------------------------------------------------------------
# docker-entrypoint.sh — subscription auth baked in (BUG-004 fix B)
# ---------------------------------------------------------------------------


def test_entrypoint_script_exists():
    assert ENTRYPOINT.exists(), f"Missing: {ENTRYPOINT}"


def test_entrypoint_holds_no_platform_login():
    """The entrypoint seeds, preserves and configures no platform login.

    Behaviour is pinned by tests/test_docker_entrypoint.py; this is the static
    counterpart.
    """
    text = ENTRYPOINT.read_text(encoding="utf-8")
    executable_text = "\n".join(
        line for line in text.splitlines()
        if not line.lstrip().startswith("#")
    )
    for retired in (
        "base64 -d",
        "auth.json",
        ".credentials.json",
        "cli_auth_credentials_store",
        "codex login",
        "--with-api-key",
        "mkdir -p",
    ):
        assert retired not in executable_text, f"entrypoint still does: {retired!r}"
    assert 'unset "${_name}"' in text


def test_host_login_keepalive_workflows_are_retired():
    workflows = REPO_ROOT / ".github" / "workflows"
    assert not (workflows / "codex-auth-keepalive.yml").exists()
    assert not (workflows / "claude-auth-keepalive.yml").exists()


def test_entrypoint_execs_cmd():
    text = ENTRYPOINT.read_text(encoding="utf-8")
    assert 'exec "$@"' in text, (
        "entrypoint must end with exec \"$@\" to preserve tini PID-1 signal forwarding"
    )


def test_dockerfile_copies_entrypoint():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "docker-entrypoint.sh" in text, (
        "Dockerfile must COPY docker-entrypoint.sh into the image"
    )


def test_dockerfile_entrypoint_uses_entrypoint_script():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "docker-entrypoint.sh" in text, (
        "Dockerfile ENTRYPOINT must invoke docker-entrypoint.sh"
    )
    # tini must still be PID 1
    assert "tini" in text, "tini must remain as PID 1 in ENTRYPOINT"


def test_ta_op_is_built_in_the_builder_stage_and_installed_read_only_outside_app():
    """Drop-first wrapper (slice 1: installed helper only).

    The static compile happens in the builder stage that already carries
    build-essential, with warnings fatal, and the final stage receives only
    the artifact: root-owned 0555 under /usr/local/libexec, which is outside
    both trees chowned to uid 1001. Both stages smoke the binary with an
    undeclared mode and require the closed-table refusal (exit 78), so an
    image whose wrapper does not refuse cannot build. Caller migration
    (healthcheck, keepalives, env-apply) is NOT asserted here — that is the
    second slice, and its own tests carry those assertions.
    """
    text = DOCKERFILE.read_text(encoding="utf-8")
    build = text.index("COPY deploy/native/ta_op.c /tmp/ta_op.c")
    venv = text.index("RUN python -m venv --copies /opt/venv")
    assert build < venv, "the wrapper must compile in the builder stage, before the venv"
    assert "gcc -static -O2 -Wall -Wextra -Werror -o /tmp/ta-op /tmp/ta_op.c" in text
    assert "ldd /tmp/ta-op 2>&1 | grep -q 'not a dynamic executable'" in text
    assert text.count("nosuchmode; [ $? -eq 78 ]") == 2, (
        "both the builder artifact and the installed binary must be smoked "
        "with an undeclared mode and refuse with exit 78"
    )
    install = text.index("COPY --from=builder /tmp/ta-op /usr/local/libexec/ta-op")
    assert install > venv, "the install belongs to the final stage"
    assert "chown root:root /usr/local/libexec/ta-op" in text
    assert "chmod 0555 /usr/local/libexec/ta-op" in text
    # No compiler in the final stage: gcc appears only in the builder RUN.
    assert text.count("gcc ") == 1
    # The Dockerfile only installs the wrapper; its callers live in
    # deploy/compose.yml, the keepalive workflows and env-apply (slice 2),
    # asserted by tests/test_drop_first_operational_migration.py.
    assert "ta-op pulse" not in text and "ta-op canary" not in text


def test_the_final_stage_ships_the_ui_preview_headless_shell():
    """custom-ui-assets D6: the agent renders its own UI to see it. The headless
    shell is installed as root in the FINAL stage (the builder's copy would not
    carry its apt libraries), readable by uid 1001, at a fixed path, pinned by
    the exact Playwright version the venv installs."""
    import tomllib

    text = DOCKERFILE.read_text(encoding="utf-8")
    final = text.split("# ---------- Stage 2: final ----------", 1)[1]
    assert "ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright" in final
    install = final.index("playwright install --with-deps --only-shell chromium")
    assert install < final.index("USER tinyassets"), "installed as root, before USER"
    assert "chmod -R a+rX /opt/ms-playwright" in final
    assert '".[mcp,browser]"' in text
    extras = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    # One extra, shared with the real-browser CI proof: two names for the same
    # pin would let the image and the proof drift onto different Chromium builds.
    optional = extras["project"]["optional-dependencies"]
    assert optional["browser"] == ["playwright==1.58.0"], (
        "exact pin: it decides the Chromium build")
    assert "preview" not in optional, "the image installs `browser`, not a second extra"
    # Interim placement is written where the box image will look for it.
    assert "sealed box image" in final
