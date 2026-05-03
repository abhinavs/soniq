# Authoring a Soniq plugin

A Soniq plugin is a regular Python package that satisfies the
`SoniqPlugin` Protocol. The contract is small on purpose: plugins
compose by calling Soniq's **public** API, the same surface a user
sees. There is no special private door for plugins; if you need
something the public surface can't express, that's a feature request
against Soniq core, not a private hook.

## The contract

```python
from soniq.plugin import SoniqPlugin

class MyPlugin:
    name = "my-plugin"
    version = "0.1.0"

    def install(self, app):
        # synchronous wiring - no I/O
        ...

    # optional, both async, both fire from the lifecycle:
    async def on_startup(self, app): ...
    async def on_shutdown(self, app): ...
```

Three required members: `name`, `version` (SemVer string), and
`install(app)`. Two optional async hooks the runner discovers via
`hasattr`:

- `on_startup(app)` runs from `Soniq.setup()` after migrations and
  backend init. Failures **propagate** - a misconfigured plugin must
  not boot silently.
- `on_shutdown(app)` runs from `Soniq.close()` in reverse install
  order. Failures are logged and swallowed - shutdown is best-effort.

`isinstance(MyPlugin(), SoniqPlugin)` works at runtime; the Protocol
is `@runtime_checkable`.

## Public extension points

Inside `install(app)`, plugins call:

| API | Use case |
| --- | --- |
| `app.middleware(fn)` | wrap every job (tracing, auth, ContextVar) |
| `app.before_job(fn)` / `app.after_job(fn)` / `app.on_error(fn)` | observe lifecycle events |
| `app.job(name=..., ...)` | register handlers |
| `app.scheduler.add(...)` | schedule recurring work |
| `app.metrics_sink = sink` | replace metrics destination |
| `app.cli.add_command(spec)` | add a `soniq <name>` subcommand |
| `app.dashboard.add_panel(spec)` | add a panel to the dashboard |
| `app.migrations.register_source(path, prefix=...)` | ship plugin-owned tables |

Plugins **must not** touch underscore-prefixed attributes on `Soniq`,
`StorageBackend`, or any other class. Soniq's CI lints first-party
features under the same rule (`scripts/lint_features_public_api.py`)
so there's no daylight between what's allowed inside the codebase and
what's allowed for a plugin author.

## Install paths

There are three ways to install a plugin, with a deliberate ergonomic
gradient:

```python
# 1. Constructor (preferred)
app = Soniq(database_url=..., plugins=[MyPlugin()])

# 2. app.use() - convenient post-construct
app = Soniq(database_url=...)
app.use(MyPlugin())

# 3. Entry-points - opt-in, never automatic
app = Soniq(database_url=..., autoload_plugins=True)
# ...or via the CLI:
#   soniq --plugins=my_plugin start
# ...or via env:
#   SONIQ_PLUGINS=my_plugin soniq worker
```

Entry-point group: `soniq.plugins`. Each entry resolves to a zero-arg
callable returning a plugin instance.

```toml
# Your plugin's pyproject.toml
[project.entry-points."soniq.plugins"]
my_plugin = "my_plugin:factory"
```

Discovery is **opt-in** by every avenue. Importing `soniq` never
auto-loads anything; `Soniq()` with no flags installs no plugins.
Side-effect imports break testability and surprise operators, so the
default stays inert.

## Idempotence and duplicates

`app.use(plugin)` raises `SoniqError(SONIQ_PLUGIN_DUPLICATE)` if a
plugin with the same `name` is already installed. Two deployments
accidentally pulling the same plugin twice fail loud rather than
double-registering hooks. To intentionally replace, construct a fresh
`Soniq` instead.

## Settings

Plugins manage their own settings the same way Soniq does - via
`pydantic_settings.BaseSettings` with a unique `env_prefix=`:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class MyPluginSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SONIQ_MY_PLUGIN_")
    api_key: str
    region: str = "us-east"
