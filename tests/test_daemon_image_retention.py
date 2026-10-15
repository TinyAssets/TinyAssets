"""No real registry, Docker host, image removal, or production access.

Historical test identities are retained; assertions track the repaired keep set,
removal budget and locked dangling cleanup rather than the incident-era policy.
"""

import hashlib
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import daemon_image_retention as retention


def identity(number):
    return "sha256:" + f"{number:064x}"


def reference(number):
    return retention.REPOSITORY + "@" + identity(number)


@pytest.fixture
def inventory():
    images = [
        dict(
            Id=identity(n),
            Created=f"2026-09-{n:02}T00:00:00Z",
            RepoDigests=[reference(n)],
            RepoTags=[],
        )
        for n in range(1, 11)
    ]
    daemon = dict(Name="/tinyassets-daemon", Image=identity(9), State={"Running": True})
    return images, [daemon], daemon, {"rollback_target": ""}


def select(inventory, configured=reference(9)):
    images, containers, daemon, receipt = inventory
    return retention.candidates(images, containers, daemon, configured, receipt)


@pytest.mark.parametrize("digest_aliases", [False, True])
def test_protects_current_two_older_newer_and_container_references(inventory, digest_aliases):
    if digest_aliases:
        for image in inventory[0]:
            image["RepoTags"] = image["RepoDigests"].copy()
    inventory[1].append(dict(Image=identity(1), State={"Running": False}))
    inventory[3]["rollback_target"] = reference(2)
    rows, protected = select(inventory, reference(3))
    assert [r["ref"] for r in rows] == [reference(n) for n in (4, 5, 6, 7, 10)]
    assert set(protected) == {identity(n) for n in (1, 2, 3, 8, 9)}


def test_containerd_captured_digest_alias_is_not_a_mutable_tag(inventory):
    # Sanitized root-observed Docker inspect shape, September 19 2026.
    digest = "sha256:494502b5ffffc3ba3eb50d86f1218b4195237261e520cce5220d1f1d76402f75"
    ref = retention.REPOSITORY + "@" + digest
    image = inventory[0][0]
    image.update(Id=digest, RepoDigests=[ref], RepoTags=[ref])
    assert {"image_id": digest, "ref": ref} in select(inventory)[0]


@pytest.mark.parametrize("aliases", [
    [retention.REPOSITORY + ":old"],
    ["other.example/image@" + identity(1)],
    [reference(99)],
    [reference(1), retention.REPOSITORY + ":old"],
    [reference(1), "other.example/image@" + identity(1)],
    [reference(1), reference(99)],
    [reference(1), reference(1)],
])
def test_digest_alias_exception_preserves_every_extra_or_mismatched_alias(inventory, aliases):
    inventory[0][0]["RepoTags"] = aliases
    assert reference(1) not in [row["ref"] for row in select(inventory)[0]]


@pytest.mark.parametrize("mutation", ["tag", "foreign", "multi"])
def test_aliases_exclude_candidate(inventory, mutation):
    row = inventory[0][0]
    if mutation == "tag":
        row["RepoTags"] = [retention.REPOSITORY + ":old"]
    elif mutation == "foreign":
        row["RepoDigests"] = ["other.example/image@" + identity(1)]
    else:
        row["RepoDigests"].append(reference(11))
    assert reference(1) not in [r["ref"] for r in select(inventory)[0]]


@pytest.mark.parametrize("mutation", ["timestamp", "current", "container", "rollback"])
def test_incomplete_protection_refuses(inventory, mutation):
    if mutation == "timestamp":
        inventory[0][0]["Created"] = "invalid"
    elif mutation == "current":
        inventory[2]["Image"] = identity(99)
    elif mutation == "container":
        inventory[1].append(dict(Image=identity(99)))
    else:
        inventory[3]["rollback_target"] = reference(99)
    with pytest.raises(retention.Refusal):
        select(inventory)


@pytest.mark.parametrize("configured", ["", "not-in-inventory", None])
def test_missing_configured_reference_refuses(inventory, configured):
    with pytest.raises(retention.Refusal):
        select(inventory, configured)


@pytest.mark.parametrize("total,free,expected", [(100, 19, 81), (100, 0, 100), (100, 100, 0)])
def test_pressure_uses_available(total, free, expected):
    assert retention.pressure(
        "unused", lambda p: SimpleNamespace(total=total, free=free)
    ) == pytest.approx(expected)


@pytest.mark.parametrize(
    "total,free", [(0, 0), (100, -1), (100, 101), (float("inf"), 1), (100, float("nan"))]
)
def test_bad_pressure_refuses(total, free):
    with pytest.raises(retention.Refusal):
        retention.pressure("unused", lambda p: SimpleNamespace(total=total, free=free))


def test_store_mapping_requires_evidence(tmp_path):
    info = dict(Driver="overlay2", DockerRootDir=str(tmp_path))
    assert retention.storage_path(info) == str(tmp_path)
    info.update(Driver="overlayfs", DriverStatus=[["driver-type", "io.containerd.snapshotter.v1"]])
    with pytest.raises(retention.Refusal, match="filesystem"):
        retention.storage_path(info)
    assert retention.storage_path(info, str(tmp_path)) == str(tmp_path)
    with pytest.raises(retention.Refusal, match="unknown_image_store"):
        retention.storage_path(dict(Driver="unknown"), str(tmp_path))


