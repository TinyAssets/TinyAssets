"""Literal role modes shared by runtime creators and offline startup migration.

No imports or executable initialization: the privileged migration loads this
root-owned, chain-verified declaration with runpy, never application imports.
"""

DAEMON_UID = 1001
BROKER_READ_GID = 1102
LIVENESS_DIRECTORY_MODE = 0o2750
LIVENESS_FILE_MODE = 0o640
BROKER_METADATA_FILE_MODE = 0o640
VAULT_FILE_MODE = 0o640
WORK_GID = 1100
SNAPSHOT_DIRECTORY_MODE = 0o2750
SNAPSHOT_FILE_MODE = 0o440
SIDECAR_PARENT_MODE = 0o711
SIDECAR_DIRECTORY_MODE = 0o2710
RELAY_SOCKET_MODE = 0o660
