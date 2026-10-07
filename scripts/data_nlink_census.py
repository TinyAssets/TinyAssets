"""Read-only census of multi-link regular files under the data root (R2).

The per-role-uid-split reader (``workspace_fs._open_regular_beneath``) refuses
every regular file whose ``st_nlink != 1``, unconditionally. This counts what
that would refuse today, by reader class. Metadata only: no payload is opened,
no link is followed, no directory is entered twice, the walk stays on the root's
device, and nothing is written. Directories open with O_NOATIME when allowed.

Paths are redacted by default (owner tree -> ``<owner#n>``, names kept only
where they are platform-fixed); ``--paths`` prints them verbatim.

    python3 -I -B scripts/data_nlink_census.py --root /data [--paths]

Exit 0: no multi-link file. Exit 3: at least one. Exit 2: the walk failed.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from collections import Counter

LIVENESS_DIR = ".consumer_liveness"
WORKSPACE_DIR = ".agent-workspace"
# tinyassets/universe_tools.py AGENT_BRAIN_FILES plus the soul files.
BRAIN = frozenset((
    "identity.md", "founder.md", "origin.md", "body.md", "orgchart.md",
    "projects.md", "goals.md", "index.md", "log.md", "voice.md", "AGENTS.md",
    "MEMORY.md", "soul.md", "soul.edit.md",
))
FIXED_FILES = frozenset((
    "activity.log", "notes.json", "config.yaml", "universe.json",
    "provider_definitions.json", "dispatcher_config.yaml",
))
SQLITE_SUFFIXES = (".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", "-journal")

# What a refusal does there, from the U1 reader map (design note, R2).
IMPACT = {
    "liveness-proof": "owner_state -> UNKNOWN (never reclaimed)",
    "brain-file": "soul/identity/memory read refuses; soul_edit refuses the write",
    "agent-workspace": "owner /u view read_file -> not found",
    "wiki": "wiki read raises; the API action errors",
    "canon": "canon/ingestion read raises",
    "output": "output read returns an error row",
    "agents-settings": "harness settings raise SettingsError",
    "skills": "skill skipped",
    "soul-versions": "version skipped",
    "package-cells": "package cell launch fails",
    "fixed-file": "per-file: activity/notes/config readers raise or degrade",
    "sqlite": "not a workspace_fs reader (sqlite opens it)",
    "git-internals": "not a workspace_fs reader except workspace bundles",
    "other-universe-file": "owner /u view read_file -> not found",
    "platform": "platform helper read raises (wiki/session/json)",
}


def _open_dir(parent, name):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        return os.open(name, flags | getattr(os, "O_NOATIME", 0), dir_fd=parent)
    except PermissionError:
        # O_NOATIME needs ownership or CAP_FOWNER; plain open still writes nothing.
        return os.open(name, flags, dir_fd=parent)


def _root_fd(path):
    if not os.path.isabs(path) or ".." in path.split("/"):
        raise ValueError("root must be absolute without parent traversal")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in [p for p in path.split("/") if p]:
            child = _open_dir(fd, part)
            os.close(fd)
            fd = child
    except BaseException:
        os.close(fd)
        raise
    return fd


def _owner_trees(fd):
    trees = set()
    for entry in os.scandir(fd):
        if not entry.is_dir(follow_symlinks=False):
            continue
        if entry.name.startswith("u-"):
            trees.add(entry.name)
            continue
        sub = _open_dir(fd, entry.name)
        try:
            os.stat("universe.json", dir_fd=sub, follow_symlinks=False)
            trees.add(entry.name)
        except FileNotFoundError:
            pass
        finally:
            os.close(sub)
    return trees


def classify(parts, trees):
    """Reader class of one relative name (a tuple of components)."""
    name = parts[-1]
    if LIVENESS_DIR in parts[:-1]:
        return "liveness-proof"
    if name.endswith(SQLITE_SUFFIXES):
        return "sqlite"
    if any(p == ".git" or p.endswith(".git") for p in parts[:-1]):
        return "git-internals"
    if parts[0] not in trees:
        return "platform"
    rel = parts[1:]
    if len(rel) == 1 and name in BRAIN:
        return "brain-file"
    if len(rel) == 1 and name in FIXED_FILES:
        return "fixed-file"
    head = rel[0]
    if head == WORKSPACE_DIR:
        return "agent-workspace"
    if head in ("wiki", "canon", "output", "skills"):
        return head
    if head == "soul_versions":
        return "soul-versions"
    if head == "agents" and name == "settings.yaml":
        return "agents-settings"
    if head == ".runtime" and len(rel) > 1 and rel[1] == "package-cells":
        return "package-cells"
    return "other-universe-file"


def _redact(parts, trees, owner_ids):
    if parts[0] not in trees:
        return "/".join(parts[:1] + tuple("*" for _ in parts[1:-1]) + parts[-1:])
    owner = "<owner#%d>" % owner_ids.setdefault(parts[0], len(owner_ids) + 1)
    fixed = parts[-1] in BRAIN or parts[-1] in FIXED_FILES
    tail = parts[-1] if fixed else "*" + os.path.splitext(parts[-1])[1]
    middle = tuple(p if p.startswith(".") or p in ("wiki", "canon", "output", "agents",
                                                   "skills", "soul_versions") else "*"
                   for p in parts[1:-1])
    return "/".join((owner,) + middle + (tail,))


def census(root, show_paths=False):
    fd = _root_fd(root)
    device = os.fstat(fd).st_dev
    trees = _owner_trees(fd)
    counts = Counter()
    inodes = {}
    errors = []

    def walk(dir_fd, prefix):
        try:
            entries = list(os.scandir(dir_fd))
        except OSError as exc:
            errors.append({"path": "/".join(prefix), "errno": exc.errno})
            return
        for entry in entries:
            parts = prefix + (entry.name,)
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                errors.append({"path": "/".join(parts), "errno": exc.errno})
                continue
            counts["entries"] += 1
            if stat.S_ISDIR(info.st_mode):
                if info.st_dev != device:
                    counts["mounts_skipped"] += 1
                    continue
                try:
                    sub = _open_dir(dir_fd, entry.name)
                except OSError as exc:
                    errors.append({"path": "/".join(parts), "errno": exc.errno})
                    continue
                try:
                    walk(sub, parts)
                finally:
                    os.close(sub)
            elif stat.S_ISREG(info.st_mode):
                counts["regular_names"] += 1
                if info.st_nlink != 1:
                    counts["multi_link_names"] += 1
                    row = inodes.setdefault((info.st_dev, info.st_ino),
                                            {"nlink": info.st_nlink, "names": []})
                    row["names"].append(parts)
            else:
                counts["special_entries"] += 1

    try:
        walk(fd, ())
    finally:
        os.close(fd)

    owner_ids = {}
    by_class = Counter()
    rows = []
    for (_dev, ino), row in sorted(inodes.items(), key=lambda kv: kv[1]["names"]):
        classes = sorted({classify(p, trees) for p in row["names"]})
        owners = sorted({p[0] for p in row["names"] if p[0] in trees})
        for name in row["names"]:
            by_class[classify(name, trees)] += 1
        rows.append({
            "nlink": row["nlink"],
            "names_seen": len(row["names"]),
            "unseen_names": row["nlink"] - len(row["names"]),
            "owner_trees": len(owners),
            "classes": classes,
            "names": ["/".join(p) if show_paths else _redact(p, trees, owner_ids)
                      for p in row["names"]],
        })
    counts["multi_link_inodes"] = len(rows)
    counts["owner_trees"] = len(trees)
    return {
        "schema": 1,
        "read_only": True,
        "root": root,
        "counts": dict(sorted(counts.items())),
        "refused_names_by_class": {
            k: {"names": v, "impact": IMPACT[k]} for k, v in sorted(by_class.items())
        },
        "inodes": rows,
        "errors": errors,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default="/data")
    parser.add_argument("--paths", action="store_true",
                        help="print names verbatim instead of redacted")
    args = parser.parse_args(argv)
    try:
        report = census(args.root, show_paths=args.paths)
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=1, sort_keys=True))
    if report["errors"]:
        return 2
    return 3 if report["inodes"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