class FakeDocker:
    def __init__(self, inventory, root):
        self.data = inventory
        self.calls = []
        self.calls_all = []
        self.layer_prunes = []
        self.builder_prunes = []
        self.image_ids = {}
        self.deadline = float("inf")
        self.before_inventory = lambda: None
        self.root = root

    def json(self, *args):
        if args[:2] == ("image", "inspect"):
            identity = self.image_ids.get(args[2] if len(args) > 2 else "")
            if not identity:
                raise retention.Refusal("docker_refused_or_invalid_output")
            return [{"Id": identity}]
        if args[:2] == ("system", "df"):
            return ""
        return dict(
            Driver="overlay2", DockerRootDir=str(self.root), OSType="linux", Architecture="x86_64"
        )

    def inventory(self):
        self.before_inventory()
        return deepcopy(self.data[:2])

    def command(self, *args, timeout_cap=10):
        self.calls_all.append(args)
        if args[:2] == ("system", "df"):
            return '{"Type":"Images","TotalCount":"0","Size":"0B","Reclaimable":"0B"}\n'
        if args[:3] == ("image", "prune", "-f"):
            assert "--volumes" not in args and "-a" not in args
            self.layer_prunes.append(args)
            return ""
        if args[:3] == ("builder", "prune", "-af"):
            assert "--volumes" not in args and "volume" not in args
            self.builder_prunes.append(args)
            return ""
        assert args[:2] == ("image", "rm")
        assert len(args) == 3 and retention.DIGEST.fullmatch(args[2])
        self.calls.append(args)
        self.data[0][:] = [row for row in self.data[0] if args[2] != row["Id"]]
        return ""


def runner(inventory, tmp_path, **kwargs):
    docker = FakeDocker(inventory, tmp_path)
    state = dict(locked=False, verified=[])

    @contextmanager
    def locker():
        state["locked"] = True
        try:
            yield
        finally:
            state["locked"] = False

    def verify(choice, platform):
        assert not state["locked"]
        assert platform == ("linux", "amd64")
        state["verified"].append(choice)

    options = dict(
        dry_run=False,
        environ={"TINYASSETS_IMAGE": reference(9)},
        docker=docker,
        registry=SimpleNamespace(verify=verify),
        locker=locker,
        receipt_reader=lambda d, c: (inventory[2], inventory[3]),
        config_reader=lambda: reference(9),
        measure=lambda p: 90,
    )
    options.update(kwargs)
    return docker, state, options


def test_no_more_than_four_nonforce_immutable_removals(inventory, tmp_path):
    docker, state, options = runner(inventory, tmp_path)
    report = retention.retain(**options)
    assert len(docker.calls) == 8
    assert report["status"] == "pressure_unmet"
    assert len(state["verified"]) == 8


@pytest.mark.parametrize("dry_run", [False, True])
def test_digest_aliases_still_require_registry_proof_and_respect_dry_run(
    inventory, tmp_path, dry_run
):
    for image in inventory[0]:
        image["RepoTags"] = image["RepoDigests"].copy()
    docker, state, options = runner(inventory, tmp_path, dry_run=dry_run)
    report = retention.retain(**options)
    assert state["verified"]
    if dry_run:
        assert report["selected"] and not docker.calls
    else:
        assert len(docker.calls) == 8
        assert report["removed"] == [reference(n) for n in (1, 2, 3, 4, 5, 6, 7, 10)]


def test_new_mutable_alias_is_rechecked_under_lock(inventory, tmp_path):
    inventory[0][0]["RepoTags"] = [reference(1)]
    docker, state, options = runner(inventory, tmp_path)

    def race():
        if state["locked"]:
            inventory[0][0]["RepoTags"].append(retention.REPOSITORY + ":keep")

    docker.before_inventory = race
    report = retention.retain(**options)
    assert state["verified"][0]["ref"] == reference(1)
    assert reference(1) not in report["removed"]


def test_threshold_mode_low_watermark_stops_after_first_removal(inventory, tmp_path):
    readings = iter([90, 90, 74, 74, 74])
    docker, _, options = runner(
        inventory, tmp_path, mode="threshold", measure=lambda p: next(readings)
    )
    report = retention.retain(**options)
    assert len(docker.calls) == 1
    assert report["status"] == "pressure_relieved"


def test_dry_run_never_removes(inventory, tmp_path):
    docker, _, options = runner(inventory, tmp_path, dry_run=True)
    assert retention.retain(**options)["status"] == "dry_run"
    assert docker.calls == []


def test_threshold_mode_below_threshold_does_not_verify_or_lock(inventory, tmp_path):
    docker, state, options = runner(
        inventory, tmp_path, mode="threshold", measure=lambda p: 84.9
    )
    assert retention.retain(**options)["status"] == "below_threshold"
    assert not docker.calls and not state["verified"]


