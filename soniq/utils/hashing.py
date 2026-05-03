"""
Utilities for deterministic hashing of job arguments.

This module provides functions to create consistent, deterministic hashes
of job arguments for reliable uniqueness checking.
"""

import hashlib
import json
from typing import Any, Dict


def compute_args_hash(args_dict: Dict[str, Any]) -> str:
    """
    Compute a deterministic hash of job arguments.

    This function ensures that identical arguments always produce the same hash,
    regardless of key ordering in the dictionary. This is critical for proper
    uniqueness checking in job queues.

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
    # Sort keys to ensure deterministic ordering
    sorted_json = json.dumps(args_dict, sort_keys=True, separators=(",", ":"))

    # Use SHA-256 for strong hash with low collision probability
    return hashlib.sha256(sorted_json.encode("utf-8")).hexdigest()


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
