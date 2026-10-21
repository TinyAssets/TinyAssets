"""One canonical turn reserves the common execution envelope, never starts it."""



from tests.test_consumer_selection import HOME, OWNER, component, install
from tests.test_consumer_selection import store as store
from tests.test_conversation_run_admissions import source_version


def setup(base, *, reply_key="answer"):
    from tinyassets.consumer_runtime import initialize

    initialize(base)
    version = source_version(base)
    config = component()
    config.update(branch_version_id=version.branch_version_id, content_hash=version.content_hash,
                  reply_key=reply_key)
    binding, _ = install(base, source=config)
    return {"version": 1, "message": "unaltered question", "input_method": "typed",
            "model_choice": None, "binding_id": binding["agent_binding_id"],
            "binding_revision": binding["revision"]}


def reserve(base, key, intent, context=None):
    from tinyassets.consumer_runtime import reserve_prepared_turn

    return reserve_prepared_turn(
        base, owner=OWNER, universe=HOME, request_key=key, intent=intent,
        context=context or {"version": 1, "history": [], "preferences": None},
    )




