def test_threshold_mode_dry_run_below_trigger_still_proves_protected_refs(inventory, tmp_path):
    docker, state, options = runner(
        inventory, tmp_path, mode="threshold", dry_run=True, measure=lambda p: 79
    )
    report = retention.retain(**options)
    assert report["status"] == "below_threshold"
    assert identity(9) in report["protected_image_ids"]
    assert state["verified"] and not report["selected"] and not docker.calls


def test_new_stopped_reference_is_rechecked(inventory, tmp_path):
    docker, state, options = runner(inventory, tmp_path)

    def race():
        if state["locked"]:
            inventory[1].append(dict(Image=identity(1)))

    docker.before_inventory = race
    report = retention.retain(**options)
    assert reference(1) not in report["removed"]


@pytest.mark.parametrize("digest_aliases", [False, True])
def test_registry_failure_causes_zero_removal(inventory, tmp_path, digest_aliases):
    if digest_aliases:
        for image in inventory[0]:
            image["RepoTags"] = image["RepoDigests"].copy()
    docker, _, options = runner(inventory, tmp_path)

    def unavailable(*args):
        raise retention.Refusal("registry_unavailable")

    options["registry"] = SimpleNamespace(verify=unavailable)
    report = retention.retain(**options)
    assert not docker.calls and not report["removed"]
    assert report["unverified"]
    assert all(row["reason"] == "registry_unavailable" for row in report["unverified"])


def test_locked_deadline_is_at_most_sixty_seconds(inventory, tmp_path):
    clock = [0]
    docker, state, options = runner(inventory, tmp_path, clock=lambda: clock[0])

    def stalled():
        if state["locked"]:
            assert docker.deadline == 60
            clock[0] = 61
            raise retention.Refusal("budget_expired")

    docker.before_inventory = stalled
    with pytest.raises(retention.Refusal):
        retention.retain(**options)
    assert not docker.calls and not state["locked"]


