"""Tests for the plugin dashboard panel extension point.

The dashboard panel API is intentionally narrow:

- ``app.dashboard.add_panel(spec)`` registers a ``PanelSpec``.
- Panel ids must be unique within the application; collisions raise at
  registration so the dashboard never tries to render two panels with
  the same DOM id.
- The HTTP layer (``/api/panels``, ``/api/panels/{id}``) is covered by
  the dashboard's own tests when FastAPI is installed; here we keep
  the unit tests Protocol-shape-only so they run without optional deps.
"""

from __future__ import annotations

import pytest

from soniq import Soniq
from soniq.plugin import PanelSpec


async def _stub_render(app):
    return {"status": "ok"}


def test_add_panel_registers_spec():
    app = Soniq(backend="memory")
    spec = PanelSpec(id="sample", title="Sample", render=_stub_render)
    app.dashboard.add_panel(spec)
    assert app.dashboard._panels == [spec]


def test_duplicate_panel_id_raises():
    app = Soniq(backend="memory")
    app.dashboard.add_panel(PanelSpec("a", "A", _stub_render))
    with pytest.raises(ValueError):
        app.dashboard.add_panel(PanelSpec("a", "A again", _stub_render))


def test_distinct_ids_coexist():
    app = Soniq(backend="memory")
    app.dashboard.add_panel(PanelSpec("a", "A", _stub_render))
    app.dashboard.add_panel(PanelSpec("b", "B", _stub_render))
    assert [p.id for p in app.dashboard._panels] == ["a", "b"]


@pytest.mark.asyncio
async def test_panel_render_callable_is_awaited():
    """Verify the spec's ``render`` is a real awaitable that resolves
    when called - the dashboard server awaits it directly."""
    app = Soniq(backend="memory")

    async def render(_app):
        return "<div>hello</div>"

    app.dashboard.add_panel(PanelSpec("greet", "Greet", render))
    out = await app.dashboard._panels[0].render(app)
    assert out == "<div>hello</div>"
