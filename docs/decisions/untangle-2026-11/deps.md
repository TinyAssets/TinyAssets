# deps-check as built (2026-11-14)

The platform's tests refuse installed dependencies that are not `platform/package-lock.json`'s. Agents ran tests against a `node_modules` linked from another checkout, and nothing stopped a stale one; CI runs `npm ci` fresh, so local results could drift from it.

## Research (Fact 14)

1. **What to compare.** Measured on a fixture (a root, a workspace, a link, a direct and a transitive package) and on the shared install (368 lockfile entries, 294 installed: the 74 others are other platforms' optional binaries and the root).
   - **`npm ls --all`** checks each package against the *range* its dependent declares, not the version locked. A transitive package at `2.5.0`, locked `2.1.0` and wanted `^2.0.0`, passes. It catches a direct mismatch (the platform pins exact versions), a missing package and a missing workspace link. It spawns npm: about 0.5 s a run.
   - **npm's hidden lockfile** (`node_modules/.package-lock.json`) is what npm last wrote, not what is there. Compared with the lockfile's `packages`, it is exact on versions and one file read, but it misses a package removed after the install, and a workspace's own `node_modules` that was never linked (`ci/node_modules/@types/node` and the like are listed in the root's hidden lockfile, whatever is in the workspace).
   - **Each lockfile entry against the `package.json` at its path** is what Node loads. It is exact on versions, transitive ones included, and catches a missing package, a workspace's missing `node_modules` and a missing or wrong workspace link. It takes 7 to 14 ms for the platform's 368 entries. **Chosen.**
2. **Where it runs.** Every workspace's `vitest.config.ts` makes its projects by `ci/project.ts`'s `project()`, and vitest loads the config whoever starts it: `npm test`, `npx vitest` in a workspace, and `npm run test:root -w sandbox` (`root-tests.mjs` spawns vitest on the sandbox's config). `project()` checks first. A `pretest` is skipped by `npx vitest`, and a `globalSetup` runs after the config, once per project. `npm run test:memory -w owner` (`owner/memory/leg.mjs`) and `npm run test:app -w doors` (`doors/test/app.e2e.mjs`) are plain Node, so each checks at its top: an import and a call, the only lines in lane O's and lane D's files.

## As built

1. `platform/ci/deps.ts`: `mismatch(root)` names the first entry the install does not hold as locked (a version, a missing package, a workspace link that does not hold its workspace's package); an optional package may be absent, never another version. `installed()` throws with that entry, ending "run `npm ci` in platform/."
2. In CI it always passes: `npm ci` ran. Nothing in it reads the environment.
3. `platform/ci/guards/deps.test.ts` holds it on a lockfile and install of its own in a temp folder.
4. `AGENTS.md` is unchanged: the refusal names its fix, so a rule there would repeat it (Direction 6).

## Left

- A workspace link is held to its workspace's *package*, not to this checkout's folder. Through a `node_modules` linked from another checkout, npm's relative links (`../../ci`) reach that checkout's workspaces, but no source imports a workspace by its package name, so nothing loads them. Holding the folder would refuse every linked install.
- A package installed but not locked is not refused: an import of it fails in CI, which never installs it.
- `link-deps.sh` (outside the repo) compares the two lockfiles' digests before linking. This check makes that one a copy; the coordinator can drop it.