@pytest.mark.skipif(os.name != "posix", reason="real advisory lock needs Linux oracle")
@pytest.mark.parametrize("which", [0, 1])
def test_real_other_process_lock_blocks_retention(tmp_path, which):
    paths = [tmp_path / "fence.lock", tmp_path / "mutation.lock"]
    source = (
        "import fcntl,sys; f=open(sys.argv[1],'w'); fcntl.flock(f,fcntl.LOCK_EX); "
        "print('ready',flush=True); sys.stdin.read()"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", source, str(paths[which])],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "ready"
        with pytest.raises(retention.Refusal, match="busy"):
            with retention.locks(paths, tmp_path / "state"):
                pytest.fail("entered locked section")
    finally:
        child.communicate("", timeout=5)


@pytest.mark.skipif(os.name != "posix", reason="real advisory lock needs Linux oracle")
def test_any_fence_state_refuses(tmp_path):
    state = tmp_path / "state"
    state.write_text('{"phase":"restored"}')
    with pytest.raises(retention.Refusal, match="fence_present"):
        with retention.locks([tmp_path / "one", tmp_path / "two"], state):
            pytest.fail("entered fenced section")


def registry_fixture(index=True):
    config = json.dumps(dict(os="linux", architecture="amd64")).encode()

    def digest(raw):
        return "sha256:" + hashlib.sha256(raw).hexdigest()

    config_id = digest(config)
    layer = identity(42)
    manifest = json.dumps(dict(config=dict(digest=config_id), layers=[dict(digest=layer)])).encode()
    manifest_id = digest(manifest)
    root = (
        json.dumps(
            dict(
                manifests=[
                    dict(digest=manifest_id, platform=dict(os="linux", architecture="amd64"))
                ]
            )
        ).encode()
        if index
        else manifest
    )
    root_id = digest(root)
    registry = retention.Registry(999999999)
    blobs = {root_id: root, manifest_id: manifest, config_id: config, layer: b"layer"}
    calls = []

    def request(url, **kwargs):
        key = url.rsplit("/", 1)[1]
        calls.append((key, kwargs.get("head", False)))
        if key not in blobs:
            raise retention.Refusal("registry_unavailable")
        return blobs[key]

    registry.request = request
    candidate = dict(ref=retention.REPOSITORY + "@" + root_id, image_id=config_id)
    return registry, candidate, blobs, calls


@pytest.mark.parametrize("index", [True, False])
@pytest.mark.parametrize("id_kind", ["config", "root"])
def test_registry_verifies_hashes_platform_and_every_blob(index, id_kind):
    registry, candidate, _, calls = registry_fixture(index)
    if id_kind == "root":
        candidate["image_id"] = candidate["ref"].split("@")[1]
    registry.verify(candidate, ("linux", "amd64"))
    assert (identity(42), True) in calls


@pytest.mark.parametrize("failure", ["hash", "missing", "local", "platform"])
def test_registry_negative_evidence_refuses(failure):
    registry, candidate, blobs, _ = registry_fixture()
    if failure == "hash":
        blobs[candidate["ref"].split("@")[1]] += b" "
    elif failure == "missing":
        del blobs[identity(42)]
    elif failure == "local":
        candidate["image_id"] = identity(99)
    platform = ("linux", "arm64") if failure == "platform" else ("linux", "amd64")
    with pytest.raises(retention.Refusal):
        registry.verify(candidate, platform)


def test_registry_redirect_never_sends_bearer_to_cdn():
    import urllib.request

    req = urllib.request.Request("https://ghcr.io/blob", headers={"Authorization": "Bearer SECRET"})
    redirect = retention.SafeRedirect()
    target = redirect.redirect_request(
        req, None, 302, "", {}, "https://pkg-containers.githubusercontent.com/blob"
    )
    assert not target.has_header("Authorization")
    with pytest.raises(retention.Refusal):
        redirect.redirect_request(req, None, 302, "", {}, "https://evil.example/blob")


def test_configuration_is_reread_under_lock(inventory, tmp_path):
    docker, state, options = runner(inventory, tmp_path)
    options["config_reader"] = lambda: reference(1) if state["locked"] else reference(9)
    result = retention.retain(**options)
    assert reference(1) not in result["removed"]
    assert all(choice["ref"] != reference(1) for choice in result["selected"])


@pytest.mark.skipif(os.name != "posix", reason="production nofollow file reads require Linux")
@pytest.mark.parametrize(
    "value", [reference(9), "'" + reference(9) + "'", '"' + reference(9) + '"']
)
def test_configuration_reader_extracts_only_exact_key(tmp_path, value):
    path = tmp_path / "env"
    path.write_text("PRIVATE_TOKEN=secret\nTINYASSETS_IMAGE=" + value + "\n")
    assert retention.read_configured_image(path) == reference(9)


@pytest.mark.skipif(os.name != "posix", reason="production nofollow file reads require Linux")
@pytest.mark.parametrize(
    "contents", ["", "TINYASSETS_IMAGE=\n", "TINYASSETS_IMAGE=x\nTINYASSETS_IMAGE=y\n"]
)
def test_missing_duplicate_empty_config_refuses(tmp_path, contents):
    path = tmp_path / "env"
    path.write_text(contents)
    with pytest.raises(retention.Refusal):
        retention.read_configured_image(path)


@pytest.mark.skipif(os.name != "posix", reason="production nofollow file reads require Linux")
def test_symlink_fifo_and_oversized_files_are_refused(tmp_path):
    path = tmp_path / "real"
    path.write_text("large")
    with pytest.raises(retention.Refusal, match="oversize"):
        retention.read_regular(path, 1)
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(OSError):
        retention.read_regular(link, 10)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(retention.Refusal, match="not_regular"):
        retention.read_regular(fifo, 10)


@pytest.mark.skipif(os.name != "posix", reason="production nofollow file reads require Linux")
@pytest.mark.parametrize(
    "fault", ["none", "missing", "invalid", "rollback_missing", "override", "mount"]
)
def test_receipt_has_one_authoritative_volume_location(tmp_path, fault):
    path = tmp_path / "release-state.json"
    path.write_text(json.dumps(dict(rollback_target="")))
    daemon = dict(
        Name="/tinyassets-daemon",
        State={"Running": True},
        Config={"Env": []},
        Mounts=[
            dict(Destination="/data", Type="volume", Name="tinyassets-data", Source=str(tmp_path))
        ],
    )
    if fault == "missing":
        path.unlink()
    elif fault == "invalid":
        path.write_text("invalid")
    elif fault == "rollback_missing":
        path.write_text("{}")
    elif fault == "override":
        daemon["Config"]["Env"] = ["TINYASSETS_RELEASE_STATE_PATH=/other"]
    elif fault == "mount":
        daemon["Mounts"][0]["Name"] = "other"
    docker = SimpleNamespace(json=lambda *args: [dict(Mountpoint=str(tmp_path))])
    if fault == "none":
        assert retention.read_receipt(docker, [daemon])[1] == dict(rollback_target="")
    else:
        with pytest.raises(retention.Refusal):
            retention.read_receipt(docker, [daemon])


def test_docker_deadline_never_starts_an_expired_command():
    calls = []
    docker = retention.Docker(1, clock=lambda: 2, runner=lambda *a, **k: calls.append(a))
    with pytest.raises(retention.Refusal, match="budget_expired"):
        docker.command("image", "rm", reference(1))
    assert not calls


def test_docker_timeout_is_bounded_by_remaining_budget():
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs["timeout"]))
        return SimpleNamespace(returncode=0, stdout="")

    docker = retention.Docker(5, clock=lambda: 2, runner=run)
    docker.command("image", "rm", reference(1))
    assert calls == [(["docker", "image", "rm", reference(1)], 3)]


