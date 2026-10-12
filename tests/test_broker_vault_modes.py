"""Credential publication seals records. It does not publish a cleartext file."""

from tinyassets import credential_vault as vault


def test_persist_seals_and_leaves_no_cleartext_file(tmp_path):
    universe = tmp_path / "center"
    universe.mkdir()
    vault._persist_credential_vault_file(
        universe, [vault.http_credential_record(destination="fixture", token="first")]
    )
    vault._persist_credential_vault_file(
        universe, [vault.http_credential_record(destination="fixture", token="rotated")]
    )
    assert vault.load_credential_vault(universe)[0]["token"] == "rotated"
    assert not vault.credential_vault_path(universe).exists()
    assert not any(path.name == vault.VAULT_FILENAME for path in universe.rglob("*"))


def test_a_password_field_is_refused_before_storage(tmp_path):
    universe = tmp_path / "center"
    universe.mkdir()
    try:
        vault.write_credential_vault(
            universe,
            [
                {
                    "credential_type": "http",
                    "destination": "example",
                    "token": "ok",
                    "password": "hunter2",
                }
            ],
        )
    except ValueError as exc:
        assert "browser" in str(exc)
    else:
        raise AssertionError("password deposit was stored")
    assert vault.load_credential_vault(universe) == []
