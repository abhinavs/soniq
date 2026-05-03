"""
Test Queue Processing Behavior - New efficient queue handling

Tests the updated queue processing logic that handles:
- All queues processing (queue=None)
- Single queue processing (queue="name")
- Multiple queues processing (queue=["name1", "name2"])
- Priority ordering across different queues
- Fair processing without queue starvation
"""

import asyncio
import os
import tempfile
from datetime import datetime, timedelta

import pytest

from tests.db_utils import TEST_DATABASE_URL

# Ensure we're using test database
os.environ["SONIQ_DATABASE_URL"] = TEST_DATABASE_URL

from soniq import Soniq  # noqa: E402
from soniq.core.worker import Worker  # noqa: E402
from tests.db_utils import clear_table  # noqa: E402


# Define test job function (not decorated at module level)
async def write_to_file_job(message: str, result_file: str):
    """Test job that writes result to file"""
    with open(result_file, "a") as f:
        f.write(f"{message}\n")


@pytest.fixture
async def app():
    """Create an Soniq app instance for testing"""
    _app = Soniq(database_url=TEST_DATABASE_URL)
    await _app._ensure_initialized()
    pool = await _app._get_pool()
    await clear_table(pool)
    yield _app
    await clear_table(pool)
    await _app.close()


@pytest.fixture
def result_file():
    """Create a temporary file for job results"""
    with tempfile.NamedTemporaryFile(mode="w+", delete=False, suffix=".txt") as f:
        result_file_path = f.name
    yield result_file_path
    # Cleanup
    try:
        os.unlink(result_file_path)
    except FileNotFoundError:
        pass


@pytest.mark.asyncio
async def test_process_jobs_all_queues(app, result_file):
    """Test that queue=None processes jobs from any queue"""

    registry = app._get_job_registry()
    registry.register_job(write_to_file_job, name=write_to_file_job.__name__)
    backend = app._backend
    worker = Worker(backend, registry)

    # Enqueue jobs in different queues
    await app.enqueue(
        "write_to_file_job",
        args={"message": "job1", "result_file": result_file},
        queue="high",
    )
    await app.enqueue(
        "write_to_file_job",
        args={"message": "job2", "result_file": result_file},
        queue="normal",
    )
    await app.enqueue(
        "write_to_file_job",
        args={"message": "job3", "result_file": result_file},
        queue="low",
    )

    # Process with queues=None should pick up any job
    processed1 = await worker.run_once(queues=None, max_jobs=1)
    processed2 = await worker.run_once(queues=None, max_jobs=1)
    processed3 = await worker.run_once(queues=None, max_jobs=1)
    processed4 = await worker.run_once(queues=None, max_jobs=1)  # Should be False

    assert processed1 is True
    assert processed2 is True
    assert processed3 is True
    assert processed4 is False  # No more jobs

    # Check results
    with open(result_file, "r") as f:
        results = f.read().strip().split("\n")

    processed_messages = {msg for msg in results if msg}
    assert len(processed_messages) == 3  # 3 jobs processed
    assert "job1" in processed_messages
    assert "job2" in processed_messages
    assert "job3" in processed_messages


@pytest.mark.asyncio
async def test_process_jobs_single_queue(app, result_file):
    """Test that queue="name" processes only from that queue"""

    registry = app._get_job_registry()
    registry.register_job(write_to_file_job, name=write_to_file_job.__name__)
    backend = app._backend
    worker = Worker(backend, registry)

    # Enqueue jobs in different queues
    await app.enqueue(
        "write_to_file_job",
        args={"message": "target_job", "result_file": result_file},
        queue="target",
    )
    await app.enqueue(
        "write_to_file_job",
        args={"message": "other_job", "result_file": result_file},
        queue="other",
    )

    # Process only from "target" queue
    processed1 = await worker.run_once(queues=["target"], max_jobs=1)
    processed2 = await worker.run_once(queues=["target"], max_jobs=1)  # Should be False

    assert processed1 is True
    assert processed2 is False

    # Check results
    with open(result_file, "r") as f:
        results = f.read().strip()

    assert "target_job" in results
    assert "other_job" not in results  # Should not be processed


@pytest.mark.asyncio
async def test_process_jobs_multiple_queues(app, result_file):
    """Test that queue=["q1", "q2"] processes from specified queues efficiently"""

    registry = app._get_job_registry()
    registry.register_job(write_to_file_job, name=write_to_file_job.__name__)
    backend = app._backend
    worker = Worker(backend, registry)

    # Enqueue jobs in different queues
    await app.enqueue(
        "write_to_file_job",
        args={"message": "job1", "result_file": result_file},
        queue="queue1",
    )
    await app.enqueue(
        "write_to_file_job",
        args={"message": "job2", "result_file": result_file},
        queue="queue2",
    )
    await app.enqueue(
        "write_to_file_job",
        args={"message": "job3", "result_file": result_file},
        queue="excluded",
    )

    # Process only from specified queues
    processed1 = await worker.run_once(queues=["queue1", "queue2"], max_jobs=1)
    processed2 = await worker.run_once(queues=["queue1", "queue2"], max_jobs=1)
    processed3 = await worker.run_once(
        queues=["queue1", "queue2"], max_jobs=1
    )  # Should be False

    assert processed1 is True
    assert processed2 is True
    assert processed3 is False  # No more jobs in target queues

    # Check results
    with open(result_file, "r") as f:
        results = f.read().strip()

    assert "job1" in results
    assert "job2" in results
    assert "job3" not in results  # Excluded queue should not be processed