def test_cli_unknown_error_never_echoes_credentials(monkeypatch, capsys):
    def fail(**kwargs):
        raise OSError("SECRET")

    monkeypatch.setattr(retention, "retain", fail)
    assert retention.main([]) == 2
    assert "SECRET" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "activation,argv,effect",
    [
        (None, ["--apply"], False),
        ("0", ["--apply"], False),
        ("1", [], False),
        ("1", ["--apply"], True),
    ],
)
def test_activation_requires_both_opt_in_and_apply(
    inventory, tmp_path, monkeypatch, activation, argv, effect
):
    docker, _, options = runner(inventory, tmp_path)
    actual_retain = retention.retain

    def exercise(**kwargs):
        options.update(kwargs)
        return actual_retain(**options)

    monkeypatch.setattr(retention, "retain", exercise)
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.delenv("DISK_AUTOPRUNE_PCT", raising=False)
    monkeypatch.delenv("DISK_AUTOPRUNE_LOW_PCT", raising=False)
    if activation is None:
        monkeypatch.delenv("TINYASSETS_DAEMON_IMAGE_RETENTION_APPLY", raising=False)
    else:
        monkeypatch.setenv("TINYASSETS_DAEMON_IMAGE_RETENTION_APPLY", activation)
    retention.main(argv)
    assert bool(docker.calls) is effect


@pytest.mark.parametrize("value", ["", "true", "yes", "2", " 1", "invalid"])
def test_malformed_activation_refuses_before_any_work(monkeypatch, capsys, value):
    monkeypatch.setenv("TINYASSETS_DAEMON_IMAGE_RETENTION_APPLY", value)
    monkeypatch.setattr(retention, "retain", lambda **kw: pytest.fail("must refuse before work"))
    assert retention.main(["--apply"]) == 2
    assert "invalid_retention_activation" in capsys.readouterr().out


def test_legacy_dry_run_can_only_reduce_authority(monkeypatch):
    monkeypatch.setenv("TINYASSETS_DAEMON_IMAGE_RETENTION_APPLY", "1")
    monkeypatch.setenv("DRY_RUN", "1")

    def check(**kwargs):
        assert kwargs["dry_run"] is True
        return dict(status="dry_run")

    monkeypatch.setattr(retention, "retain", check)
    assert retention.main(["--apply"]) == 1


def test_retention_activation_does_not_control_transcript_rotation(tmp_path, monkeypatch):
    from scripts import rotate_run_transcripts as rotation

    calls = []
    monkeypatch.setenv("TINYASSETS_DAEMON_IMAGE_RETENTION_APPLY", "0")
    monkeypatch.setenv("DRY_RUN", "1")
    monkeypatch.setattr(rotation, "data_dir", lambda: tmp_path)

    def rotate(path, **kwargs):
        calls.append(path)
        return SimpleNamespace(as_dict=lambda: {})

    monkeypatch.setattr(rotation, "rotate_run_transcripts", rotate)
    assert rotation.main([]) == 0
    assert calls == [tmp_path / "runs"]
    calls.clear()
    assert rotation.main(["--dry-run"]) == 0
    assert calls == []


# ---------------------------------------------------------------------------
# Count-based retention (2026-09-24): the keep set is a rule, not a pressure
# response. Deploys pull a ~2.7 GB image many times a day; the threshold-gated
# pass reported "below_threshold" with 23 daemon images on a 50 GB disk.
# ---------------------------------------------------------------------------


def drain(inventory, tmp_path, passes=5, **kwargs):
    """Run passes until one removes nothing; return surviving refs and removals."""
    removed = []
    for _ in range(passes):
        _, _, options = runner(inventory, tmp_path, **kwargs)
        report = retention.retain(**options)
        removed.extend(report["removed"])
        if not report["removed"]:
            break
    survivors = {row["RepoDigests"][0] for row in inventory[0]}
    return survivors, removed


def test_count_mode_removes_excess_images_below_the_threshold(inventory, tmp_path):
    docker, _, options = runner(inventory, tmp_path, measure=lambda p: 50)
    report = retention.retain(**options)
    assert report["removed"] == [reference(n) for n in (1, 2, 3, 4, 5, 6, 7, 10)]
    assert report["status"] == "retained"
    assert report["mode"] == "count"


def test_count_mode_ignores_the_low_watermark(inventory, tmp_path):
    # 70% is under both the 85% trigger and the 75% stop: the legacy path did
    # nothing here; the count rule still removes every image outside the keep set.
    docker, _, options = runner(inventory, tmp_path, measure=lambda p: 70)
    retention.retain(**options)
    assert len(docker.calls) == 8


def test_count_keep_set_is_current_plus_two_previous(inventory, tmp_path):
    # Unadopted newer images have no implicit indefinite pin.
    survivors, removed = drain(inventory, tmp_path, measure=lambda p: 50)
    assert survivors == {reference(n) for n in (8, 9)}
    assert sorted(removed) == sorted(reference(n) for n in (1, 2, 3, 4, 5, 6, 7, 10))


def test_count_mode_preserves_rollback_target_and_any_container_image(inventory, tmp_path):
    inventory[3]["rollback_target"] = reference(2)
    inventory[1].append(dict(Name="/exited-helper", Image=identity(4), State={"Running": False}))
    survivors, _ = drain(inventory, tmp_path, measure=lambda p: 50)
    assert survivors == {reference(n) for n in (2, 4, 8, 9)}


