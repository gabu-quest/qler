"""Tests for _context.py — current_job() ContextVar access."""

import pytest

from qler._context import _current_job, current_job
from qler.models.job import Job


class TestCurrentJob:
    """Verify current_job() returns the active job or raises."""

    def test_raises_outside_context(self):
        with pytest.raises(RuntimeError, match="outside of a task execution context"):
            current_job()

    def test_returns_job_when_set(self):
        job = Job(ulid="01H_TEST_ULID", task="my_module.my_task")
        token = _current_job.set(job)
        try:
            result = current_job()
            assert result is job
            assert result.ulid == "01H_TEST_ULID"
            assert result.task == "my_module.my_task"
        finally:
            _current_job.reset(token)

    def test_raises_after_reset(self):
        job = Job(ulid="01H_TEST_ULID")
        token = _current_job.set(job)
        _current_job.reset(token)
        with pytest.raises(RuntimeError, match="outside of a task execution context"):
            current_job()

    def test_nested_context_vars(self):
        """Inner set overrides outer; reset restores outer."""
        outer_job = Job(ulid="OUTER")
        inner_job = Job(ulid="INNER")

        token_outer = _current_job.set(outer_job)
        try:
            assert current_job().ulid == "OUTER"
            token_inner = _current_job.set(inner_job)
            try:
                assert current_job().ulid == "INNER"
            finally:
                _current_job.reset(token_inner)
            assert current_job().ulid == "OUTER"
        finally:
            _current_job.reset(token_outer)
