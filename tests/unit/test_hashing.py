"""
Test suite for utils/hashing.py module - comprehensive coverage for job argument hashing.
"""

import hashlib
import json

from soniq.utils.hashing import args_are_equivalent, compute_args_hash


class TestComputeArgsHash:
    """Test compute_args_hash function."""

    def test_basic_hash_computation(self):
        """Test basic hash computation with simple dictionary."""
        args = {"key1": "value1", "key2": "value2"}
        hash_result = compute_args_hash(args)

        # Should return a SHA-256 hex string (64 characters)
        assert isinstance(hash_result, str)
        assert len(hash_result) == 64
        assert all(c in "0123456789abcdef" for c in hash_result)

    def test_deterministic_hashing(self):
        """Test that same input always produces same hash."""
        args = {"name": "test", "count": 42}

        hash1 = compute_args_hash(args)
        hash2 = compute_args_hash(args)

        assert hash1 == hash2

    def test_key_order_independence(self):
        """Test that dictionary key order doesn't affect hash."""
        args1 = {"a": 1, "b": 2, "c": 3}
        args2 = {"c": 3, "a": 1, "b": 2}
        args3 = {"b": 2, "c": 3, "a": 1}

        hash1 = compute_args_hash(args1)
        hash2 = compute_args_hash(args2)
        hash3 = compute_args_hash(args3)

        assert hash1 == hash2 == hash3

    def test_different_values_produce_different_hashes(self):
        """Test that different values produce different hashes."""
        args1 = {"key": "value1"}
        args2 = {"key": "value2"}

        hash1 = compute_args_hash(args1)
        hash2 = compute_args_hash(args2)

        assert hash1 != hash2

    def test_different_keys_produce_different_hashes(self):
        """Test that different keys produce different hashes."""
        args1 = {"key1": "value"}
        args2 = {"key2": "value"}

        hash1 = compute_args_hash(args1)
        hash2 = compute_args_hash(args2)

        assert hash1 != hash2

    def test_empty_dictionary(self):
        """Test hashing of empty dictionary."""
        args = {}
        hash_result = compute_args_hash(args)

        assert isinstance(hash_result, str)
        assert len(hash_result) == 64

    def test_nested_dictionaries(self):
        """Test hashing of nested dictionaries."""
        args1 = {"user": {"name": "John", "age": 30}}
        args2 = {"user": {"age": 30, "name": "John"}}  # Different order

        hash1 = compute_args_hash(args1)
        hash2 = compute_args_hash(args2)

        # Should be the same due to sort_keys=True in JSON serialization
        assert hash1 == hash2

    def test_list_values(self):
        """Test hashing of dictionary with list values."""
        args1 = {"items": [1, 2, 3]}
        args2 = {"items": [1, 2, 3]}
        args3 = {"items": [3, 2, 1]}  # Different order

        hash1 = compute_args_hash(args1)
        hash2 = compute_args_hash(args2)
        hash3 = compute_args_hash(args3)

        assert hash1 == hash2
        assert hash1 != hash3  # List order matters

    def test_numeric_types(self):
        """Test hashing with different numeric types."""
        args_int = {"value": 42}
        args_float = {"value": 42.0}

        hash_int = compute_args_hash(args_int)
        hash_float = compute_args_hash(args_float)

        # Different types should produce different hashes
        assert hash_int != hash_float

    def test_boolean_values(self):
        """Test hashing with boolean values."""
        args_true = {"flag": True}
        args_false = {"flag": False}

        hash_true = compute_args_hash(args_true)
        hash_false = compute_args_hash(args_false)

        assert hash_true != hash_false

    def test_none_values(self):
        """Test hashing with None values."""
        args_none = {"value": None}
        args_empty = {"value": ""}

        hash_none = compute_args_hash(args_none)
        hash_empty = compute_args_hash(args_empty)

        assert hash_none != hash_empty

    def test_string_with_special_characters(self):
        """Test hashing with strings containing special characters."""
        args = {
            "message": "Hello, 世界! 🌍",
            "path": "/path/with spaces/file.txt",
            "json": '{"nested": "json"}',
        }

        hash_result = compute_args_hash(args)
        assert isinstance(hash_result, str)
        assert len(hash_result) == 64

    def test_large_dictionary(self):
        """Test hashing with large dictionary."""
        args = {f"key_{i}": f"value_{i}" for i in range(100)}

        hash_result = compute_args_hash(args)
        assert isinstance(hash_result, str)
        assert len(hash_result) == 64

    def test_complex_nested_structure(self):
        """Test hashing with complex nested data structure."""
        args = {
            "user": {
                "id": 123,
                "profile": {
                    "name": "John Doe",
                    "emails": ["john@example.com", "john.doe@work.com"],
                    "settings": {
                        "notifications": True,
                        "theme": "dark",
                    },
                },
            },
            "metadata": {
                "created_at": "2023-01-01T00:00:00Z",
                "tags": ["important", "user-generated"],
            },
            "options": [
                {"name": "option1", "value": True},
                {"name": "option2", "value": 42},
            ],
        }

        hash_result = compute_args_hash(args)
        assert isinstance(hash_result, str)
        assert len(hash_result) == 64

    def test_hash_matches_manual_computation(self):
        """Test that hash matches manual SHA-256 computation."""
        args = {"test": "value", "number": 123}

        # Manually compute expected hash
        sorted_json = json.dumps(args, sort_keys=True, separators=(",", ":"))
        expected_hash = hashlib.sha256(sorted_json.encode("utf-8")).hexdigest()

        actual_hash = compute_args_hash(args)

        assert actual_hash == expected_hash

    def test_json_serialization_consistency(self):
        """Test that the JSON serialization is consistent."""
        args = {"b": 2, "a": 1}

        # Verify that the function uses the expected JSON format
        sorted_json = json.dumps(args, sort_keys=True, separators=(",", ":"))
        assert sorted_json == '{"a":1,"b":2}'  # No spaces, sorted keys

        hash_result = compute_args_hash(args)
        expected_hash = hashlib.sha256(sorted_json.encode("utf-8")).hexdigest()
        assert hash_result == expected_hash

    def test_collision_resistance(self):
        """Test basic collision resistance with similar inputs."""
        # These are different inputs that could potentially collide
        test_cases = [
            {"a": "1", "b": "2"},
            {"a": 1, "b": 2},
            {"ab": "12"},
            {"a": "12"},
            {"": "ab12"},
            {"a": "", "b": "12"},
        ]

        hashes = [compute_args_hash(args) for args in test_cases]

        # All hashes should be different
        assert len(set(hashes)) == len(hashes)

    def test_unicode_handling(self):
        """Test proper handling of Unicode characters."""
        args1 = {"message": "café"}
        args2 = {"message": "cafe\u0301"}  # Same visually, different Unicode

        hash1 = compute_args_hash(args1)
        hash2 = compute_args_hash(args2)

        # These should produce different hashes as they're different Unicode sequences
        assert hash1 != hash2

    def test_edge_case_empty_strings_and_lists(self):
        """Test edge cases with empty strings and lists."""
        test_cases = [
            {"empty_string": ""},
            {"empty_list": []},
            {"empty_dict": {}},
            {"mixed": {"str": "", "list": [], "dict": {}}},
        ]

        hashes = [compute_args_hash(args) for args in test_cases]

        # All should produce valid, different hashes
        for hash_result in hashes:
            assert isinstance(hash_result, str)
            assert len(hash_result) == 64

        assert len(set(hashes)) == len(hashes)


