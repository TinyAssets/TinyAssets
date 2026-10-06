"""Generation copies facts without deciding authority or quota policy."""

import ast

from scripts.generate_guard_inventories import browser_workflow, insert_entries


def test_generation_preserves_review_and_marks_new_entries_unclassified():
    source = 'CLASSIFICATION: dict = {\n    "old": ("call_scoped", "bounded"),\n}\n'
    generated = insert_entries(
        source, "CLASSIFICATION", ['"new": ("UNCLASSIFIED", "review required")'],
    )
    values = ast.literal_eval(ast.parse(generated).body[0].value)
    assert values["old"] == ("call_scoped", "bounded")
    assert values["new"] == ("UNCLASSIFIED", "review required")
    assert insert_entries(generated, "CLASSIFICATION", []) == generated


def test_browser_generation_replaces_only_copied_proof_paths():
    source = (
        'paths:\n  - product.js\n      # The proofs.\n      - old.py\n'
        '  workflow_dispatch:\njobs: {}\n'
    )
    generated = browser_workflow(source, {"tests/new.py"})
    assert "product.js" in generated
    assert "old.py" not in generated
    assert '"tests/new.py"' in generated
    assert '"tests/test_ui_preview.py"' in generated
    assert generated.endswith("  workflow_dispatch:\njobs: {}\n")
    assert browser_workflow(generated, {"tests/new.py"}) == generated
