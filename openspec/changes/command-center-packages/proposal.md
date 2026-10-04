# Share a whole command center as one package

## Why

Founder, 2026-10-02: "the publish and use as the second user will be on the
sharing a whole command center as a package". The acceptance test is the GTM
Village: one owner's agent builds a working village command center and
publishes all of it; a second account finds it, installs it into their own
command center and runs it.

What exists covers part of that. The `publish` ask (`in-platform-agent-systems`)
publishes workflows, one UI and automation triggers as one public agent
definition, capped at 256 KiB. Installing is a recipe the agent follows by hand:
remix each workflow, add the UI, recreate each automation. The village also
lives in files (the board the agents write, its skills, its wiki), and none of
those travel. `universe-agent-harness` §4.15 and §4.17 designed the manifest,
the scrub and quarantine for a harness-sized `share` profile and a private
`export` folder, but nothing larger than 256 KiB can be shared.

## What Changes

- **A third manifest profile, `publish`.** It is the §4.17 export layout minus
  the private items: memory unless the owner names items, session logs,
  credentials, browser state, contact details, platform runtime files and the
  owner-describing brain files. Connections become named references in the
  manifest's `needs`.
- **An immutable, versioned public package.** The file tree is stored as a
  content-addressed blob in platform storage. Its size is charged to the
  publisher's storage quota, not limited to 256 KiB. A refusal names the
  package's size. The listing is the one public definition the `publish` ask
  already writes, tagged as a package and carrying a summary: version, size,
  file count, agents, and what it needs (model and connections).
- **Publishing stays the owner-confirmed `publish` ask.** It gains an optional
  `package` block. The tab, written by the platform, lists every file that
  becomes public and every file left out, with the reason. The pinned digest
  covers the blob. Anything changed before confirmation publishes nothing.
- **Installing is one owner-confirmed `install` ask.** The package's content is
  checked by the ingestion boundary and held in quarantine in platform-owned
  storage. The tab previews it. Only the owner's confirmation materialises it,
  as the installer's own copy: private workflow copies, the UI in their
  library, automations created paused, and files written into their command
  center. The package's main harness becomes a roster agent under
  `agents/<slug>/`.
- **Discovery.** `browse_commons kind="packages"` lists packages with their
  summaries.

## Impact

- Public MCP surface: one new `browse_commons` kind on the served tool. One
  new pending-request action type, `install`. The `publish` action gains an
  optional field. Connector handles are unchanged.
- Storage: a new data-root directory, `.command-center-packages/`, holding
  blobs, an index and quarantine/activation records. It is registered as the
  `packages` account store. The definition carries a new component kind as data.
- Authority: installed workflows and automations run only as the installer,
  with the installer's own connections. Both acts happen only on an answer from
  the person's own surface.
- Specs: `universe-agent-harness` (delta), `universe-custom-agents` (delta).