class TestArgsAreEquivalent:
    """Test args_are_equivalent function."""

    def test_identical_dictionaries(self):
        """Test with identical dictionaries."""
        args1 = {"key": "value", "number": 42}
        args2 = {"key": "value", "number": 42}

        assert args_are_equivalent(args1, args2) is True

    def test_different_key_order(self):
        """Test with different key order."""
        args1 = {"a": 1, "b": 2, "c": 3}
        args2 = {"c": 3, "a": 1, "b": 2}

        assert args_are_equivalent(args1, args2) is True

    def test_different_values(self):
        """Test with different values."""
        args1 = {"key": "value1"}
        args2 = {"key": "value2"}

        assert args_are_equivalent(args1, args2) is False

    def test_different_keys(self):
        """Test with different keys."""
        args1 = {"key1": "value"}
        args2 = {"key2": "value"}

        assert args_are_equivalent(args1, args2) is False

    def test_subset_dictionaries(self):
        """Test with one dictionary being a subset of another."""
        args1 = {"a": 1, "b": 2}
        args2 = {"a": 1}

        assert args_are_equivalent(args1, args2) is False

    def test_empty_dictionaries(self):
        """Test with empty dictionaries."""
        args1 = {}
        args2 = {}

        assert args_are_equivalent(args1, args2) is True

    def test_one_empty_one_not(self):
        """Test with one empty and one non-empty dictionary."""
        args1 = {}
        args2 = {"key": "value"}

        assert args_are_equivalent(args1, args2) is False

    def test_nested_dictionaries_equivalent(self):
        """Test with equivalent nested dictionaries."""
        args1 = {"user": {"name": "John", "age": 30}}
        args2 = {"user": {"age": 30, "name": "John"}}

        assert args_are_equivalent(args1, args2) is True

    def test_nested_dictionaries_different(self):
        """Test with different nested dictionaries."""
        args1 = {"user": {"name": "John", "age": 30}}
        args2 = {"user": {"name": "Jane", "age": 30}}

        assert args_are_equivalent(args1, args2) is False

    def test_complex_equivalent_structures(self):
        """Test with complex but equivalent structures."""
        args1 = {
            "config": {"debug": True, "workers": 4},
            "tasks": ["task1", "task2"],
            "metadata": {"version": "1.0"},
        }
        args2 = {
            "metadata": {"version": "1.0"},
            "config": {"workers": 4, "debug": True},
            "tasks": ["task1", "task2"],
        }

        assert args_are_equivalent(args1, args2) is True

    def test_list_order_matters(self):
        """Test that list order matters in equivalence."""
        args1 = {"items": [1, 2, 3]}
        args2 = {"items": [3, 2, 1]}

        assert args_are_equivalent(args1, args2) is False

    def test_type_differences(self):
        """Test with different types for same logical value."""
        args1 = {"value": 42}
        args2 = {"value": "42"}

        assert args_are_equivalent(args1, args2) is False

    def test_boolean_vs_integer(self):
        """Test boolean vs integer equivalence."""
        args1 = {"flag": True}
        args2 = {"flag": 1}

        assert args_are_equivalent(args1, args2) is False

    def test_none_vs_empty_string(self):
        """Test None vs empty string equivalence."""
        args1 = {"value": None}
        args2 = {"value": ""}

        assert args_are_equivalent(args1, args2) is False

    def test_function_is_symmetric(self):
        """Test that the function is symmetric."""
        args1 = {"a": 1, "b": 2}
        args2 = {"b": 2, "a": 1}

        assert args_are_equivalent(args1, args2) == args_are_equivalent(args2, args1)

    def test_function_is_reflexive(self):
        """Test that the function is reflexive."""
        args = {"key": "value", "nested": {"inner": "data"}}

        assert args_are_equivalent(args, args) is True

    def test_large_dictionaries_equivalent(self):
        """Test equivalence with large dictionaries."""
        args1 = {f"key_{i}": f"value_{i}" for i in range(100)}
        args2 = {f"key_{i}": f"value_{i}" for i in reversed(range(100))}

        assert args_are_equivalent(args1, args2) is True

    def test_large_dictionaries_different(self):
        """Test non-equivalence with large dictionaries."""
        args1 = {f"key_{i}": f"value_{i}" for i in range(100)}
        args2 = {f"key_{i}": f"value_{i}" for i in range(100)}
        args2["key_50"] = "different_value"  # Change one value

        assert args_are_equivalent(args1, args2) is False


