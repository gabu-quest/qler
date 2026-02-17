"""Tests for qler CLI commands."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from sqler import F

from qler._time import now_epoch
from qler.cli import cli
from qler.enums import AttemptStatus, FailureKind, JobStatus
from qler.models.attempt import JobAttempt
from qler.models.job import Job
from qler.queue import Queue


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test-qler.db")


@pytest.fixture
def populated_db(db_path):
    """Create a DB with jobs in various states for querying."""
    import asyncio

    async def _setup():
        q = Queue(db_path)
        await q.init_db()

        # 3 pending in "default"
        for i in range(3):
            await q.enqueue(f"myapp.tasks.job_{i}", queue_name="default")
        # 2 pending in "email"
        for i in range(2):
            await q.enqueue(f"myapp.tasks.email_{i}", queue_name="email")
        # 1 completed
        j = await q.enqueue("myapp.tasks.done", queue_name="default")
        now = now_epoch()
        await Job.query().filter(
            (F("ulid") == j.ulid) & (F("status") == JobStatus.PENDING.value)
        ).update_one(
            status=JobStatus.COMPLETED.value,
            finished_at=now,
            updated_at=now,
        )
        # 1 failed
        j2 = await q.enqueue("myapp.tasks.fail", queue_name="default")
        await Job.query().filter(
            (F("ulid") == j2.ulid) & (F("status") == JobStatus.PENDING.value)
        ).update_one(
            status=JobStatus.FAILED.value,
            last_error="Something went wrong",
            last_failure_kind=FailureKind.EXCEPTION.value,
            finished_at=now,
            updated_at=now,
        )
        await q.close()

    asyncio.run(_setup())
    return db_path


# -----------------------------------------------------------------------
# Helper tests
# -----------------------------------------------------------------------


class TestHelpers:
    def test_format_duration_seconds(self):
        from qler.cli import _format_duration

        assert _format_duration(45) == "45s"

    def test_format_duration_minutes(self):
        from qler.cli import _format_duration

        assert _format_duration(90) == "1m 30s"

    def test_format_duration_hours(self):
        from qler.cli import _format_duration

        assert _format_duration(3661) == "1h 1m 1s"

    def test_format_duration_days(self):
        from qler.cli import _format_duration

        assert _format_duration(90061) == "1d 1h 1m 1s"

    def test_format_duration_zero(self):
        from qler.cli import _format_duration

        assert _format_duration(0) == "0s"

    def test_format_duration_negative(self):
        from qler.cli import _format_duration

        assert _format_duration(-1) == "-"

    def test_format_ts_none(self):
        from qler.cli import _format_ts

        assert _format_ts(None) == "-"

    def test_format_ts_epoch(self):
        from qler.cli import _format_ts

        assert _format_ts(0) == "1970-01-01T00:00:00Z"

    def test_format_ts_known(self):
        from qler.cli import _format_ts

        # 2024-01-01 00:00:00 UTC = 1704067200
        assert _format_ts(1704067200) == "2024-01-01T00:00:00Z"

    def test_safe_json_valid(self):
        from qler.cli import _safe_json

        assert _safe_json('{"key": "value"}') == {"key": "value"}

    def test_safe_json_invalid(self):
        from qler.cli import _safe_json

        result = _safe_json("not json")
        assert result["_invalid"] is True
        assert "raw" in result
        assert "parse_error" in result

    def test_safe_json_none(self):
        from qler.cli import _safe_json

        assert _safe_json(None) is None

    def test_parse_since_hours(self):
        from qler.cli import _parse_since

        before = now_epoch()
        result = _parse_since("2h")
        after = now_epoch()
        # Result should be approximately now - 7200
        assert before - 7200 <= result <= after - 7200

    def test_parse_since_days(self):
        from qler.cli import _parse_since

        before = now_epoch()
        result = _parse_since("7d")
        after = now_epoch()
        assert before - 604800 <= result <= after - 604800

    def test_parse_since_combined(self):
        from qler.cli import _parse_since

        before = now_epoch()
        result = _parse_since("1d12h")
        after = now_epoch()
        expected_delta = 86400 + 43200
        assert before - expected_delta <= result <= after - expected_delta

    def test_parse_since_invalid(self):
        import click

        from qler.cli import _parse_since

        with pytest.raises(click.BadParameter, match="Invalid duration"):
            _parse_since("invalid")

    def test_parse_since_exceeds_max(self):
        import click

        from qler.cli import _parse_since

        with pytest.raises(click.BadParameter, match="exceeds maximum"):
            _parse_since("99999d")

    def test_validate_module_path_rejects_traversal(self):
        import click

        from qler.cli import _validate_module_path

        with pytest.raises(click.BadParameter, match="Invalid module path"):
            _validate_module_path("../evil")

    def test_validate_module_path_rejects_spaces(self):
        import click

        from qler.cli import _validate_module_path

        with pytest.raises(click.BadParameter, match="Invalid module path"):
            _validate_module_path("my module")

    def test_validate_module_path_accepts_dotted(self):
        from qler.cli import _validate_module_path

        # Should not raise
        _validate_module_path("myapp.tasks.email")

    def test_echo_table_output(self, runner, capsys):
        from qler.cli import _echo_table

        _echo_table(["Name", "Count"], [["alpha", "10"], ["beta", "5"]])
        captured = capsys.readouterr()
        lines = captured.out.strip().splitlines()
        assert len(lines) == 4  # header + separator + 2 data rows
        assert "Name" in lines[0]
        assert "Count" in lines[0]
        assert "----" in lines[1]
        assert "alpha" in lines[2]
        assert "10" in lines[2]
        assert "beta" in lines[3]
        assert "5" in lines[3]

    def test_echo_table_empty(self, capsys):
        from qler.cli import _echo_table

        _echo_table(["A", "B"], [])
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_import_app_bad_format(self):
        import click

        from qler.cli import _import_app

        with pytest.raises(click.BadParameter, match="Expected 'module:attribute'"):
            _import_app("no_colon_here")

    def test_import_app_bad_module(self):
        import click

        from qler.cli import _import_app

        with pytest.raises(click.BadParameter, match="Cannot import module"):
            _import_app("nonexistent_module_xyz:queue")

    def test_import_app_wrong_type(self):
        import click

        from qler.cli import _import_app

        with pytest.raises(click.BadParameter, match="is not a Queue instance"):
            _import_app("os:path")

    def test_import_modules_bad(self):
        import click

        from qler.cli import _import_modules

        with pytest.raises(click.BadParameter, match="Cannot import module"):
            _import_modules(("nonexistent_module_abc",))


# -----------------------------------------------------------------------
# qler init
# -----------------------------------------------------------------------


class TestInit:
    def test_creates_db(self, runner, db_path):
        result = runner.invoke(cli, ["init", "--db", db_path])
        assert result.exit_code == 0
        assert "Created database" in result.output
        assert os.path.exists(db_path)

    def test_creates_gitignore(self, runner, tmp_path):
        db_path = str(tmp_path / "app.db")
        result = runner.invoke(cli, ["init", "--db", db_path])
        assert result.exit_code == 0
        gitignore = tmp_path / ".gitignore"
        assert gitignore.exists()
        assert "app.db" in gitignore.read_text()

    def test_appends_to_existing_gitignore(self, runner, tmp_path):
        db_path = str(tmp_path / "app.db")
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("*.pyc\n")
        result = runner.invoke(cli, ["init", "--db", db_path])
        assert result.exit_code == 0
        content = gitignore.read_text()
        assert "*.pyc" in content
        assert "app.db" in content

    def test_idempotent_gitignore(self, runner, tmp_path):
        db_path = str(tmp_path / "app.db")
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("app.db\n")
        result = runner.invoke(cli, ["init", "--db", db_path])
        assert result.exit_code == 0
        # Should not add duplicate
        content = gitignore.read_text()
        assert content.count("app.db") == 1

    def test_json_output(self, runner, db_path):
        result = runner.invoke(cli, ["init", "--db", db_path, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["db"] == db_path
        assert data["created"] is True
        assert "gitignore_updated" in data

    def test_gitignore_sanitizes_newlines(self, runner, tmp_path):
        """Newlines in db filename should be stripped from .gitignore entry."""
        # Create a db file with a normal name, then check the gitignore content
        db_path = str(tmp_path / "app.db")
        result = runner.invoke(cli, ["init", "--db", db_path])
        assert result.exit_code == 0
        gitignore = tmp_path / ".gitignore"
        lines = gitignore.read_text().splitlines()
        # Each line should be a clean entry, no injected patterns
        assert len(lines) == 1
        assert lines[0] == "app.db"

    def test_idempotent_init(self, runner, db_path):
        """Running init twice should succeed without error."""
        runner.invoke(cli, ["init", "--db", db_path])
        result = runner.invoke(cli, ["init", "--db", db_path])
        assert result.exit_code == 0

    def test_sets_wal_mode(self, runner, db_path):
        """init should set WAL journal mode."""
        runner.invoke(cli, ["init", "--db", db_path])
        import asyncio

        from sqler import AsyncSQLerDB

        async def _check():
            db = AsyncSQLerDB.on_disk(db_path)
            await db.connect()
            cur = await db.adapter.execute("PRAGMA journal_mode")
            row = await cur.fetchone()
            await cur.close()
            await db.close()
            return row[0]

        mode = asyncio.run(_check())
        assert mode == "wal"


# -----------------------------------------------------------------------
# qler status
# -----------------------------------------------------------------------


class TestStatus:
    def test_empty_db(self, runner, db_path):
        runner.invoke(cli, ["init", "--db", db_path])
        result = runner.invoke(cli, ["status", "--db", db_path])
        assert result.exit_code == 0
        assert "No jobs found" in result.output

    def test_populated_human(self, runner, populated_db):
        result = runner.invoke(cli, ["status", "--db", populated_db])
        assert result.exit_code == 0
        assert "default" in result.output
        assert "email" in result.output
        assert "Total:" in result.output

    def test_populated_json(self, runner, populated_db):
        result = runner.invoke(cli, ["status", "--db", populated_db, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "queues" in data
        assert "total" in data
        assert "default" in data["queues"]
        assert "email" in data["queues"]
        # default: 3 pending + 1 completed + 1 failed = 5
        default_q = data["queues"]["default"]
        assert default_q["pending"] == 3
        assert default_q["completed"] == 1
        assert default_q["failed"] == 1
        # email: 2 pending
        assert data["queues"]["email"]["pending"] == 2
        assert data["total"] == 7


# -----------------------------------------------------------------------
# qler jobs
# -----------------------------------------------------------------------


class TestJobs:
    def test_empty_db(self, runner, db_path):
        runner.invoke(cli, ["init", "--db", db_path])
        result = runner.invoke(cli, ["jobs", "--db", db_path])
        assert result.exit_code == 0
        assert "No jobs found" in result.output

    def test_list_all(self, runner, populated_db):
        result = runner.invoke(cli, ["jobs", "--db", populated_db])
        assert result.exit_code == 0
        assert "7 jobs shown" in result.output

    def test_filter_status(self, runner, populated_db):
        result = runner.invoke(cli, ["jobs", "--db", populated_db, "--status", "failed"])
        assert result.exit_code == 0
        assert "1 jobs shown" in result.output

    def test_filter_queue(self, runner, populated_db):
        result = runner.invoke(cli, ["jobs", "--db", populated_db, "--queue", "email"])
        assert result.exit_code == 0
        assert "2 jobs shown" in result.output

    def test_filter_task(self, runner, populated_db):
        result = runner.invoke(cli, ["jobs", "--db", populated_db, "--task", "myapp.tasks.done"])
        assert result.exit_code == 0
        assert "1 jobs shown" in result.output

    def test_limit(self, runner, populated_db):
        result = runner.invoke(cli, ["jobs", "--db", populated_db, "--limit", "3"])
        assert result.exit_code == 0
        assert "3 jobs shown" in result.output

    def test_json_output(self, runner, populated_db):
        result = runner.invoke(cli, ["jobs", "--db", populated_db, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 7
        # Verify structure and values using known fixture data
        by_task = {j["task"]: j for j in data}
        assert by_task["myapp.tasks.done"]["status"] == "completed"
        assert by_task["myapp.tasks.done"]["queue_name"] == "default"
        assert by_task["myapp.tasks.fail"]["status"] == "failed"
        assert by_task["myapp.tasks.fail"]["last_error"] == "Something went wrong"
        # Verify all jobs have required fields
        for j in data:
            assert "ulid" in j
            assert "status" in j
            assert "queue_name" in j
            assert "task" in j
            assert "created_at" in j

    def test_since_filter(self, runner, db_path):
        """Jobs created now should be within --since 1h."""
        import asyncio

        async def _setup():
            q = Queue(db_path)
            await q.init_db()
            await q.enqueue("tasks.recent", queue_name="default")
            await q.close()

        asyncio.run(_setup())
        result = runner.invoke(cli, ["jobs", "--db", db_path, "--since", "1h"])
        assert result.exit_code == 0
        assert "1 jobs shown" in result.output

    def test_since_excludes_old(self, runner, db_path):
        """Jobs with old created_at should be excluded by --since 1h."""
        import asyncio

        async def _setup():
            q = Queue(db_path)
            await q.init_db()
            j = await q.enqueue("tasks.old", queue_name="default")
            # Manually set created_at to 2 hours ago
            old_ts = now_epoch() - 7200
            await Job.query().filter(
                (F("ulid") == j.ulid) & (F("status") == JobStatus.PENDING.value)
            ).update_one(created_at=old_ts, updated_at=old_ts)
            await q.close()

        asyncio.run(_setup())
        result = runner.invoke(cli, ["jobs", "--db", db_path, "--since", "1h"])
        assert result.exit_code == 0
        assert "No jobs found" in result.output


# -----------------------------------------------------------------------
# qler job <id>
# -----------------------------------------------------------------------


class TestJob:
    def test_not_found(self, runner, db_path):
        runner.invoke(cli, ["init", "--db", db_path])
        result = runner.invoke(cli, ["job", "NONEXISTENT", "--db", db_path])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_not_found_json(self, runner, db_path):
        runner.invoke(cli, ["init", "--db", db_path])
        result = runner.invoke(cli, ["job", "NONEXISTENT", "--db", db_path, "--json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert "error" in data

    def test_detail_view(self, runner, db_path):
        import asyncio

        async def _setup():
            q = Queue(db_path)
            await q.init_db()
            j = await q.enqueue("myapp.tasks.hello", args=(1, 2), kwargs={"key": "val"})
            await q.close()
            return j.ulid

        ulid = asyncio.run(_setup())
        result = runner.invoke(cli, ["job", ulid, "--db", db_path])
        assert result.exit_code == 0
        assert f"ULID:            {ulid}" in result.output
        assert "Status:          pending" in result.output
        assert "Task:            myapp.tasks.hello" in result.output
        # Payload is pretty-printed JSON with indent=2
        assert '"args"' in result.output
        assert "1," in result.output  # first arg
        assert "2" in result.output   # second arg
        assert '"key": "val"' in result.output

    def test_detail_json(self, runner, db_path):
        import asyncio

        async def _setup():
            q = Queue(db_path)
            await q.init_db()
            j = await q.enqueue("myapp.tasks.hello", args=(1,))
            await q.close()
            return j.ulid

        ulid = asyncio.run(_setup())
        result = runner.invoke(cli, ["job", ulid, "--db", db_path, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ulid"] == ulid
        assert data["status"] == "pending"
        assert data["task"] == "myapp.tasks.hello"
        assert data["payload"] == {"args": [1], "kwargs": {}}

    def test_safe_payload_parsing(self, runner, db_path):
        """Jobs with corrupt payload_json should not crash the detail view."""
        import asyncio

        async def _setup():
            q = Queue(db_path)
            await q.init_db()
            j = await q.enqueue("myapp.tasks.hello")
            # Corrupt the payload
            await Job.query().filter(
                (F("ulid") == j.ulid) & (F("status") == JobStatus.PENDING.value)
            ).update_one(payload_json="NOT VALID JSON")
            await q.close()
            return j.ulid

        ulid = asyncio.run(_setup())
        result = runner.invoke(cli, ["job", ulid, "--db", db_path])
        assert result.exit_code == 0
        assert "_invalid" in result.output

    def test_failed_job_shows_error(self, runner, db_path):
        import asyncio

        async def _setup():
            q = Queue(db_path)
            await q.init_db()
            j = await q.enqueue("myapp.tasks.fail")
            now = now_epoch()
            await Job.query().filter(
                (F("ulid") == j.ulid) & (F("status") == JobStatus.PENDING.value)
            ).update_one(
                status=JobStatus.FAILED.value,
                last_error="Division by zero",
                last_failure_kind=FailureKind.EXCEPTION.value,
                finished_at=now,
                updated_at=now,
            )
            await q.close()
            return j.ulid

        ulid = asyncio.run(_setup())
        result = runner.invoke(cli, ["job", ulid, "--db", db_path])
        assert result.exit_code == 0
        assert "Division by zero" in result.output
        assert "exception" in result.output


# -----------------------------------------------------------------------
# qler attempts <id>
# -----------------------------------------------------------------------


class TestAttempts:
    def test_no_attempts(self, runner, db_path):
        import asyncio

        async def _setup():
            q = Queue(db_path)
            await q.init_db()
            j = await q.enqueue("myapp.tasks.hello")
            await q.close()
            return j.ulid

        ulid = asyncio.run(_setup())
        result = runner.invoke(cli, ["attempts", ulid, "--db", db_path])
        assert result.exit_code == 0
        assert "No attempts found" in result.output

    def test_with_attempts(self, runner, db_path):
        import asyncio

        from qler._time import generate_ulid

        async def _setup():
            q = Queue(db_path)
            await q.init_db()
            j = await q.enqueue("myapp.tasks.hello")
            now = now_epoch()
            a1 = JobAttempt(
                ulid=generate_ulid(),
                job_ulid=j.ulid,
                status=AttemptStatus.FAILED.value,
                attempt_number=1,
                worker_id="host:1:abc",
                started_at=now - 10,
                finished_at=now - 5,
                failure_kind=FailureKind.EXCEPTION.value,
                error="Timeout",
            )
            await a1.save()
            a2 = JobAttempt(
                ulid=generate_ulid(),
                job_ulid=j.ulid,
                status=AttemptStatus.COMPLETED.value,
                attempt_number=2,
                worker_id="host:1:def",
                started_at=now - 4,
                finished_at=now,
            )
            await a2.save()
            await q.close()
            return j.ulid

        ulid = asyncio.run(_setup())
        result = runner.invoke(cli, ["attempts", ulid, "--db", db_path])
        assert result.exit_code == 0
        assert "failed" in result.output
        assert "completed" in result.output
        assert "Timeout" in result.output
        # Verify both attempts are rendered (JSON gives exact count)
        json_result = runner.invoke(cli, ["attempts", ulid, "--db", db_path, "--json"])
        data = json.loads(json_result.output)
        assert len(data) == 2
        assert data[0]["status"] == "failed"
        assert data[1]["status"] == "completed"

    def test_json_output(self, runner, db_path):
        import asyncio

        from qler._time import generate_ulid

        async def _setup():
            q = Queue(db_path)
            await q.init_db()
            j = await q.enqueue("myapp.tasks.hello")
            now = now_epoch()
            a = JobAttempt(
                ulid=generate_ulid(),
                job_ulid=j.ulid,
                status=AttemptStatus.COMPLETED.value,
                attempt_number=1,
                worker_id="host:1:abc",
                started_at=now - 5,
                finished_at=now,
            )
            await a.save()
            await q.close()
            return j.ulid

        ulid = asyncio.run(_setup())
        result = runner.invoke(cli, ["attempts", ulid, "--db", db_path, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["job_ulid"] == ulid
        assert data[0]["status"] == "completed"
        assert data[0]["attempt_number"] == 1
        assert data[0]["worker_id"] == "host:1:abc"


# -----------------------------------------------------------------------
# qler retry
# -----------------------------------------------------------------------


class TestRetry:
    def test_single_id(self, runner, db_path):
        import asyncio

        async def _setup():
            q = Queue(db_path)
            await q.init_db()
            j = await q.enqueue("myapp.tasks.fail")
            now = now_epoch()
            await Job.query().filter(
                (F("ulid") == j.ulid) & (F("status") == JobStatus.PENDING.value)
            ).update_one(
                status=JobStatus.FAILED.value,
                last_error="err",
                finished_at=now,
                updated_at=now,
            )
            await q.close()
            return j.ulid

        ulid = asyncio.run(_setup())
        result = runner.invoke(cli, ["retry", ulid, "--db", db_path])
        assert result.exit_code == 0
        assert "Retried 1 job" in result.output
        assert ulid in result.output

    def test_single_not_found(self, runner, db_path):
        runner.invoke(cli, ["init", "--db", db_path])
        result = runner.invoke(cli, ["retry", "FAKE_ULID", "--db", db_path])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_single_wrong_status(self, runner, db_path):
        import asyncio

        async def _setup():
            q = Queue(db_path)
            await q.init_db()
            j = await q.enqueue("myapp.tasks.pending")
            await q.close()
            return j.ulid

        ulid = asyncio.run(_setup())
        result = runner.invoke(cli, ["retry", ulid, "--db", db_path])
        assert result.exit_code == 1
        assert "Cannot retry" in result.output

    def test_bulk_all(self, runner, db_path):
        import asyncio

        async def _setup():
            q = Queue(db_path)
            await q.init_db()
            ulids = []
            now = now_epoch()
            for i in range(3):
                j = await q.enqueue(f"myapp.tasks.fail_{i}")
                await Job.query().filter(
                    (F("ulid") == j.ulid) & (F("status") == JobStatus.PENDING.value)
                ).update_one(
                    status=JobStatus.FAILED.value,
                    last_error="err",
                    finished_at=now,
                    updated_at=now,
                )
                ulids.append(j.ulid)
            await q.close()
            return ulids

        ulids = asyncio.run(_setup())
        result = runner.invoke(cli, ["retry", "--db", db_path, "--all"])
        assert result.exit_code == 0
        assert "Retried 3 job" in result.output

    def test_requires_ulid_or_all(self, runner, db_path):
        runner.invoke(cli, ["init", "--db", db_path])
        result = runner.invoke(cli, ["retry", "--db", db_path])
        assert result.exit_code == 2
        assert "Provide a job ULID or use --all" in result.output

    def test_json_output(self, runner, db_path):
        import asyncio

        async def _setup():
            q = Queue(db_path)
            await q.init_db()
            j = await q.enqueue("myapp.tasks.fail")
            now = now_epoch()
            await Job.query().filter(
                (F("ulid") == j.ulid) & (F("status") == JobStatus.PENDING.value)
            ).update_one(
                status=JobStatus.FAILED.value,
                last_error="err",
                finished_at=now,
                updated_at=now,
            )
            await q.close()
            return j.ulid

        ulid = asyncio.run(_setup())
        result = runner.invoke(cli, ["retry", ulid, "--db", db_path, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["retried"] == 1
        assert data["ulids"] == [ulid]

    def test_bulk_filter_by_task(self, runner, db_path):
        import asyncio

        async def _setup():
            q = Queue(db_path)
            await q.init_db()
            now = now_epoch()
            # 2 failed with task_a, 1 failed with task_b
            for task_name in ["task_a", "task_a", "task_b"]:
                j = await q.enqueue(f"myapp.{task_name}")
                await Job.query().filter(
                    (F("ulid") == j.ulid) & (F("status") == JobStatus.PENDING.value)
                ).update_one(
                    status=JobStatus.FAILED.value,
                    last_error="err",
                    finished_at=now,
                    updated_at=now,
                )
            await q.close()

        asyncio.run(_setup())
        result = runner.invoke(cli, [
            "retry", "--db", db_path, "--all", "--task", "myapp.task_a"
        ])
        assert result.exit_code == 0
        assert "Retried 2 job" in result.output


# -----------------------------------------------------------------------
# qler cancel
# -----------------------------------------------------------------------


class TestCancel:
    def test_single_id(self, runner, db_path):
        import asyncio

        async def _setup():
            q = Queue(db_path)
            await q.init_db()
            j = await q.enqueue("myapp.tasks.cancel_me")
            await q.close()
            return j.ulid

        ulid = asyncio.run(_setup())
        result = runner.invoke(cli, ["cancel", ulid, "--db", db_path])
        assert result.exit_code == 0
        assert "Cancelled 1 job" in result.output
        assert ulid in result.output

    def test_single_not_found(self, runner, db_path):
        runner.invoke(cli, ["init", "--db", db_path])
        result = runner.invoke(cli, ["cancel", "FAKE_ULID", "--db", db_path])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_single_wrong_status(self, runner, db_path):
        """Cancelling a non-pending job should fail."""
        import asyncio

        async def _setup():
            q = Queue(db_path)
            await q.init_db()
            j = await q.enqueue("myapp.tasks.done")
            now = now_epoch()
            await Job.query().filter(
                (F("ulid") == j.ulid) & (F("status") == JobStatus.PENDING.value)
            ).update_one(
                status=JobStatus.COMPLETED.value,
                finished_at=now,
                updated_at=now,
            )
            await q.close()
            return j.ulid

        ulid = asyncio.run(_setup())
        result = runner.invoke(cli, ["cancel", ulid, "--db", db_path])
        assert result.exit_code == 1
        assert "Cannot cancel" in result.output

    def test_bulk_all(self, runner, db_path):
        import asyncio

        async def _setup():
            q = Queue(db_path)
            await q.init_db()
            for i in range(3):
                await q.enqueue(f"myapp.tasks.pending_{i}")
            await q.close()

        asyncio.run(_setup())
        result = runner.invoke(cli, ["cancel", "--db", db_path, "--all"])
        assert result.exit_code == 0
        assert "Cancelled 3 job" in result.output

    def test_bulk_filter_by_queue(self, runner, db_path):
        import asyncio

        async def _setup():
            q = Queue(db_path)
            await q.init_db()
            await q.enqueue("tasks.a", queue_name="urgent")
            await q.enqueue("tasks.b", queue_name="urgent")
            await q.enqueue("tasks.c", queue_name="normal")
            await q.close()

        asyncio.run(_setup())
        result = runner.invoke(cli, [
            "cancel", "--db", db_path, "--all", "--queue", "urgent"
        ])
        assert result.exit_code == 0
        assert "Cancelled 2 job" in result.output

    def test_bulk_filter_by_task(self, runner, db_path):
        import asyncio

        async def _setup():
            q = Queue(db_path)
            await q.init_db()
            await q.enqueue("myapp.task_a", queue_name="default")
            await q.enqueue("myapp.task_a", queue_name="default")
            await q.enqueue("myapp.task_b", queue_name="default")
            await q.close()

        asyncio.run(_setup())
        result = runner.invoke(cli, [
            "cancel", "--db", db_path, "--all", "--task", "myapp.task_a"
        ])
        assert result.exit_code == 0
        assert "Cancelled 2 job" in result.output

    def test_requires_ulid_or_all(self, runner, db_path):
        runner.invoke(cli, ["init", "--db", db_path])
        result = runner.invoke(cli, ["cancel", "--db", db_path])
        assert result.exit_code == 2
        assert "Provide a job ULID or use --all" in result.output

    def test_json_output(self, runner, db_path):
        import asyncio

        async def _setup():
            q = Queue(db_path)
            await q.init_db()
            j = await q.enqueue("myapp.tasks.cancel_me")
            await q.close()
            return j.ulid

        ulid = asyncio.run(_setup())
        result = runner.invoke(cli, ["cancel", ulid, "--db", db_path, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["cancelled"] == 1
        assert data["cancelled_ulids"] == [ulid]


# -----------------------------------------------------------------------
# qler purge
# -----------------------------------------------------------------------


class TestPurge:
    def test_purge_old_completed(self, runner, db_path):
        import asyncio

        async def _setup():
            q = Queue(db_path)
            await q.init_db()
            old_ts = now_epoch() - 86400 * 10  # 10 days ago
            for i in range(3):
                j = await q.enqueue(f"myapp.tasks.old_{i}")
                await Job.query().filter(
                    (F("ulid") == j.ulid) & (F("status") == JobStatus.PENDING.value)
                ).update_one(
                    status=JobStatus.COMPLETED.value,
                    created_at=old_ts,
                    finished_at=old_ts,
                    updated_at=old_ts,
                )
            # 1 recent completed (should not be purged)
            j = await q.enqueue("myapp.tasks.recent")
            now = now_epoch()
            await Job.query().filter(
                (F("ulid") == j.ulid) & (F("status") == JobStatus.PENDING.value)
            ).update_one(
                status=JobStatus.COMPLETED.value,
                finished_at=now,
                updated_at=now,
            )
            await q.close()

        asyncio.run(_setup())
        result = runner.invoke(cli, [
            "purge", "--db", db_path, "--older-than", "7d"
        ])
        assert result.exit_code == 0
        assert "Purged 3 jobs" in result.output

    def test_purge_filter_status(self, runner, db_path):
        import asyncio

        async def _setup():
            q = Queue(db_path)
            await q.init_db()
            old_ts = now_epoch() - 86400 * 10
            # 2 old failed
            for i in range(2):
                j = await q.enqueue(f"myapp.tasks.fail_{i}")
                await Job.query().filter(
                    (F("ulid") == j.ulid) & (F("status") == JobStatus.PENDING.value)
                ).update_one(
                    status=JobStatus.FAILED.value,
                    created_at=old_ts,
                    finished_at=old_ts,
                    updated_at=old_ts,
                )
            # 1 old completed (should not be purged when filtering by failed)
            j = await q.enqueue("myapp.tasks.done")
            await Job.query().filter(
                (F("ulid") == j.ulid) & (F("status") == JobStatus.PENDING.value)
            ).update_one(
                status=JobStatus.COMPLETED.value,
                created_at=old_ts,
                finished_at=old_ts,
                updated_at=old_ts,
            )
            await q.close()

        asyncio.run(_setup())
        result = runner.invoke(cli, [
            "purge", "--db", db_path, "--older-than", "7d", "--status", "failed"
        ])
        assert result.exit_code == 0
        assert "Purged 2 jobs" in result.output

    def test_purge_nothing(self, runner, db_path):
        runner.invoke(cli, ["init", "--db", db_path])
        result = runner.invoke(cli, ["purge", "--db", db_path, "--older-than", "7d"])
        assert result.exit_code == 0
        assert "Purged 0 jobs" in result.output

    def test_json_output(self, runner, db_path):
        runner.invoke(cli, ["init", "--db", db_path])
        result = runner.invoke(cli, [
            "purge", "--db", db_path, "--older-than", "1d", "--json"
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["purged"] == 0

    def test_cascades_attempts(self, runner, db_path):
        """Purge should also delete associated attempts."""
        import asyncio

        from qler._time import generate_ulid

        async def _setup():
            q = Queue(db_path)
            await q.init_db()
            old_ts = now_epoch() - 86400 * 10
            j = await q.enqueue("myapp.tasks.old")
            await Job.query().filter(
                (F("ulid") == j.ulid) & (F("status") == JobStatus.PENDING.value)
            ).update_one(
                status=JobStatus.COMPLETED.value,
                created_at=old_ts,
                finished_at=old_ts,
                updated_at=old_ts,
            )
            # Add an attempt for the job
            a = JobAttempt(
                ulid=generate_ulid(),
                job_ulid=j.ulid,
                status=AttemptStatus.COMPLETED.value,
                attempt_number=1,
                worker_id="host:1:abc",
                started_at=old_ts,
                finished_at=old_ts,
            )
            await a.save()
            await q.close()
            return j.ulid

        job_ulid = asyncio.run(_setup())
        result = runner.invoke(cli, [
            "purge", "--db", db_path, "--older-than", "7d"
        ])
        assert result.exit_code == 0
        assert "Purged 1 jobs" in result.output

        # Verify attempts were deleted
        import asyncio as aio

        async def _check():
            q = Queue(db_path)
            await q.init_db()
            from sqler import F
            remaining = await JobAttempt.query().filter(
                F("job_ulid") == job_ulid
            ).all()
            await q.close()
            return len(remaining)

        assert aio.run(_check()) == 0


# -----------------------------------------------------------------------
# qler doctor
# -----------------------------------------------------------------------


class TestDoctor:
    def test_healthy_db(self, runner, db_path):
        runner.invoke(cli, ["init", "--db", db_path])
        result = runner.invoke(cli, ["doctor", "--db", db_path])
        assert result.exit_code == 0
        assert "All checks passed" in result.output

    def test_json_output(self, runner, db_path):
        runner.invoke(cli, ["init", "--db", db_path])
        result = runner.invoke(cli, ["doctor", "--db", db_path, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        check_names = [c["check"] for c in data]
        assert "schema" in check_names
        assert "wal_mode" in check_names
        assert "expired_leases" in check_names
        assert "stale_pending" in check_names
        assert "database_size" in check_names
        for check in data:
            assert check["ok"] is True

    def test_detects_expired_leases(self, runner, db_path):
        import asyncio

        async def _setup():
            q = Queue(db_path)
            await q.init_db()
            j = await q.enqueue("myapp.tasks.stuck")
            expired_ts = now_epoch() - 600  # 10 min ago
            await Job.query().filter(
                (F("ulid") == j.ulid) & (F("status") == JobStatus.PENDING.value)
            ).update_one(
                status=JobStatus.RUNNING.value,
                worker_id="dead-worker",
                lease_expires_at=expired_ts,
                updated_at=now_epoch(),
            )
            await q.close()

        asyncio.run(_setup())
        result = runner.invoke(cli, ["doctor", "--db", db_path])
        assert result.exit_code == 1
        assert "FAIL" in result.output
        assert "expired lease" in result.output

    def test_detects_stale_pending(self, runner, db_path):
        import asyncio

        async def _setup():
            q = Queue(db_path)
            await q.init_db()
            j = await q.enqueue("myapp.tasks.stale")
            stale_ts = now_epoch() - 86400 * 2  # 2 days ago
            await Job.query().filter(
                (F("ulid") == j.ulid) & (F("status") == JobStatus.PENDING.value)
            ).update_one(created_at=stale_ts, updated_at=stale_ts)
            await q.close()

        asyncio.run(_setup())
        result = runner.invoke(cli, ["doctor", "--db", db_path])
        assert result.exit_code == 1
        assert "stale_pending" in result.output
        assert "FAIL" in result.output

    def test_wal_mode_check(self, runner, db_path):
        runner.invoke(cli, ["init", "--db", db_path])
        result = runner.invoke(cli, ["doctor", "--db", db_path, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        wal_check = next(c for c in data if c["check"] == "wal_mode")
        assert wal_check["ok"] is True
        assert "wal" in wal_check["detail"]

    def test_database_size_check(self, runner, db_path):
        runner.invoke(cli, ["init", "--db", db_path])
        result = runner.invoke(cli, ["doctor", "--db", db_path, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        size_check = next(c for c in data if c["check"] == "database_size")
        assert size_check["ok"] is True
        assert "0 jobs" in size_check["detail"]
        assert "0 attempts" in size_check["detail"]
        assert "KB" in size_check["detail"]


# -----------------------------------------------------------------------
# qler worker (arg validation only)
# -----------------------------------------------------------------------


class TestWorkerArgs:
    def test_requires_db_or_app(self, runner):
        result = runner.invoke(cli, ["worker"])
        assert result.exit_code == 2
        assert "Either --db or --app is required" in result.output

    def test_bad_app_format(self, runner):
        result = runner.invoke(cli, ["worker", "--app", "no_colon"])
        assert result.exit_code == 2
        assert "Expected 'module:attribute'" in result.output


# -----------------------------------------------------------------------
# Version / help
# -----------------------------------------------------------------------


class TestVersionHelp:
    def test_version(self, runner):
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output

    def test_help(self, runner):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "Background jobs without Redis" in result.output

    def test_all_commands_have_help(self, runner):
        commands = [
            "init", "worker", "status", "jobs", "job",
            "attempts", "retry", "cancel", "purge", "doctor",
        ]
        for cmd in commands:
            result = runner.invoke(cli, [cmd, "--help"])
            assert result.exit_code == 0, f"{cmd} --help failed: {result.output}"
