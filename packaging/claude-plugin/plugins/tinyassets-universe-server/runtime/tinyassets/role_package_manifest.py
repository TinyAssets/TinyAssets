"""Provider-neutral, exact-revision executable package manifest."""
from __future__ import annotations

import hashlib
import json
import re

MANIFEST_BYTES = 1024 * 1024
PACKAGE_BYTES = 64 * 1024 * 1024
MAX_FILES = 4096
RUNTIMES = {'python': '/opt/venv/bin/python', 'node': '/usr/bin/node', 'shell': '/bin/sh'}


def component_path(value):
    if (type(value) is not str or len(value) > 512 or '\\' in value or '\0' in value
            or any(part in ('', '.', '..') for part in value.split('/'))
            or len(value.split('/')) > 32):
        raise ValueError('invalid package relative path')
    return value


def parse(raw, revision):
    if (type(raw) is not bytes or len(raw) > MANIFEST_BYTES
            or type(revision) is not str or not re.fullmatch('[a-f0-9]{64}', revision)
            or hashlib.sha256(raw).hexdigest() != revision):
        raise ValueError('package revision mismatch')
    doc = json.loads(raw)
    if (type(doc) is not dict or set(doc) != {'runtime', 'entry', 'args', 'files', 'slots'}
            or doc['runtime'] not in RUNTIMES or type(doc['files']) is not dict
            or not 1 <= len(doc['files']) <= MAX_FILES
            or type(doc['args']) is not list or len(doc['args']) > 32
            or any(type(arg) is not str or '\0' in arg or len(arg) > 4096 for arg in doc['args'])
            or type(doc['slots']) is not list or len(doc['slots']) > 32
            or any(type(slot) is not str or not re.fullmatch('[a-z][a-z0-9_-]{0,63}', slot)
                   for slot in doc['slots']) or len(set(doc['slots'])) != len(doc['slots'])):
        raise ValueError('invalid package manifest')
    for path, digest in doc['files'].items():
        component_path(path)
        if path == 'manifest.json' or type(digest) is not str or not re.fullmatch(
                '[a-f0-9]{64}', digest):
            raise ValueError('invalid package content digest')
    if component_path(doc['entry']) not in doc['files']:
        raise ValueError('package entry is not pinned')
    return doc
