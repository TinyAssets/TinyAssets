"""Tier-3 OSS install smoke tests.

Fast tests that verify the project boots cleanly on a fresh clone +
``pip install -e .``. Distinct from the main ``tests/`` suite — smoke
only asserts "does it import and do basic plumbing" not "does a feature
work correctly."

Invoked by ``.github/workflows/tier3-oss-clone-nightly.yml`` after a
fresh clone. Also safe to run locally: ``pytest tests/smoke/ -q``.

Per ``2026-04-19-tier3-oss-clone-nightly-gha.md (archived in git)`` §6.
"""
