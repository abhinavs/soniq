"""
Tests for _rows_affected() — verifies the shared helper and all call sites.

This test was written to catch the infinite recursion bug in dead_letter.py
where _rows_affected called itself instead of parsing the result string.
"""


class TestRowsAffectedDeadLetter:
    """Test _rows_affected from dead_letter.py specifically — the broken copy."""

    def test_delete_5(self):
        from soniq.features.dead_letter import _rows_affected

        assert _rows_affected("DELETE 5") == 5

    def test_update_0(self):
        from soniq.features.dead_letter import _rows_affected

        assert _rows_affected("UPDATE 0") == 0

    def test_insert_1(self):
        from soniq.features.dead_letter import _rows_affected

        assert _rows_affected("INSERT 0 1") == 1

    def test_empty_string(self):
        from soniq.features.dead_letter import _rows_affected

        assert _rows_affected("") == 0

    def test_nonsense(self):
        from soniq.features.dead_letter import _rows_affected

        assert _rows_affected("NONSENSE") == 0

    def test_no_recursion_error(self):
        """The original bug: _rows_affected called itself, causing RecursionError."""
        from soniq.features.dead_letter import _rows_affected

        # Should not raise RecursionError
        result = _rows_affected("DELETE 3")
        assert result == 3


class TestRowsAffectedSharedImport:
    """Verify the shared helper in db/helpers.py works."""

    def test_helpers_rows_affected(self):
        from soniq.backends.helpers import rows_affected

        assert rows_affected("DELETE 5") == 5
        assert rows_affected("UPDATE 0") == 0
        assert rows_affected("") == 0


class TestRowsAffectedSharedHelper:
    """Test the shared helper after deduplication."""

    def test_shared_helper_exists(self):
        from soniq.backends.helpers import rows_affected

        assert rows_affected("DELETE 5") == 5

    def test_shared_helper_update(self):
        from soniq.backends.helpers import rows_affected

        assert rows_affected("UPDATE 10") == 10

    def test_shared_helper_empty(self):
        from soniq.backends.helpers import rows_affected

        assert rows_affected("") == 0

    def test_shared_helper_nonsense(self):
        from soniq.backends.helpers import rows_affected

        assert rows_affected("NONSENSE") == 0

    def test_shared_helper_insert(self):
        from soniq.backends.helpers import rows_affected

        assert rows_affected("INSERT 0 1") == 1
