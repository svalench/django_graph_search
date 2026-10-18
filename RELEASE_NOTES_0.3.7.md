# django-graph-search 0.3.7

Security and reliability maintenance release. Upgrade to 0.3.7 and rebuild
existing indexes after deploying the update.

- Consistent password-field exclusion from search content and result data.
- Authenticated conversation histories are isolated per user, including deletion.
- Search POST/DELETE views respect Django CSRF middleware. When enabled, all
  clients (including token/custom-permission integrations) must supply a valid
  CSRF cookie and token. Keep CsrfViewMiddleware enabled for session authentication.
- Fail closed for unusable configured permission/throttle entries.
- Invalid JSON payload shapes receive HTTP 400; backend error details remain in logs.
- pgvector model-filtered searches now bind SQL parameters in the correct order.

Existing conversation history starts fresh after upgrading. Anonymous
conversation IDs remain bearer identifiers; require authentication for private
history. Regression tests cover the corrected behavior.
