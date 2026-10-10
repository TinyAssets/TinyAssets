"""Production-copy proof may substitute vendor transport, never execution authority."""
import ast
from pathlib import Path

import pytest


def substitutions(source):
    tree = ast.parse(source)
    application = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or '').startswith('tinyassets'):
            application.update(name.asname or name.name for name in node.names)
        elif isinstance(node, ast.Import):
            application.update(name.asname or name.name.split('.')[0] for name in node.names
                               if name.name.startswith('tinyassets'))
    permitted = {'provider_base.subprocess_env_for_provider',
                 'claude_provider.subprocess_env_for_provider',
                 'codex_provider.subprocess_env_for_provider'}
    failures = []
    for node in ast.walk(tree):
        targets = (node.targets if isinstance(node, ast.Assign) else
                   [node.target] if isinstance(node, (ast.AugAssign, ast.AnnAssign)) else [])
        for target in targets:
            text = ast.unparse(target)
            if text.split('.')[0] in application and text not in permitted:
                failures.append(text)
        if isinstance(node, ast.Call):
            function = ast.unparse(node.func)
            if any(part in function.lower() for part in ('mock', 'patch', 'setattr')):
                failures.append(function)
    return failures


def test_production_copy_never_substitutes_broker_cell_launcher_or_relay():
    root = Path(__file__).resolve().parents[1] / 'scripts'
    for name in ('role_image_oracle.py', 'role_chat_probe.py', 'role_run_probe.py',
                 'role_http_fixture.py'):
        assert not substitutions((root / name).read_text(encoding='utf-8')), name


@pytest.mark.parametrize('module,seam', [
    ('storage.outbound_connections', '_broker_channel'), ('role_tools', 'run'),
    ('role_node', 'run'), ('role_provider_discovery', 'cell_config'),
    ('universe_egress', '_checked_addresses'), ('role_decoder', '_bounded_client'),
])
def test_guard_catches_execution_substitutions(module, seam):
    source = f'import tinyassets.{module} as boundary\nboundary.{seam} = replacement\n'
    assert substitutions(source) == ['boundary.' + seam]
