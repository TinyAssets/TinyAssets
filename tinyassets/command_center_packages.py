"""A whole command center as one public package: manifest, scrub, store, quarantine.

Founder, 2026-10-02: "the publish and use as the second user will be on the
sharing a whole command center as a package". The ``publish`` ask already makes
workflows, one UI and automation triggers public as one definition. This module
adds what travels beside them -- the command center's FILES (harness,
workspace, wiki pages) -- and the install side (change
``command-center-packages``).

* **One manifest.** ``command-center.json`` (``build_manifest``) is the document
  the D11 export writes too; ``profile`` says which cut it is. Only ``publish``
  is built here.
* **The scrub** (``classify``) decides per file whether it may be public. The
  never-list and the detections win over the owner: a dot entry, a runtime
  file, an owner-describing brain file, a binary, or a detected credential or
  contact detail (in the content OR the path) stays out whatever the owner
  names. ``final_check`` then scans the whole public output once more.
* **The store.** A package's content is ONE canonical JSON blob, written once
  under ``<data root>/.command-center-packages/blobs/<sha256>.json`` -- outside
  every command-center folder, so no agent environment reaches it (harness
  §4.16). Ownership is recorded BEFORE the write, so the ``packages`` store
  charges even a blob a failed publish left unlisted.
* **The ingestion boundary** (``check_blob``) runs on every read of a blob,
  before anything is planned from it. ``scan_install`` then screens the
  verified files' content for the exfiltration patterns the ClawHub poisoning
  wave used; the install tab shows every hit and the owner decides.
* **Pins.** The consent record of a ``publish`` or ``install`` ask -- its
  action, digest, tab text and (for install) destination plan -- lives here,
  keyed by (command center, request). The rail renders those asks from the pin
  and the answer executes the pin, never the pending-request row, which sits
  inside the agent-writable folder.
* **The writer** (``write_new_file``) creates each file ``O_EXCL |
  O_NOFOLLOW`` beneath directories opened one component at a time, so an
  install never overwrites the installer's own file and never follows a link
  the installer's agent planted.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import time
import unicodedata
from pathlib import Path
from typing import Any, Iterator

from tinyassets.api.interlocutor import FOUNDER_PRIVATE_GROUNDING as _FOUNDER_PRIVATE
from tinyassets.automation_context import BRAIN_FILES as _GOVERNED_BRAIN
from tinyassets.ingestion.canon_io import CANON_DIRNAME as _CANON_DIRNAME
from tinyassets.universe_files import (
    MAX_UNIVERSE_FILE_BYTES,
    list_universe_dir,
    read_data_path,
    read_universe_file,
)
from tinyassets.work_targets import ARTIFACTS_DIRNAME as _ARTIFACTS_DIRNAME
from tinyassets.work_targets import REQUESTS_FILENAME as _REQUESTS_FILENAME

FORMAT_VERSION = 1
PROFILE_PUBLISH = "publish"
PACKAGE_TAG = "tinyassets.command-center-package.v1"
PACKAGE_KIND = "tinyassets.package.v1"

#: The data-root directory every package record lives in (§4.16: outside every
#: command-center folder; no jail binds the data root).
ROOT_DIR = ".command-center-packages"
_BLOBS = "blobs"
_DB = "packages.db"

# -- the ingestion boundary's bounds -------------------------------------------
#: Also the preview's bound: the tab lists every file, so a package is never
#: larger than a list an owner can read in full (gpt-6-astra, code r2 #4).
MAX_FILES = 2000
MAX_DEPTH = 24
MAX_PATH_CHARS = 400
MAX_FILE_BYTES = MAX_UNIVERSE_FILE_BYTES
#: A ceiling no package passes whatever the quota: import is never unlimited
#: (§4.17). Below it, the publisher's storage quota is the limit.
MAX_PACKAGE_BYTES = 256 * 1024 * 1024
#: Entries the publish walk visits before refusing.
MAX_WALK_ENTRIES = 20000

# -- the scrub -------------------------------------------------------------------
#: Harness files at the command-center root: they travel, and an install lands
#: them under ``agents/<slug>/`` (§4.14 roster layout).
def fold(name: str) -> str:
    """A name as a case-insensitive, Unicode-normalising filesystem sees it.

    Every protected-name comparison goes through this: on such a filesystem
    ``Founder.md`` IS ``founder.md``, so an exact-case test would publish it.
    """
    return unicodedata.normalize("NFC", name).casefold()


HARNESS_ROOT_FILES = frozenset({"AGENTS.md", "identity.md", "MEMORY.md", "settings.yaml"})
HARNESS_ROOT_DIRS = frozenset({"skills", "extensions", "prompts"})
MEMORY_FILE = "MEMORY.md"
_HARNESS_FILES_F = frozenset(fold(n) for n in HARNESS_ROOT_FILES)
_HARNESS_DIRS_F = frozenset(fold(n) for n in HARNESS_ROOT_DIRS)
_MEMORY_F = fold(MEMORY_FILE)

#: Brain files that never travel. Derived from two authorities rather than
#: re-listed by name, because spelling them out once is what let ``orgchart.md``
#: through while the publish confirmation said brain files were left out:
#:
#: * ``FOUNDER_PRIVATE_GROUNDING`` (``api/interlocutor.py``) -- withheld from
#:   every non-founder interlocutor *regardless of the command center's
#:   visibility level*: a command center may be fully public without its
#:   founder's private description becoming public.
#: * ``automation_context.BRAIN_FILES`` -- the governed grounding set, private by
#:   default (host decision 2026-10-03).
#:
#: Minus ``HARNESS_ROOT_FILES``, which travel on purpose: ``destination``
#: remaps them into ``agents/<slug>/`` so a published command center arrives as
#: a roster agent, and ``identity.md`` is that agent's own self-description --
#: the thing being shared, not a founder fact. Excluding it would install an
#: agent with no identity. ``MEMORY.md`` is in that set too and is handled
#: per-item by the scrub, not wholesale.
_BRAIN_FILES = (
    frozenset({"founder.md", "soul.md", "soul.edit.md", "log.md"})
    | _FOUNDER_PRIVATE
    | (frozenset(_GOVERNED_BRAIN) - HARNESS_ROOT_FILES)
)
#: Platform-written runtime state at the folder root. RETAINED only to name a
#: reason in the tab: since the root became an allowlist (:data:`ROOT_FILES`)
#: an unlisted root file stays home whether or not it appears here, so this set
#: no longer has to be complete. It was never close: a grep of the root-level
#: filenames platform code writes found 21 more that travelled, including
#: ``branch_tasks.json`` -- the work queue, the same class as
#: ``requests.json``. Enumerating private names was the losing half of the game.
_RUNTIME_FILES = frozenset({
    "activity.log", "status.json", "ledger.json", "work_targets.json", "notes.json",
    "timeline.json", "promises.json", "facts.json", "characters.json",
    "dispatcher_config.yaml", "config.yaml", _REQUESTS_FILENAME,
    "branch_tasks.json", "branch_tasks_archive.json", "enrichment_signals.json",
    "hard_priorities.json",
})

#: **The root is an allowlist.** Every file directly at the command center's
#: root that may travel, and nothing else.
#:
#: This is the boundary that matters, because the root is where the platform
#: writes its own state -- every known-private name is checked at depth 1
#: only, and a same-named file in a user's own folder (``notes/orgchart.md``) is
#: that user's note and still travels. So a closed set here, and the existing
#: per-folder rules below it, is the whole fix: a platform file added to the
#: root by a future change is private by default instead of public by default.
#:
#: The members are exactly the kinds the ask already publishes:
#: ``HARNESS_ROOT_FILES`` (remapped into ``agents/<slug>/`` by
#: :func:`destination`, so a published command center arrives as a roster
#: agent) plus the UI bundle's entry point.
UI_ROOT_FILE = "app.html"
ROOT_FILES = HARNESS_ROOT_FILES | frozenset({UI_ROOT_FILE})
_ROOT_FILES_F = frozenset(fold(n) for n in ROOT_FILES)

#: Root folders that never travel. Unlike :data:`ROOT_FILES` this cannot be a
#: closed allowlist: a user may make any folder, and their content is most of
#: what sharing a command center means. So the platform's own folders are named
#: here, and **derived from the writer's constants** rather than spelled out --
#: ``artifacts/`` was missed by exactly the hand-listing this avoids, and it
#: holds the review, execution and discarded-target records, which preserve the
#: whole work target including its request text (``work_targets.py:137-146``).
#: A review of the root-only version found it there (2026-10-03), which is also
#: why "the top folder is where platform state lives" was too strong: most of
#: it is, but not all.
#: ``canon/`` holds the owner's UPLOADS. Private by default (host decision
#: 2026-10-03): an upload can be anything personal, and Hard Rule 9 makes it
#: authoritative content the platform never reshapes -- so it is not the
#: platform's to publish on the owner's behalf. Note this is a name match, not
#: a derivation: every writer spells the folder as a bare literal
#: (``api/universe.py``, ``work_targets.py``), so ``canon_io.CANON_DIRNAME``
#: names it once rather than deriving from them. A rename would have to change
#: that constant too; the test below pins it.
NEVER_DIRS = frozenset({
    "workspaces", "soul_versions", _ARTIFACTS_DIRNAME, _CANON_DIRNAME,
})
_BRAIN_F = frozenset(fold(n) for n in _BRAIN_FILES)
_RUNTIME_F = frozenset(fold(n) for n in _RUNTIME_FILES)
#: Under ``wiki/`` only the curated ``pages/`` travel (okf_export's set).
WIKI_DIR = "wiki"
WIKI_PAGES = "pages"
_DB_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".db-journal")

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_PHONE = re.compile(
    r"(?<![\w+])(?:\+\d{1,3}[\s.-]?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}"
    r"|\(\d{3}\)\s?\d{3}[-.\s]\d{4}|\d{3}[-.]\d{3}[-.]\d{4})(?!\w)"
)
_MEMORY_ID = re.compile(r"m_[A-Za-z0-9]{1,32}")
_MEMORY_ITEM = re.compile(r"^\s*[-*]\s*\[(m_[A-Za-z0-9]{1,32})\]")
_CONNECTION_NAME = re.compile(r"^[a-z0-9][a-z0-9._:-]{1,126}$")
_CONNECTION_KEYS = frozenset({"destination", "connection", "connection_name"})
_SLUG = re.compile(r"[^a-z0-9]+")
_AGENT_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
#: Schema-known identifier fields: platform ids (ULIDs, hex uuids, actor ids)
#: and digests are key-shaped by construction, so under exactly these keys an
#: id-shaped value skips the CREDENTIAL test. Contact detection always runs, and
#: any key not named here is scanned in full (gpt-6-astra, code r2 #2: a
#: suffix rule let ``customer_id: "0123456789abcdef"`` through). Matched by
#: SCHEMA LOCATION, not key name (code r3: ``{"id": ...}`` nested in a state
#: default is user data): list indices are dropped and a definition's
#: component key reads as ``*``.
_ID_PATHS = frozenset({
    # A branch row's own platform ids.
    "branch_def_id", "parent_def_id", "fork_from", "author", "goal_id", "domain_id",
    "entry_point", "node_defs.node_id", "node_defs.author", "node_defs.approved_by",
    "node_defs.approved_source_hash", "graph_nodes.id", "graph_nodes.node_def_id",
    "edges.from_node", "edges.to_node",
    # A definition's platform-computed references and digests.
    "components.*.published_version_id", "components.*.workflow", "components.*.ui_id",
    "components.*.blob_sha256", "package.blob_sha256",
})
_ID_VALUE = re.compile(r"^[A-Za-z0-9._:@-]{1,200}$")
#: Words that often mark private material. A hit is NOT an exclusion: it puts
#: the file on the tab's "worth a look" list, so the owner reviews it before
#: confirming rather than it being silently included.
#: Phrases, not common words: ``private`` and ``diagnosis`` were 50 of 52 word
#: flags on the live village (a code keyword and "CI diagnosis"), and a list
#: that long is clicked through.
_REVIEW_WORDS = re.compile(
    r"\b(confidential|internal only|do not (?:share|distribute|forward)|"
    r"password|passcode|salary|payroll|social security|ssn|bank account|iban|"
    r"routing number|date of birth|home address|medical record|nda)\b",
    re.IGNORECASE)

#: Parser findings that are credentials by STRUCTURE, wherever they appear.
_CERTAIN_LABELS = frozenset({
    "private_key_block", "auth_header", "jwt", "url_userinfo", "url_secret_parameter",
})
#: A value ASSIGNED to a secret's name: ``api_key = …``, ``"token": "…"``,
#: ``password: …``, ``my key is …``. The value itself must be what the parser
#: flags. Merely sharing a line with the word was tried and still dropped 123
#: files and 32 workflows of the live village: code says ``token`` beside every
#: kind of identifier.
_SECRET_ASSIGN = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?key|secret|token|passw(?:or)?d|\bpwd|credential|"
    r"private[_-]?key|authorization|\bauth|cookie|session[_-]?id|\bkeys?)"
    r"[\"'\]]?\s*(?:[:=]|=>|\bis\b)\s*[\"'\[]?([^\s\"',;\])}]+)")
#: Published key FORMATS, certain wherever they appear. Lifted from the gitleaks
#: default ruleset (MIT; github.com/gitleaks/gitleaks, config/gitleaks.toml),
#: keeping only the high-precision rules: each needs its exact issued prefix AND
#: a body of the issued length and alphabet. The lead's rule (2026-10-01): the
#: review list a person can actually read is short, so a key pasted bare must
#: not depend on it. Named by FORMAT, never by service, so the substrate names
#: no channel (``check_channel_agnostic``).
_MIXED_BODY = (r"(?=[A-Za-z0-9_-]*[0-9])(?=[A-Za-z0-9_-]*[A-Z])(?=[A-Za-z0-9_-]*[a-z])"
               r"[A-Za-z0-9_-]{{{n},}}")
_KEY_FORMATS = (
    # The open-alphabet ``sk-`` bodies must also mix a digit with upper and
    # lower case, so a kebab-case identifier that happens to start ``sk-`` is not
    # taken for a key.
    re.compile(r"(?<![A-Za-z0-9])sk-(?:ant-(?:api|admin)\d{2}-|proj-|svcacct-|admin-)"
               + _MIXED_BODY.format(n=20)),
    re.compile(r"(?<![A-Za-z0-9])sk-or-v1-[0-9a-f]{64}(?![0-9a-f])"),
    re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{20}T3BlbkFJ[A-Za-z0-9]{20}"),
    re.compile(r"(?<![A-Za-z0-9])sk-" + _MIXED_BODY.format(n=40)),
    re.compile(r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{36,255}(?![A-Za-z0-9])"),
    # A fine-grained personal access token: ``<issuer>_pat_`` and an 82-character
    # body. Matched by that shape, not by the issuer's name.
    re.compile(r"(?<![A-Za-z0-9])[a-z]{3,12}_pat_[A-Za-z0-9_]{82}(?![A-Za-z0-9_])"),
    re.compile(r"(?<![A-Z0-9])(?:A3T[A-Z0-9]|AKIA|ASIA|ABIA|ACCA)[A-Z2-7]{16}(?![A-Z0-9])"),
    re.compile(r"(?<![A-Za-z0-9])xox[abposr]-[0-9]{10,13}-[0-9A-Za-z-]{10,}"),
    re.compile(r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])"),
    re.compile(r"(?<![A-Za-z0-9])glpat-[0-9A-Za-z_-]{20}(?![0-9A-Za-z_-])"),
    re.compile(r"(?<![A-Za-z0-9])(?:sk|rk)_(?:live|test)_[0-9A-Za-z]{24,99}(?![0-9A-Za-z])"),
    re.compile(r"(?<![A-Za-z0-9])hf_[A-Za-z]{34}(?![A-Za-z])"),
    re.compile(r"(?<![A-Za-z0-9])npm_[A-Za-z0-9]{36}(?![A-Za-z0-9])"),
    re.compile(r"(?<![A-Za-z0-9])SG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])"),
    re.compile(r"(?<![0-9])[0-9]{8,10}:AA[0-9A-Za-z_-]{33}(?![0-9A-Za-z_-])"),
    re.compile(r"-----BEGIN[ A-Z]*PRIVATE KEY-----"),
)
#: Lowercase hex of an id's or digest's length is an id, not a key: a run id
#: (16), an md5 or uuid4 hex (32), a git sha (40), a sha256 (64). Not flagged
#: for review at all (lead, 2026-10-01: 289 flags is a list nobody reads).
_HEX_ID = re.compile(r"^(?:[0-9a-f]{16}|[0-9a-f]{32}|[0-9a-f]{40}|[0-9a-f]{64})$")

#: Review notes, as the tab words them.
N_OPAQUE = ("holds a long random-looking string: usually an id or a hash, but check it "
            "is not a key")

#: Excluded-file reasons, as the tab words them.
R_DOT = "platform or private state"
R_RUNTIME = "platform runtime file"
R_BRAIN = "describes you or holds your command center's control settings"
R_WIKI = "wiki drafts and raw material stay private"
R_MEMORY = "memory is private unless you name items"
R_CREDENTIAL = "a credential was detected"
R_CONTACT = "contact details were detected"
R_PATH = "its name carries a credential or contact details"
R_BINARY = "not text, so it cannot be checked"
R_DATABASE = "a database file"
R_EXCLUDED = "you left it out"
R_TOO_BIG = "over the per-file size bound"
R_UNREADABLE = "a link or not a regular file"
R_CHECKOUT = "a managed repository checkout"
R_DEEP = "deeper than a package may go"
R_ROOT_UNLISTED = "not one of the files a package carries from the top folder"
R_WORK_RECORDS = "your command center's own work records"
R_UPLOADS = "the files you uploaded stay yours"

#: One reason per never-folder, so the tab says which kind of state it is. The
#: assertion is the guard: a name added to :data:`NEVER_DIRS` without a reason
#: here fails on import rather than reading as something it is not.
_NEVER_DIR_REASON = {
    fold("workspaces"): R_CHECKOUT,
    fold("soul_versions"): R_BRAIN,
    fold(_ARTIFACTS_DIRNAME): R_WORK_RECORDS,
    fold(_CANON_DIRNAME): R_UPLOADS,
}
assert set(_NEVER_DIR_REASON) == {fold(n) for n in NEVER_DIRS}


class PackageError(ValueError):
    """A package was refused. The message is the owner-facing reason."""


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #


def check_path(raw: Any) -> str:
    """``raw`` as a package-relative POSIX path, or raise `PackageError`.

    Refused: non-text, empty, absolute, a drive, a backslash, a control
    character, an empty / ``.`` / ``..`` component, any dot-prefixed component,
    over ``MAX_PATH_CHARS``, deeper than ``MAX_DEPTH``.
    """
    if not isinstance(raw, str) or not raw:
        raise PackageError("a package path must be non-empty text")
    if len(raw) > MAX_PATH_CHARS:
        raise PackageError(f"a package path is over {MAX_PATH_CHARS} characters")
    if "\\" in raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise PackageError(f"{raw!r} is not a relative package path")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        raise PackageError("a package path holds a control character")
    parts = raw.split("/")
    if len(parts) > MAX_DEPTH:
        raise PackageError(f"{raw!r} is deeper than {MAX_DEPTH} folders")
    for part in parts:
        if part in ("", ".", ".."):
            raise PackageError(f"{raw!r} has an empty or traversal component")
        if part.startswith("."):
            raise PackageError(f"{raw!r} has a hidden component")
    return raw


def collision_key(path: str) -> str:
    """Two paths with one key would be one file on a case-insensitive or
    Unicode-normalising filesystem."""
    return fold(path)


def check_tree(paths: list[str]) -> None:
    """Refuse a set of paths that cannot coexist as one tree: two that collide,
    or a file where another path needs a folder."""
    keys: dict[str, str] = {}
    for path in paths:
        key = collision_key(path)
        if key in keys:
            raise PackageError(f"{path!r} collides with {keys[key]!r}")
        keys[key] = path
    folders = {collision_key("/".join(p.split("/")[:i]))
               for p in paths for i in range(1, p.count("/") + 1)}
    for key, path in keys.items():
        if key in folders:
            raise PackageError(f"{path!r} is a file where another path needs a folder")


# --------------------------------------------------------------------------- #
# The scrub
# --------------------------------------------------------------------------- #


def structural_exclusion(rel: str) -> str | None:
    """The reason ``rel`` can never be public, from its path alone, or None."""
    parts = rel.split("/")
    reason = dir_exclusion("/".join(parts[:-1])) if len(parts) > 1 else None
    if reason:
        return reason
    if parts[-1].startswith("."):
        return R_DOT
    head = fold(parts[0])
    if parts[-1].endswith((".pyc", ".lock")):
        return R_RUNTIME
    if len(parts) == 1 and head in _BRAIN_F:
        return R_BRAIN
    if len(parts) == 1 and head in _RUNTIME_F:
        return R_RUNTIME
    if head == WIKI_DIR and (len(parts) < 3 or fold(parts[1]) != WIKI_PAGES):
        return R_WIKI
    if parts[-1].lower().endswith(_DB_SUFFIXES):
        return R_DATABASE
    if len(parts) == 1 and head not in _ROOT_FILES_F:
        # THE ROOT IS AN ALLOWLIST, and this is deliberately the LAST root rule:
        # every specific reason above keeps its own wording in the tab, so a
        # database still reads "a database file" rather than this catch-all.
        # What lands here is a root file nobody enumerated -- where both real
        # leaks lived, and the 21 found after them.
        return R_ROOT_UNLISTED
    return None


def dir_exclusion(rel_dir: str) -> str | None:
    """The reason no file under the folder ``rel_dir`` can be public, or None.
    The walk does not descend into such a folder at all."""
    parts = rel_dir.split("/")
    if any(part.startswith(".") for part in parts):
        return R_DOT
    head = fold(parts[0])
    if head in NEVER_DIRS:
        return _NEVER_DIR_REASON[head]
    if "__pycache__" in parts or "node_modules" in parts:
        return R_RUNTIME
    if head == WIKI_DIR and len(parts) >= 2 and fold(parts[1]) != WIKI_PAGES:
        return R_WIKI
    return None


def _line_verdict(line: str) -> str:
    """``certain`` / ``suspect`` / ``""`` for one line, by the shared parser.

    The parser is tuned for a sentence typed into a box, where any opaque run is
    worth refusing. Over a whole command center that is the wrong default: a
    live dry run of the founder's GTM Village (2026-10-01) excluded 246 of its
    300 text files and refused 150 of its 217 workflows, nearly all for ids,
    hashes, CSS class names and code identifiers. So only what is a credential
    BY STRUCTURE, or an opaque value assigned to a secret's name, is certain.
    An opaque run anywhere else is a suspect: the file stays in, and the tab
    lists it for the owner to look at.
    """
    from tinyassets.credential_shape import credential_shape

    if any(rule.search(line) for rule in _KEY_FORMATS):
        return "certain"
    label = credential_shape(line)
    if label is None:
        return ""
    if label in _CERTAIN_LABELS:
        return "certain"
    if any(credential_shape(m.group(1)) and _mixed_classes(m.group(1))
           for m in _SECRET_ASSIGN.finditer(line)):
        return "certain"
    return "suspect" if _has_key_like_run(line) else ""


def _has_key_like_run(line: str) -> bool:
    """Whether a run the parser flags on this line is genuinely key-like."""
    from tinyassets.credential_shape import _TOKEN_SPLIT_RE, _without_stamps, credential_shape

    for token in _TOKEN_SPLIT_RE.split(line):
        if not credential_shape(token):
            continue
        # Judged piece by piece: a URL or a path is its segments, and a dated
        # name is what is left beside its timestamp.
        for word in re.split(r"[=:,;/?&#]", _without_stamps(token)):
            word = word.strip("\"'`.,;:()[]{}-")
            if key_like(word):
                return True
    return False


#: The suspect tier's high-entropy test, after detect-secrets'
#: ``HighEntropyString`` plugins (Apache-2.0; Yelp/detect-secrets): Shannon
#: entropy per character over the run. Its base64 default is 4.5 and gitleaks'
#: generic-key rule uses 3.5; the lead set 4.0 between them (2026-10-01), with
#: a 24-character floor and at least three character classes, so code
#: identifiers and hashes stay out of a review list a person must read.
SUSPECT_MIN_CHARS = 24
SUSPECT_MIN_ENTROPY = 4.0
SUSPECT_MIN_CLASSES = 3


def _entropy(run: str) -> float:
    counts: dict[str, int] = {}
    for char in run:
        counts[char] = counts.get(char, 0) + 1
    total = len(run)
    return -sum(n / total * math.log2(n / total) for n in counts.values())


def _classes(run: str) -> int:
    """Lowercase, uppercase and digits. Punctuation is not counted: a type prefix
    like ``u-`` or ``run_`` would make every platform id "mix" classes."""
    return sum((any(c.islower() for c in run), any(c.isupper() for c in run),
                any(c.isdigit() for c in run)))


#: A short type prefix on an id (``user_``, ``u-``, ``req_``) is not part of the
#: id's randomness; it is taken off before the run is judged.
_TYPE_PREFIX = re.compile(r"^[a-z]{1,8}[_-]")


_IDENT_SEGMENT = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|[0-9]+")


def identifier_shaped(run: str) -> bool:
    """camelCase, PascalCase, snake_case or kebab-case built from word-like
    segments: every letter segment is three or more letters (or an acronym),
    and digit segments are at most a date long (an index, a version,
    ``20260930``). Key material falls apart into one- and two-character
    fragments; an identifier does not."""
    parts = [p for p in re.split(r"[_\-./]+", run) if p]
    segments = [s for p in parts for s in _IDENT_SEGMENT.findall(p)]
    if not segments or "".join(segments) != "".join(parts):
        return False
    letters = [s for s in segments if s.isalpha()]
    digits = [s for s in segments if s.isdigit()]
    return (bool(letters) and all(len(s) >= 3 or s.isupper() and len(s) >= 2 for s in letters)
            and all(len(s) <= 8 for s in digits))


def key_like(run: str) -> bool:
    """Long, high-entropy, at least three character classes, and not an id or
    an identifier: the suspect tier's whole test."""
    body = _TYPE_PREFIX.sub("", run)
    return (len(body) >= SUSPECT_MIN_CHARS and _entropy(body) >= SUSPECT_MIN_ENTROPY
            and _classes(body) >= SUSPECT_MIN_CLASSES and not _HEX_ID.match(body)
            and not identifier_shaped(run))


