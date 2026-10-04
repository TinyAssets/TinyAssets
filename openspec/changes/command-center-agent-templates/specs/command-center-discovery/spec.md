## ADDED Requirements

### Requirement: Copied agent references resolve only to declared recipient templates
Command-center copy SHALL validate all declared agent references and supported workflow dependencies before any component is installed, remap UI agent aliases to fresh recipient bindings, and disclose when no public agent templates were included.

#### Scenario: A public screen declares an agent alias
- **WHEN** its declared alias names an included fingerprinted public instruction template
- **THEN** the installed alias identifies the recipient binding rather than a publisher binding
- **AND** source scripts remain verbatim

#### Scenario: A source has unresolved or unsupported dependencies
- **WHEN** an agent alias is missing, its definition fingerprint changed, or an included workflow invokes another branch through an unsupported nested dependency
- **THEN** copy fails before workflows, screens, bindings, files or automations are created
- **AND** the platform never reads private publisher bindings to fill the gap
