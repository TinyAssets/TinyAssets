"""Production TOOL first-use, promotion and restrictive-mode accounting recovery."""
import role_tool_launcher_probe as probe

EXTRA = r'''
        from tinyassets.universe_files import read_universe_file
        from tinyassets.storage_accounting import _walk_bytes
        script = """
import os
from pathlib import Path
Path('voice.md').write_bytes(b'published verbatim\\r\\n')
Path('notes/private').mkdir(exist_ok=True)
Path('notes/private/value').write_bytes(b'accounted owner bytes')
os.chmod('notes/private/value',0)
os.chmod('notes/private',0)
"""
        result=tools.run_jailed(center,['/usr/local/bin/python','-I','-c',script],agent_id='main')
        assert result.exit_code==0,result
        assert read_universe_file(center,'voice.md')==b'published verbatim\r\n'
        assert read_universe_file(center,'notes/private/value')==b'accounted owner bytes'
        assert _walk_bytes(center)>0
        content=tools.read_file(center,'notes/private/value',agent_id='main')
        assert 'accounted owner bytes' in content
        assert tools.run_jailed(center,['/bin/true'],agent_id='main').exit_code==0
        print(owner+': first-use preparation, atomic brain promotion, mode000 recovery '
              'and daemon accounting PASS; ZERO FOREIGN_BYTES',flush=True)
'''


if __name__ == '__main__':
    # Keep the original actual-tool/foreign-access assertions, but stop
    # pre-creating the seven harness directories that runtime must prepare.
    probe.SETUP = probe.SETUP.replace(
        "('.agent-workspace','skills','prompts','extensions',\n"
        "                 'workflows','bin','notes','wiki')", "('.agent-workspace',)")
    marker = "        other=root/('decoder-'+('bob' if owner=='alice' else 'alice'))"
    assert probe.TOOL.count(marker) == 1
    probe.TOOL = probe.TOOL.replace(marker, EXTRA + '\n' + marker)
    raise SystemExit(probe.main(timeout=600))
