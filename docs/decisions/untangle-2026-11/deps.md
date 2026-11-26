# deps-check as built (2026-11-14)

The platform's tests refuse installed dependencies that are not `platform/package-lock.json`'s. Agents ran tests against a `node_modules` linked from another checkout, and nothing stopped a stale one; CI runs `npm ci` fresh, so local results could drift from it.

## Research (Fact 14)

1. **What to compare.** Measured on a fixture (a root, a workspace, a link, a direct and a transitive package) and on the shared install (368 lockfile entries, 294 installed: the 74 others are other platforms' optional binaries and the root).
   - **`npm ls --all`** checks each package against the *range* its dependent declares, not the version locked. A transitive package at `2.5.0`, locked `2.1.0` and wanted `^2.0.0`, passes. It catches a direct mismatch (the platform pins exact versions), a missing package and a missing workspace link. It spawns npm: about 0.5 s a run.
   - **npm's hidden lockfile** (`node_modules/.package-lock.json`) is what npm last wrote, not what is there. Compared with the lockfile's `packages`, it is exact on versions and one file read, but it misses a package removed after the install, and a workspace's own `node_modules` that was never linked (`ci/node_modules/@types/node` and the like are listed in the root's hidden lockfile, whatever is in the workspace).
   - **Each lockfile entry against the `package.json` at its path** is what Node loads. It is exact on versions, transitive ones included, and catches a missing package, a workspace's missing `node_modules` and a missing or wrong workspace link. It takes 7 to 14 ms for the platform's 368 entries. **Chosen.**
2. **Where it runs.** Every workspace's `vitest.config.ts` makes its projects by `ci/project.ts`'s `project()`, and vitest loads the config whoever starts it: `npm test`, `npx vitest` in a workspace, and `npm run test:root -w sandbox` (`root-tests.mjs` spawns vitest on the sandbox's config). `project()` checks first. A `pretest` is skipped by `npx vitest`, and a `globalSetup` runs after the config, once per project. `npm run test:memory -w owner` (`owner/memory/leg.mjs`) and `npm run test:app -w doors` (`doors/test/app.e2e.mjs`) are plain Node, so each checks at its top: an import and a call, the only lines in lane O's and lane D's files.

## As built

1. `platform/ci/deps.ts`: `mismatch(root)` names the first entry the install does not hold as locked (a version, a missing package, a workspace link that does not hold its workspace's package, a package installed in a `node_modules` but not locked); an optional package may be absent if its platform is excluded or every locked path to it passes through an absent, excluded variant. An installed optional must match its locked version. `installed()` throws with that entry, ending "run `npm ci` in platform/."
2. The check uses the installed manifests, lockfile, host OS and architecture. It runs after the CI install.
3. `platform/ci/guards/deps.test.ts` holds it on a lockfile and install of its own in a temp folder.
4. `AGENTS.md` is unchanged: the refusal names its fix, so a rule there would repeat it (Direction 6).

Review (2026-11-14): an optional package was allowed absent on every machine, so one npm installs here (a Linux binary marked optional) could be missing unnoticed; and a stale package left in a `node_modules`, nested ones included, would be loaded, or would shadow the hoisted one its parent loads in CI. Both are now refused; the walk adds about 9 ms.

### Portable children of optional variants (November 26)

On main `d662e63e`, a clean npm 11.19.1 install on Linux installed 277 packages. The ordinary test entry refused to start because it required `@emnapi/runtime` 1.11.3. In this lockfile the only paths to it run through the FreeBSD and WebContainers builds of sharp. Both variants are excluded here. npm 10 also produced a usable install, with the portable children present. Treating every optional package as dispensable would hide missing native binaries on compatible machines.

The guard now follows declared edges to their actual locked paths, including hoisting and workspace-local versions. It propagates exclusions only from absent incompatible packages. A second applicable dependency path, an installed parent, or an unproved cycle keeps the child required. The prior version checks still cover packages that are present.

- [x] Run the original and corrected checks on both clean installs. Original: npm 11 refused and npm 10 passed. Corrected: both passed on Node 24. The twelve focused guards and full platform TypeScript passed on the npm 11 install.
- [x] On `5d401928`, the full CI workspace passed 367 tests with one skipped. An initial sparse-checkout attempt lacked repository files: nine assertions failed and one suite could not load. The complete checkout supplied those files.
- [x] A second, offline npm 11 install using three local tarballs found a missed case: the excluded platform variant was the only way into a two-package cycle. npm omitted both members but the first change required them. Trace reachability from excluded variants through cycles, then remove any path with an applicable parent. Making one cycle member a direct dependency installed both. Removing its sibling was rejected and restoring it passed, on Node 22 and 24 with the real generated locks.
- [x] After the cycle correction, 13 focused guards, the full CI workspace (368 passed, one skipped), and full platform TypeScript passed. The actual npm 10 and npm 11 platform installs remain covered.
- [ ] Coordinator review and integration. The change is staged on the ordinary CI branch.

### Earlier staged heads and optional peers

The Nov 26, 05:20 Pacific inventory covered 151 remote heads. Six shared main's 365-entry lock, 142 shared the 368-entry historical lock, and three had no platform lock. This compares file contents across refs. Installs and test execution used one checkout for each distinct lock.

On the historical lock (`341e4223dde7af8fcf76f23af7df9677201d82e8`), npm 10.9.11 installed 282 packages and npm 11.19.1 installed 279. Both commands exited successfully. From the Owner's installed Agents SDK, normal Node resolution selected the locked MCP Server 2.4.0 with npm 10 and the hoisted 2.3.1 with npm 11. The SDK declares the optional peer at 2.4.0. The corrected guard stops the old npm 11 install before Vitest starts. With npm 10, all seven original guards and all thirteen guards from `b2f91312` passed on that tree. On main's lock both installs resolve 2.4.0 and the corrected guard accepts both. The resolution and mismatch results matched on Node 22 and 24.

- [x] Add a permanent nested optional peer regression. Removing the locked workspace peer makes the older hoisted package visible and is refused. Restoring it passes. Fourteen focused cases passed.
- [ ] For integration, run the actual joined lock and guard before accepting test evidence. Use the verified old npm 10 install when reproducing a historical staged head. The historical npm 11 refusal identifies genuine version drift.

## Left

- A workspace link is held to its workspace's *package*, not to this checkout's folder. Through a `node_modules` linked from another checkout, npm's relative links (`../../ci`) reach that checkout's workspaces, but no source imports a workspace by its package name, so nothing loads them. Holding the folder would refuse every linked install.
- vitest started in `platform/` itself (`npx vitest run owner/x.test.ts`) finds no config, so it makes no workspace's projects and skips the check, as it already skipped the size classes. A config there would be a script at the platform's top, which the layer table refuses (`ci/guards/isolation.test.ts`); whether to allow one is the coordinator's call.
- `link-deps.sh` (outside the repo) compares the two lockfiles' digests before linking. This check makes that one a copy; the coordinator can drop it.
