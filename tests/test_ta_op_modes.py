"""The closed ta-op mode table has exactly one source of truth.

``deploy/native/ta_op_modes.tsv`` is read by the repo gate
(``scripts/check_drop_first_exec.py``); ``deploy/native/ta_op.c`` is what
actually runs in production. If those two drift, the gate green-lights a mode
the runtime refuses — or worse, stops noticing one it accepts. This asserts
they are the same table, and that every row is a fixed, absolute, shell-free
argv.
"""

from __future__ import annotations

import re
from pathlib import Path

from scripts.check_drop_first_exec import load_modes

REPO = Path(__file__).resolve().parent.parent
TA_OP_C = REPO / "deploy" / "native" / "ta_op.c"

_ENTRY = re.compile(
    r'\{"(?P<name>[a-z-]+)",\s*(?P<argc>\d+),\s*(?P<builtin>BUILTIN_\w+|0),\s*'
    r'\{(?P<argv>.*?)\}\}',
    re.DOTALL,
)


def _c_modes() -> dict[str, dict[str, object]]:
    text = TA_OP_C.read_text(encoding="utf-8")
    block = text.split("static const struct mode MODES[] = {", 1)[1].split("\n};", 1)[0]
    out: dict[str, dict[str, object]] = {}
    for m in _ENTRY.finditer(block):
        argv = [a for a in re.findall(r'"((?:[^"\\]|\\.)*)"', m.group("argv"))]
        out[m.group("name")] = {
            "argc": int(m.group("argc")),
            "kind": "builtin" if m.group("builtin") != "0" else "exec",
            "argv": argv,
        }
    return out


def test_c_table_and_tsv_declare_the_same_modes():
    tsv, c = load_modes(), _c_modes()
    assert set(tsv) == set(c), f"mode name drift: tsv={sorted(tsv)} c={sorted(c)}"
    for name in sorted(tsv):
        assert tsv[name]["argc"] == c[name]["argc"], name
        assert tsv[name]["kind"] == c[name]["kind"], name
        assert tsv[name]["argv"] == c[name]["argv"], name


def test_version_banner_counts_every_mode():
    text = TA_OP_C.read_text(encoding="utf-8")
    banner = re.search(r'#define TA_OP_VERSION "ta-op 1 modes=(\d+)"', text)
    assert banner, "version banner missing — the env-apply preflight parses it"
    assert int(banner.group(1)) == len(load_modes())


def test_no_mode_reaches_a_shell_or_an_interpreter_switch():
    for name, spec in load_modes().items():
        argv = list(spec["argv"])  # type: ignore[arg-type]
        if not argv:
            continue
        assert argv[0].startswith("/"), f"{name}: argv[0] must be absolute, got {argv[0]!r}"
        assert Path(argv[0]).name not in {"sh", "bash", "dash", "env", "su", "sudo"}, name
        assert "-c" not in argv, f"{name}: -c would make this a command interface"
        assert "--eval" not in argv and "-e" not in argv, name


def test_only_printenv_takes_an_operand_and_it_is_a_validated_name():
    modes = load_modes()
    with_operand = [n for n, s in modes.items() if s["argc"] == 3]
    assert with_operand == ["printenv"], with_operand
    assert all(s["argc"] == 2 for n, s in modes.items() if n != "printenv")
    text = TA_OP_C.read_text(encoding="utf-8")
    # The NAME validator is hand-rolled precisely so no regex/locale/NSS code
    # runs; assert the character classes it enforces.
    assert "is_env_name" in text
    assert 'isupper((unsigned char)s[0]) || s[0] == \'_\'' in text


def test_env_summary_is_in_wrapper_and_matches_only_the_flag_families():
    text = TA_OP_C.read_text(encoding="utf-8")
    assert 'SUMMARY_PATTERNS[] = {"AUTO_SHIP", "OLLAMA", "PIN_WRITER"}' in text
    assert load_modes()["env-summary"]["kind"] == "builtin", (
        "env-summary must be an in-wrapper post-drop print, never an exec of a "
        "`printenv | grep | sort` pipeline"
    )
    # Matched on the NAME slice only — never the value, so a secret whose VALUE
    # happens to contain 'ollama' is not printed.
    assert "name_matches_summary(*e, (size_t)(eq - *e))" in text


def test_wrapper_lives_outside_the_writable_trees():
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY --from=builder /tmp/ta-op /usr/local/libexec/ta-op" in dockerfile
    assert "chmod 0555 /usr/local/libexec/ta-op" in dockerfile
    assert "chown root:root /usr/local/libexec/ta-op" in dockerfile
    assert "/usr/local/libexec/ta-op" not in "/app /data"
    # Nothing setuid/setgid: the wrapper grants nothing, it retires.
    assert "chmod 4" not in dockerfile and "chmod u+s" not in dockerfile


def test_identity_readback_is_an_anchored_whole_line_match():
    """Regression: a substring test on `/proc/self/status` is a prefix test.

    `strstr(buf, "Uid:\\t1001\\t1001\\t1001\\t1001")` also matches the line
    `Uid:\t1001\t1001\t1001\t10010` — an fsuid of 10010, a different user,
    satisfying the readback that is supposed to prove the identity is retired.
    The predicate must require a line boundary on both sides.
    """
    text = TA_OP_C.read_text(encoding="utf-8")
    assert "status_has(" not in text, (
        "the unanchored substring predicate is the defect; it must not return"
    )
    assert "static int status_line_is(const char *line)" in text
    body = text.split("static int status_line_is(const char *line) {", 1)[1]
    body = body.split("\n}", 1)[0]
    assert "at[-1] != '\\n'" in body, "the match must start at a line boundary"
    assert "at[len] == '\\n'" in body, "the match must end at a line boundary"
    for field in ("Uid", "Gid"):
        assert f'status_line_is("{field}:' in text, field


def test_the_status_path_override_is_compile_time_only_and_never_built_in():
    """The native regression needs a crafted status file; production must not.

    A test switch reachable at runtime (env var, argv, config) would be a way
    to feed the identity readbacks a file the process controls. This one is a
    compile-time macro with a fixed default, and the production image never
    defines it.
    """
    text = TA_OP_C.read_text(encoding="utf-8")
    assert '#define TA_STATUS_PATH "/proc/self/status"' in text
    assert "getenv" not in text, "no runtime switch may reach the status path"
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert "TA_STATUS_PATH" not in dockerfile, (
        "the production build must compile the fixed /proc/self/status default"
    )


def test_no_mode_logs_in_to_or_exercises_a_model_provider():
    """The platform has no LLM (AGENTS.md Hard Rule 15).

    `claude-keepalive`, `codex-keepalive` and `claude-login` kept a host model
    login alive or created one. They were removed with the host logins; no mode
    may exec a provider CLI at all.
    """
    modes = load_modes()
    for retired in ("claude-keepalive", "codex-keepalive", "claude-login"):
        assert retired not in modes
    for name, spec in modes.items():
        argv0 = (spec.get("argv") or [""])[0]
        assert argv0 not in ("/usr/local/bin/claude", "/usr/local/bin/codex"), (
            f"mode {name!r} execs a model provider CLI"
        )


def test_static_link_is_asserted_at_build_time():
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert "gcc -static" in dockerfile
    assert "not a dynamic executable" in dockerfile, (
        "a dynamically linked wrapper would pull the loader and NSS into the "
        "pre-drop path"
    )
