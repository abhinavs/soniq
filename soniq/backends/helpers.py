"""Backend helper utilities."""


def rows_affected(result: str) -> int:
    """Extract the number of affected rows from an asyncpg status string like 'UPDATE 3'."""
    try:
        return int(result.split()[-1])
    except (ValueError, IndexError):
        return 0
