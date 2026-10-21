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
    failures = []
    for node in ast.walk(tree):
        targets = (node.targets if isinstance(node, ast.Assign) else
                   [node.target] if isinstance(node, (ast.AugAssign, ast.AnnAssign)) else [])
        for target in targets:
            text = ast.unparse(target)
            if text.split('.')[0] in application:
                failures.append(text)
        if isinstance(node, ast.Call):
            function = ast.unparse(node.func)
            if any(part in function.lower() for part in ('mock', 'patch', 'setattr')):
                failures.append(function)
    return failures


def test_production_copy_never_substitutes_broker_box_host_or_relay():
    """The persistent entry, owner daemon and lifecycle retain real authority."""
    root = Path(__file__).resolve().parents[1]
    for name in ("deploy/box_daemon_entry.py", "tinyassets/owner_daemon.py",
                 "tinyassets/boxes/host_service.py", "scripts/core_capability_live.py"):
        assert not substitutions((root / name).read_text(encoding="utf-8")), name


@pytest.mark.parametrize('module,seam', [
    ('storage.outbound_connections', '_broker_channel'),
    ('boxes.boxd', 'OwnerBoxd'),
    ('boxes.daemon_control', 'ensure_awake'),
    ('broker.public_egress', 'PublicEgress'),
])
def test_guard_catches_execution_substitutions(module, seam):
    source = f'import tinyassets.{module} as boundary\nboundary.{seam} = replacement\n'
    assert substitutions(source) == ['boundary.' + seam]


pytestmark = pytest.mark.verifies(
    'core-capability-assurance:'
    'Production image acceptance before merge',
)