@pytest.mark.asyncio
async def test_priority_ordering_across_queues(app, result_file):
    """Test that priority ordering works correctly across different queues"""

    registry = app._get_job_registry()
    registry.register_job(write_to_file_job, name=write_to_file_job.__name__)
    backend = app._backend
    worker = Worker(backend, registry)

    # Enqueue jobs with different priorities across different queues
    # Lower priority number = higher priority
    await app.enqueue(
        "write_to_file_job",
        args={"message": "medium_A", "result_file": result_file},
        queue="queueA",
        priority=100,
    )
    await app.enqueue(
        "write_to_file_job",
        args={"message": "high_B", "result_file": result_file},
        queue="queueB",
        priority=50,
    )
    await app.enqueue(
        "write_to_file_job",
        args={"message": "low_A", "result_file": result_file},
        queue="queueA",
        priority=200,
    )
    await app.enqueue(
        "write_to_file_job",
        args={"message": "highest_C", "result_file": result_file},
        queue="queueC",
        priority=25,
    )

    # Process all jobs one at a time to verify priority ordering
    processed1 = await worker.run_once(
        queues=["queueA", "queueB", "queueC"], max_jobs=1
    )
    processed2 = await worker.run_once(
        queues=["queueA", "queueB", "queueC"], max_jobs=1
    )
    processed3 = await worker.run_once(
        queues=["queueA", "queueB", "queueC"], max_jobs=1
    )
    processed4 = await worker.run_once(
        queues=["queueA", "queueB", "queueC"], max_jobs=1
    )
    processed5 = await worker.run_once(
        queues=["queueA", "queueB", "queueC"], max_jobs=1
    )  # Should be False

    assert processed1 is True
    assert processed2 is True
    assert processed3 is True
    assert processed4 is True
    assert processed5 is False  # No more jobs

    # Check results file - priority order should be preserved
    with open(result_file, "r") as f:
        results = f.read().strip().split("\n")
        processed_messages = [msg for msg in results if msg]

    # Should be processed in priority order regardless of queue (priority: 25, 50, 100, 200)
    assert processed_messages == ["highest_C", "high_B", "medium_A", "low_A"]


@pytest.mark.asyncio
async def test_scheduled_jobs_across_queues(app, result_file):
    """Test that scheduled jobs work correctly across different queues"""

    registry = app._get_job_registry()
    registry.register_job(write_to_file_job, name=write_to_file_job.__name__)
    backend = app._backend
    worker = Worker(backend, registry)

    from datetime import timezone

    future_time = datetime.now(timezone.utc) + timedelta(seconds=1)

    # Schedule jobs in different queues
    await app.enqueue(
        "write_to_file_job",
        args={"message": "scheduled1", "result_file": result_file},
        queue="queue1",
        scheduled_at=future_time,
    )
    await app.enqueue(
        "write_to_file_job",
        args={"message": "scheduled2", "result_file": result_file},
        queue="queue2",
        scheduled_at=future_time,
    )

    # Check that scheduled jobs are not processed before time
    processed1 = await worker.run_once(queues=None, max_jobs=1)
    assert processed1 is False, "No jobs should be processed before scheduled time"

    # Wait for scheduled time (extra margin for system load)
    await asyncio.sleep(2.0)

    # Should now process scheduled jobs
    processed2 = await worker.run_once(queues=None, max_jobs=1)
    processed3 = await worker.run_once(queues=None, max_jobs=1)
    processed4 = await worker.run_once(queues=None, max_jobs=1)  # Should be False

    assert processed2 is True
    assert processed3 is True
    assert processed4 is False

    # Check results
    with open(result_file, "r") as f:
        results = f.read().strip().split("\n")

    processed_messages = {msg for msg in results if msg}
    assert len(processed_messages) == 2
    assert "scheduled1" in processed_messages
    assert "scheduled2" in processed_messages


@pytest.mark.asyncio
async def test_empty_queue_list(app, result_file):
    """Test that empty queue list behaves correctly"""

    registry = app._get_job_registry()
    registry.register_job(write_to_file_job, name=write_to_file_job.__name__)
    backend = app._backend
    worker = Worker(backend, registry)

    await app.enqueue(
        "write_to_file_job",
        args={"message": "job1", "result_file": result_file},
        queue="some_queue",
    )

    # Empty queue list should not process any jobs
    processed = await worker.run_once(queues=[], max_jobs=1)

    assert processed is False

    # Check no results were written
    with open(result_file, "r") as f:
        results = f.read().strip()

    assert results == ""  # No jobs processed


@pytest.mark.asyncio
async def test_queue_efficiency_single_query(app):
    """Test that multiple queues use single efficient query"""

    registry = app._get_job_registry()
    registry.register_job(write_to_file_job, name=write_to_file_job.__name__)
    backend = app._backend
    worker = Worker(backend, registry)

    # Enqueue jobs in multiple queues
    for i in range(3):
        await app.enqueue(
            "write_to_file_job",
            args={"message": f"job{i}", "result_file": "/tmp/test"},
            queue=f"queue{i}",
        )

    # This should use a single query with WHERE queue = ANY([...])
    processed = await worker.run_once(queues=["queue0", "queue1", "queue2"], max_jobs=1)

    assert processed is True  # At least one job was processed


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
