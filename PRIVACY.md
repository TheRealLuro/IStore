# Privacy Notice

neuthek stores user-uploaded files, generated previews, optional AI summaries,
optional tags, and optional biometric face embeddings when the user grants the
matching consent scope. Raw uploads are validated in a quarantine bucket before
promotion to permanent storage.

The backend uses bearer JWTs and localStorage in the frontend. The backend does
not intentionally set cookies; public deployments should keep the no
`Set-Cookie` test green. If cookies are added later, update this file and add a
cookie banner before release.

Uploaded images are re-decoded and metadata-stripped before served variants are
stored. GPS/EXIF-derived rows are retained only when the corresponding consent
scope is active. Face crops and embeddings are stored separately from content
objects and use the biometric encryption scope.

Users can delete single images, bulk-selected images, or their account. Image
and account deletion hard-delete content objects, served variants, thumbnails,
derived rows, face crops, face detections, orphan people, cloud-file mappings,
and user-specific bandit state. Audit rows are retained as a legal and security
record and contain references rather than file content.

Backups must be encrypted before leaving the host. Backup retention and delayed
backup invalidation are documented in `SECURITY.md` and the production runbook.
Do not use real user images, EXIF, or embeddings in tests or fixtures.