```

Don't push knobs into `SoniqSettings`. The plugin owns its
configuration; Soniq just hosts the lifecycle.

## Migrations

Plugins that need persistence ship their own migrations:

```
my_plugin/
├── __init__.py
└── migrations/
    ├── 001_endpoints.sql
    └── 002_add_signed_at.sql
```

Register the directory in `install`:

```python
from pathlib import Path

class MyPlugin:
    name = "my-plugin"
    version = "0.1.0"

    def install(self, app):
        app.migrations.register_source(
            Path(__file__).parent / "migrations",
            prefix="0100",  # see "Table-prefix conventions" below
        )
```

Plugin migrations apply during `app.setup()` under the same advisory
lock as core, so two deploy nodes calling `setup` at once don't race.
Versions on disk (`001_*.sql`, `002_*.sql`) are concatenated with the
prefix to produce the recorded version (`0100001`, `0100002`), which
keeps your plugin's range contiguous and distinct from core.

### Table-prefix conventions

| Range | Reserved for |
| --- | --- |
| `0001`-`0099` | Soniq core migrations |
| `0100`-`8999` | OSS plugins (community / first-party-but-optional) |
| `9000`-`9999` | Reserved for `soniq-pro` and other vendor packages |

Use `soniq_<plugin-name>_*` as your table-name prefix to avoid
collisions: `soniq_stripe_events`, `soniq_sentry_breadcrumbs`. Soniq
doesn't enforce this at runtime; the convention exists so two plugins
in the wild can't accidentally claim the same table.

## CLI subcommands

```python
from soniq.plugin import CommandSpec

class MyPlugin:
    name = "my-plugin"
    version = "0.1.0"

    def install(self, app):
        app.cli.add_command(
            CommandSpec(
                name="my-plugin-status",
                help="Show MyPlugin status",
                handler=self._status,
                arguments=[
                    {"args": ["--verbose"], "kwargs": {"action": "store_true"}},
                ],
            )
        )

    def _status(self, args):
        if args.verbose:
            ...
        return 0
```

`handler` may be sync (returns `int`) or async (returns
`Awaitable[int]`). The CLI dispatcher runs whichever shape the
function has.

The CLI parser starts before any `Soniq` instance exists in the user's
program. To make plugin commands available, the parser builder
constructs a bootstrap `Soniq`, runs `discover_plugins(...)`, calls
their `install()` (which registers the specs), and only then folds the
specs into argparse. This happens **only** when the operator opts in
via `--plugins=...` / `SONIQ_PLUGINS=...`. A bare `soniq --help` does
no entry-point work.

## Dashboard panels

```python
from soniq.plugin import PanelSpec

async def render_panel(app):
    return {"queue_depth": await app.get_queue_stats()}

class MyPlugin:
    def install(self, app):
        app.dashboard.add_panel(
            PanelSpec(id="my-plugin", title="My Plugin", render=render_panel)
        )
```

Panels render lazily: the dashboard server exposes `/api/panels` (a
list) and `/api/panels/{id}` (the rendered content). The UI only
fetches a panel when the user opens it, so a slow plugin never blocks
the rest of the dashboard.

## Version pinning

Plugins should pin a Soniq version range so a major Soniq release
doesn't silently break them:

```toml
dependencies = ["soniq>=0.0.2,<0.1.0"]
```

The plugin contract (`install`, `on_startup`, `on_shutdown`) is part
of Soniq's public-API stability promise. Breaking it requires a major
bump.

## Stability and breakage

- Adding a new public extension point is non-breaking.
- Changing or removing one is breaking.
- Reaching for a private (`_`-prefixed) name in a plugin gets no
  support guarantee. We will rename or remove those at will; that's
  why they're underscored.

## Reference

A working example plugin lives at
[`examples/plugins/sentry_breadcrumb/`](https://github.com/abhinavs/soniq/tree/main/examples/plugins/sentry_breadcrumb).
It exercises every extension point in this guide and is imported and
installed in CI as a smoke test, so the patterns above stay live with
the plugin contract.
