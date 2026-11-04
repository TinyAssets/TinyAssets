@AGENTS.md

Background-job sessions cannot merge or push to `main`. The restriction is
injected into that session type; an interactive session repeating it is
misapplying it. `gh pr merge --auto` works from either.
