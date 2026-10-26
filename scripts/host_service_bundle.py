"""Derive installed Python sources from unit commands and their local import closure.

Includes imports in function bodies and optional branches: a quiet timer tick must
not determine what a later recovery tick is allowed to import. Never imports the
application while building a manifest.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
from collections.abc import Callable
from pathlib import Path

BOXHOSTD_HOST_PROBES = ("scripts/wait_boxhostd_ready.py",)


def command_files(root: Path, text: str, *, strict: bool = False,
                  exists: Callable[[Path], bool] = Path.is_file) -> set[Path]:
    """Resolve repo scripts named by units, shell commands, or Python launchers."""
    files = set()
    for name in re.findall(r"[A-Za-z_][A-Za-z0-9_.-]*\.(?:py|sh)\b", text):
        hits = [root / base / name for base in ("deploy", "scripts")
                if exists(root / base / name)]
        if len(hits) > 1:
            raise ValueError(f"ambiguous service script: {name}")
        if strict and not hits:
            raise ValueError(f"missing service script: {name}")
        files.update(hits)
    for module in re.findall(r"(?:-m\s+)([A-Za-z_][\w.]*)", text):
        path = root / (module.replace(".", "/") + ".py")
        if not exists(path):
            path = root / module.replace(".", "/") / "__main__.py"
        if not exists(path):
            raise ValueError(f"missing service module: {module}")
        files.add(path)
    return files


def service_bundle(root: Path, units: list[Path], *,
                   entrypoints: tuple[str, ...] = (),
                   source_files: set[str] | None = None,
                   read: Callable[[str], str] | None = None,
                   stop: Callable[[str], bool] = lambda _: False) -> list[str]:
    """One conservative local-source closure rooted in actual unit commands.

    Follow deferred imports too: installed modules may expose other operations,
    but those extra definitions must never shrink a command's source bundle.
    The read/files seam lets the deployment classifier inspect a git revision.
    """
    root = root.resolve()

    def relative(path):
        return path.relative_to(root).as_posix()

    def exists(path):
        return path.is_file() if source_files is None else relative(path) in source_files

    def contents(path):
        return path.read_text(encoding="utf-8") if read is None else read(relative(path))

    pending = set()
    for entrypoint in entrypoints:
        path = root / entrypoint
        if not exists(path):
            raise ValueError(f"missing host entrypoint: {entrypoint}")
        pending.add(path)
    for unit in units:
        commands = "\n".join(line for line in contents(unit.resolve()).splitlines()
                             if re.match(r"Exec(?:Start|Stop)(?:Pre|Post)?=", line))
        pending.update(command_files(root, commands, strict=True, exists=exists))
    found = set()

    def module_files(module: str, local: Path, *, relative_import=False) -> None:
        if not module:
            return
        bases = (local,) if relative_import else (root, local, root / "scripts")
        for base in bases:
            path = base.joinpath(*module.split("."))
            for candidate in (path.with_suffix(".py"), path / "__init__.py"):
                if exists(candidate):
                    pending.add(candidate)
                    for parent in candidate.parents:
                        if parent == root or not parent.is_relative_to(root):
                            break
                        if exists(parent / "__init__.py"):
                            pending.add(parent / "__init__.py")
                    return
        if module.startswith(("tinyassets.", "scripts.", "domains.")):
            raise ValueError(f"missing local service import: {module}")

    while pending:
        path = pending.pop()
        if path in found:
            continue
        found.add(path)
        for parent in path.parents:
            if parent == root or not parent.is_relative_to(root):
                break
            if exists(parent / "__init__.py"):
                pending.add(parent / "__init__.py")
        if stop(relative(path)):
            continue
        text = contents(path)
        if path.suffix == ".sh":
            code = "\n".join(line for line in text.splitlines()
                             if not line.lstrip().startswith("#"))
            pending.update(command_files(root, code, exists=exists))
            trees = []
            for strip_tabs, _, program in re.findall(
                    r"<<(-?)['\"]?(\w+)['\"]?[^\n]*\n(.*?)^[\t]*\2$", code, re.M | re.S):
                if strip_tabs:
                    program = re.sub(r"^\t+", "", program, flags=re.M)
                try:
                    trees.append(ast.parse(program))
                except SyntaxError:
                    continue  # shell/JSON heredocs are not Python programs
        else:
            trees = [ast.parse(text, filename=str(path))]
            for node in ast.walk(trees[0]):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and node.value.endswith((".sh", ".py")) and "/" in node.value
                        and "\n" not in node.value and " " not in node.value):
                    pending.update(command_files(root, node.value, exists=exists))
        for tree in trees:
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        module_files(alias.name, path.parent)
                elif isinstance(node, ast.ImportFrom):
                    local = path.parents[node.level - 1] if node.level else path.parent
                    module = node.module or ""
                    module_files(module, local, relative_import=bool(node.level))
                    for alias in node.names:
                        # From-imports can name attributes or submodules.
                        name = ".".join(filter(None, (module, alias.name)))
                        for base in (local, root):
                            candidate = base.joinpath(*name.split("."))
                            if exists(candidate.with_suffix(".py")) or exists(
                                    candidate / "__init__.py"):
                                module_files(name, local, relative_import=bool(node.level))
                                break
    return sorted(relative(path) for path in found)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--install-to", type=Path)
    parser.add_argument("--flat-scripts", action="store_true")
    parser.add_argument("--list-install-paths", action="store_true")
    parser.add_argument("--boxhostd-probes", action="store_true")
    parser.add_argument("units", nargs="+")
    args = parser.parse_args()
    manifest = service_bundle(
        args.root, [args.root / "deploy" / u for u in args.units],
        entrypoints=BOXHOSTD_HOST_PROBES if args.boxhostd_probes else (),
    )
    installed = set(manifest)
    if args.flat_scripts:
        installed.update(Path(p).name for p in manifest if len(Path(p).parts) == 2
                         and Path(p).parts[0] in {"deploy", "scripts"}
                         and Path(p).name != "__init__.py")
    if args.list_install_paths:
        for relative in sorted(installed | {".host-service-files.json"}):
            print(relative)
        return
    if args.install_to:
        args.install_to = args.install_to.resolve()
        record = args.install_to / ".host-service-files.json"
        previous = json.loads(record.read_text()) if record.exists() else []
        for relative in sorted(set(previous) - installed):
            target = args.install_to / relative
            if not target.parent.resolve().is_relative_to(args.install_to):
                raise ValueError("installed manifest escapes bundle")
            target.unlink(missing_ok=True)
            print(relative)
    for path in manifest:
        if args.install_to:
            relative = Path(path)
            destination = args.install_to / relative
            source = args.root / path
            changed = False
            if not destination.is_file() or destination.read_bytes() != source.read_bytes():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                destination.chmod(0o755)
                changed = True
            # Units and standalone subprocesses use historical flat entrypoints;
            # packages retain their importable directory structure.
            if (args.flat_scripts and len(relative.parts) == 2
                    and relative.parts[0] in {"deploy", "scripts"}
                    and relative.name != "__init__.py"):
                alias = args.install_to / relative.name
                if not alias.is_symlink() or alias.readlink() != relative:
                    alias.unlink(missing_ok=True)
                    alias.symlink_to(relative)
                    changed = True
            if not changed:
                continue
        print(path)
    if args.install_to:
        record.write_text(json.dumps(sorted(installed)) + "\n")


if __name__ == "__main__":
    main()
