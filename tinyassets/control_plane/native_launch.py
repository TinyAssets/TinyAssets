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
    raise BoxAuthError("native execution requires the admitted session binding")