def test_count_mode_preserves_the_running_image_even_when_it_is_the_oldest(inventory, tmp_path):
    # A manual rollback to an old digest: the running image is image 1 and every
    # newer image is removable unless explicitly pinned.
    inventory[2]["Image"] = identity(1)
    survivors, removed = drain(
        inventory, tmp_path, measure=lambda p: 50, config_reader=lambda: reference(1)
    )
    assert reference(1) in survivors
    assert len(removed) == 9


def test_count_mode_emergency_threshold_grades_the_pass(inventory, tmp_path):
    # Above the trigger the same keep set applies; the threshold grades the
    # result so pressure the rule cannot relieve stays loud (exit 1).
    docker, _, options = runner(inventory, tmp_path)
    options["measure"] = lambda p: 90 - 6 * len(docker.calls)
    report = retention.retain(**options)
    assert len(docker.calls) == 8
    assert report["status"] == "pressure_relieved"
    docker, _, options = runner(inventory, tmp_path, measure=lambda p: 95)
    assert retention.retain(**options)["status"] == "pressure_unmet"


def test_threshold_mode_is_still_available_as_an_operator_lever(inventory, tmp_path):
    docker, _, options = runner(inventory, tmp_path, mode="threshold", measure=lambda p: 90)
    report = retention.retain(**options)
    assert report["mode"] == "threshold"
    assert len(docker.calls) == 8
    assert report["status"] == "pressure_unmet"


def test_one_unrecoverable_image_does_not_block_the_rule(inventory, tmp_path):
    docker, _, options = runner(inventory, tmp_path, measure=lambda p: 50)

    def verify(choice, platform):
        if choice["ref"] == reference(1):
            raise retention.Refusal("registry_object_unavailable")

    options["registry"] = SimpleNamespace(verify=verify)
    report = retention.retain(**options)
    assert report["removed"] == [reference(n) for n in (2, 3, 4, 5, 6, 7, 10)]
    assert report["unverified"] == [dict(ref=reference(1), reason="registry_object_unavailable")]
    assert report["status"] == "retention_incomplete"


def test_nothing_to_remove_does_not_contend_for_the_host_lock(inventory, tmp_path):
    keep = {identity(n) for n in (7, 8, 9, 10)}
    inventory[0][:] = [row for row in inventory[0] if row["Id"] in keep]
    docker, _, options = runner(inventory, tmp_path, measure=lambda p: 50)

    @contextmanager
    def busy():
        raise retention.Refusal("host_mutation_busy")
        yield

    options["locker"] = busy
    with pytest.raises(retention.Refusal, match="host_mutation_busy"):
        retention.retain(**options)
    assert not docker.calls


def test_count_mode_dry_run_selects_without_removing(inventory, tmp_path):
    docker, _, options = runner(inventory, tmp_path, dry_run=True, measure=lambda p: 50)
    report = retention.retain(**options)
    assert report["status"] == "dry_run"
    assert [c["ref"] for c in report["selected"]] == [
        reference(n) for n in (1, 2, 3, 4, 5, 6, 7, 10)]
    assert not docker.calls


@pytest.mark.parametrize(
    "value,expected", [(None, "count"), ("count", "count"), ("threshold", "threshold")]
)
def test_mode_is_read_from_the_environment(monkeypatch, value, expected):
    seen = {}

    def check(**kwargs):
        seen.update(kwargs)
        return dict(status="retained")

    monkeypatch.setattr(retention, "retain", check)
    monkeypatch.setenv("TINYASSETS_DAEMON_IMAGE_RETENTION_APPLY", "1")
    monkeypatch.delenv("DRY_RUN", raising=False)
    if value is None:
        monkeypatch.delenv("TINYASSETS_DAEMON_IMAGE_RETENTION_MODE", raising=False)
    else:
        monkeypatch.setenv("TINYASSETS_DAEMON_IMAGE_RETENTION_MODE", value)
    assert retention.main(["--apply"]) == 0
    assert seen["mode"] == expected


@pytest.mark.parametrize("value", ["", "Count", "age", "0"])
def test_malformed_mode_refuses_before_any_work(monkeypatch, capsys, value):
    monkeypatch.setenv("TINYASSETS_DAEMON_IMAGE_RETENTION_APPLY", "1")
    monkeypatch.setenv("TINYASSETS_DAEMON_IMAGE_RETENTION_MODE", value)
    monkeypatch.setattr(retention, "retain", lambda **kw: pytest.fail("must refuse before work"))
    assert retention.main(["--apply"]) == 2

    assert "invalid_retention_mode" in capsys.readouterr().out

def test_incident_untagged_digest_images_leave_only_running_rollback_and_cutover(tmp_path):
    fixture = Path(__file__).parent / "fixtures/daemon_images_20261011.json"
    images = json.loads(fixture.read_text())["images"]
    running, rollback, cutover = images[:3]
    daemon = dict(Name="/tinyassets-daemon", Image=running["Id"], State={"Running": True})
    data = (images, [daemon], daemon, {"rollback_target": rollback["RepoDigests"][0]})
    docker, _, options = runner(data, tmp_path, measure=lambda _: 54,
                               config_reader=lambda: running["RepoDigests"][0])
    report = retention.retain(**options)
    assert report["status"] == "retained"
    assert len(report["removed"]) == 11
    assert {row["Id"] for row in images} == {running["Id"], rollback["Id"], cutover["Id"]}
    assert all(call[:2] == ("image", "rm") and len(call) == 3 for call in docker.calls)


