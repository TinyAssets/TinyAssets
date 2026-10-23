"""Replay a committed owner-send queue through the box converse door.

The owner door schedules this continuation. It does not import the model door;
the door only learns that a holder released the session lock.
"""


def continue_session(runtime, stop, home, session):
    from tinyassets.addressed_agents import agent_of_session
    from tinyassets.auth.middleware import (
        claim_provider_request,
        identity_context,
        reserve_provider_request,
        revoke_provider_request,
    )
    from tinyassets.auth.provider import Identity
    from tinyassets.owner_activity import activity
    from tinyassets.owner_door.send import resume_pending_sends
    from tinyassets.universe_server import converse

    # Identity comes from startup admission, never from a stored session key.
    agent = agent_of_session(session, runtime.principal)
    if agent is None:
        raise ValueError('stored send thread is not in this owner box')

    def replay(thread, ident, message, options):
        from tinyassets.webhook_inbound import ready_for_send

        if not ready_for_send(runtime.root, ident):
            return
        # Each queued row needs its own drain admission before dispatch state
        # changes. Nested owner work in this task belongs to this admission.
        with activity.enter(keep_awake=False):
            if stop.is_set():
                return
            with identity_context(Identity(
                user_id=runtime.principal, username=runtime.principal, capabilities=['write'],
            )):
                # This is local request authority, never a provider credential.
                reserve = reserve_provider_request(
                    principal_id=runtime.principal, session_id=f'send:{home.name}:{thread}',
                    request_id=ident, tool_name='converse',
                )
                capability = claim_provider_request(reserve, tool_name='converse')
                try:
                    converse(
                        message, graph_id=home.name, client_send_id=ident, agent_id=agent,
                        **options,
                    )
                    if ident.startswith('inb_'):
                        from tinyassets import conversation_store as store
                        from tinyassets.storage.inbound_events import finish

                        state, _ = store.owner_send_state(home, thread, ident)
                        if state == 'completed':
                            finish(runtime.root, ident)
                finally:
                    revoke_provider_request(capability)

    resume_pending_sends(home, replay, session=session, stop=stop)
