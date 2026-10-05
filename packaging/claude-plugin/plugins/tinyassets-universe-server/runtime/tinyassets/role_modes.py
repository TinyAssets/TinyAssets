"""Literal role modes shared by runtime creators and offline startup migration.

No imports or executable initialization: the privileged migration loads this
root-owned, chain-verified declaration with runpy, never application imports.
"""

DAEMON_UID = 1001
BROKER_READ_GID = 1102
LIVENESS_DIRECTORY_MODE = 0o2750
LIVENESS_FILE_MODE = 0o640
BROKER_METADATA_FILE_MODE = 0o640
LEGACY_LIVENESS_DIRECTORY_MODE = 0o700
LEGACY_LIVENESS_FILE_MODE = 0o600
