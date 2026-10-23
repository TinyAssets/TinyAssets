"""Review-4 findings A, B and C on conv/source-records. Run from the worktree root."""
import pathlib


def swap(rel, old, new, count=1):
    p = pathlib.Path(rel)
    t = p.read_text(encoding="utf-8")
    assert t.count(old) == count, (rel, t.count(old), old[:60])
    p.write_text(t.replace(old, new), encoding="utf-8", newline="\n")


# A: a broker outage reaches the box as end-of-file, raised as PermissionError by the
# owner channel. Retry it like OSError; the read-back still tells a real generation
# conflict apart from an outage.
swap("tinyassets/providers/subscription_lease.py",
     """            return custody(runtime, "save", revision=revision, state=state)
        except OSError:""",
     """            return custody(runtime, "save", revision=revision, state=state)
        except (OSError, PermissionError):""")
swap("tinyassets/providers/subscription_lease.py",
     """                observed = custody(runtime, "read")
            except OSError:
                observed = None""",
     """                observed = custody(runtime, "read")
            except (OSError, PermissionError):
                observed = None""")

# B: only a ready assignment projects candidates; a failed or pending one keeps its
# state without them, so disconnect never stops halfway.
swap("tinyassets/provider_assignment_departure.py",
     """                assignment_candidates=updated.candidates or None)""",
     """                assignment_candidates=(updated.candidates or None)
                if updated.state == "ready" else None)""")

# C: login operations take a thread lock that disconnect can hold while it waits for
# admission; never take it on the event loop.
swap("tinyassets/onboarding/subscription_connect.py",
     """            state = login_operation("poll", flow=doc["flow"])
            if state["status"] != "connected":""",
     """            state = await run_in_threadpool(login_operation, "poll", flow=doc["flow"])
            if state["status"] != "connected":""")
swap("tinyassets/onboarding/subscription_connect.py",
     """        else:
            result = login_operation(action, **doc)""",
     """        else:
            result = await run_in_threadpool(login_operation, action, **doc)""")
print("ok")
