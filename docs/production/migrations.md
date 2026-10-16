# Migrations with Alembic

`soniq setup` applies the Soniq schema. If your team manages the database
schema with Alembic, you can export the Soniq schema as an Alembic revision
instead. Alembic then owns every schema change, and `soniq setup` has
nothing left to do.

## Export a revision

```bash
soniq migrations export --alembic --down-revision <rev> \
  > migrations/versions/xxxx_soniq_0005.py
```

Replace `<rev>` with your current Alembic head. Omit the option if this is
your first revision.

The command needs no database connection. The generated file:

- runs each Soniq SQL migration with `op.execute()`.
- creates the `soniq_migrations` table if it is missing.
- writes one row per migration to `soniq_migrations`.

After `alembic upgrade head`, `soniq setup` reports that the schema is up
to date. `soniq migrate-status` shows no pending migrations.

The revision id is `soniq_<last version>`. Use `--revision <id>` to set a
different id.

The generated file does not import Soniq. It needs only Alembic.

## Upgrade Soniq

A new Soniq release can add migrations. Export only the new ones:

```bash
soniq migrations export --alembic --from 0005 --down-revision <rev> \
  > migrations/versions/yyyy_soniq_upgrade.py
```

`--from 0005` means "I already have 0005". The file contains only newer
migrations.

## Export raw SQL

Teams with other tools can print plain SQL:

```bash
soniq migrations export --sql > soniq_schema.sql
```

`--from` works here too. This output does not write to `soniq_migrations`.
If you also run `soniq setup`, it applies the migrations again.

## Limits

- The export covers the core migrations only. Plugin migrations are not
  included.
- The revision has no downgrade. `downgrade()` raises an error. Drop the
  `soniq_*` tables by hand if you must remove Soniq.
- Do not mix tools on one database. Use Alembic or `soniq setup`, not both.
