"""
Tests for dead_letter.py data structures and filter logic.

Covers: DeadLetterReason enum, DeadLetterJob.from_job_record,
DeadLetterFilter.to_sql_conditions, DeadLetterStats.
"""

from datetime import datetime, timezone

from soniq.features.dead_letter import (
    DeadLetterFilter,
    DeadLetterJob,
    DeadLetterReason,
    DeadLetterStats,
)


class TestDeadLetterReason:
    def test_enum_values(self):
        assert DeadLetterReason.MAX_RETRIES_EXCEEDED == "max_retries_exceeded"
        assert DeadLetterReason.PERMANENT_FAILURE == "permanent_failure"
        assert DeadLetterReason.JOB_NOT_FOUND == "job_not_found"
        assert DeadLetterReason.TIMEOUT == "timeout"
        assert DeadLetterReason.MANUAL_MOVE == "manual_move"

    def test_enum_is_string(self):
        assert isinstance(DeadLetterReason.MAX_RETRIES_EXCEEDED, str)


class TestDeadLetterJob:
    def test_from_job_record_with_dict_args(self):
        record = {
            "id": "job-2",
            "job_name": "my_module.my_task",
            "args": {"key": "value"},
            "queue": "default",
            "priority": 100,
            "max_attempts": 3,
            "attempts": 3,
            "last_error": "boom",
            "created_at": datetime.now(timezone.utc),
        }
        job = DeadLetterJob.from_job_record(record, DeadLetterReason.PERMANENT_FAILURE)
        assert job.args == {"key": "value"}

    def test_dataclass_fields(self):
        now = datetime.now(timezone.utc)
        job = DeadLetterJob(
            id="j1",
            job_name="mod.task",
            args={},
            queue="default",
            priority=100,
            max_attempts=3,
            attempts=3,
            last_error="err",
            dead_letter_reason="max_retries_exceeded",
            original_created_at=now,
            moved_to_dead_letter_at=now,
            resurrection_count=1,
            tags={"env": "prod"},
        )
        assert job.resurrection_count == 1
        assert job.tags == {"env": "prod"}


class TestDeadLetterFilter:
    def test_empty_filter(self):
        f = DeadLetterFilter()
        conditions, params = f.to_sql_conditions()
        assert conditions == []
        assert params == []

    def test_filter_by_job_names(self):
        f = DeadLetterFilter()
        f.job_names = ["my_module.my_task"]
        conditions, params = f.to_sql_conditions()
        assert len(conditions) >= 1
        assert "my_module.my_task" in str(params)

    def test_filter_by_queues(self):
        f = DeadLetterFilter()
        f.queues = ["emails"]
        conditions, params = f.to_sql_conditions()
        assert len(conditions) >= 1

    def test_filter_by_reasons(self):
        f = DeadLetterFilter()
        f.reasons = ["timeout"]
        conditions, params = f.to_sql_conditions()
        assert len(conditions) >= 1

    def test_filter_by_date_range(self):
        f = DeadLetterFilter()
        f.date_from = datetime(2025, 1, 1, tzinfo=timezone.utc)
        f.date_to = datetime(2025, 12, 31, tzinfo=timezone.utc)
        conditions, params = f.to_sql_conditions()
        assert len(conditions) >= 2

    def test_multiple_filters(self):
        f = DeadLetterFilter()
        f.job_names = ["task"]
        f.queues = ["default"]
        f.reasons = ["timeout"]
        conditions, params = f.to_sql_conditions()
        assert len(conditions) >= 3


class TestDeadLetterStats:
    def test_stats_dataclass(self):
        stats = DeadLetterStats(
            total_count=100,
            by_job_name={"mod.task": 100},
            by_queue={"default": 60, "emails": 40},
            by_reason={"max_retries_exceeded": 80, "timeout": 20},
            by_date={"2025-01": 100},
            oldest_job_age_hours=48.0,
            resurrection_success_rate=0.75,
        )
        assert stats.total_count == 100
        assert stats.by_reason["timeout"] == 20
        assert stats.resurrection_success_rate == 0.75