@pytest.mark.skipif(os.name != "posix", reason="real nofollow reads require Linux")
def test_retention_reads_receipt_with_full_git_containment(tmp_path):
    receipt = {"rollback_target": reference(2), "containment": {"commits": ["a" * 40] * 6000}}
    (tmp_path / "release-state.json").write_text(json.dumps(receipt))
    daemon = dict(Name="/tinyassets-daemon", State={"Running": True},
                  Mounts=[dict(Destination="/data", Type="volume", Name="tinyassets-data",
                               Source=str(tmp_path))])
    docker = SimpleNamespace(json=lambda *args: [dict(Mountpoint=str(tmp_path))])
    assert retention.read_receipt(docker, [daemon])[1]["rollback_target"] == reference(2)


@pytest.mark.parametrize("free_gib,refused", [(9, True), (10, True), (11, False)])
def test_headroom_checks_absolute_reserve_and_80_percent(tmp_path, free_gib, refused):
    docker = FakeDocker(None, tmp_path)
    usage = SimpleNamespace(total=50 * 1024**3, free=free_gib * 1024**3)
    if refused:
        with pytest.raises(retention.Refusal, match="headroom"):
            retention.check_pull_headroom(docker, disk_fn=lambda _: usage)
    else:
        retention.check_pull_headroom(docker, disk_fn=lambda _: usage)


def test_dangling_cleanup_preserves_digest_refs_and_container_images(inventory, tmp_path):
    orphan = dict(Id=identity(80), RepoTags=[], RepoDigests=[])
    used = dict(Id=identity(81), RepoTags=[], RepoDigests=[])
    inventory[0].extend([orphan, used])
    inventory[1].append(dict(Image=used["Id"]))
    docker = FakeDocker(inventory, tmp_path)
    assert retention.prune_dangling(docker) == [orphan["Id"]]
    assert len(docker.data[0]) == 11


def test_bounded_pass_reports_incomplete_not_success(inventory, tmp_path, monkeypatch):
    monkeypatch.setattr(retention, "MAX_REMOVALS", 2)
    _, _, options = runner(inventory, tmp_path, measure=lambda _: 50)
    report = retention.retain(**options)
    assert len(report["removed"]) == 2
    assert report["status"] == "retention_incomplete"


def test_old_receipt_during_canary_protects_immediate_rollback(inventory):
    inventory[3]["rollback_target"] = reference(7)
    choices, protected = select(inventory)
    assert identity(8) in protected and identity(7) in protected
    assert reference(8) not in {row["ref"] for row in choices}


def test_cached_prefetch_needs_no_second_reserve_but_missing_image_does(inventory, tmp_path):
    docker = FakeDocker(inventory, tmp_path)
    usage = SimpleNamespace(total=50 * 1024**3, free=8 * 1024**3)
    retention.check_pull_headroom(docker, disk_fn=lambda _: usage, image_ref=reference(10))
    with pytest.raises(retention.Refusal, match="headroom"):
        retention.check_pull_headroom(docker, disk_fn=lambda _: usage, image_ref=reference(11))


BOX = "ghcr.io/tinyassets/tinyassets-box"


def box_ref(number):
    return BOX + "@" + identity(number)


def harness_name(number):
    return "harness-" + f"{number:064x}"


def test_count_mode_keeps_current_and_one_rollback_per_owned_repository(inventory, tmp_path):
    for number, day in ((21, 1), (22, 2), (23, 3), (24, 4)):
        inventory[0].append(dict(
            Id=identity(number),
            Created=f"2026-08-{day:02}T00:00:00Z",
            RepoDigests=[box_ref(number)],
            RepoTags=[],
        ))
    inventory[0].append(dict(
        Id=identity(40),
        Created="2026-01-01T00:00:00Z",
        RepoDigests=["cloudflare/cloudflared@sha256:" + f"{40:064x}"],
        RepoTags=[],
    ))
    docker, _, options = runner(inventory, tmp_path, measure=lambda _p: 50)
    options["configured_images_reader"] = lambda: [reference(9), box_ref(23)]
    report = retention.retain(**options)
    removed = set(report["removed"])
    assert box_ref(21) in removed and box_ref(24) in removed
    assert box_ref(22) not in removed and box_ref(23) not in removed
    assert reference(8) not in removed and reference(9) not in removed
    assert "cloudflare/cloudflared@sha256:" + f"{40:064x}" not in removed
    assert {row["Id"] for row in inventory[0]} >= {identity(n) for n in (8, 9, 22, 23, 40)}


