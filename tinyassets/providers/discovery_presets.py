"""Installed compatibility documents, separate from owner-authored metadata.

This file contains no source behavior or code callbacks. New user connections
use SourceContract; they cannot opt into bundled trust or legacy parsing.
"""

def bundled_discovery_documents():
    """Detached data on each read; no CWD dependency or network discovery."""
    from tinyassets.providers.source_record import records

    return {r.id: r.invoke["http"]["discovery"] for r in records().values()
            if "discovery" in r.invoke.get("http", {})}


def compatibility_document():
    documents = [value for value in bundled_discovery_documents().values()
                 if value.get("compatibility_default") is True]
    if len(documents) != 1:
        raise ValueError("exactly one legacy compatibility document required")
    return documents[0]
