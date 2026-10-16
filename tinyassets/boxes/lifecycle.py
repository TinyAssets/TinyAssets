"""Select the committed owner storage layout for admission and deletion."""

from pathlib import Path

from tinyassets.storage_layout import KNOWN_LAYOUTS, LayoutRefused, read_marker


def owner_layout(data_root: str | Path) -> str:
    marker = read_marker(Path(data_root))
    if marker is None:
        return "owner-split"
    layout = marker.get("box_layout", "owner-split")
    if (marker.get("state") != "stable" or marker.get("layout") not in KNOWN_LAYOUTS
            or layout not in ("owner-split", "owner-box")):
        raise LayoutRefused("owner admission requires a stable, known storage layout")
    return layout


def admit_center(data_root: str | Path, *, principal: str, center: str) -> int:
    """Broker admission reserves the owner; host binding provisions its box."""
    from tinyassets.agent_loop.served_chat import configured_box_provider
    from tinyassets.broker.owner_identities import center_admission

    provider, _ = configured_box_provider()
    if provider is None:
        raise RuntimeError("committed owner-box layout has no admitted provider")
    generation, _ = center_admission(Path(data_root), event="admit",
                                     principal=principal, center=center)
    # bind reaches BOX_ADMISSION through the root host. It verifies the broker
    # reservation and applies the owner's quota before exposing the center.
    handle = provider.bind(center, account_id=principal,
                           turn_id=f"center-admission-{generation}")
    if handle.command_center_id != center or handle.account_id != principal:
        raise RuntimeError("box host returned another center admission")
    from tinyassets.boxes.layout import host_center_stub

    stub = host_center_stub(data_root, center)
    stub.mkdir(mode=0o700, exist_ok=True)
    if stub.is_symlink() or not stub.is_dir():
        raise RuntimeError("host center stub is not a plain directory")
    return generation
