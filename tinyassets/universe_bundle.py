"""Blank OKF soul-bundle seeder for new command centers.

Implements the ``universe-creation`` creation contract (D4/D5): creation seeds
one linked OKF concept-document bundle rooted at ``soul.md``. Non-reserved
files are OKF concept documents (YAML frontmatter with a non-empty ``type``);
RESERVED structural files (``index.md``, ``log.md``, ``soul_versions/index.md``)
carry no concept frontmatter — root ``index.md`` permits only ``okf_version``
(upstream OKF SPEC.md, Codex 2026-07-02 adapt). The bundle
tracks the *latest-main* OKF spec on GitHub rather than a pinned copy, so it
never goes stale.

OKF files seeded (13), plus editable connect and share-after-publish starter skills:

    index.md  log.md  soul.md  soul.edit.md  identity.md  founder.md
    orgchart.md  projects.md  goals.md  body.md  origin.md
    soul_versions/index.md  soul_versions/0001.md

Creation does NOT create ``self/``, ``soul/``, ``notes.json``, or
``activity.log``. A blank command center is unnamed: its self-name is learned later
through ``identity.md`` and the linked soul files.

The seeded ``soul.md`` stays parseable by :mod:`tinyassets.universe_soul` (a
blank command center simply reads back as an empty :class:`UniverseSoul`), so persona
resolution and status reads are unaffected.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from tinyassets.starter_skills import (
    CAPABILITIES_SKILL_PATH,
    CONNECT_SKILL_PATH,
    SHARE_SKILL_PATH,
    capabilities_skill,
    connect_skill,
    share_skill,
)
from tinyassets.universe_files import write_data_path
from tinyassets.universe_soul import (
    SOUL_FILENAME,
    UniverseSoul,
    read_universe_soul,
)

OKF_VERSION = "0.1"
OKF_SPEC_URL = (
    "https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md"
)
OKF_TRACKING_POLICY = "latest-main"

# The OKF baseline. Starter skills are ordinary files, not OKF concepts.
BASELINE_FILES: tuple[str, ...] = (
    "index.md",
    "log.md",
    "soul.md",
    "soul.edit.md",
    "identity.md",
    "founder.md",
    "orgchart.md",
    "projects.md",
    "goals.md",
    "body.md",
    "origin.md",
    "soul_versions/index.md",
    "soul_versions/0001.md",
)

# Soul-edit-governed files (D6): only these are edited through the soul.edit
# policy. orgchart.md joined this set 2026-08-23 so the agent can RECORD its org
# structure via write_brain like its other grounding docs (it previously could not
# edit orgchart at all, so it re-asked every turn). projects/goals stay
# learned/runtime, NOT governed.
SOUL_EDIT_GOVERNED = (
    "soul.md", "identity.md", "founder.md", "body.md", "origin.md", "orgchart.md",
)

# Files that must NOT be created at baseline (D5).
FORBIDDEN_BASELINE = ("self", "soul", "notes.json", "activity.log")


def _frontmatter(concept_type: str, **fields: str) -> str:
    # Use a real YAML dumper so values containing ``:`` (e.g. descriptions,
    # URLs) are quoted/escaped and the frontmatter always parses with
    # yaml.safe_load — the OKF conformance requirement.
    data: dict[str, str] = {"type": concept_type, **fields}
    dumped = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{dumped}\n---"


def _doc(concept_type: str, body: str, **fields: str) -> str:
    return f"{_frontmatter(concept_type, **fields)}\n\n{body.strip()}\n"


def _soul_md(purpose: str, loop_branch_def_id: str) -> str:
    body_lines = [
        "# Command Center Soul",
        "",
        "This is the central, editable soul entrypoint for this command center. It is",
        "an OKF concept document that tracks the latest OKF spec on GitHub as the",
        "living standard, not a pinned copy.",
        "",
        f"- OKF source: {OKF_SPEC_URL}",
        f"- OKF tracking: {OKF_TRACKING_POLICY}",
        "- Edit authority: soul.edit",
        "",
        "## Learned Soul Files",
        "",
        "How changes to this soul are learned is governed by",
        "[soul.edit](soul.edit.md). Soul-governed files:",
        "",
        "- [identity](identity.md) — the command center's learned self-name and self-understanding",
        "- [founder](founder.md) — the oath-confirmed founder this command center is bonded to",
        "- [body](body.md) — the learned embodiment (surfaces, voice, hands, senses)",
        "- [origin](origin.md) — how and why this command center came to be",
        "- [orgchart](orgchart.md) — the org chart; the founder is the sole "
        "member by default, always the top anchor",
        "",
        "## Open Questions",
        "",
        "These files are learned/runtime, not soul-edit-governed, and start empty:",
        "",
        "- [projects](projects.md) — the founder's projects index",
        "- [goals](goals.md) — runtime goals and the Branch uses/runs attached to them",
        "",
        "See the full bundle map in [index](index.md); update history in [log](log.md).",
    ]
    if purpose.strip():
        body_lines += ["", "## Purpose", "", purpose.strip()]
    if loop_branch_def_id.strip():
        body_lines += ["", f"- Loop branch: {loop_branch_def_id.strip()}"]
    return _doc(
        "Universe Soul",
        "\n".join(body_lines),
        title="Command Center Soul",
        description="Central editable soul entrypoint for this command center.",
        okf_source=OKF_SPEC_URL,
        okf_tracking=OKF_TRACKING_POLICY,
        edit_authority="soul.edit",
    )


def _soul_edit_md() -> str:
    governed = "\n".join(f"- `{name}`" for name in SOUL_EDIT_GOVERNED)
    body = f"""# Soul Edit Policy

