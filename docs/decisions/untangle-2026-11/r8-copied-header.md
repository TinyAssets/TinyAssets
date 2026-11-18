# R8 copied-header discovery as built (2026-11-18)

The off-account copy holds the Files Worker's object bytes. A namespace and generation
ID do not identify an owner. The owner is in the export header, the first line of base
part zero. The Worker seals each part under the namespace's data key and binds the
ciphertext to its object key. The generation root carries the wrapped data key and
authenticates the part list, hashes and generation time. This protection travels with
the bytes when they leave R2, independently of the bucket provider's storage encryption.

The files test creates two finished generations in a workerd bucket and copies both
roots and only the newest generation's first part into a separate workerd bucket. It
removes the original objects and runs the existing `FilesLocate.locate` against a
read-only view of the copy. With a generated keyring inside the Worker, it identifies
the owner and generation by the authenticated time even when the older generation
sorts last. Every root must open. A copy from before rotation's sweep requires the
retained old kid. The same copied part opens after retirement when the later copy of
the rewrapped root replaces the old one. Missing or damaged newest parts refuse
discovery. A mark returns `erased` before any root is listed or read.

This is a local prerequisite for `r8-lost-copy`. Copying the bytes requires no
FILES_KEYRING or plaintext on the host. A targeted restore still needs a private way
for the Files Worker to read eligible copied objects, check which ones belong to the
requested owner, and honor the existing erasure and hold rules before restoring.
The live `restore.locate` operator call is not a read-only preview: the doors erase a
located namespace when the directory marks its owner erased. This fixture uses no
live bucket, queue or owner.