def _mixed_classes(value: str) -> bool:
    """At least two of lowercase, uppercase and digits: key material mixes them,
    while ``token: "punc-before-expression"`` (a lowercase compound the parser
    reads as opaque) does not. A one-class value stays a suspect, for review."""
    return sum((any(c.islower() for c in value), any(c.isupper() for c in value),
                any(c.isdigit() for c in value))) >= 2


def text_detection(text: str) -> str | None:
    """``R_CREDENTIAL`` / ``R_CONTACT`` for text that may not be public, else None.

    ``R_CREDENTIAL`` only for a certain finding (`_line_verdict`); contact
    details are an email address or a phone number.
    """
    for line in text.splitlines() or [text]:
        if _line_verdict(line) == "certain":
            return R_CREDENTIAL
    if _EMAIL.search(text) or _PHONE.search(text):
        return R_CONTACT
    return None


def text_suspect(text: str) -> bool:
    """Whether the text holds an opaque run that is not certainly a credential."""
    return any(_line_verdict(line) == "suspect" for line in text.splitlines() or [text])


def as_text(data: bytes) -> str | None:
    """The file as text, or None when it is not inspectable UTF-8 text."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return None if "\x00" in text else text


def _excluded_by_owner(rel: str, exclude: list[str]) -> bool:
    return any(rel == item or rel.startswith(item + "/") for item in exclude)


def is_memory_file(rel: str) -> bool:
    parts = rel.split("/")
    folded = [fold(p) for p in parts]
    return folded == [_MEMORY_F] or (len(parts) == 3 and folded[0] == "agents"
                                     and folded[2] == _MEMORY_F)


def classify(rel: str, data: bytes, *, exclude: list[str],
             memory_items: dict[str, list[str]]) -> tuple[bytes | None, str]:
    """``(bytes to publish, "")`` or ``(None, reason)`` for one file.

    Order is the floor: the path never-list, the path's own text, the owner's
    exclusions and memory choice, then the content: a binary, a credential,
    contact details. Nothing the owner names lifts a never-list or a detection.
    """
    reason = structural_exclusion(rel)
    if reason:
        return None, reason
    if text_detection(rel.replace("/", " ")):
        return None, R_PATH
    if len(data) > MAX_FILE_BYTES:
        return None, R_TOO_BIG
    if _excluded_by_owner(rel, exclude):
        return None, R_EXCLUDED
    if is_memory_file(rel):
        wanted = {fold(k): v for k, v in memory_items.items()}.get(fold(rel))
        if not wanted:
            return None, R_MEMORY
        data = select_memory(data, wanted, rel)
    text = as_text(data)
    if text is None:
        return None, R_BINARY
    reason = text_detection(text)
    if reason:
        return None, reason
    parts = rel.split("/")
    if fold(rel) == "settings.yaml" or (
        len(parts) == 3 and fold(parts[0]) == "agents" and fold(parts[2]) == "settings.yaml"
    ):
        from tinyassets.harness_settings import SettingsError, package_settings

        try:
            data = package_settings(data)
        except SettingsError as exc:
            raise PackageError(f"{rel}: invalid harness settings") from exc
    return data, ""


def select_memory(data: bytes, wanted: list[str], rel: str = MEMORY_FILE) -> bytes:
    """Only the memory bullets whose ids the owner named (§4.13 ids)."""
    keep = set(wanted)
    chosen = []
    for line in data.decode("utf-8", "replace").splitlines():
        match = _MEMORY_ITEM.match(line)
        if match and match.group(1) in keep:
            chosen.append(line)
            keep.discard(match.group(1))
    if keep:
        raise PackageError(f"{rel} has no item {', '.join(sorted(keep))}")
    return ("# Memory\n\n" + "\n".join(chosen) + "\n").encode("utf-8")


def _strings(value: Any) -> list[str]:
    return [value] if isinstance(value, str) else [v for v in value if isinstance(v, str)]


def _id_shaped(value: Any) -> bool:
    """A string, or a list of strings, that each look like an identifier."""
    values = [value] if isinstance(value, str) else value if isinstance(value, list) else None
    return bool(values) and all(isinstance(v, str) and _ID_VALUE.match(v) for v in values)


def scan_public(value: Any, where: str = "", notes: list[str] | None = None,
                schema: str = "") -> None:
    """The final-output check over every string in ``value``, keys included.

    A certain credential or contact details anywhere raises `PackageError`
    naming where. A suspect string (an opaque run that is not certainly a
    credential) is appended to ``notes`` as its location, for the tab's review
    list. A platform id at a known schema location (``_ID_PATHS``; ``schema``
    is the path from the scan root) skips the credential test, never the
    contact test.
    """
    if isinstance(value, dict):
        for key, child in value.items():
            here = f"{where}.{key}" if where else str(key)
            if text_detection(str(key)):
                raise PackageError(f"{where or 'the package'} has a field name that "
                                   "carries a credential or contact details")
            step = "*" if schema == "components" else str(key)
            path = f"{schema}.{step}" if schema else step
            if path in _ID_PATHS and _id_shaped(child):
                if any(_EMAIL.search(v) or _PHONE.search(v) for v in _strings(child)):
                    raise PackageError(f"{here}: {R_CONTACT}; nothing was published. "
                                       "Remove it and ask again")
                continue
            scan_public(child, here, notes, path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            scan_public(child, f"{where}[{index}]", notes, schema)
    elif isinstance(value, str):
        reason = text_detection(value)
        if reason:
            raise PackageError(f"{where or 'the package'}: {reason}; nothing was "
                               "published. Remove it and ask again")
        if notes is not None and text_suspect(value):
            notes.append(where or "the package")


# --------------------------------------------------------------------------- #
# The publish walk
# --------------------------------------------------------------------------- #


def _entry_kind(universe_dir: Path, rel: str) -> str:
    try:
        info = os.lstat(Path(universe_dir) / rel)
    except OSError:
        return "other"
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0):
        return "other"
    if stat.S_ISDIR(info.st_mode):
        return "dir"
    return "file" if stat.S_ISREG(info.st_mode) else "other"


def walk(universe_dir: Path) -> Iterator[tuple[str, str]]:
    """Every entry under the folder as ``(rel, kind)``, never following a link.

    Listing goes through the no-follow lister. A never-list folder is reported
    once (``skipped-dir``) and not descended into, so its contents are never
    read.
    """
    pending = [""]
    seen = 0
    while pending:
        rel_dir = pending.pop()
        try:
            names = list_universe_dir(universe_dir, rel_dir)
        except OSError:
            continue
        for name in names:
            seen += 1
            if seen > MAX_WALK_ENTRIES:
                raise PackageError(
                    f"this command center has over {MAX_WALK_ENTRIES} files and folders; "
                    "leave the large folders out and ask again")
            rel = f"{rel_dir}/{name}" if rel_dir else name
            kind = _entry_kind(universe_dir, rel)
            if kind == "dir":
                if dir_exclusion(rel) or rel.count("/") + 1 >= MAX_DEPTH:
                    yield rel + "/", "skipped-dir"
                    continue
                pending.append(rel)
            else:
                yield rel, kind


def review_word(data: bytes) -> str:
    """The first often-private word in an included text file, or ``""``."""
    match = _REVIEW_WORDS.search(data.decode("utf-8", "replace"))
    return match.group(0) if match else ""


def review_note(data: bytes) -> str:
    """Why an included file is worth a look before publishing, or ``""``."""
    word = review_word(data)
    if word:
        return f'mentions "{word}"'
    return N_OPAQUE if text_suspect(data.decode("utf-8", "replace")) else ""


#: The tab's review list is a summary a person can read: grouped by kind, with
#: a count and the first few, never one line per file (the full file list is
#: above it).
REVIEW_SHOWN = 5


def review_groups(flagged: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Flags grouped by kind: often-private words, then random-looking strings."""
    words = [f for f in flagged if f["note"].startswith("mentions")]
    opaque = [f for f in flagged if not f["note"].startswith("mentions")]
    groups = []
    if words:
        groups.append({"kind": "mentions an often-private word", "count": len(words),
                       "shown": [f"{f['path']} ({f['note'][len('mentions '):]})"
                                 for f in words[:REVIEW_SHOWN]]})
    if opaque:
        groups.append({"kind": N_OPAQUE, "count": len(opaque),
                       "shown": [f["path"] for f in opaque[:REVIEW_SHOWN]]})
    return groups


