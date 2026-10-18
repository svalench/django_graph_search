# Security notes

Keep Django authentication and CSRF middleware enabled when using session
credentials. Search endpoints are public unless GRAPH_SEARCH.API permissions
are configured. Anonymous conversation IDs are bearer identifiers; require
authentication for private history. Only index fields suitable for the audience
allowed to search them, including related-object fields.

FAISS persistence loads Python pickle data. Its configured local path must not
be writable by untrusted parties. This is a documented trust boundary, not a
safe format for uploaded or externally supplied indexes.

## Dependency audit assessment (2026-10-18)

The latest resolved ChromaDB 1.5.9 has four unique published server-side
advisories, with no patched release listed at this assessment:

- [CVE-2026-45829](https://github.com/advisories/GHSA-f4j7-r4q5-qw2c)
- [CVE-2026-45833](https://github.com/advisories/GHSA-36p7-vc44-83pf)
- [CVE-2026-45831](https://github.com/advisories/GHSA-xph7-9rjv-w5fr)
- [CVE-2026-45830](https://github.com/advisories/GHSA-2wm9-hf6c-p5cr)

They concern Chroma HTTP server collection operations and server authorization.
The documented local configuration and CI integration tests use in-process
`chromadb.Client` / `PersistentClient`; this package does not expose a Chroma
HTTP server, accept collection/embedding-function configuration from HTTP users,
or rely on Chroma server RBAC for Django API authorization.

CI requires a passing audit of the base runtime dependencies. The separate
optional-integration audit preserves its raw findings as an artifact, including
these unresolved advisories, and is explicitly non-blocking. This does **not**
mean that the optional dependencies are vulnerability-free or patched.

Deployments that run a Chroma HTTP server, import untrusted collection data, or
supply custom client settings must assess these advisories separately. Keep
local collection data and client configuration trusted. The local-client
assessment is not a proof that every possible Chroma configuration is safe.
Do not expose the affected Chroma server to untrusted clients; use a different
backend if that deployment is required. Reassess when upstream releases a fix
or client usage changes.
