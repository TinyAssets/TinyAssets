"""Durable activity admission using the ordinary run owner, budget and agent loop."""
from __future__ import annotations

import contextvars
import time
from pathlib import Path

from tinyassets import agent_activities as activities
from tinyassets import runs
from tinyassets.activity_runner import ActivityRunBinding, ActivityYielded


def enqueue(base: Path, universe: str, record: dict, generation: int) -> str:
    """Commit the run binding before submission; caller inputs supply no authority."""
    from tinyassets.api.permissions import owner_run_identity
    from tinyassets.control_plane.lease import current_owner_lease

    current_owner_lease().check()
    root = base / universe
    stored = activities.get(root, record["activity_id"])
    if (stored is None or stored["runner_generation"] != generation
            or stored["runner_token"] or stored["status"] != activities.IN_PROGRESS):
        raise activities.ActivityRefused("Activity claim changed", kind="superseded")
    owner = stored["owner_principal"]
    with owner_run_identity(base, universe, owner) as bound:
        if not bound:
            raise activities.ActivityRefused(
                "Activity owner lost this command center", kind="owner_lost")
        run_id = runs.create_run(
            base, branch_def_id="", thread_id=stored["session_key"],
            inputs={"activity_id": stored["activity_id"], "generation": generation},
            run_name=f"activity:{stored['activity_id']}", actor=f"universe:{universe}",
            owner_user_id=owner, queue_universe_id=universe,
        )
    if not activities.bind_run(root, stored["activity_id"], generation, run_id):
        runs.request_cancel(base, run_id)
        runs.update_run_status(base, run_id, status="cancelled", finished_at=time.time())
        return run_id
    try:
        current_owner_lease().check()
        from tinyassets.provider_admission import independent_provider_work
        from tinyassets.universe_seats import detach_seat

        with independent_provider_work():
            context = contextvars.copy_context()
        context.run(detach_seat)
        future = runs._get_executor(pool_key=runs.run_pool_key(base, universe)).submit(
            context.run, execute, base, universe, stored["activity_id"], generation, run_id,
        )
        runs._track_future(run_id, future)
    except Exception:
        runs.update_run_status(base, run_id, status="failed", error="activity submission failed",
                               finished_at=time.time())
        raise
    return run_id


def execute(base: Path, universe: str, activity_id: str, generation: int, run_id: str) -> None:
    """A single claimed execution; run recovery alone replaces a dead worker."""
    from tinyassets import universe_seats as seats
    from tinyassets.api.permissions import owner_run_identity
    from tinyassets.control_plane import session
    from tinyassets.control_plane.lease import current_owner_lease
    from tinyassets.foreground_run_provider import new_foreground_run_provider_session
    from tinyassets.providers.model_call import text_sync
    from tinyassets.workspace_family import execution_member

    binding = ActivityRunBinding(base / universe, activity_id, generation, run_id)
    provider = held = None
    with runs._managed_execution_scope(base, run_id):
        runs._require_managed_start_status(base, run_id, "queued")
        member = execution_member(base, run_id, for_compile=True)
        update = runs._family_status_writer(member)
        try:
            current_owner_lease().check()
            binding.check()
            record = activities.get(base / universe, activity_id)
            owner = record["owner_principal"]
            with owner_run_identity(base, universe, owner) as bound:
                if not bound:
                    raise PermissionError("activity owner no longer owns this command center")
                update(base, run_id, status="running")

                def check_waiting(_waiting=None):
                    current_owner_lease().check()
                    try:
                        binding.check()
                        if runs.is_cancel_requested(base, run_id):
                            raise runs.RunCancelledError("activity cancelled")
                    except (ActivityYielded, PermissionError) as exc:
                        raise runs.RunCancelledError(str(exc)) from exc

                held = seats.acquire_blocking(
                    seats.account_key(universe, root=base), universe_id=universe, run_id=run_id,
                    kind=seats.KIND_AGENT_NODE, wait_s=None, on_waiting=check_waiting,
                    db=seats.ledger_path(base),
                )
                check_waiting()
                provider = new_foreground_run_provider_session(
                    base, universe_id=universe, principal_id=owner, provider_call=text_sync,
                )
                record = provider.prepare_activity(
                    run_id=run_id, activity_id=activity_id, generation=generation,
                )
                provider._ensure_admitted()
                prompt = (f"Work on this activity until it is done.\n\n{record['title']}\n\n"
                          f"{record['brief']}\n\n"
                          "Finish with a short result summary and file locations.")
                with seats.carrying(held):
                    reply = session.start(record["agent_id"], prompt, session.Activity(
                        session=provider, prompt_config=None, policy=None, activity=record,
                    ))
                binding.check()
                update(base, run_id, status="completed", output={"result": reply},
                       finished_at=time.time())
        except (ActivityYielded, runs.RunCancelledError):
            update(base, run_id, status="cancelled", finished_at=time.time())
        except Exception as exc:
            cancelled = (runs.is_cancel_requested(base, run_id) or not activities.holds(
                base / universe, activity_id, generation, run_id=run_id))
            update(base, run_id, status="cancelled" if cancelled else "failed",
                   error=str(exc), finished_at=time.time())
        finally:
            try:
                if provider is not None:
                    provider.close()
            finally:
                if held is not None:
                    seats.release(held.seat_id, db=held.db)
