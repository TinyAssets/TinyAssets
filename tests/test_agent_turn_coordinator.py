"""The shared coordinator stays free of served-request authority."""


def test_shared_progress_has_no_served_request_authority_import():
    import ast
    import inspect

    from tinyassets import agent_turn_coordinator

    tree = ast.parse(inspect.getsource(agent_turn_coordinator))
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert "tinyassets.provider_assignment" not in imports
    assert "tinyassets.foreground_run_provider" not in imports
    assert "tinyassets.background_served_provider" not in imports
