_PRE_RENAME_FOREIGN_CODE = (
    "Node 'edit' carries source_code this run did not author (caller provenance: "
    "public-foreign). Code runs only in the universe that authored it: remix the "
    "branch into your universe with write_graph (fork_from) so the code is yours."
)


def test_a_stored_pre_rename_foreign_code_failure_still_classifies():
    from tinyassets.api.runs import _classify_run_outcome_error
    from tinyassets.runs import _classify_failure

    assert _classify_run_outcome_error(_PRE_RENAME_FOREIGN_CODE)[0] == "node_not_accepted"
    assert _classify_failure(
        {"status": "failed", "error": _PRE_RENAME_FOREIGN_CODE},
    ) == "node_not_accepted"


def test_a_stored_pre_rename_held_authority_error_keeps_its_class():
    from tinyassets.api.runs import _classify_run_outcome_error
    from tinyassets.providers.owner_binding import (
        AUTHORITY_HELD_DETAIL,
        LEGACY_AUTHORITY_HELD_DETAIL,
    )

    tail = "a timeout while checking the credential"
    current = _classify_run_outcome_error(AUTHORITY_HELD_DETAIL + tail)
    legacy = _classify_run_outcome_error(LEGACY_AUTHORITY_HELD_DETAIL + tail)
    assert current is not None and legacy is not None
    assert legacy[0] == current[0]


def test_the_old_identity_boilerplate_is_still_not_learned():
    from tinyassets.universe_intelligence import _is_generic_identity_boilerplate

    assert _is_generic_identity_boilerplate("I am a personified universe.")
    assert _is_generic_identity_boilerplate("I am a personified command center.")
