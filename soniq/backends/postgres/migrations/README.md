# Soniq Postgres migrations

Schema migrations for the Postgres backend. Files are named
`NNNN_short_name.sql`, applied in numeric order, and recorded in the
`soniq_migrations` table by `MigrationRunner`.

## Numbering convention

| Range       | Owner                                            |
|-------------|--------------------------------------------------|
| `0001-0099` | Soniq core (always applied by `Soniq.setup()`)   |
| `0100-8999` | Reserved for OSS plugins                         |
| `9000-9999` | Reserved for first-party commercial / soniq-pro  |

Within the core range:

| Version | File                  | Applied by      |
|---------|-----------------------|-----------------|
| `0001`  | `0001_core.sql`       | `Soniq.setup()` |
| `0002`  | `0002_dead_letter.sql`| `Soniq.setup()` |
| `0003`  | `0003_scheduler.sql`  | `Soniq.setup()` |
| `0004`  | `0004_webhooks.sql`   | `Soniq.setup()` |
| `0005`  | `0005_logs.sql`       | `Soniq.setup()` |

`Soniq.setup()` applies the `0001-0099` core slice. There is no
`--features` flag any more: 0.0.2 always creates every soniq-owned table
on first setup. Tables that the deployment never writes to stay empty
and cost ~16KB each, which we trade for a smaller mental surface
(`setup()` either ran or it didn't).

The `soniq_jobs.status` CHECK pins live values to
`queued / processing / done / cancelled`. Failures either re-queue
(status flips back to `queued`) or move into `soniq_dead_letter_jobs`;
there is no `failed` row state. See `docs/_internals/contracts/job_lifecycle.md`
and `docs/_internals/contracts/dead_letter.md` for the contracts.

### Additive core changes

Additive core changes (a new column or table that lives in the core
write path) get a new file in the next free `00NN` slot, not an edit
to `0001_core.sql`. The baseline stays the baseline.

## Plugin migrations

Plugins ship their own `NNNN_*.sql` files inside their package and
register the directory through `app.migrations.register_source(path,
prefix=...)`. The runner discovers and applies them under the same
advisory-lock guard as core. Pick a prefix in the `0100-8999` range
that does not collide with other plugins.
