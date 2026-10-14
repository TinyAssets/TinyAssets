"""Bind the existing CLI package runner to its admitted session and custody."""

from tinyassets.boxes.cli_launcher import BoxLauncher
from tinyassets.boxes.git_bridge import environment
from tinyassets.boxes.provider import BoxAuthError
from tinyassets.control_plane.binding import current_binding


class SelectedBoxLauncher(BoxLauncher):
    def __init__(self, binding, custody, *, operation_id, connections=(), agent="main"):
        binding.validate(custody.owner_user_id, custody.universe_dir)
        super().__init__(binding.provider, binding.handle, custody, operation_id=operation_id)
        self.git_environment = environment(connections, agent)

    async def spawn(self, argv, env, **kwargs):
        return await super().spawn(argv, {**env, **self.git_environment}, **kwargs)


def launcher(custody, *, operation_id, connections=(), agent="main", binding=None):
    binding = binding or current_binding()
    if binding is not None:
        return SelectedBoxLauncher(binding, custody, operation_id=operation_id,
                                   connections=connections, agent=agent)
    from tinyassets.agent_loop.served_chat import configured_box_provider
    from tinyassets.storage_layout import ACTIVE_BOX_LAYOUT

    if ACTIVE_BOX_LAYOUT == "owner-box" or configured_box_provider()[0] is not None:
        raise BoxAuthError("native execution requires the admitted session binding")
    # Retained only for the current pre-migration image. C6's coordinated cell
    # removal deletes this branch with the owner-split layout at protected cutover.
    from tinyassets.role_external_agent import CellLauncher

    return CellLauncher(universe_dir=custody.universe_dir, owner_user_id=custody.owner_user_id)