Concept id: `soul.edit`. This file states the hard rules for how this command center
learns high-authority changes to its own soul. These are rules, not open
questions.

## Governed files

A soul edit MAY update only these explicitly changed files:

{governed}

`projects.md` and `goals.md` are learned/runtime files and are NOT governed by
this policy.

## Rules

- A soul edit is a learning event: caller input is proposed learning with
  source and context, never a blind overwrite of the soul.
- Only the explicitly changed governed files above are updated.
- Every accepted soul edit appends an entry to [log](log.md).
- Every accepted soul edit writes a new snapshot under
  [soul_versions](soul_versions/index.md).
- Learning requests relayed through `converse` read and follow this file; the
  authority lives here, not in a hardcoded string.
"""
    return _doc(
        "Soul Edit Policy",
        body,
        id="soul.edit",
        title="Soul Edit Policy",
        description="Hard rules for learning changes to this command center's soul.",
    )


def _identity_md() -> str:
    body = """# Identity

Status: not learned yet.

This command center does not have a learned self-name yet. Its name and
self-understanding are learned after creation through interaction with its
founder; creation never sets a persona name. Until then this command center is
unnamed.
"""
    return _doc(
        "Universe Identity",
        body,
        title="Identity",
        description="The command center's learned self-name and self-understanding.",
        status="not-learned",
    )


def _founder_md() -> str:
    body = """# Founder

Status: not learned yet.

The oath-confirmed founder this command center is bonded to is recorded here once
confirmed. Nothing about the founder is invented at creation.
"""
    return _doc(
        "Founder",
        body,
        title="Founder",
        description="The oath-confirmed founder this command center is bonded to.",
        status="not-learned",
    )


def _orgchart_md() -> str:
    body = """# Org Chart

The founder is the sole member of this org chart. Assume this by default — the
oath-confirmed founder is the only member unless the founder tells you about
additional members. Do NOT ask about the org chart when it is just the founder;
this default already answers it.

The founder is always the top anchor. If the founder later names roles, teams,
daemons, collaborators, delegations, responsibilities, or reporting lines, record
them here (via write_brain orgchart) beneath the founder. Nothing beyond the
founder is invented at creation.
"""
    return _doc(
        "Org Chart",
        body,
        title="Org Chart",
        description="Org chart; the founder is the sole member by default, always the top anchor.",
        status="learned",
    )


def _projects_md() -> str:
    body = """# Projects

Status: not learned yet.

This is a one-line index of the founder's projects, products, experiments, and
things the founder is building, with pointers to per-project files as needed.
Runtime goals and Branch runs live in [goals](goals.md), not here. No founder
projects are learned yet.
"""
    return _doc(
        "Projects",
        body,
        title="Projects",
        description="One-line index of the founder's projects, with pointers as needed.",
        status="not-learned",
    )


def _goals_md() -> str:
    body = """# Goals

Status: not learned yet.

This file describes the runtime goals this command center runs, plus the Branch
uses/runs attached to those goals. Founder projects belong in
[projects](projects.md), not here.

Every command center run or use of a Branch must be attached to a goal. A commons
Branch may be reusable across many goals and command centers; each command center's use of
it is a separate goal-bound instance.
"""
    return _doc(
        "Goals",
        body,
        title="Goals",
        description="Runtime goals and the Branch uses/runs attached to them.",
        status="not-learned",
    )


def _body_md() -> str:
    body = """# Body

Status: not learned yet.

This document describes the command center's embodiment by analogy, to aid
personification:

- The command center is the brain.
- Live platforms, applications, interfaces, and hosted services are body
  surfaces people can interact with.
