"""Browser proofs must collect in the merge-group job without Playwright."""

import ast
from pathlib import Path

import pytest


def _collection_imports(source):
    tree = ast.parse(source)
    if not any(isinstance(node, ast.Attribute) and node.attr == "real_browser"
               for node in ast.walk(tree)):
        return []

    imports = []

    class CollectionImports(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            pass  # Fixtures and test bodies execute after collection.

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Import(self, node):
            if any(alias.name.split(".")[0] == "playwright" for alias in node.names):
                imports.append(node.lineno)

        def visit_ImportFrom(self, node):
            if (node.module or "").split(".")[0] == "playwright":
                imports.append(node.lineno)

    CollectionImports().visit(tree)
    return imports


def test_real_browser_modules_do_not_import_playwright_during_collection():
    root = Path(__file__).parent
    violations = {
        str(path.relative_to(root)): lines
        for path in sorted(root.rglob("test_*.py"))
        if (lines := _collection_imports(path.read_text(encoding="utf-8")))
    }
    assert not violations, f"Move Playwright imports into fixtures/tests: {violations}"


@pytest.mark.parametrize("statement", [
    "import playwright",
    "import playwright.sync_api as browser",
    "from playwright.async_api import async_playwright",
    "if True:\n    from playwright.sync_api import sync_playwright",
    "try:\n    import playwright\nexcept ImportError:\n    pass",
    "class Browser:\n    import playwright",
])
def test_guard_catches_collection_imports(statement):
    assert _collection_imports("pytestmark = pytest.mark.real_browser\n" + statement)


@pytest.mark.parametrize("definition", ["def", "async def"])
def test_guard_allows_deferred_browser_imports(definition):
    source = ("pytestmark = pytest.mark.real_browser\n"
              f"{definition} browser():\n    from playwright.sync_api import sync_playwright\n")
    assert _collection_imports(source) == []
