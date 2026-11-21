"""Whether Python files and TOML files hold together, read from stdin and never run.

The cutover guard (`ci/guards/cutover.test.ts`) hands it the tree each phase leaves, as JSON:
`{"<phase>": {"<path>": {"text": "<text>", "modules": {"<module>": "<path>"}}}}`, each file
with the file of each module it imports, as the guard found it. It prints each problem, as JSON:
- a `.py` file that does not compile;
- a name a `.py` file reads that nothing binds: not the module, not a builtin, and not an
  enclosing function (Python raises `NameError` when that line runs);
- a name a `.py` file imports (`from x import name`), or reads from a module it imports by
  an alias (`import x as m`, `m.name`), from a module of the same tree that does not define
  it;
- a `.toml` file that does not parse.
Only the standard library: `symtable` resolves the scopes as the compiler does.
"""

from __future__ import annotations

import ast
import builtins
import json
import symtable
import sys
import tomllib

#: What every module binds without a statement.
MODULE = frozenset(dir(builtins)) | {"__file__", "__name__", "__doc__", "__spec__", "__path__",
                                     "__loader__", "__package__", "__builtins__",
                                     "__annotations__", "__cached__"}


def scopes(table: symtable.SymbolTable):
    yield table
    for child in table.get_children():
        yield from scopes(child)


def bound(table: symtable.SymbolTable) -> set[str]:
    """The names a module binds: its own, and each a function declares global and assigns."""
    names = {each.get_name() for each in table.get_symbols()
             if each.is_assigned() or each.is_imported()}
    for scope in scopes(table):
        names |= {each.get_name() for each in scope.get_symbols()
                  if each.is_declared_global() and each.is_assigned()}
    return names


def star(tree: ast.Module) -> bool:
    return any(isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names)
               for node in ast.walk(tree))


def find(path: str, module: str, files: dict[str, dict]) -> str | None:
    """The file `module` names from `path`, as the guard found it (`pythonModules`)."""
    return files[path]["modules"].get(module)


def problems(files: dict[str, dict]) -> list[str]:
    found: list[str] = []
    modules: dict[str, tuple[ast.Module, set[str]]] = {}
    for path, file in files.items():
        text = file["text"]
        if path.endswith(".toml"):
            try:
                tomllib.loads(text)
            except tomllib.TOMLDecodeError as error:
                found.append(f"{path}: is no TOML: {error}")
            continue
        try:
            tree = ast.parse(text, path)
            table = symtable.symtable(text, path, "exec")
        except SyntaxError as error:
            found.append(f"{path}:{error.lineno}: does not compile: {error.msg}")
            continue
        modules[path] = (tree, bound(table))
        if star(tree):
            continue
        known = modules[path][1] | MODULE
        read = {each.get_name() for scope in scopes(table) for each in scope.get_symbols()
                if each.is_global() and each.is_referenced()} - known
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in read:
                found.append(f"{path}:{node.lineno}: reads {node.id}, which nothing binds")
                read.discard(node.id)
    for path, (tree, _) in modules.items():
        aliases = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    target = find(path, alias.name, files)
                    if alias.asname and target in modules and not star(modules[target][0]):
                        aliases[alias.asname] = target
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.value.id in aliases):
                defined = modules[aliases[node.value.id]][1]
                if node.attr not in defined and "__getattr__" not in defined:
                    found.append(f"{path}:{node.lineno}: reads {node.value.id}.{node.attr}, "
                                 f"which {aliases[node.value.id]} does not define")
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level != 0 or node.module is None:
                continue
            target = find(path, node.module, files)
            if target is None or target not in modules or star(modules[target][0]):
                continue
            defined = modules[target][1]
            for alias in node.names:
                package = target.endswith("/__init__.py") and find(
                    path, f"{node.module}.{alias.name}", files) is not None
                if alias.name not in defined and "__getattr__" not in defined and not package:
                    found.append(f"{path}:{node.lineno}: imports {alias.name} from {target}, "
                                 "which does not define it")
    return found


if __name__ == "__main__":
    trees = json.load(sys.stdin)
    json.dump({phase: problems(files) for phase, files in trees.items()}, sys.stdout)
