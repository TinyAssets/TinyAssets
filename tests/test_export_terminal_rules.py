"""Every portable action class reaches an owner decision, never model self-approval."""

from __future__ import annotations

import json

import pytest

from tests.test_export_local_runner import RUNNER, agent_policy, folder  # noqa: F401


@pytest.mark.parametrize("action", sorted(RUNNER["ACTION_CLASSES"]))
@pytest.mark.parametrize(
    "behavior,approved,executes",
    [
        ("do", False, True),
        ("ask_first", False, False),
        ("ask_first", True, True),
        ("do_if_preapproved", False, False),
        ("do_if_preapproved", True, True),
        ("hand_off", True, False),
    ],
)
def test_every_shell_effect_class_obeys_owner_rule(folder, action, behavior, approved, executes):  # noqa: F811
    rules = dict.fromkeys(RUNNER["ACTION_CLASSES"], "do")
    rules[action] = behavior
    agent_policy(folder, **rules)
    (folder / "run/rules.json").write_text('{"bash":"allow"}')
    handoffs = []
    tools = RUNNER["LocalTools"](
        folder,
        classify=lambda _: {action},
        approve=lambda _: approved,
        handoff=lambda action: (
            handoffs.append(action) or "Owner-reported result: completed manually"
        ),
    )
    tools.call("bash", {"command": "printf ran > proof.txt"})
    assert (folder / "proof.txt").exists() is executes
    assert bool(handoffs) is (behavior == "hand_off")


def test_unattended_shell_cannot_discard_consequential_rules(folder, monkeypatch):  # noqa: F811
    agent_policy(folder)
    (folder / "run/rules.json").write_text('{"bash":"allow"}')
    tools = RUNNER["LocalTools"](folder, approve=lambda _: True)
    monkeypatch.setattr(tools.bash.__globals__["sys"].stdin, "isatty", lambda: False)
    result = tools.call("bash", {"command": "printf ran > proof.txt"})
    assert result.startswith("Refused:") and not (folder / "proof.txt").exists()
    with pytest.raises(ValueError, match="declared"):
        tools.call(
            "bash", {"command": "printf ran > proof.txt", "action_classes": "workspace.shell"}
        )
    assert not (folder / "proof.txt").exists()


def test_real_terminal_classification_and_handoff_never_execute_command(folder, monkeypatch):  # noqa: F811
    agent_policy(folder)
    tools = RUNNER["LocalTools"](folder, approve=lambda _: pytest.fail("hand-off became approval"))
    namespace = tools.bash.__globals__
    monkeypatch.setattr(namespace["sys"].stdin, "isatty", lambda: True)
    prompts = []
    answers = iter(["money.move", "Completed outside the agent"])

    def respond(prompt):
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setitem(namespace, "input", respond)
    result = tools.call("bash", {"command": "printf ran > proof.txt; # \x1b[2J"})
    assert result == "Owner-reported result (agent did not execute): Completed outside the agent"
    assert len(prompts) == 2 and all("\x1b" not in prompt for prompt in prompts)
    assert not (folder / "proof.txt").exists()


def test_file_handoff_reports_owner_result_without_reading_or_writing(folder):  # noqa: F811
    agent_policy(folder, **{"workspace.files": "hand_off"})
    seen = []
    tools = RUNNER["LocalTools"](
        folder, handoff=lambda action: seen.append(action) or "Owner did it"
    )
    assert tools.call("write", {"path": "proof.txt", "content": "never write"}) == "Owner did it"
    assert "workspace.files" in seen[0] and not (folder / "proof.txt").exists()


def test_explicit_all_do_policy_keeps_unattended_owner_opt_in(folder):  # noqa: F811
    agent_policy(folder, **dict.fromkeys(RUNNER["ACTION_CLASSES"], "do"))
    (folder / "run/rules.json").write_text('{"bash":"allow"}')
    tools = RUNNER["LocalTools"](folder, classify=lambda _: pytest.fail("unneeded classification"))
    result = json.loads(tools.call("bash", {"command": "printf ran"}))
    assert result["exit_code"] == 0 and result["output"] == "ran"
