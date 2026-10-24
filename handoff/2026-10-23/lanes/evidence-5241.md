Approval of the C12 / S17 knowledge-as-files spec, spec only.
- Cross-family review: DISAGREE_EVIDENCE (HIGH: S17 guard was prose; MEDIUM: T10 publish copy missing, conversion could drop data, slices did not name deletions; LOW: export doors, import format, over-build).
- Fix 1e90de31ff7, checked by the lead: exact (glob, regex) pairs and zero ceiling for check_shapes.py S17 installed at cutover (design.md:30, tasks 4.3); BOX_PUBLISH copies selected public knowledge at publish time; one loss-refusing owner conversion; named deletion targets per slice; every export door's disposition; import through the export manifest.
- Validation: strict OpenSpec, 26 targeted Linux oracle tests, ruff.