# -- the install-side content screen -------------------------------------------
#: What the ClawHub poisoning wave (Feb 2026) taught: a shared package is
#: untrusted code. The ingestion boundary checks structure; this checks content
#: for the exfiltration patterns that wave used, and the install tab shows
#: every hit. Hits are review flags, never refusals: a notifier package
#: legitimately posts to a webhook, and only the owner knows which endpoints
#: are theirs.

#: Kept as data next to this module so platform code names no channel
#: (tests/test_channel_agnostic_ratchet.py); edit the JSON to add endpoints.
_EXFIL_HOSTS = tuple(json.loads(
    read_data_path(Path(__file__).with_name("package_screen_exfil_hosts.json")) or b"{}"
)["exfil_hosts"])

#: Secret locations the ClawHavoc skills harvested before exfiltrating.
_SECRET_READS = (
    ".aws/credentials",
    ".ssh/id_rsa",
    ".ssh/id_ed25519",
    ".gnupg/secring",
    "login.keychain",
    ".clawdbot",
)

#: Fragments of reverse shells; the wave hid these in functional code.
_SHELL_RUNS = (
    "/dev/tcp/",
    "bash -i >&",
    "nc -e",
    "ncat -e",
)

# Start only at a run boundary: retrying at each character of a 159-char
# near-match makes large ordinary scripts unnecessarily expensive.
_B64_RUN = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{160}")
_DECODE_EXEC = re.compile(
    r"(eval|exec)\s*\(\s*(base64\.b64decode|bytes\.fromhex|codecs\.decode)",
    re.IGNORECASE)
