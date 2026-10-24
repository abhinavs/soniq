# Stability and versioning

Soniq follows semantic versioning with the usual 0.x caveat.

## What 0.x means

The API is small and settling, but it is not frozen. Minor versions (0.1, 0.2, ...) may change it. Patch versions (0.1.1, 0.1.2, ...) only fix bugs.

## Breaking changes

- Breaking changes happen only in a minor version, never in a patch.
- Every breaking change is listed in the [changelog](https://github.com/abhinavs/soniq/blob/main/CHANGELOG.md).
- Features are deprecated first and removed in a later minor version. See [Deprecations](deprecations.md) for the current list and removal versions.

## Upgrade promise

Upgrading within 0.x never needs a code change unless the changelog lists a deprecation that has reached its removal version.

## Schema changes

Every change to the database schema is a numbered migration in `soniq/backends/postgres/migrations/`. Run `soniq setup` on deploy and it applies pending migrations. Upgrading never needs a manual SQL step. Use `soniq migrate-status` to see what is applied and what is pending.

What goes into the rows is specified separately, in [Storage format](reference/storage-format.md). Changes to it follow the same rules as the API.

## Supported versions

| Component  | Supported                                      |
| ---------- | ---------------------------------------------- |
| Python     | 3.10 and newer (CI runs 3.10 to 3.14)          |
| PostgreSQL | CI runs the integration tests on PostgreSQL 16 |

Soniq's package metadata declares `requires-python >= 3.10`. Other PostgreSQL versions may work, but only 16 is tested.

## How long old versions get fixes

Fixes go to the latest minor version only. If you hit a bug on an older minor, upgrade to the latest patch of the newest minor. Because upgrades within 0.x are meant to be small, this should be a short step.