def test_harness_store_keeps_configured_current_and_one_rollback(inventory, tmp_path):
    root = tmp_path / "boxhostd"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("keep")
    for number, when in ((1, 1), (8, 2), (9, 3)):
        directory = root / harness_name(number)
        directory.mkdir()
    (root / harness_name(1) / "link").symlink_to(outside)
    for number, when in ((1, 1), (8, 2), (9, 3)):
        os.utime(root / harness_name(number), (when, when))
    docker, _, options = runner(
        inventory, tmp_path, measure=lambda _p: 50,
        environ={"BOX_HARNESS_ROOT": str(root)},
    )
    docker.image_ids[reference(9)] = identity(9)
    report = retention.retain(**options)
    assert report["harness_kept"] == [harness_name(8), harness_name(9)]
    assert report["harness_removed"] == [harness_name(1)]
    assert outside.read_text() == "keep"
    assert (root / harness_name(9)).is_dir()
    assert not (root / harness_name(1)).exists()


def test_apply_prunes_dangling_layers_and_builder_cache_never_volumes(inventory, tmp_path):
    docker, _, options = runner(inventory, tmp_path, measure=lambda _p: 50)
    retention.retain(**options)
    assert docker.layer_prunes == [("image", "prune", "-f")]
    assert docker.builder_prunes == [("builder", "prune", "-af")]
    flat = " ".join(" ".join(call) for call in docker.calls_all)
    assert "volume" not in flat


def test_every_refusal_prints_disk_docker_df_kept_and_removed(monkeypatch, capsys):
    class DF:
        def command(self, *args, timeout_cap=10):
            assert args[:2] == ("system", "df")
            return (
                '{"Type":"Images","TotalCount":"4","Size":"8GB","Reclaimable":"3GB"}\n'
                '{"Type":"Local Volumes","TotalCount":"1","Size":"20GB","Reclaimable":"0B"}\n'
            )

    usage = {
        "/": SimpleNamespace(total=50 * 1024**3, free=8 * 1024**3),
        "/var/lib/docker": SimpleNamespace(total=50 * 1024**3, free=7 * 1024**3),
    }

    def boom(**kwargs):
        context = kwargs["context"]
        context.kept.append("kept-ref")
        context.removed.append("removed-ref")
        context.paths = ["/", "/var/lib/docker"]
        context.disk_fn = lambda path: usage[path]
        context.docker = DF()
        raise retention.Refusal("deploy_disk_headroom_insufficient")

    monkeypatch.setattr(retention, "retain", boom)
    monkeypatch.setenv("TINYASSETS_DAEMON_IMAGE_RETENTION_APPLY", "1")
    monkeypatch.delenv("DRY_RUN", raising=False)
    assert retention.main(["--apply", "--prepare-pull", "--image-ref", reference(9)]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["reason"] == "deploy_disk_headroom_insufficient"
    assert payload["kept"] == ["kept-ref"]
    assert payload["removed"] == ["removed-ref"]
    by_path = {row["path"]: row for row in payload["disk"]}
    assert by_path["/"]["free"] == 8 * 1024**3
    assert by_path["/"]["total"] == 50 * 1024**3
    assert by_path["/"]["percent"] == pytest.approx(84)
    assert by_path["/var/lib/docker"]["free"] == 7 * 1024**3
    types = {row["type"]: row for row in payload["docker_df"]}
    assert types["Images"]["total"] == "4"
    assert types["Images"]["size"] == "8GB"
    assert types["Local Volumes"]["size"] == "20GB"
    assert retention.PULL_FREE_BYTES == 10 * 1024**3


def test_log_cap_merges_without_dropping_other_keys_or_raising_a_tighter_cap(tmp_path):
    path = tmp_path / "daemon.json"
    path.write_text(json.dumps({
        "live-restore": True,
        "log-opts": {"max-buffer-size": "1m"},
    }))
    assert retention.ensure_docker_log_cap(path)["changed"] is True
    data = json.loads(path.read_text())
    assert data["live-restore"] is True
    assert data["log-driver"] == "json-file"
    assert data["log-opts"]["max-size"] == "10m"
    assert data["log-opts"]["max-file"] == "3"
    assert data["log-opts"]["max-buffer-size"] == "1m"
    assert retention.ensure_docker_log_cap(path)["changed"] is False
    path.write_text(json.dumps({
        "log-driver": "json-file",
        "log-opts": {"max-size": "5m", "max-file": 2},
    }))
    assert retention.ensure_docker_log_cap(path)["changed"] is False
    assert json.loads(path.read_text())["log-opts"]["max-size"] == "5m"


def test_log_cap_refuses_a_symlink(tmp_path):
    target = tmp_path / "real.json"
    target.write_text("{}")
    link = tmp_path / "daemon.json"
    link.symlink_to(target)
    with pytest.raises(retention.Refusal, match="symlink"):
        retention.ensure_docker_log_cap(link)


@pytest.mark.skipif(os.name != "posix", reason="production nofollow file reads require Linux")
def test_configured_images_include_every_owned_image_key(tmp_path):
    path = tmp_path / "env"
    path.write_text(
        "TINYASSETS_IMAGE=" + reference(9) + "\n"
        "TINYASSETS_BOX_IMAGE=" + box_ref(3) + "\n"
        "UNRELATED=1\n"
    )
    assert retention.read_configured_images(path) == [reference(9), box_ref(3)]