class TestHashingEdgeCases:
    """Test edge cases and boundary conditions for hashing utilities."""

    def test_very_deep_nesting(self):
        """Test with very deeply nested structures."""
        # Create a deeply nested structure
        deep_dict = {"level": 0}
        current = deep_dict
        for i in range(1, 20):
            current["nested"] = {"level": i}
            current = current["nested"]

        hash_result = compute_args_hash(deep_dict)
        assert isinstance(hash_result, str)
        assert len(hash_result) == 64

    def test_circular_reference_avoidance(self):
        """Test that we can handle structures that would cause circular references in JSON."""
        # Note: JSON serialization will fail with circular references,
        # but our args should be JSON-serializable by design
        args = {
            "self_reference": "not_circular",
            "nested": {"back": "reference"},
        }

        # This should work fine
        hash_result = compute_args_hash(args)
        assert isinstance(hash_result, str)

    def test_very_long_strings(self):
        """Test with very long string values."""
        long_string = "x" * 10000
        args = {"long_value": long_string}

        hash_result = compute_args_hash(args)
        assert isinstance(hash_result, str)
        assert len(hash_result) == 64

    def test_many_keys(self):
        """Test with dictionary containing many keys."""
        args = {f"key_{i:06d}": f"value_{i}" for i in range(1000)}

        hash_result = compute_args_hash(args)
        assert isinstance(hash_result, str)
        assert len(hash_result) == 64

    def test_special_float_values(self):
        """Test with special float values."""
        args1 = {"value": float("inf")}
        args2 = {"value": float("-inf")}
        args3 = {"value": float("nan")}

        # These should all produce valid hashes
        hash1 = compute_args_hash(args1)
        hash2 = compute_args_hash(args2)
        hash3 = compute_args_hash(args3)

        assert all(isinstance(h, str) and len(h) == 64 for h in [hash1, hash2, hash3])
        assert hash1 != hash2  # inf and -inf should be different
        # Note: NaN behavior in JSON might be special, but should still be hashable

    def test_equivalence_with_special_floats(self):
        """Test equivalence with special float values."""
        args1 = {"value": float("inf")}
        args2 = {"value": float("inf")}

        assert args_are_equivalent(args1, args2) is True

    def test_hash_consistency_across_calls(self):
        """Test that hashing is consistent across multiple calls."""
        args = {"consistent": "test", "number": 42}

        hashes = [compute_args_hash(args) for _ in range(10)]

        # All hashes should be identical
        assert all(h == hashes[0] for h in hashes)

    def test_equivalence_consistency(self):
        """Test that equivalence check is consistent across multiple calls."""
        args1 = {"a": 1, "b": 2}
        args2 = {"b": 2, "a": 1}

        results = [args_are_equivalent(args1, args2) for _ in range(10)]

        # All results should be True and identical
        assert all(r is True for r in results)


class TestHashingPerformance:
    """Test performance characteristics of hashing functions."""

    def test_reasonable_performance_large_dict(self):
        """Test that hashing large dictionaries completes in reasonable time."""
        # This is more of a smoke test than a real performance test
        large_args = {
            f"key_{i}": {"nested": f"value_{i}", "list": [i, i + 1, i + 2]}
            for i in range(1000)
        }

        # Should complete without timing out
        hash_result = compute_args_hash(large_args)
        assert isinstance(hash_result, str)
        assert len(hash_result) == 64

    def test_equivalence_performance(self):
        """Test that equivalence check performs reasonably on large dictionaries."""
        args1 = {f"key_{i}": f"value_{i}" for i in range(1000)}
        args2 = {f"key_{i}": f"value_{i}" for i in reversed(range(1000))}

        # Should complete without timing out
        result = args_are_equivalent(args1, args2)
        assert result is True
