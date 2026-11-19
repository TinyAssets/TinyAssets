# Sandbox sync streaming digests as built (2026-11-18)

The runner records the SHA-256 of a whole file in its SQLite mirror. A download updates it as bytes reach the local file. An upload computes it during the scan that supplies the separate chunk digests for the upload request. Both paths now begin with the contract's existing `sha256Stream()`, as C6 and O4 specified. The same Node hash consumes the same bytes in the same order. The runner already imported this module for individual chunk digests. Its two B7 exceptions can be removed.

The sync regression downloads a binary file with a short final chunk, then uploads it from a fresh mirror. It checks both stored file digests against a one-shot digest of the original bytes and checks the separate ordered chunk hashes offered to the upload door. Omitting the first byte from each update independently on either path failed its mirror assertion. Restoring both paths passed.

The Node 22 runner and probes build as a pair. Changing the runner changes both bundle digests because the probes embed the runner digest. The existing pair check therefore applies to the eventual integrated runner before deployment.
