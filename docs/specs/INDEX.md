# Legacy Specs Index

`docs/specs/` is a historical and provenance collection. It is not the current
specification system.

- As-built behavioral requirements live in [`openspec/specs/`](../../openspec/specs/).
- In-flight target requirements live in [`openspec/changes/`](../../openspec/changes/).
- Architecture lives in [`docs/architecture.md`](../architecture.md); design
  decisions in [`docs/decisions/`](../decisions/).
- The exact disposition of all 52 Markdown files in this directory is recorded
  in the [2026-07-22 legacy-spec disposition audit](../audits/2026-07-22-legacy-spec-disposition.md).

Embedded `status:` fields and phrases such as “current,” “active,” “shipped,”
or “executable” record what a file claimed when written. They do not override
the current OpenSpec/ADR authority split. A historical idea can become active
again only through a new OpenSpec change.

## Disposition summary

| Disposition | Count | Meaning |
|---|---:|---|
| CANONICAL | 18 | Surviving shipped substance has a canonical OpenSpec owner. |
| ACTIVE | 0 | No legacy file itself is the owner of an active OpenSpec target. |
| CLAIMED | 18 | Residue is assigned to a live STATUS backfill, host-decision, or target lane. |
| HISTORY | 16 | Superseded, research, fixture, exemplar, fragment, or parked input. |
| **Total** | **52** | Includes this index. |

Use the disposition audit—not this directory's old frontmatter—to decide
whether a legacy file may inform current work.