# Bounded whitespace after the pipe: an unbounded \s* let ~40 `curl` tokens in
# one window each rescan the same long whitespace run (16s per 8 MiB file).
_PIPE_TO_SHELL = re.compile(r"\b(curl|wget)\b[^\n]{0,200}\|[ \t]{0,20}(ba|z)?sh\b",
                            re.IGNORECASE)
_POWERSHELL_IEX = re.compile(r"\biex\s*\(", re.IGNORECASE)
#: The wave's "Prerequisites" docs pointed at paste sites for the payload.
_PASTE_SITES = ("glot.io", "pastebin.com", "paste.rs", "termbin.com", "ix.io", "0x0.st")

#: Only scripts get the long-encoded-blob flag; docs and data carry long
#: opaque strings for innocent reasons.
_SCRIPT_SUFFIXES = (".py", ".js", ".sh", ".ps1", ".bat")


def _install_flags(path: str, text: str) -> list[tuple[str, str]]:
    """``(kind, detail)`` content hits for one package file's text."""
    lower = text.lower()
    hits: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(kind: str, detail: str) -> None:
        if kind not in seen:
            seen.add(kind)
            hits.append((kind, detail))

    for host in _EXFIL_HOSTS:
        if host in lower:
            add("exfiltration endpoint", host)
            break
    for secret in _SECRET_READS:
        if secret in lower:
            add("reads a well-known secret location", secret)
            break
    for run in _SHELL_RUNS:
        if run in lower:
            add("reverse-shell fragment", run.strip())
            break
    if _PIPE_TO_SHELL.search(text):
        # The wave's signature move: the agent itself ran the install line
        # from the skill's docs.
        where = "shared docs" if path.endswith(".md") else "a script"
        add("pipes a download into a shell", f"curl/wget into sh in {where}")
    if _POWERSHELL_IEX.search(text):
        add("pipes a download into a shell", "IEX( download cradle")
    if _DECODE_EXEC.search(text):
        add("executes decoded content", "eval/exec of decoded bytes")
    elif path.endswith(_SCRIPT_SUFFIXES) and _B64_RUN.search(text):
        add("long encoded blob in a script", "160+ base64 chars")
    if path.endswith(".md") and any(site in lower for site in _PASTE_SITES) \
            and ("bash" in lower or re.search(r"\bsh\b", lower)):
        add("shell install instructions in shared docs", "paste-site link beside shell")
    return hits


