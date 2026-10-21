"""Read-only cutover refusals on a synthetic host layout."""

from scripts.live_preflight import check_box_store, check_core_canary, main


def test_pending_move_names_leftover_images_and_locks(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    (store / "300001.img").write_bytes(b"synthetic")
    (store / ".locks").mkdir()
    check = tmp_path / "box-store-check.sh"
    check.write_text("exit 0\n")

    reasons = check_box_store(store, {}, check)

    assert len(reasons) == 1
    assert "check=box_store_not_empty" in reasons[0]
    assert "300001.img" in reasons[0]
    assert ".locks" in reasons[0]
    assert (store / "300001.img").exists()
    assert (store / ".locks").exists()


def test_data_path_and_canary_waiver(tmp_path, monkeypatch, capsys):
    data = tmp_path / "data"
    data.mkdir()
    store = tmp_path / "store"
    store.mkdir()
    check = tmp_path / "box-store-check.sh"
    check.write_text("exit 0\n")
    monkeypatch.setenv("CORE_CANARY_PROVISIONED", "")
    args = ["--data", str(data), "--box-store", str(store),
            "--box-store-check", str(check), "--harness-root", str(tmp_path),
            "--env-file", str(tmp_path / "env")]

    assert main(args) == 1
    assert "core_canary_not_provisioned" in capsys.readouterr().out
    assert main([*args, "--waiver"]) == 0
    assert "live_preflight=ok" in capsys.readouterr().out


def test_canary_provisioning_only_matters_for_pending_layout():
    assert check_core_canary("", {})
    assert check_core_canary("", {"box_layout": "owner-box", "state": "stable"}) == []
