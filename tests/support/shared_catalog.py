"""In-process published-store transport for catalog and package unit fixtures.

The process tests exercise the actual relay and separate published tree. These
fixtures inject just that transport and run the broker's real catalog readers.
"""
from tinyassets import commons_catalog as catalog


def install(monkeypatch, published):
    def remote(operation, **args):
        if operation == "catalog":
            return catalog._list_definitions(published, **args)
        if operation == "definition":
            return catalog._get_definition(published, **args)
        if operation == "bundle_version":
            with catalog._definition_connect(published) as db:
                if db is None:
                    return 1
                return db.execute(
                    "SELECT COALESCE(MAX(version), 0) + 1 FROM commons_bundle_versions "
                    "WHERE bundle_id=?", (args["bundle_id"],)).fetchone()[0]
        raise AssertionError("unexpected shared catalog query: " + operation)
    monkeypatch.setattr("tinyassets.broker.owner_reads.remote", remote)