def scan_install(files: dict[str, bytes]) -> list[dict[str, str]]:
    """Content-safety flags for a package's files, for the install tab.

    Runs on the verified files at quarantine time, before anything is planned
    from them. Every hit is ``{"path", "kind", "note"}``; an empty list means
    nothing worth flagging. Flags never refuse the install: the tab shows
    them and the owner decides.
    """
    flagged: list[dict[str, str]] = []
    for path in sorted(files):
        text = as_text(files[path])
        if text is None:
            continue
        for kind, detail in _install_flags(path, text):
            flagged.append({"path": path, "kind": kind, "note": detail})
    return flagged


def install_review_groups(flagged: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Flags grouped by kind for the install tab, mirroring ``review_groups``."""
    by_kind: dict[str, list[str]] = {}
    order: list[str] = []
    for found in flagged:
        by_kind.setdefault(found["kind"], []).append(found["path"])
        if found["kind"] not in order:
            order.append(found["kind"])
    return [{"kind": kind, "count": len(by_kind[kind]),
             "shown": by_kind[kind][:REVIEW_SHOWN]} for kind in order]


def collect(universe_dir: Path, *, exclude: list[str],
            memory_items: dict[str, list[str]]
            ) -> tuple[dict[str, bytes], list[dict[str, str]]]:
    """The files a ``publish`` package carries, and every entry left out with why."""
    files: dict[str, bytes] = {}
    excluded: list[dict[str, str]] = []
    for rel, kind in sorted(walk(universe_dir)):
        if kind == "skipped-dir":
            reason = dir_exclusion(rel.rstrip("/")) or R_DEEP
            if reason != R_DOT:
                # Dot folders are platform state the owner never sees; listing
                # each would only bury what they need to read.
                excluded.append({"path": rel, "reason": reason})
            continue
        if kind != "file":
            excluded.append({"path": rel, "reason": R_UNREADABLE})
            continue
        reason = structural_exclusion(rel)
        if reason:
            if reason != R_DOT:
                excluded.append({"path": rel, "reason": reason})
            continue
        try:
            check_path(rel)
            data = read_universe_file(universe_dir, rel, max_bytes=MAX_FILE_BYTES)
        except PackageError:
            excluded.append({"path": rel, "reason": R_UNREADABLE})
            continue
        except OSError as exc:
            reason = R_TOO_BIG if "bound" in str(exc) else R_UNREADABLE
            excluded.append({"path": rel, "reason": reason})
            continue
        kept, reason = classify(rel, data, exclude=exclude, memory_items=memory_items)
        if kept is None:
            excluded.append({"path": rel, "reason": reason})
            continue
        files[rel] = kept
    missing = sorted(set(memory_items) - {fold(p) for p in files})
    if missing:
        raise PackageError(f"you named memory items in {', '.join(missing)}, but that "
                           "file could not be included")
    try:
        check_tree(list(files))
    except PackageError as exc:
        raise PackageError(f"two files in this command center would be one file in a "
                           f"copy: {exc}") from None
    return files, excluded


def connection_names(rows: Any) -> list[str]:
    """Connection names a published workflow refers to: named references only."""
    found: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if (str(key).lower() in _CONNECTION_KEYS and isinstance(child, str)
                        and _CONNECTION_NAME.match(child.strip().lower())):
                    found.add(child.strip().lower())
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(rows)
    return sorted(found)


def model_need(files: dict[str, bytes]) -> str:
    raw = files.get("settings.yaml")
    if not raw:
        return ""
    from tinyassets.universe_files import load_untrusted_yaml

    try:
        doc = load_untrusted_yaml(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError):
        return ""
    model = doc.get("model") if isinstance(doc, dict) else None
    if isinstance(model, dict):
        model = model.get("id")
    return model.strip()[:120] if isinstance(model, str) else ""


def agents_in(files: dict[str, bytes]) -> list[str]:
    names = {p.split("/")[1] for p in files
             if fold(p.split("/")[0]) == "agents" and p.count("/") >= 2}
    root = any(fold(p) in _HARNESS_FILES_F or fold(p.split("/")[0]) in _HARNESS_DIRS_F
               for p in files)
    return (["main"] if root else []) + sorted(names)


def build_manifest(*, profile: str, name: str, description: str, files: dict[str, bytes],
                   workflows: list[dict[str, Any]], ui: str, automations: list[dict[str, Any]],
                   connections: list[str]) -> dict[str, Any]:
    """``command-center.json``: one manifest for every profile (§4.17, D1)."""
    return {
        "format_version": FORMAT_VERSION,
        "profile": profile,
        "name": name,
        "description": description,
        "agents": agents_in(files),
        "files": [{"path": p, "size": len(b), "sha256": hashlib.sha256(b).hexdigest()}
                  for p, b in sorted(files.items())],
        "workflows": workflows,
        "ui": ui,
        "automations": automations,
        "needs": {"model": model_need(files), "connections": connections},
    }


def build_blob(manifest: dict[str, Any], files: dict[str, bytes]) -> bytes:
    """The canonical package content. Its sha256 is the package's identity."""
    doc = {"format_version": FORMAT_VERSION, "manifest": manifest,
           "files": {p: base64.b64encode(b).decode("ascii") for p, b in sorted(files.items())}}
    return json.dumps(doc, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("ascii")


def build_publish_package(universe_dir: Path, *, name: str, description: str,
                          options: dict[str, Any], branch_rows: Any,
                          workflows: list[dict[str, Any]], ui: str,
                          automations: list[dict[str, Any]], bundle_id: str = "") -> dict[str, Any]:
    """Everything the ``publish`` ask pins for a package. Reads the folder only."""
    files, excluded = collect(universe_dir, exclude=options["exclude"],
                              memory_items=options["memory_items"])
    flagged = [{"path": p, "note": n} for p, b in sorted(files.items())
               if (n := review_note(b))]
    if not files:
        raise PackageError("nothing in this command center can be published as files")
    if len(files) > MAX_FILES:
        raise PackageError(f"a package holds at most {MAX_FILES} files; this one has "
                           f"{len(files)}. Leave some folders out and ask again")
    manifest = build_manifest(
        profile=PROFILE_PUBLISH, name=name, description=description, files=files,
        workflows=workflows, ui=ui, automations=automations,
        connections=connection_names(branch_rows))
    if bundle_id:
        manifest["bundle_id"] = bundle_id
    blob = build_blob(manifest, files)
    if len(blob) > MAX_PACKAGE_BYTES:
        raise PackageError(f"this package is {human(len(blob))}, over the "
                           f"{human(MAX_PACKAGE_BYTES)} a package may be")
    return {"blob": blob, "sha256": hashlib.sha256(blob).hexdigest(), "manifest": manifest,
            "excluded": excluded, "flagged": flagged}


def narrow_package(blob: bytes, leave_out: list[str]) -> dict[str, Any]:
    """The verified package with ``leave_out`` paths (files or folders) removed.

    Built from the blob already pinned and verified, never from the live
    folder: what remains is byte-for-byte what the owner was shown.
    """
    manifest, files = check_blob(blob)
    kept = {p: b for p, b in files.items()
            if not any(p == x or p.startswith(x + "/") for x in leave_out)}
    if not kept:
        raise PackageError("that leaves nothing to publish as files")
    narrowed = build_manifest(
        profile=PROFILE_PUBLISH, name=manifest["name"], description=manifest["description"],
        files=kept, workflows=manifest["workflows"], ui=manifest["ui"],
        automations=manifest["automations"], connections=manifest["needs"]["connections"])
    if "bundle_id" in manifest:
        narrowed["bundle_id"] = manifest["bundle_id"]
    new_blob = build_blob(narrowed, kept)
    return {"blob": new_blob, "sha256": hashlib.sha256(new_blob).hexdigest(),
            "manifest": narrowed}


def human(size: int | float) -> str:
    value = float(size)
    for unit in ("bytes", "KiB", "MiB"):
        if value < 1024:
            return f"{int(value)} bytes" if unit == "bytes" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GiB"


def validate_options(raw: Any) -> dict[str, Any]:
    """The ``package`` block of a ``publish`` action, shape only."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("package must be an object")
    unknown = set(raw) - {"exclude", "memory_items", "agent"}
    if unknown:
        raise ValueError(f"package has unknown fields: {', '.join(sorted(unknown))}")
    value = raw.get("exclude") or []
    if not isinstance(value, list) or len(value) > 500:
        raise ValueError("package.exclude must be a list of at most 500 paths")
    exclude: list[str] = []
    for item in value:
        try:
            path = check_path(item.rstrip("/") if isinstance(item, str) else item)
        except PackageError as exc:
            raise ValueError(f"package.exclude: {exc}") from None
        if path not in exclude:
            exclude.append(path)
    items = raw.get("memory_items") or []
    if not isinstance(items, list) or len(items) > 500:
        raise ValueError("package.memory_items must be a list of memory item ids")
    memory: dict[str, list[str]] = {}
    for item in items:
        if not isinstance(item, str):
            raise ValueError("package.memory_items must be a list of memory item ids")
        path, _, ident = item.rpartition("#")
        path = path or MEMORY_FILE
        if not _MEMORY_ID.fullmatch(ident) or not is_memory_file(path):
            raise ValueError(f"package.memory_items: {item!r} is not an item id like m_7f3a "
                             "or agents/<id>/MEMORY.md#m_7f3a")
        key = fold(path)
        memory.setdefault(key, [])
        if ident not in memory[key]:
            memory[key].append(ident)
    return {"exclude": sorted(exclude),
            "memory_items": {p: sorted(ids) for p, ids in sorted(memory.items())},
            "agent": agent_id(raw.get("agent"))}


def agent_id(raw: Any) -> str:
    """The acting agent (multi-agent invariant, §4.18). ``main`` is a default,
    not a special case."""
    from tinyassets.agent_rules import MAIN_AGENT

    text = MAIN_AGENT if raw in (None, "") else raw
    if not isinstance(text, str) or not _AGENT_ID.fullmatch(text):
        raise ValueError("agent must be an agent id")
    return text


# --------------------------------------------------------------------------- #
# Store: blobs, versions, pins
# --------------------------------------------------------------------------- #

_SCHEMA = """
CREATE TABLE IF NOT EXISTS blobs (
    author_id   TEXT NOT NULL,
    blob_sha256 TEXT NOT NULL,
    size_bytes  INTEGER NOT NULL CHECK (size_bytes >= 0),
    created_at  REAL NOT NULL,
    PRIMARY KEY (author_id, blob_sha256)
);
CREATE TABLE IF NOT EXISTS package_versions (
    author_id     TEXT NOT NULL,
    name          TEXT NOT NULL,
    version       INTEGER NOT NULL CHECK (version >= 1),
    blob_sha256   TEXT NOT NULL,
    definition_id TEXT NOT NULL DEFAULT '',
    created_at    REAL NOT NULL,
    PRIMARY KEY (author_id, name, version)
);
-- The consent record of a publish or install ask, bound to the request that
-- displayed it. Answers execute THIS, never the pending-request row.
CREATE TABLE IF NOT EXISTS pins (
    universe_id  TEXT NOT NULL,
    owner_id     TEXT NOT NULL DEFAULT '',
    pin_id       TEXT NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('publish', 'install')),
    agent_id     TEXT NOT NULL,
    digest       TEXT NOT NULL,
    request_id   TEXT NOT NULL,
    record_json  TEXT NOT NULL,
    state        TEXT NOT NULL DEFAULT 'pinned'
                 CHECK (state IN ('pinned', 'activating', 'activated')),
    progress_json TEXT NOT NULL DEFAULT '{}',
    claimed_at   REAL,
    claim_token  TEXT NOT NULL DEFAULT '',
    created_at   REAL NOT NULL,
    activated_at REAL,
    PRIMARY KEY (universe_id, pin_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_pins_request ON pins(universe_id, request_id);
"""


def store_dir(base_path: str | Path) -> Path:
    return Path(base_path) / ROOT_DIR


def database_path(base_path: str | Path) -> Path:
    return store_dir(base_path) / _DB


def ensure_pin_owners(conn: sqlite3.Connection) -> None:
    """Migrate within the caller's write transaction, including during erasure.

    Release links are platform-written consent evidence. Their author remains
    authoritative after a home rebind/removal; never infer it from current homes.
    Older non-release pins have no such evidence and keep the empty owner.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(pins)")}
    if "owner_id" not in columns:
        conn.execute("ALTER TABLE pins ADD COLUMN owner_id TEXT NOT NULL DEFAULT ''")
    conn.execute(
        "UPDATE pins SET owner_id = json_extract(record_json, '$.action.release_link.author_id') "
        "WHERE owner_id = '' AND kind = 'publish' "
        "AND json_type(record_json, '$.action.release_link.author_id') = 'text'"
    )


@contextlib.contextmanager
def _db(base_path: str | Path) -> Iterator[sqlite3.Connection]:
    root = store_dir(base_path)
    root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(database_path(base_path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.executescript(_SCHEMA)
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            ensure_pin_owners(conn)
        yield conn
    finally:
        conn.close()


def next_version(base_path: str | Path, author_id: str, name: str) -> int:
    with _db(base_path) as conn:
        row = conn.execute(
            "SELECT MAX(version) AS v FROM package_versions WHERE author_id = ? AND name = ?",
            (author_id, name)).fetchone()
    return int(row["v"] or 0) + 1


def _blob_path(base_path: str | Path, sha256: str) -> Path:
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise PackageError("not a package content id")
    return store_dir(base_path) / _BLOBS / f"{sha256}.json"


def blob_owned(base_path: str | Path, author_id: str, sha256: str) -> bool:
    """Already paid for: owned by this author AND on disk."""
    with _db(base_path) as conn:
        owned = conn.execute(
            "SELECT 1 FROM blobs WHERE author_id = ? AND blob_sha256 = ?",
            (author_id, sha256)).fetchone() is not None
    return owned and _blob_path(base_path, sha256).exists()


def store_blob(base_path: str | Path, *, author_id: str, blob: bytes) -> str:
    """Write ``blob`` once, then record ``author_id`` as an owner of it.

    The write first, under the caller's storage reservation: ownership is the
    "already paid for" record, so it exists only once the bytes do (gpt-6-astra,
    code r2 #6). A crash between the two leaves an unowned file and no record,
    and the retry reserves again. Content-addressed, so rewriting identical
    bytes is a no-op.
    """
    from tinyassets.universe_files import write_data_path

    sha = hashlib.sha256(blob).hexdigest()
    path = _blob_path(base_path, sha)
    if not path.exists():
        # The platform's one link-free writer: atomic temp + rename.
        write_data_path(path, blob, make_parents=True)
    with _db(base_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO blobs (author_id, blob_sha256, size_bytes, created_at) "
            "VALUES (?, ?, ?, ?)", (author_id, sha, len(blob), time.time()))
    return sha


def read_blob(base_path: str | Path, sha256: str) -> bytes:
    """The blob, verified against its own name."""
    from tinyassets.universe_files import read_data_path

    path = _blob_path(base_path, sha256)
    try:
        data = read_data_path(path, max_bytes=MAX_PACKAGE_BYTES)
    except OSError:
        raise PackageError("this package's content is not available") from None
    if data is None:
        raise PackageError("this package's content is not available")
    if hashlib.sha256(data).hexdigest() != sha256:
        raise PackageError("this package's content does not match its id")
    return data


def record_version(base_path: str | Path, *, author_id: str, name: str, version: int,
                   sha256: str) -> None:
    """List ``version``. Re-recording the same content is a no-op (a retry);
    another content under the same number is a lost race."""
    with _db(base_path) as conn:
        row = conn.execute(
            "SELECT blob_sha256 FROM package_versions WHERE author_id = ? AND name = ? "
            "AND version = ?", (author_id, name, version)).fetchone()
        if row is not None:
            if row["blob_sha256"] == sha256:
                return
            raise PackageError(
                f"version {version} of \"{name}\" was published meanwhile; ask again")
        conn.execute(
            "INSERT INTO package_versions (author_id, name, version, blob_sha256, "
            "created_at) VALUES (?, ?, ?, ?, ?)",
            (author_id, name, version, sha256, time.time()))


def set_version_definition(base_path: str | Path, *, author_id: str, name: str,
                           version: int, definition_id: str) -> None:
    with _db(base_path) as conn:
        conn.execute(
            "UPDATE package_versions SET definition_id = ? WHERE author_id = ? AND name = ? "
            "AND version = ?", (definition_id, author_id, name, version))


def drop_version(base_path: str | Path, *, author_id: str, name: str, version: int) -> None:
    with _db(base_path) as conn:
        conn.execute(
            "DELETE FROM package_versions WHERE author_id = ? AND name = ? AND version = ? "
            "AND definition_id = ''", (author_id, name, version))


def measure_packages(base_path: str | Path, actors: list[str]) -> int:
    """The ``packages`` store: every blob these principals own, listed or not."""
    path = store_dir(base_path) / _DB
    if not path.exists() or not actors:
        return 0
    marks = ",".join("?" * len(actors))
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30)
    try:
        row = conn.execute(
            f"SELECT SUM(size_bytes) FROM blobs WHERE author_id IN ({marks})",
            tuple(actors)).fetchone()
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()
    return int(row[0] or 0)


def pin(base_path: str | Path, *, universe_id: str, kind: str, agent: str, digest: str,
        record: dict[str, Any], owner_id: str = "") -> str:
    """Pin a consent record under a request id the PLATFORM allocates; returns it.

    The id is minted here, before any pending-request row exists, and the row
    is then created under it. An id is never adopted from the agent-writable
    request store, so no row the agent planted can become a consent's display.
    Written once: a request's record is never replaced.
    """
    import uuid

    from tinyassets.storage import _connect as author_connection
    from tinyassets.storage.current_home import check_principal_not_deleted

    request_id = "req_" + uuid.uuid4().hex[:24]
    pin_id = hashlib.sha256(f"{universe_id}\x00{request_id}".encode()).hexdigest()[:32]
    with _db(base_path) as conn:
        # As in run-file custody, hold the canonical writer exclusion from the
        # tombstone check through destination commit. Deletion's tombstone uses
        # this same writer, even after a home rebind/removal. Package schema
        # initialization above finishes before acquiring the canonical fence;
        # the INSERT below autocommits before releasing it.
        with author_connection(base_path) as author:
            author.execute("BEGIN IMMEDIATE")
            if owner_id:
                check_principal_not_deleted(author, owner_id)
            conn.execute(
                "INSERT INTO pins (universe_id, owner_id, pin_id, kind, agent_id, digest, "
                "request_id, record_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (universe_id, owner_id, pin_id, kind, agent, digest, request_id,
                 json.dumps(record, sort_keys=True), time.time()))
    return request_id


def open_pins(base_path: str | Path, *, universe_id: str, kind: str, agent: str,
              digest: str) -> list[str]:
    """Request ids already pinned with this exact content and not yet activated,
    newest first: the same ask raised again reuses its tab when that is still up."""
    with _db(base_path) as conn:
        rows = conn.execute(
            "SELECT request_id FROM pins WHERE universe_id = ? AND kind = ? AND agent_id = ? "
            "AND digest = ? AND state = 'pinned' ORDER BY created_at DESC",
            (universe_id, kind, agent, digest)).fetchall()
    return [str(r["request_id"]) for r in rows]


def completed_system_copies(base_path: str | Path, *, universe_id: str) -> list[dict]:
    """Stored install evidence only; callers must establish owner/home authority."""
    with _db(base_path) as conn:
        rows = conn.execute(
            "SELECT request_id,record_json FROM pins WHERE universe_id=? "
            "AND kind='install' AND state='activated' ORDER BY activated_at DESC LIMIT 100",
            (universe_id,),
        ).fetchall()
    result = []
    for row in rows:
        plan = json.loads(row["record_json"]).get("action", {}).get("plan", {})
        if plan.get("publication_kind") == "system":
            result.append({"request_id": row["request_id"], "name": plan.get("name", ""),
                           "source_definition_id": plan.get("definition_id", "")})
    return result


def pin_for_request(base_path: str | Path, *, universe_id: str,
                    request_id: str) -> dict[str, Any] | None:
    """The consent record bound to ``request_id`` in this command center, or None."""
    if not request_id:
        return None
    with _db(base_path) as conn:
        row = conn.execute(
            "SELECT * FROM pins WHERE universe_id = ? AND request_id = ?",
            (universe_id, request_id)).fetchone()
    if row is None:
        return None
    return {"pin_id": row["pin_id"], "kind": row["kind"], "agent": row["agent_id"],
            "digest": row["digest"], "record": json.loads(row["record_json"]),
            "state": row["state"], "progress": json.loads(row["progress_json"])}


#: How long one activation holds its claim. A second confirm inside it is
#: refused rather than run beside the first; after it, the claim is a crashed
#: activation's and a confirm resumes it.
CLAIM_LEASE_S = 600.0


class LostClaim(PackageError):
    """Another confirm took this activation over; the holder must stop."""


def claim(base_path: str | Path, *, universe_id: str, pin_id: str,
          now: float | None = None) -> tuple[str, str]:
    """Claim a pin for activation, atomically: ``(state, token)``.

    ``state`` is ``pinned`` (claimed fresh), ``activating`` (a lapsed claim
    taken over: resume) or ``activated`` (done already; no token). A live claim
    raises `PackageError`. The token FENCES the holder: progress, release and
    finish all require it, and every progress write renews the lease, so a slow
    activation that was taken over cannot write over its successor
    (gpt-6-astra, code r2 #7).
    """
    import uuid

    moment = time.time() if now is None else now
    token = uuid.uuid4().hex
    with _db(base_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT state, claimed_at FROM pins WHERE universe_id = ? AND pin_id = ?",
                (universe_id, pin_id)).fetchone()
            if row is None:
                raise PackageError("this request's consent record is missing")
            state = str(row["state"])
            if state == "activating" and moment - float(row["claimed_at"] or 0) < CLAIM_LEASE_S:
                raise PackageError("this is already being installed; wait a moment")
            if state != "activated":
                conn.execute("UPDATE pins SET state = 'activating', claimed_at = ?, "
                             "claim_token = ? WHERE universe_id = ? AND pin_id = ?",
                             (moment, token, universe_id, pin_id))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return state, ("" if state == "activated" else token)


def unclaim(base_path: str | Path, *, universe_id: str, pin_id: str, token: str) -> None:
    """Let the next confirm resume at once: a failed activation stops holding.
    Only the current holder can release; a superseded one changes nothing."""
    with _db(base_path) as conn:
        conn.execute("UPDATE pins SET claimed_at = 0 WHERE universe_id = ? AND pin_id = ? "
                     "AND state = 'activating' AND claim_token = ?",
                     (universe_id, pin_id, token))


def pin_progress(base_path: str | Path, *, universe_id: str, pin_id: str) -> dict[str, Any]:
    with _db(base_path) as conn:
        row = conn.execute("SELECT progress_json FROM pins WHERE universe_id = ? AND pin_id = ?",
                           (universe_id, pin_id)).fetchone()
    return json.loads(row["progress_json"]) if row else {}


def record_progress(base_path: str | Path, *, universe_id: str, pin_id: str,
                    progress: dict[str, Any], token: str) -> None:
    """Save progress and renew the lease, as the current holder only.
    Raises `LostClaim` when another confirm has taken over."""
    with _db(base_path) as conn:
        cur = conn.execute(
            "UPDATE pins SET progress_json = ?, claimed_at = ? WHERE universe_id = ? "
            "AND pin_id = ? AND state = 'activating' AND claim_token = ?",
            (json.dumps(progress, sort_keys=True, default=str), time.time(), universe_id,
             pin_id, token))
    if cur.rowcount != 1:
        raise LostClaim("another confirm took this over; this one stopped")


def finish(base_path: str | Path, *, universe_id: str, pin_id: str,
           progress: dict[str, Any], token: str) -> None:
    with _db(base_path) as conn:
        cur = conn.execute(
            "UPDATE pins SET state = 'activated', progress_json = ?, activated_at = ? "
            "WHERE universe_id = ? AND pin_id = ? AND state = 'activating' "
            "AND claim_token = ?",
            (json.dumps(progress, sort_keys=True, default=str), time.time(), universe_id,
             pin_id, token))
    if cur.rowcount != 1:
        raise LostClaim("another confirm took this over; this one stopped")


# --------------------------------------------------------------------------- #
# The ingestion boundary
# --------------------------------------------------------------------------- #


def check_blob(blob: bytes) -> tuple[dict[str, Any], dict[str, bytes]]:
    """``(manifest, files)`` from a blob, or `PackageError`. Nothing is written.

    Bounds bytes, file count, depth and path length; refuses absolute,
    traversal, hidden and colliding paths and a file where a folder must be;
    every file must be listed in the manifest with the size and sha256 it has.
    """
    if len(blob) > MAX_PACKAGE_BYTES:
        raise PackageError(f"this package is over {human(MAX_PACKAGE_BYTES)}")
    try:
        doc = json.loads(blob)
    except (ValueError, RecursionError):
        raise PackageError("this package's content is not a package") from None
    if not isinstance(doc, dict) or doc.get("format_version") != FORMAT_VERSION:
        raise PackageError("this package's format is not one this platform reads")
    manifest, raw_files = doc.get("manifest"), doc.get("files")
    if not isinstance(manifest, dict) or not isinstance(raw_files, dict):
        raise PackageError("this package's content is not a package")
    if manifest.get("profile") != PROFILE_PUBLISH:
        raise PackageError("only published packages install")
    if len(raw_files) > MAX_FILES:
        raise PackageError(f"this package has over {MAX_FILES} files")
    listed = manifest.get("files")
    if not isinstance(listed, list) or len(listed) != len(raw_files):
        raise PackageError("this package's file list does not match its content")
    files: dict[str, bytes] = {}
    for path, encoded in raw_files.items():
        check_path(path)
        if not isinstance(encoded, str):
            raise PackageError(f"{path!r} is not a regular file")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise PackageError(f"{path!r} is not a regular file") from None
        if len(data) > MAX_FILE_BYTES:
            raise PackageError(f"{path!r} is over the per-file bound")
        files[path] = data
    check_tree(list(files))
    for entry in listed:
        if not isinstance(entry, dict) or entry.get("path") not in files:
            raise PackageError("this package's file list does not match its content")
        data = files[entry["path"]]
        if entry.get("size") != len(data) or entry.get("sha256") != hashlib.sha256(
                data).hexdigest():
            raise PackageError(f"{entry['path']!r} does not match its listed digest")
    for path, data in files.items():
        parts = path.split("/")
        if fold(path) == "settings.yaml" or (
            len(parts) == 3 and fold(parts[0]) == "agents" and fold(parts[2]) == "settings.yaml"
        ):
            from tinyassets.harness_settings import SettingsError, parse_settings

            try:
                settings = parse_settings(data)
            except SettingsError as exc:
                raise PackageError(f"{path}: invalid harness settings") from exc
            if settings.model is not None and settings.model.connection is not None:
                raise PackageError(f"{path}: package model needs a recipient-local binding")
    return manifest, files


# --------------------------------------------------------------------------- #
# Install: where files land, and the writer
# --------------------------------------------------------------------------- #


def slug(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    text = _SLUG.sub("-", ascii_name.lower()).strip("-")[:40].strip("-")
    return text or "package"


def destination(path: str, agent_slug: str) -> str:
    """Where a package file lands in the installer's command center.

    Root harness files become a roster agent (``agents/<slug>/``) and the
    package's own roster agents ``agents/<slug>-<id>/``: the installer's main
    agent is never written. Workspace files and wiki pages keep their path,
    because the UI and the workflows address them by path.
    """
    parts = path.split("/")
    if fold(path) in _HARNESS_FILES_F or (len(parts) > 1 and fold(parts[0]) in _HARNESS_DIRS_F):
        return f"agents/{agent_slug}/{path}"
    if fold(parts[0]) == "agents" and len(parts) >= 3:
        return f"agents/{agent_slug}-{parts[1]}/" + "/".join(parts[2:])
    return path


def _exists(universe_dir: Path, rel: str) -> bool:
    try:
        os.lstat(Path(universe_dir) / rel)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _blocked(universe_dir: Path, rel: str) -> bool:
    """A parent of ``rel`` exists and is not a plain folder (a file or a link)."""
    parts = rel.split("/")
    for i in range(1, len(parts)):
        try:
            info = os.lstat(Path(universe_dir) / "/".join(parts[:i]))
        except FileNotFoundError:
            return False
        except OSError:
            return True
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) \
                or getattr(info, "st_reparse_tag", 0):
            return True
    return False


def plan_install(universe_dir: Path, manifest: dict[str, Any],
                 files: dict[str, bytes]) -> dict[str, Any]:
    """Every file's destination, and which stay the installer's own.

    A destination that already exists, or whose folder is taken by a file or a
    link, is kept as theirs and named. The destination map is checked as a
    tree after relocation.
    """
    base = slug(str(manifest.get("name") or "package"))
    agent_slug, n = base, 1
    while _exists(universe_dir, f"agents/{agent_slug}") or any(
            _exists(universe_dir, f"agents/{agent_slug}-{a}") for a in manifest.get(
                "agents") or [] if isinstance(a, str) and a != "main"):
        n += 1
        agent_slug = f"{base}-{n}"
    mapping = {path: check_path(destination(path, agent_slug)) for path in sorted(files)}
    check_tree(list(mapping.values()))
    land: list[dict[str, str]] = []
    keep: list[str] = []
    for path, dest in mapping.items():
        if _exists(universe_dir, dest) or _blocked(universe_dir, dest):
            keep.append(dest)
        else:
            land.append({"path": path, "to": dest})
    return {"agent_slug": agent_slug, "land": land, "keep": keep,
            "bytes": sum(len(files[e["path"]]) for e in land)}


def write_new_file(universe_dir: Path, rel: str, data: bytes) -> bool:
    """Create ``rel`` holding ``data``. False if anything is already there.

    The platform's one link-free writer (``universe_files.write_universe_file``,
    mode ``exclusive``): every directory is created and opened without following
    a link and the file is created ``O_EXCL | O_NOFOLLOW``, so a link planted
    anywhere on the path refuses (``OSError``) rather than redirecting.
    """
    from tinyassets.universe_files import write_universe_file

    check_path(rel)
    try:
        write_universe_file(universe_dir, rel, data, make_parents=True, mode="exclusive")
    except FileExistsError:
        return False
    return True


__all__ = [
    "FORMAT_VERSION",
    "PACKAGE_KIND",
    "PACKAGE_TAG",
    "PROFILE_PUBLISH",
    "PackageError",
    "build_manifest",
    "build_publish_package",
    "check_blob",
    "check_path",
    "classify",
    "install_review_groups",
    "plan_install",
    "read_blob",
    "scan_install",
    "scan_public",
    "store_blob",
    "write_new_file",
]
