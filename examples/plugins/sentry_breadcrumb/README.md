# soniq-sentry-breadcrumb

A reference Soniq plugin that adds a Sentry breadcrumb for every job
execution. Use it as a template for writing your own plugin.

This package exists primarily as **documentation that compiles**: it is
imported and installed in Soniq's CI to verify the public plugin
contract is sufficient. The actual integration is small (~100 LOC); the
README walks through the moving parts.

## What it shows

The plugin exercises every public extension point a real plugin uses:

| Extension point | Where in this plugin |
| --- | --- |
| `app.middleware(fn)` | wraps the handler with `_add_breadcrumb` |
| `app.before_job(fn)` | logs claims via `_log_claim` |
| `app.cli.add_command(spec)` | adds `soniq sentry-test` |
| Plugin-owned `BaseSettings` | `SentrySettings` reads `SONIQ_SENTRY_*` env |
| `install` for synchronous wiring | registers handlers, no I/O |
| `on_startup` for deferred I/O | initializes the Sentry SDK |
| `on_shutdown` for cleanup | flushes pending events |

It does **not** ship migrations or dashboard panels - those extension
points exist (`app.migrations.register_source`,
`app.dashboard.add_panel`); the example just doesn't need them. Real
plugins that need persistence or a UI panel use those APIs the same
way.

## Using it

```python
from soniq import Soniq
from sentry_breadcrumb import SentryBreadcrumbPlugin

app = Soniq(
    database_url="postgresql://...",
    plugins=[SentryBreadcrumbPlugin()],
)
await app.setup()  # plugin's on_startup runs here
```

Or via entry points (after `pip install`):

```bash
SONIQ_PLUGINS=sentry_breadcrumb SONIQ_SENTRY_DSN=https://... soniq worker
# or
soniq --plugins=sentry_breadcrumb start
```

Verify the wiring without running a job:

```bash
SONIQ_SENTRY_DSN=https://... soniq --plugins=sentry_breadcrumb sentry-test
```

## Settings

| Env var | Default | Notes |
| --- | --- | --- |
| `SONIQ_SENTRY_DSN` | unset | Sentry DSN. With no DSN the plugin runs inert - safe to install in tests / CI without a real project. |
| `SONIQ_SENTRY_ENVIRONMENT` | `production` | Passed to `sentry_sdk.init`. |
| `SONIQ_SENTRY_BREADCRUMB_CATEGORY` | `soniq.job` | Category on each breadcrumb. |

## How to publish your own plugin to PyPI

Soniq plugins are normal Python packages. The contract for shipping is
small.

1. **Pick a SemVer-stable name and version.** Plugins should pin a
   Soniq range so a major Soniq release doesn't silently break them:
   ```toml
   dependencies = ["soniq>=0.0.2,<0.1.0"]
   ```

2. **Implement `SoniqPlugin`.** A plain class with three required
   members - `name`, `version`, `install(app)` - is enough. Optional
   `on_startup(app)` / `on_shutdown(app)` async hooks fire from
   `Soniq.setup()` and `Soniq.close()`.

3. **Use Soniq's public API.** Plugins call `app.middleware`,
   `app.before_job`, `app.cli.add_command`, etc. Nothing under an
   underscore. If a feature you need can't be expressed via the public
   surface, that's a Soniq feature request, not a plugin escape hatch.

4. **Ship a zero-arg factory in entry points.** This is what makes
   `--plugins=<name>` discovery work:
   ```toml
   [project.entry-points."soniq.plugins"]
   sentry_breadcrumb = "sentry_breadcrumb:factory"
   ```
   The factory returns a fresh plugin instance. A class works too -
   classes are zero-arg callables when their `__init__` accepts none.

5. **Reserve a table prefix range** (only if your plugin owns tables).
   Use `0100`-`8999` for OSS plugins; `9000`-`9999` is reserved. Ship
   migrations under `<your_pkg>/migrations/0NNN_*.sql` and register
   the directory in `install`:
   ```python
   def install(self, app):
       from pathlib import Path
       app.migrations.register_source(
           Path(__file__).parent / "migrations",
           prefix="0100",
       )
   ```

6. **Document your settings.** Use `pydantic_settings.BaseSettings`
   with a unique `env_prefix=` (`SONIQ_<YOURPLUGIN>_`). Don't reach for
   Soniq's settings.

7. **Publish.** Standard PyPI flow:
   ```bash
   python -m build
   python -m twine upload dist/*
   ```

That's the whole pipeline.
