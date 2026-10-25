"""
Utilities for deterministic hashing of job arguments.

This module provides functions to create consistent, deterministic hashes
of job arguments for reliable uniqueness checking. The database function
soniq_args_hash() computes the same hash, so a job enqueued from SQL and
one enqueued from Python dedup against each other.
"""

import hashlib
import json
from decimal import Decimal
from typing import Any, Dict

from ..errors import SONIQ_TASK_ARGS_INVALID, SoniqError

# soniq_enqueue() rejects unique args this deep too: hashing them in SQL
# recurses once per level and runs out of stack past about a thousand.
MAX_UNIQUE_ARGS_DEPTH = 500


def _float(f: float) -> str:
    # Print the float the way Postgres prints it back from jsonb, which is
    # what soniq_args_hash() sees: no exponent and no negative zero.
    if f == 0:
        return "0.0"
    text = repr(f)
    return format(Decimal(text), "f") if "e" in text else text


def _key(k: Any) -> str:
    # The text json.dumps gives a non-string key, so {10: x} hashes like {"10": x}.
    return k if isinstance(k, str) else json.dumps(k)


def _canonical(value: Any, depth: int) -> str:
    if depth >= MAX_UNIQUE_ARGS_DEPTH:
        raise SoniqError(
            f"Arguments of a unique job nest {MAX_UNIQUE_ARGS_DEPTH} or more levels deep",
            SONIQ_TASK_ARGS_INVALID,
        )
    if isinstance(value, str) or value is None or isinstance(value, bool):
        return json.dumps(value)
    if isinstance(value, int):
        return int.__repr__(value)
    if isinstance(value, float):
        return _float(value)
    # Plain loops, not generator expressions: one stack frame per level.
    parts = []
    if isinstance(value, dict):
        # Later keys win, as in jsonb, when two keys stringify the same.
        items = {_key(k): v for k, v in value.items()}
        for k in sorted(items):
            parts.append(json.dumps(k) + ":" + _canonical(items[k], depth + 1))
        return "{" + ",".join(parts) + "}"
    if isinstance(value, (list, tuple)):
        for v in value:
            parts.append(_canonical(v, depth + 1))
        return "[" + ",".join(parts) + "]"
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def compute_args_hash(args_dict: Dict[str, Any]) -> str:
    """
    Compute a deterministic hash of job arguments.

    This function ensures that identical arguments always produce the same hash,
    regardless of key ordering in the dictionary. This is critical for proper
    uniqueness checking in job queues.

    The text hashed is ``json.dumps(args, sort_keys=True, separators=(",", ":"))``,
    except that floats print as Postgres stores them (``1e16`` as
    ``10000000000000000``) and non-string keys are turned into strings before
    sorting. See docs/reference/storage-format.md.

    Args:
        args_dict: Dictionary of job arguments

    Returns:
        str: SHA-256 hash of the deterministically serialized arguments

    Examples:
        >>> compute_args_hash({"a": 1, "b": 2})
        'abc123...'
        >>> compute_args_hash({"b": 2, "a": 1})  # Same hash!
        'abc123...'
    """
    canonical = _canonical(args_dict, 0)

    # Use SHA-256 for strong hash with low collision probability
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def args_are_equivalent(args1: Dict[str, Any], args2: Dict[str, Any]) -> bool:
    """
    Check if two argument dictionaries are equivalent for uniqueness purposes.

    Args:
        args1: First argument dictionary
        args2: Second argument dictionary

    Returns:
        bool: True if arguments produce the same hash
    """
    return compute_args_hash(args1) == compute_args_hash(args2)
