# Picker source warnings contract fails on PR #4431

The unchanged test
`tests/test_native_model_options_api.py::test_native_public_picker_lists_default_and_discovered_model[discovered]`
raises `KeyError: 'warnings'` at line 79. The returned source omits that field.

Reproduced on the pulled PR commit `dc42d8748e8c009c5cb200bd0d89c84b94a978cf`
in an external archive checkout, as well as after the P1 history-retention fix.
This predates that fix. The main-branch test was left unchanged.

Resolve the source document contract and run the unchanged test before closing.
