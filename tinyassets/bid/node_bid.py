"""NodeBid dataclass + I/O helpers (Phase G).

A NodeBid is a cross-universe, bid-priced, single-node execution
request stored as YAML under ``<repo_root>/bids/<node_bid_id>.yaml``.
The NodeBidProducer reads these, emits BranchTasks with
``branch_def_id = "<node_bid>" + node_def_id`` so the dispatcher
cycle can pick them by tier, and ``_run_graph`` routes them to
``execute_node_bid`` instead of the Branch wrapper stream.

Flat-dict invariant on ``inputs`` is inherited from Phase F
(``validate_pool_task_inputs``). See docs/architecture.md §R4.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from tinyassets.bid.input_validation import validate_node_bid_inputs as validate_node_bid_inputs

logger = logging.getLogger(__name__)

BIDS_DIRNAME = "bids"


@dataclass
class NodeBid:
    """Cross-universe node-execution bid.

    ``status`` transitions:
      open → claimed:<daemon_id> → succeeded | failed
      open → expired (by external sweep, not this module)
    """

    node_bid_id: str
    node_def_id: str
    required_llm_type: str = ""
    inputs: dict = field(default_factory=dict)
    bid: float = 0.0
    submitted_by: str = ""
    owner_user_id: str = ""
    daemon_id: str = ""
    runtime_instance_id: str = ""
    worker_id: str = ""
    status: str = "open"
    evidence_url: str = ""
    submitted_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "NodeBid":
        known = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


def bids_dir(repo_root: Path) -> Path:
    return Path(repo_root) / BIDS_DIRNAME


def bid_path(repo_root: Path, node_bid_id: str) -> Path:
    return bids_dir(repo_root) / f"{node_bid_id}.yaml"


def _lock_path(repo_root: Path, node_bid_id: str) -> Path:
    return bids_dir(repo_root) / f"{node_bid_id}.yaml.lock"


@contextlib.contextmanager
def _bid_file_lock(repo_root: Path, node_bid_id: str):
    """Cross-platform lock on a sidecar ``.lock`` file for one bid.

    Mirrors ``branch_tasks._file_lock`` but per-bid so concurrent
    operations on different bids don't serialize against each other.
    """
    bids_dir(repo_root).mkdir(parents=True, exist_ok=True)
    lf = _lock_path(repo_root, node_bid_id)
    fd = os.open(str(lf), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if sys.platform == "win32":
            import msvcrt
            while True:
                try:
                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if sys.platform == "win32":
                import msvcrt
                try:
                    os.lseek(fd, 0, 0)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
    finally:
        os.close(fd)


def write_node_bid_post(repo_root: Path, bid_dict: dict) -> Path:
    """Write a NodeBid YAML under ``<repo_root>/bids/<id>.yaml``.

    Returns the output path. Caller is responsible for validation.
    """
    import yaml

    node_bid_id = str(bid_dict.get("node_bid_id") or "").strip()
    if not node_bid_id:
        raise ValueError("node_bid_id required")
    out = bid_path(repo_root, node_bid_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        yaml.safe_dump(dict(bid_dict), sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    return out


def read_node_bids(repo_root: Path) -> list[NodeBid]:
    """Scan ``<repo_root>/bids/*.yaml``. Malformed entries are skipped
    with a WARN log (never raise from read)."""
    d = bids_dir(repo_root)
    if not d.is_dir():
        return []
    import yaml
    out: list[NodeBid] = []
    for p in sorted(d.glob("*.yaml")):
        try:
            raw = p.read_text(encoding="utf-8")
            data = yaml.safe_load(raw) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("node_bid: malformed YAML at %s: %s", p, exc)
            continue
        if not isinstance(data, dict):
            logger.warning("node_bid: %s is not a mapping; skipping", p)
            continue
        # Filename stem wins — prevents a malicious rename-in-place attack.
        data["node_bid_id"] = p.stem
        try:
            out.append(NodeBid.from_dict(data))
        except Exception as exc:  # noqa: BLE001
            logger.warning("node_bid: %s failed to parse: %s", p, exc)
            continue
    return out


def read_node_bid(repo_root: Path, node_bid_id: str) -> NodeBid | None:
    """Read one bid by id. Returns None on missing."""
    p = bid_path(repo_root, node_bid_id)
    if not p.exists():
        return None
    import yaml
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("node_bid: read %s failed: %s", p, exc)
        return None
    if not isinstance(data, dict):
        return None
    data["node_bid_id"] = p.stem
    try:
        return NodeBid.from_dict(data)
    except Exception:  # noqa: BLE001
        return None


def git_has_remote(repo_root: Path) -> bool:
    """True if the repo has at least one configured remote.

    Local-only installs (no `git remote`) skip the pull/push steps
    of the claim dance — single-daemon, no race. Exposed publicly
    so callers (e.g. the node-bid execution fallback in
    ``fantasy_author/__main__.py``) can gate behaviour on whether a
    remote race is even possible.
    """
    try:
        result = subprocess.run(
            ["git", "remote"],
            cwd=str(repo_root),
            capture_output=True, text=True, check=False, timeout=5,
        )
        return bool(result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


# Phase G.1 compatibility alias — internal call sites use the
# underscore name. Keep until the rename is mechanically swept.
_git_has_remote = git_has_remote


def _git_current_branch(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(repo_root),
            capture_output=True, text=True, check=False, timeout=5,
        )
        return result.stdout.strip() or "main"
    except (OSError, subprocess.SubprocessError):
        return "main"


def _revert_claim(
    repo_root: Path, branch: str, node_bid_id: str, daemon_id: str,
) -> None:
    """Clean up after a failed claim-push.

    Hard-resets the working tree to ``origin/<branch>`` and deletes
    any bid_outputs directory the executor may have populated before
    the push-failure was detected (preflight §4.1 #1 step 5 + the
    reviewer-polish cleanup requirement).
    """
    if branch:
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["git", "reset", "--hard", f"origin/{branch}"],
                cwd=str(repo_root),
                check=False, timeout=10, capture_output=True,
            )
    outputs_dir = Path(repo_root) / "bid_outputs" / node_bid_id
    if outputs_dir.exists():
        with contextlib.suppress(OSError):
            shutil.rmtree(outputs_dir)
    logger.info(
        "claim_node_bid: reverted claim for %s (daemon %s)",
        node_bid_id, daemon_id,
    )


def claim_node_bid(
    repo_root: Path,
    node_bid_id: str,
    daemon_id: str,
    *,
    owner_user_id: str = "",
    runtime_instance_id: str = "",
    worker_id: str = "",
) -> "NodeBid | None":
    """Atomic claim via git-rename + push (preflight §4.1 #1).

    Sequence:
      1. `git pull --rebase` (remote-only).
      2. File-lock; read YAML; assert status == "open".
      3. Rename `bids/<id>.yaml` → `bids/<id>.yaml.claimed_by_<daemon>`
         with status updated to `claimed:<daemon_id>`.
      4. `git add + commit + push` (remote-only).
      5. Push-fail → `git reset --hard origin/<branch>` + delete
         `bid_outputs/<id>/`; return None.
      6. Push succeeds → return the claimed NodeBid.

    Returns ``NodeBid | None``. ``None`` means: bid missing, already
    claimed, terminal state, or remote race lost. Never raises.
    """
    import yaml

    p = bid_path(repo_root, node_bid_id)
    has_remote = _git_has_remote(repo_root)
    branch = _git_current_branch(repo_root) if has_remote else ""

    # Step 1: pull (remote-only).
    if has_remote:
        try:
            subprocess.run(
                ["git", "pull", "--rebase"],
                cwd=str(repo_root),
                check=False, timeout=30, capture_output=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("claim_node_bid: git pull failed: %s", exc)

    # Step 2 + 3: file-lock, read, rename.
    claimed_path = p.with_suffix(f"{p.suffix}.claimed_by_{daemon_id}")
    data: dict = {}
    with _bid_file_lock(repo_root, node_bid_id):
        if not p.exists():
            return None
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(data, dict) or data.get("status") != "open":
            return None
        data["node_bid_id"] = p.stem
        data["status"] = f"claimed:{daemon_id}"
        data["owner_user_id"] = owner_user_id or ""
        data["daemon_id"] = daemon_id or ""
        data["runtime_instance_id"] = runtime_instance_id or ""
        data["worker_id"] = worker_id or ""
        try:
            claimed_path.write_text(
                yaml.safe_dump(data, sort_keys=False, default_flow_style=False),
                encoding="utf-8",
            )
            os.remove(p)
        except OSError:
            with contextlib.suppress(OSError):
                if claimed_path.exists():
                    os.remove(claimed_path)
            return None

    # Step 4 + 5: commit + push + revert-on-fail.
    if has_remote:
        try:
            subprocess.run(
                ["git", "add", str(p), str(claimed_path)],
                cwd=str(repo_root),
                check=False, timeout=10, capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m",
                 f"bid: {node_bid_id} claimed by {daemon_id}"],
                cwd=str(repo_root),
                check=False, timeout=10, capture_output=True,
            )
            push = subprocess.run(
                ["git", "push", "origin", branch],
                cwd=str(repo_root),
                check=False, timeout=30, capture_output=True, text=True,
            )
            if push.returncode != 0:
                logger.warning(
                    "claim_node_bid: push failed (%s); reverting",
                    (push.stderr or "").strip()[:200],
                )
                _revert_claim(repo_root, branch, node_bid_id, daemon_id)
                return None
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning(
                "claim_node_bid: git op raised (%s); reverting", exc,
            )
            _revert_claim(repo_root, branch, node_bid_id, daemon_id)
            return None

    try:
        return NodeBid.from_dict(data)
    except Exception:  # noqa: BLE001
        return None


def update_node_bid_status(
    repo_root: Path,
    node_bid_id: str,
    *,
    status: str,
    evidence_url: str = "",
) -> bool:
    """File-locked status write. Returns True on success."""
    import yaml

    with _bid_file_lock(repo_root, node_bid_id):
        p = bid_path(repo_root, node_bid_id)
        if not p.exists():
            return False
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            return False
        if not isinstance(data, dict):
            return False
        data["status"] = status
        if evidence_url:
            data["evidence_url"] = evidence_url
        p.write_text(
            yaml.safe_dump(data, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
        return True


def new_node_bid_id() -> str:
    """Generate a unique node_bid_id. Prefix ``nb_`` distinguishes
    from BranchTask ids (``bt_``).
    """
    return f"nb_{int(time.time() * 1000):013d}_{os.urandom(4).hex()}"
