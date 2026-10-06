"""Read ordered instruction files; no provisioning, fallback or turn activation.

The caller must authorize the owner and supply the selected agent's root. D10
owns installation/receipts; the renderer cutover will consume this reader only
after that API and the D6 prerequisites are verified.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tinyassets.universe_files import MAX_BRAIN_FILE_BYTES, read_universe_text

INSTRUCTION_PATHS = ("starter/hooks.md", "AGENTS.md")


@dataclass(frozen=True)
class InstructionFiles:
    blocks: tuple[tuple[str, str], ...]
    notices: tuple[str, ...]

    def render(self) -> str:
        """Source-labelled content in precedence order; report refused reads."""
        sections = [f"## {path}\n{body}" for path, body in self.blocks]
        return "\n\n".join([*sections, *self.notices])


def read_instruction_files(selected_agent_root: Path) -> InstructionFiles:
    """Actual files, hooks first and owner AGENTS last; never create anything."""
    blocks = []
    notices = []
    for path in INSTRUCTION_PATHS:
        try:
            body = read_universe_text(
                selected_agent_root, path, max_bytes=MAX_BRAIN_FILE_BYTES,
            )
        except FileNotFoundError:
            continue
        except (OSError, UnicodeError):
            # Do not expose exception text: it may contain external path data.
            notices.append(f"Could not read {path}; no substitute instructions loaded.")
            continue
        blocks.append((path, body))
    return InstructionFiles(tuple(blocks), tuple(notices))
