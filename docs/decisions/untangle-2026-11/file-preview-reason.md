# Owner file preview as built (2026-11-18)

`files/read` previews the UTF-8 bytes the owner returns inline. The app
displays the route's explanation when it gets no text. Previously the route
claimed that any file returned as chunks was larger than one chunk. The
owner stores an ordinary 4,097-byte write as one chunk. A successful write
through `files/write` could therefore give that incorrect reason on the next
read. The edit checks the returned representation, with no separate size
rule or lookup. The reason now describes chunk storage. A non-UTF-8 inline
file keeps its existing explanation.

A loopback Worker test on `f934871ad` wrote through the owner app route,
opened 4,096 bytes as text, then replaced it with 4,097 bytes using the
returned etag. The failure on the second read was the explanation alone.
The regression follows the owner's shared inline boundary. Step 19 still
owns delivery of bytes to a download or an outside tool.
