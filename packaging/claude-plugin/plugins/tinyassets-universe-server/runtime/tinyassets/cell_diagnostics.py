"""Bounded, data-free diagnostics for the immutable cell bootstrap."""
from __future__ import annotations

import os
import re

MARKER = 'TA_CELL_FAILURE '
FILES = ('role_decoder.py', 'role_owner_launcher.py', 'role_provider_cell.py',
         'role_tools.py', 'role_tool_files.py', 'universe_tools.py', 'provider_jail.py',
         'jail_seccomp.py', 'role_git.py', 'role_provision.py', 'library')
ALIASES = {'ta-decoder.py': 'role_decoder.py', 'ta-owner-launch.py': 'role_owner_launcher.py',
           'ta-git.py': 'role_git.py', 'ta-provision.py': 'role_provision.py'}
ERRORS = ('ValueError', 'TypeError', 'KeyError', 'RuntimeError', 'OSError',
          'PermissionError', 'FileNotFoundError', 'ProcessLookupError',
          'ConnectionResetError', 'BrokenPipeError', 'TimeoutError', 'Exception')
PATTERN = re.compile(
    rb'TA_CELL_FAILURE ((?:decoder|launcher|mapper):(?:'
    + b'|'.join(re.escape(name.encode()) for name in FILES)
    + rb'):[0-9]{1,6}:(?:' + b'|'.join(name.encode() for name in ERRORS)
    + rb'):errno=(?:None|[0-9]{1,4})|relay:(?:bootstrap|pump|half-close|connect)'
      rb':errno=(?:None|[0-9]{1,4}))\n')


def failure_reason(exc, operation):
    trace = exc.__traceback__
    while trace is not None and trace.tb_next is not None:
        trace = trace.tb_next
    filename = os.path.basename(trace.tb_frame.f_code.co_filename) if trace else 'library'
    filename = ALIASES.get(filename, filename)
    filename = filename if filename in FILES else 'library'
    location = f'{filename}:{min(trace.tb_lineno, 999999) if trace else 0}'
    # Never include exception text, source lines, arguments or locals.
    number = exc.errno if isinstance(exc, OSError) else None
    number = number if type(number) is int and 0 <= number <= 4095 else None
    kind = type(exc).__name__ if type(exc).__name__ in ERRORS else 'Exception'
    return f'{operation}:{location}:{kind}:errno={number}'


def report_failure(exc, operation):
    os.write(2, (MARKER + failure_reason(exc, operation) + '\n').encode('ascii'))


def exception_hook(kind, exc, trace):
    report_failure(exc.with_traceback(trace), 'decoder')
