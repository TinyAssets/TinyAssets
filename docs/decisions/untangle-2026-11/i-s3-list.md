# Window bucket listings as built (2026-11-18)

The close decides whether to remove the import key from the staged bundle indexes. The wipe checks for remaining files under private namespaces. Both use the import host's signed S3 ListObjectsV2 request. On f934871 a successful HTTP response with an error document, incomplete XML or a missing continuation could stand for a completed empty list. A repeated continuation could loop.

The job now reads the S3 response as UTF-8. The page must identify the requested bucket and prefix, agree with the number of object keys and the requested page limit, and say whether another page follows. Key text and the opaque cursor use XML character-reference decoding. Repeated cursors and keys fail. A malformed page ends the step before its results can authorize cleanup or closing.

The reader accepts the unencoded S3 element form the job requests, its standard default namespace, its XML declaration and nested object metadata. An unsupported representation fails with a generic error. It uses no package and the importer keeps its existing layer boundary. A prototype general XML reader passed the protocol controls but failed the import-layer guard. The candidate here added about 1.2 KiB to the compressed job. A synthetic 1,000-object page took about 6.4 ms locally compared with 1.9 ms on main.

Local tests covered a complete two-page list, entity-encoded keys and cursors, repeated and missing cursors, incorrect counts, hidden fields, malformed XML and a cap of one key. The examples and response fields were checked against the vendor references. No real account was accessed. A representative stage response remains a live rehearsal check.

- https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html
- https://developers.cloudflare.com/r2/api/s3/api/
