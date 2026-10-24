# Deprecations

This page lists every deprecated Soniq feature and the plan to remove it.

## Policy

- A deprecated feature keeps working for at least one minor version.
- A deprecated feature is removed only in a later minor version.
- Every deprecation and every removal appears in the changelog.
- The replacement is named in the warning and in the table below.

## What you see

Soniq emits a `SoniqDeprecationWarning`. It is a subclass of `DeprecationWarning`.
Python hides `DeprecationWarning` by default outside `__main__`. For this reason,
Soniq also writes each message once to the `soniq` logger at WARNING level.

Set the `SONIQ_DEPRECATIONS` environment variable to change this behavior:

| Value    | Effect                                          |
| -------- | ----------------------------------------------- |
| `error`  | Raise `SoniqDeprecationWarning`. Use in tests.  |
| `ignore` | Do not warn and do not log.                     |
| unset    | Warn and log once.                              |

## Deprecated features

| Name | Deprecated in | Removed in | Use instead |
| ---- | ------------- | ---------- | ----------- |
| Job names derived from the module path (`@soniq.job()` with no `name=`) | 0.2.0 | 1.0 | `@soniq.job(name="...")`, set to the name Soniq derived so queued jobs still match. See [Storage format](reference/storage-format.md#job-names). |
