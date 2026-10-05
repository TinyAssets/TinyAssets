"""Owned advisory metadata shared by replaceable engine processes.

Only custody digests and model facts are persisted. Readers reconstruct a snapshot
against current custody; this store is never a source of invocation authority.
"""

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from tinyassets.credential_vault import current_llm_subscription_custody
from tinyassets.provider_assignment import provider_assignment_admission
from tinyassets.providers.native_catalogue import NativeCatalogue, NativeModel
from tinyassets.providers.native_discovery import NativeDiscoverySnapshot
from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore


def _table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS native_shortlist_cache (
        owner_user_id TEXT NOT NULL, universe_id TEXT NOT NULL, provider TEXT NOT NULL,
        custody_digest TEXT NOT NULL, document TEXT NOT NULL,
        PRIMARY KEY (owner_user_id, universe_id, provider))""")


def _custody(conn, universe, owner, service):
    return current_llm_subscription_custody(
        conn, universe_dir=universe, owner_user_id=owner,
        universe_id=universe.name, service=service,
    )


def save(snapshot):
    universe = snapshot.universe
    if not universe.is_dir():
        return
    document = {
        "service": snapshot.custody.service,
        "observed_at": snapshot.observed_at.isoformat(),
        "completed_at": snapshot.completed_at.isoformat(),
        "catalogue_observed_at": snapshot.catalogue.observed_at.isoformat(),
        "default_model_id": snapshot.catalogue.default_model_id,
        "models": [{**asdict(model), "input_modalities": sorted(model.input_modalities)}
                   for model in snapshot.catalogue.models],
    }
    with provider_assignment_admission().shared(universe):
        with SQLiteProviderWorkAuthorityStore(universe.parent).connection() as conn:
            _table(conn)
            conn.execute("BEGIN IMMEDIATE")
            if (_custody(conn, universe, snapshot.owner_id, snapshot.custody.service)
                    != snapshot.custody):
                return
            old = conn.execute(
                "SELECT custody_digest, document FROM native_shortlist_cache "
                "WHERE owner_user_id=? AND universe_id=? AND provider=?",
                (snapshot.owner_id, universe.name, snapshot.provider),
            ).fetchone()
            if (old is not None and old[0] == snapshot.custody.reference_digest
                    and datetime.fromisoformat(json.loads(old[1])["observed_at"])
                    > snapshot.observed_at):
                return
            conn.execute(
                "INSERT OR REPLACE INTO native_shortlist_cache VALUES (?, ?, ?, ?, ?)",
                (snapshot.owner_id, universe.name, snapshot.provider,
                 snapshot.custody.reference_digest, json.dumps(document)),
            )
            conn.commit()


def load(*, base, owner, universe_id, provider):
    universe = Path(base).resolve() / universe_id
    # A read never creates a command center or a platform DB for an absent home.
    if not universe.is_dir():
        return None
    with SQLiteProviderWorkAuthorityStore(universe.parent, busy_timeout_ms=50).connection() as conn:
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='native_shortlist_cache'",
        ).fetchone() is None:
            return None
        conn.execute("BEGIN")
        row = conn.execute(
            "SELECT custody_digest, document FROM native_shortlist_cache "
            "WHERE owner_user_id=? AND universe_id=? AND provider=?",
            (owner, universe_id, provider),
        ).fetchone()
        if row is None:
            return None
        data = json.loads(row[1])
        custody = _custody(conn, universe, owner, data["service"])
        if custody is None or custody.reference_digest != row[0]:
            return None
    catalogue = NativeCatalogue(
        tuple(NativeModel(**{**model, "input_modalities": frozenset(model["input_modalities"]),
                             "effort_levels": tuple(model["effort_levels"])})
              for model in data["models"]),
        data["default_model_id"], datetime.fromisoformat(data["catalogue_observed_at"]),
    )
    return NativeDiscoverySnapshot(
        provider, owner, universe, custody, datetime.fromisoformat(data["observed_at"]),
        datetime.fromisoformat(data["completed_at"]), catalogue,
    )
