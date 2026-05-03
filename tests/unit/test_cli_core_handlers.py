"""
Tests for shared CLI helpers in ``soniq.cli._helpers``.

The flat-CLI rewrite (S8) consolidated ``resolve_soniq_instance`` into
this one module so every subcommand resolves ``--database-url`` the
same way. The decision branches under test are:

1. No ``database_url`` attribute on args → return ``None`` (operator
   wants the global app).
2. Empty ``database_url`` → also ``None`` (an unset env var resolves
   to "" via argparse defaults in some shells).
3. A populated ``database_url`` → a fresh ``Soniq`` instance carrying
   it through to settings.
"""

import argparse

import pytest

from soniq.cli._helpers import resolve_soniq_instance


class TestResolveSoniqInstance:
    @pytest.mark.asyncio
    async def test_returns_none_without_database_url(self):
        args = argparse.Namespace()
        result = await resolve_soniq_instance(args)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_database_url_empty(self):
        args = argparse.Namespace(database_url="")
        result = await resolve_soniq_instance(args)
        assert result is None

    @pytest.mark.asyncio
    async def test_creates_instance_with_valid_url(self):
        args = argparse.Namespace(
            database_url="postgresql://user:pass@localhost/testdb"
        )
        instance = await resolve_soniq_instance(args)
        assert instance is not None
        assert (
            instance.settings.database_url == "postgresql://user:pass@localhost/testdb"
        )

    @pytest.mark.asyncio
    async def test_returns_instance_for_any_url(self):
        # Soniq accepts any URL format (validation happens at connect time)
        args = argparse.Namespace(database_url="postgresql://localhost/any")
        result = await resolve_soniq_instance(args)
        assert result is not None