- Text that lands in the real world is voice.
- Branches the command center runs are hands taking actions.
- Real-world feedback is eyes, ears, and other sensory input.

No body is learned yet. This command center does not claim any live platforms,
applications, voice, hands, or senses until real surfaces, actions, or feedback
have actually been built or observed.
"""
    return _doc(
        "Body",
        body,
        title="Body",
        description="Learned embodiment: surfaces are body, text is voice, Branches are hands.",
        status="not-learned",
    )


def _origin_md() -> str:
    body = """# Origin

Status: not learned yet.

How and why this command center came to be is recorded here as it is learned. Nothing
is invented at creation beyond the fact that a founder brought this command center
into being.
"""
    return _doc(
        "Universe Origin",
        body,
        title="Origin",
        description="How and why this command center came to be.",
        status="not-learned",
    )


def _index_md() -> str:
    links = "\n".join(
        f"- [{name}]({name})"
        for name in (
            "soul.md",
            "soul.edit.md",
            "identity.md",
            "founder.md",
            "orgchart.md",
            "projects.md",
            "goals.md",
            "body.md",
            "origin.md",
            "log.md",
            "soul_versions/index.md",
        )
    )
    body = f"""# Bundle Index

This is the OKF bundle map for this command center. Every baseline file is linked
here.

{links}
"""
    # OKF: index.md is a RESERVED structural file — root index.md permits only
    # `okf_version` frontmatter, no concept `type` (Codex 2026-07-02 adapt vs
    # upstream SPEC.md; universe_self_model._read_okf_version reads this key).
    return f"---\nokf_version: {OKF_VERSION}\n---\n\n{body}"


def _log_md() -> str:
    body = """# Update Log

Human-readable history of soul and baseline updates for this command center.

- created: blank command center seeded with the OKF soul bundle.
"""
    # OKF: log.md is a RESERVED structural file — no concept frontmatter.
    return body


def _soul_versions_index_md() -> str:
    body = """# Soul Version Index

Snapshots of this command center's soul over time.

- [0001](0001.md) — initial blank soul snapshot at creation.
"""
    # OKF: index.md files are RESERVED — no concept frontmatter.
    return body


def seed_okf_bundle(
    universe_dir: Path,
    *,
    purpose: str = "",
    loop_branch_def_id: str = "",
    owner_id: str = "",
) -> UniverseSoul:
    """Seed the blank OKF soul bundle into ``universe_dir`` and return the
    parsed :class:`UniverseSoul` view of the new ``soul.md``.

    Idempotent-safe on a fresh directory; callers create the directory first.
    Does not create ``self/``, ``soul/``, ``notes.json``, or ``activity.log``.
    """
    from tinyassets.role_center_admission import ensure_center_dir

    ensure_center_dir(universe_dir)

    soul_text = _soul_md(purpose, loop_branch_def_id)

    files: dict[str, str] = {
        "index.md": _index_md(),
        "log.md": _log_md(),
        SOUL_FILENAME: soul_text,
        "soul.edit.md": _soul_edit_md(),
        "identity.md": _identity_md(),
        "founder.md": _founder_md(),
        "orgchart.md": _orgchart_md(),
        "projects.md": _projects_md(),
        "goals.md": _goals_md(),
        "body.md": _body_md(),
        "origin.md": _origin_md(),
        "soul_versions/index.md": _soul_versions_index_md(),
        # 0001 is a snapshot of the initial soul so version matching works.
        "soul_versions/0001.md": soul_text,
    }

    for rel, content in files.items():
        # Link-free: a seed over an existing folder never writes through a link.
        write_data_path(universe_dir / rel, content)

    # Creation only, not a turn-time default or an existing-center upgrade.
    # Preserve an existing skill if a caller repeats provisioning.
    try:
        write_data_path(universe_dir / CONNECT_SKILL_PATH, connect_skill(), mode="exclusive")
    except FileExistsError:
        pass

    try:
        write_data_path(universe_dir / SHARE_SKILL_PATH, share_skill(), mode="exclusive")
    except FileExistsError:
        pass

    if owner_id:
        from tinyassets.starter_release import prepare_starter

        prepare_starter(universe_dir, owner_id=owner_id, center_id=universe_dir.name, fresh=True)
    soul = read_universe_soul(universe_dir)
    try:
        write_data_path(universe_dir / CAPABILITIES_SKILL_PATH,
                        capabilities_skill(), mode="exclusive")
    except FileExistsError:
        pass
    # read_universe_soul returns None only if soul.md is unreadable, which we
    # just wrote — fall back to a blank soul rather than propagate None.
    return soul if soul is not None else UniverseSoul()
