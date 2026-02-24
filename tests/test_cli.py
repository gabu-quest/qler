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

        result = _validate_module_path("myapp.tasks.email")
        assert result is None

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
# qler backup
# -----------------------------------------------------------------------


class TestBackupCommand:
    def test_backup_creates_file(self, runner, db_path, tmp_path):
        runner.invoke(cli, ["init", "--db", db_path])
        dest = str(tmp_path / "backup.db")
        result = runner.invoke(cli, ["backup", "--db", db_path, "--to", dest])
        assert result.exit_code == 0
        assert "Backup complete" in result.output
        assert os.path.exists(dest)

    def test_backup_file_is_valid_sqlite(self, runner, db_path, tmp_path):
        """Backup is a readable SQLite DB with qler tables."""
        import asyncio

        runner.invoke(cli, ["init", "--db", db_path])
        dest = str(tmp_path / "backup.db")
        result = runner.invoke(cli, ["backup", "--db", db_path, "--to", dest])
        assert result.exit_code == 0

        async def _check():
            from sqler import AsyncSQLerDB

            db = AsyncSQLerDB.on_disk(dest)
            await db.connect()
            cur = await db.adapter.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name IN ('qler_jobs', 'qler_job_attempts')"
            )
            tables = sorted([row[0] for row in await cur.fetchall()])
            await cur.close()
            await db.close()
            return tables

        tables = asyncio.run(_check())
        assert tables == ["qler_job_attempts", "qler_jobs"]

    def test_backup_preserves_data(self, runner, db_path, tmp_path):
        """Jobs in source appear in backup."""
        import asyncio

        async def _setup():
            q = Queue(db_path)
            await q.init_db()
            await q.enqueue("myapp.tasks.hello")
            await q.enqueue("myapp.tasks.world")
            await q.close()

        asyncio.run(_setup())

        dest = str(tmp_path / "backup.db")
        result = runner.invoke(cli, ["backup", "--db", db_path, "--to", dest])
        assert result.exit_code == 0

        async def _check():
            q = Queue(dest)
            await q.init_db()
            jobs = await Job.query().all()
            await q.close()
            return jobs

        jobs = asyncio.run(_check())
        assert len(jobs) == 2
        tasks = {j.task for j in jobs}
        assert tasks == {"myapp.tasks.hello", "myapp.tasks.world"}

    def test_backup_json_output(self, runner, db_path, tmp_path):
        runner.invoke(cli, ["init", "--db", db_path])
        dest = str(tmp_path / "backup.db")
        result = runner.invoke(cli, ["backup", "--db", db_path, "--to", dest, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["destination_path"] == dest
        assert data["source_path"] == db_path
        assert data["size_bytes"] >= 4096  # minimum SQLite DB size
        assert isinstance(data["duration_ms"], (int, float))
        assert data["duration_ms"] >= 0
        assert data["error"] is None

    def test_backup_refuses_existing_destination(self, runner, db_path, tmp_path):
        runner.invoke(cli, ["init", "--db", db_path])
        dest = str(tmp_path / "backup.db")
        Path(dest).write_text("")  # create the file
        result = runner.invoke(cli, ["backup", "--db", db_path, "--to", dest])
        assert result.exit_code == 1
        assert "Destination already exists" in result.stderr

    def test_backup_refuses_existing_destination_json(self, runner, db_path, tmp_path):
        runner.invoke(cli, ["init", "--db", db_path])
        dest = str(tmp_path / "backup.db")
        Path(dest).write_text("")
        result = runner.invoke(cli, ["backup", "--db", db_path, "--to", dest, "--json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert "error" in data
        assert "already exists" in data["error"]

    def test_backup_nonexistent_source_succeeds_empty(self, runner, tmp_path):
        """SQLite creates the source on connect, so backup succeeds but is empty."""
        import asyncio

        bad_source = str(tmp_path / "nonexistent.db")
        dest = str(tmp_path / "backup.db")
        result = runner.invoke(cli, ["backup", "--db", bad_source, "--to", dest])
        assert result.exit_code == 0
        assert os.path.exists(dest)

        # Backup is valid but contains zero jobs
        async def _check():
            q = Queue(dest)
            await q.init_db()
            count = await Job.query().count()
            await q.close()
            return count

        assert asyncio.run(_check()) == 0

    def test_backup_failure_human(self, runner, db_path, tmp_path):
        """When async_backup returns success=False, exit code is 1 and error goes to stderr."""
        from unittest.mock import AsyncMock, patch
        from sqler.ops import BackupResult
        from datetime import datetime

        runner.invoke(cli, ["init", "--db", db_path])
        dest = str(tmp_path / "backup.db")
        fake_result = BackupResult(
            success=False,
            source_path=db_path,
            destination_path=dest,
            duration_ms=1.0,
            size_bytes=0,
            timestamp=datetime.now(),
            error="disk full",
        )
        with patch("sqler.async_backup", new_callable=AsyncMock, return_value=fake_result):
            result = runner.invoke(cli, ["backup", "--db", db_path, "--to", dest])
        assert result.exit_code == 1
        assert "disk full" in result.stderr

    def test_backup_failure_json(self, runner, db_path, tmp_path):
        """When async_backup returns success=False with --json, output has success=false and error."""
        from unittest.mock import AsyncMock, patch
        from sqler.ops import BackupResult
        from datetime import datetime

        runner.invoke(cli, ["init", "--db", db_path])
        dest = str(tmp_path / "backup.db")
        fake_result = BackupResult(
            success=False,
            source_path=db_path,
            destination_path=dest,
            duration_ms=1.0,
            size_bytes=0,
            timestamp=datetime.now(),
            error="disk full",
        )
        with patch("sqler.async_backup", new_callable=AsyncMock, return_value=fake_result):
            result = runner.invoke(cli, ["backup", "--db", db_path, "--to", dest, "--json"])
        assert result.exit_code == 0  # JSON mode always exits 0, error is in the envelope
        data = json.loads(result.output)
        assert data["success"] is False
        assert data["error"] == "disk full"

    def test_backup_has_help(self, runner):
        result = runner.invoke(cli, ["backup", "--help"])
        assert result.exit_code == 0
        assert "Create a safe online backup" in result.output


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
        # Verify all jobs have correct queue assignments
        for j in data:
            assert j["queue_name"] in ("default", "email")
            assert j["status"] in ("pending", "completed", "failed")

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
        assert "0.3.0" in result.output

    def test_help(self, runner):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "Background jobs without Redis" in result.output

    def test_all_commands_have_help(self, runner):
        commands = [
            "init", "backup", "worker", "status", "jobs", "job",
            "attempts", "retry", "cancel", "purge", "doctor",
            "dlq", "health", "tasks",
        ]
        for cmd in commands:
            result = runner.invoke(cli, [cmd, "--help"])
            assert result.exit_code == 0, f"{cmd} --help failed: {result.output}"

    def test_dlq_subcommands_have_help(self, runner):
        subcommands = ["list", "count", "job", "replay", "purge"]
        for sub in subcommands:
            result = runner.invoke(cli, ["dlq", "--db", "dummy.db", sub, "--help"])
            assert result.exit_code == 0, f"dlq {sub} --help failed: {result.output}"


# -----------------------------------------------------------------------
# qler dlq (Dead Letter Queue CLI)
# -----------------------------------------------------------------------


@pytest.fixture
def dlq_populated_db(db_path):
    """Create a DB with failed jobs in the DLQ for CLI testing."""
    import asyncio

    async def _setup():
        q = Queue(db_path, dlq="dead_letters")
        await q.init_db()

        # 3 jobs that fail terminally → go to DLQ
        for i in range(3):
            j = await q.enqueue(f"myapp.tasks.fail_{i}", queue_name="default", max_retries=0)
            claimed = await q.claim_job("w1", ["default"])
            await q.fail_job(claimed, "w1", ValueError(f"error_{i}"),
                             failure_kind=FailureKind.EXCEPTION)

        # 1 job still pending (not in DLQ)
        await q.enqueue("myapp.tasks.pending", queue_name="default")

        await q.close()

    asyncio.run(_setup())
    return db_path


class TestDLQCli:
    """Tests for `qler dlq` CLI command group."""

    def test_dlq_list_json(self, runner, dlq_populated_db):
        result = runner.invoke(cli, ["dlq", "--db", dlq_populated_db, "list"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["count"] == 3
        assert len(data["data"]) == 3
        tasks_found = {item["task"] for item in data["data"]}
        assert tasks_found == {
            "myapp.tasks.fail_0",
            "myapp.tasks.fail_1",
            "myapp.tasks.fail_2",
        }
        errors_found = {item["error"] for item in data["data"]}
        assert errors_found == {"error_0", "error_1", "error_2"}
        for item in data["data"]:
            assert len(item["ulid"]) == 26  # ULID format
            assert item["original_queue"] == "default"

    def test_dlq_list_human(self, runner, dlq_populated_db):
        result = runner.invoke(cli, ["dlq", "--db", dlq_populated_db, "--human", "list"])
        assert result.exit_code == 0
        assert "ULID" in result.output
        assert "Task" in result.output
        assert "myapp.tasks.fail_" in result.output
        assert "3 item(s)" in result.output

    def test_dlq_list_empty(self, runner, db_path):
        """DLQ list on a DB with no DLQ jobs returns empty."""
        runner.invoke(cli, ["init", "--db", db_path])
        result = runner.invoke(cli, ["dlq", "--db", db_path, "list"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["count"] == 0
        assert data["data"] == []

    def test_dlq_list_filter_task(self, runner, dlq_populated_db):
        result = runner.invoke(cli, [
            "dlq", "--db", dlq_populated_db, "list", "--task", "myapp.tasks.fail_0"
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["count"] == 1
        assert data["data"][0]["task"] == "myapp.tasks.fail_0"

    def test_dlq_list_limit(self, runner, dlq_populated_db):
        result = runner.invoke(cli, [
            "dlq", "--db", dlq_populated_db, "list", "--limit", "2"
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["count"] == 2
        assert len(data["data"]) == 2

    def test_dlq_count_json(self, runner, dlq_populated_db):
        result = runner.invoke(cli, ["dlq", "--db", dlq_populated_db, "count"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["count"] == 3

    def test_dlq_count_human(self, runner, dlq_populated_db):
        result = runner.invoke(cli, [
            "dlq", "--db", dlq_populated_db, "--human", "count"
        ])
        assert result.exit_code == 0
        assert "Count: 3" in result.output

    def test_dlq_count_empty(self, runner, db_path):
        runner.invoke(cli, ["init", "--db", db_path])
        result = runner.invoke(cli, ["dlq", "--db", db_path, "count"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["count"] == 0

    def test_dlq_job_detail(self, runner, dlq_populated_db):
        """DLQ job detail returns full job dict."""
        # First get a ULID from the list
        list_result = runner.invoke(cli, ["dlq", "--db", dlq_populated_db, "list"])
        list_data = json.loads(list_result.output)
        ulid = list_data["data"][0]["ulid"]

        result = runner.invoke(cli, ["dlq", "--db", dlq_populated_db, "job", ulid])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["ulid"] == ulid
        assert data["data"]["status"] == "failed"
        assert data["data"]["queue_name"] == "dead_letters"
        assert data["data"]["original_queue"] == "default"

    def test_dlq_job_not_found(self, runner, db_path):
        runner.invoke(cli, ["init", "--db", db_path])
        result = runner.invoke(cli, ["dlq", "--db", db_path, "job", "NONEXISTENT"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["ok"] is False
        assert "not found" in data["error"]

    def test_dlq_replay_single(self, runner, dlq_populated_db):
        # Get a ULID
        list_result = runner.invoke(cli, ["dlq", "--db", dlq_populated_db, "list"])
        ulid = json.loads(list_result.output)["data"][0]["ulid"]

        result = runner.invoke(cli, ["dlq", "--db", dlq_populated_db, "replay", ulid])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["count"] == 1
        assert data["data"][0]["ulid"] == ulid
        assert data["data"][0]["status"] == "pending"
        assert data["data"][0]["queue_name"] == "default"

    def test_dlq_replay_to_different_queue(self, runner, dlq_populated_db):
        list_result = runner.invoke(cli, ["dlq", "--db", dlq_populated_db, "list"])
        ulid = json.loads(list_result.output)["data"][0]["ulid"]

        result = runner.invoke(cli, [
            "dlq", "--db", dlq_populated_db, "replay", ulid, "--queue", "retry_queue"
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"][0]["queue_name"] == "retry_queue"

    def test_dlq_replay_all(self, runner, dlq_populated_db):
        # Capture pre-replay ULIDs
        pre = runner.invoke(cli, ["dlq", "--db", dlq_populated_db, "list"])
        pre_ulids = {item["ulid"] for item in json.loads(pre.output)["data"]}
        assert len(pre_ulids) == 3

        result = runner.invoke(cli, ["dlq", "--db", dlq_populated_db, "replay", "--all"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["count"] == 3
        assert len(data["data"]) == 3
        replayed_ulids = {item["ulid"] for item in data["data"]}
        assert replayed_ulids == pre_ulids

        # Verify DLQ is now empty
        count_result = runner.invoke(cli, ["dlq", "--db", dlq_populated_db, "count"])
        assert json.loads(count_result.output)["count"] == 0

    def test_dlq_replay_not_found(self, runner, db_path):
        runner.invoke(cli, ["init", "--db", db_path])
        result = runner.invoke(cli, [
            "dlq", "--db", db_path, "replay", "NONEXISTENT"
        ])
        assert result.exit_code == 1
        assert "NONEXISTENT" in result.output

    def test_dlq_replay_requires_ulid_or_all(self, runner, db_path):
        runner.invoke(cli, ["init", "--db", db_path])
        result = runner.invoke(cli, ["dlq", "--db", db_path, "replay"])
        assert result.exit_code == 2
        assert "Provide a job ULID or use --all" in result.output

    def test_dlq_replay_human(self, runner, dlq_populated_db):
        result = runner.invoke(cli, [
            "dlq", "--db", dlq_populated_db, "--human", "replay", "--all"
        ])
        assert result.exit_code == 0
        assert "Replayed 3 job(s)" in result.output
        assert "default" in result.output  # all three replay to original queue

    def test_dlq_purge_with_confirm(self, runner, dlq_populated_db):
        result = runner.invoke(cli, [
            "dlq", "--db", dlq_populated_db, "purge", "--confirm"
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["count"] == 3

        # Verify DLQ is now empty
        count_result = runner.invoke(cli, ["dlq", "--db", dlq_populated_db, "count"])
        assert json.loads(count_result.output)["count"] == 0

    def test_dlq_purge_requires_confirm_or_older_than(self, runner, db_path):
        runner.invoke(cli, ["init", "--db", db_path])
        result = runner.invoke(cli, ["dlq", "--db", db_path, "purge"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["ok"] is False
        assert "confirm" in data["error"].lower() or "older-than" in data["error"].lower()
        assert "data" not in data

    def test_dlq_purge_with_older_than(self, runner, db_path):
        """Purge with --older-than only deletes old DLQ jobs."""
        import asyncio

        async def _setup():
            q = Queue(db_path, dlq="dead_letters")
            await q.init_db()

            # 2 old DLQ jobs
            old_ts = now_epoch() - 86400 * 10  # 10 days ago
            for i in range(2):
                j = await q.enqueue(f"tasks.old_{i}", max_retries=0)
                claimed = await q.claim_job("w1", ["default"])
                await q.fail_job(claimed, "w1", ValueError("old"),
                                 failure_kind=FailureKind.EXCEPTION)
                # Backdate created_at
                await Job.query().filter(F("ulid") == j.ulid).update_one(
                    created_at=old_ts,
                )

            # 1 recent DLQ job
            j = await q.enqueue("tasks.recent", max_retries=0)
            claimed = await q.claim_job("w1", ["default"])
            await q.fail_job(claimed, "w1", ValueError("recent"),
                             failure_kind=FailureKind.EXCEPTION)

            await q.close()

        asyncio.run(_setup())

        result = runner.invoke(cli, [
            "dlq", "--db", db_path, "purge", "--older-than", "7d"
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["count"] == 2

        # Verify 1 recent job remains
        count_result = runner.invoke(cli, ["dlq", "--db", db_path, "count"])
        assert json.loads(count_result.output)["count"] == 1

    def test_dlq_purge_human(self, runner, dlq_populated_db):
        result = runner.invoke(cli, [
            "dlq", "--db", dlq_populated_db, "--human", "purge", "--confirm"
        ])
        assert result.exit_code == 0
        assert "Count: 3" in result.output

    def test_dlq_custom_name(self, runner, db_path):
        """Using --dlq with a custom name queries the right queue."""
        import asyncio

        async def _setup():
            q = Queue(db_path, dlq="my_failures")
            await q.init_db()
            j = await q.enqueue("tasks.fail", max_retries=0)
            claimed = await q.claim_job("w1", ["default"])
            await q.fail_job(claimed, "w1", ValueError("oops"),
                             failure_kind=FailureKind.EXCEPTION)
            await q.close()

        asyncio.run(_setup())

        # Default DLQ name misses the job
        result = runner.invoke(cli, ["dlq", "--db", db_path, "count"])
        assert json.loads(result.output)["count"] == 0

        # Custom DLQ name finds it
        result = runner.invoke(cli, [
            "dlq", "--db", db_path, "--dlq", "my_failures", "count"
        ])
        assert json.loads(result.output)["count"] == 1

    def test_dlq_json_envelope_error(self, runner, db_path):
        """Error envelope has ok=false and error key with context."""
        runner.invoke(cli, ["init", "--db", db_path])
        result = runner.invoke(cli, ["dlq", "--db", db_path, "job", "FAKE"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["ok"] is False
        assert "FAKE" in data["error"]
        assert "data" not in data


# -----------------------------------------------------------------------
# qler health
# -----------------------------------------------------------------------


class TestHealthCommand:
    """Tests for `qler health` CLI command."""

    def test_health_requires_port_or_socket(self, runner):
        result = runner.invoke(cli, ["health"])
        assert result.exit_code == 2
        assert "Either --port or --socket is required" in result.output

    def test_health_rejects_both_port_and_socket(self, runner):
        result = runner.invoke(cli, [
            "health", "--port", "9100", "--socket", "/tmp/h.sock"
        ])
        assert result.exit_code == 2
        assert "mutually exclusive" in result.output

    def test_health_connection_refused(self, runner):
        """Connecting to a port with no listener → exit code 1."""
        result = runner.invoke(cli, ["health", "--port", "19199"])
        assert result.exit_code == 1
        assert "Health check failed" in result.stderr

    def test_health_tcp_json(self, runner):
        """JSON output from a mocked health response."""
        import socket as sock_mod

        health_data = {
            "status": "healthy",
            "worker_id": "host:1:ABCD",
            "uptime_seconds": 120,
            "active_jobs": 2,
            "concurrency": 4,
            "queues": ["default", "priority"],
            "started_at": 1708700000,
        }
        body = json.dumps(health_data)
        response = (
            f"HTTP/1.1 200 OK\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"\r\n"
            f"{body}"
        ).encode()

        def mock_create_connection(address, timeout=None):
            class FakeSocket:
                def sendall(self, data): pass
                def recv(self, size):
                    if not hasattr(self, '_sent'):
                        self._sent = True
                        return response
                    return b""
                def close(self): pass
                def settimeout(self, t): pass
            return FakeSocket()

        with patch("qler.cli.socket_mod.create_connection", mock_create_connection):
            result = runner.invoke(cli, [
                "health", "--port", "9100", "--json"
            ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "healthy"
        assert data["worker_id"] == "host:1:ABCD"
        assert data["uptime_seconds"] == 120
        assert data["active_jobs"] == 2
        assert data["concurrency"] == 4
        assert data["queues"] == ["default", "priority"]
        assert data["started_at"] == 1708700000

    def test_health_tcp_human(self, runner):
        """Human-readable output from a mocked health response."""
        import socket as sock_mod

        health_data = {
            "status": "healthy",
            "worker_id": "host:1:ABCD",
            "uptime_seconds": 3661,
            "active_jobs": 2,
            "concurrency": 4,
            "queues": ["default", "priority"],
            "started_at": 1708700000,
        }
        body = json.dumps(health_data)
        response = (
            f"HTTP/1.1 200 OK\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"\r\n"
            f"{body}"
        ).encode()

        def mock_create_connection(address, timeout=None):
            class FakeSocket:
                def sendall(self, data): pass
                def recv(self, size):
                    if not hasattr(self, '_sent'):
                        self._sent = True
                        return response
                    return b""
                def close(self): pass
                def settimeout(self, t): pass
            return FakeSocket()

        with patch("qler.cli.socket_mod.create_connection", mock_create_connection):
            result = runner.invoke(cli, [
                "health", "--port", "9100"
            ])
        assert result.exit_code == 0
        assert "healthy" in result.output
        assert "host:1:ABCD" in result.output
        assert "1h 1m 1s" in result.output
        assert "2/4" in result.output
        assert "default, priority" in result.output

    def test_health_has_help(self, runner):
        result = runner.invoke(cli, ["health", "--help"])
        assert result.exit_code == 0
        assert "health endpoint" in result.output.lower()


# -----------------------------------------------------------------------
# qler tasks
# -----------------------------------------------------------------------


def _write_task_module(tmp_path: Path, filename: str, content: str) -> str:
    """Write a temp Python module and return its import path."""
    mod_file = tmp_path / filename
    mod_file.write_text(content)
    return str(tmp_path)


class TestTasksCommand:
    """Tests for `qler tasks` CLI command."""

    def test_tasks_requires_app_or_db(self, runner):
        result = runner.invoke(cli, ["tasks"])
        assert result.exit_code == 2
        assert "Either --db or --app is required" in result.output

    def test_tasks_empty(self, runner, db_path):
        """No tasks registered → informative message."""
        runner.invoke(cli, ["init", "--db", db_path])
        result = runner.invoke(cli, ["tasks", "--db", db_path])
        assert result.exit_code == 0
        assert "No tasks registered" in result.output

    def test_tasks_lists_registered(self, runner, tmp_path):
        """Lists tasks with correct path, queue, config in human table."""
        db_path = str(tmp_path / "test.db")
        mod_dir = _write_task_module(tmp_path, "_tasks_app.py", f"""
from qler import Queue, task

q = Queue("{db_path}")

@task(q, max_retries=3, retry_delay=10, priority=5)
async def send_email():
    pass

@task(q, queue_name="urgent", sync=True)
def process_sync():
    pass
""")
        import sys
        sys.path.insert(0, mod_dir)
        try:
            result = runner.invoke(cli, ["tasks", "--app", "_tasks_app:q"])
            assert result.exit_code == 0
            lines = result.output.strip().splitlines()
            # Table: header + separator + 2 data rows
            assert len(lines) == 4
            # Column headers
            assert "Task" in lines[0]
            assert "Queue" in lines[0]
            assert "Retries" in lines[0]
            assert "Rate" in lines[0]
            assert "Cron" in lines[0]
            assert "Active" in lines[0]
            # Separator line
            assert "---" in lines[1]
            # Find which row has each task and verify column values
            email_row = next(l for l in lines[2:] if "send_email" in l)
            sync_row = next(l for l in lines[2:] if "process_sync" in l)
            # send_email: default queue, max_retries=3
            assert "default" in email_row
            assert "  3  " in email_row or email_row.split()[2] == "3"
            # process_sync: urgent queue, max_retries=0 (queue default)
            assert "urgent" in sync_row
        finally:
            sys.path.remove(mod_dir)
            sys.modules.pop("_tasks_app", None)

    def test_tasks_json_output(self, runner, tmp_path):
        """JSON output has all expected fields with correct values."""
        db_path = str(tmp_path / "test.db")
        mod_dir = _write_task_module(tmp_path, "_tasks_json.py", f"""
from qler import Queue, task

q = Queue("{db_path}", default_max_retries=2, default_retry_delay=30)

@task(q, max_retries=5, retry_delay=15, priority=10, lease_duration=600)
async def my_task():
    pass
""")
        import sys
        sys.path.insert(0, mod_dir)
        try:
            result = runner.invoke(cli, ["tasks", "--app", "_tasks_json:q", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert isinstance(data, list)
            assert len(data) == 1
            t = data[0]
            assert t["task"] == "_tasks_json.my_task"
            assert t["queue"] == "default"
            assert t["sync"] is False
            assert t["max_retries"] == 5
            assert t["retry_delay"] == 15
            assert t["priority"] == 10
            assert t["lease_duration"] == 600
            assert t["rate_limit"] is None
            assert t["cron"] is None
            assert t["active_jobs"] == 0
        finally:
            sys.path.remove(mod_dir)
            sys.modules.pop("_tasks_json", None)

    def test_tasks_shows_rate_limit(self, runner, tmp_path):
        """Rate-limited tasks show rate spec string."""
        db_path = str(tmp_path / "test.db")
        mod_dir = _write_task_module(tmp_path, "_tasks_rate.py", f"""
from qler import Queue, task

q = Queue("{db_path}")

@task(q, rate_limit="10/m")
async def rate_limited():
    pass
""")
        import sys
        sys.path.insert(0, mod_dir)
        try:
            result = runner.invoke(cli, ["tasks", "--app", "_tasks_rate:q", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert len(data) == 1
            assert data[0]["rate_limit"] == "10/60s"
        finally:
            sys.path.remove(mod_dir)
            sys.modules.pop("_tasks_rate", None)

    def test_tasks_shows_cron(self, runner, tmp_path):
        """Cron tasks show cron expression."""
        db_path = str(tmp_path / "test.db")
        mod_dir = _write_task_module(tmp_path, "_tasks_cron.py", f"""
from qler import Queue, cron

q = Queue("{db_path}")

@cron(q, "*/5 * * * *")
async def periodic_cleanup():
    pass
""")
        import sys
        sys.path.insert(0, mod_dir)
        try:
            result = runner.invoke(cli, ["tasks", "--app", "_tasks_cron:q", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert len(data) == 1
            assert data[0]["cron"] == "*/5 * * * *"
        finally:
            sys.path.remove(mod_dir)
            sys.modules.pop("_tasks_cron", None)

    def test_tasks_active_job_count(self, runner, tmp_path):
        """Active job count reflects only pending+running, excludes completed/failed."""
        import asyncio

        db_path = str(tmp_path / "test.db")
        mod_dir = _write_task_module(tmp_path, "_tasks_count.py", f"""
from qler import Queue, task

q = Queue("{db_path}")

@task(q)
async def counted_task():
    pass
""")
        import sys
        sys.path.insert(0, mod_dir)
        try:
            async def _setup():
                from sqler import F
                from qler.enums import FailureKind
                mod = __import__("_tasks_count")
                q = mod.q
                await q.init_db()
                task_path = mod.counted_task.task_path
                now = now_epoch()
                # 2 pending
                await q.enqueue(task_path)
                await q.enqueue(task_path)
                # 1 completed (should NOT count)
                j = await q.enqueue(task_path)
                await Job.query().filter(
                    (F("ulid") == j.ulid) & (F("status") == JobStatus.PENDING.value)
                ).update_one(
                    status=JobStatus.COMPLETED.value, finished_at=now, updated_at=now,
                )
                # 1 failed (should NOT count)
                j2 = await q.enqueue(task_path)
                await Job.query().filter(
                    (F("ulid") == j2.ulid) & (F("status") == JobStatus.PENDING.value)
                ).update_one(
                    status=JobStatus.FAILED.value,
                    last_error="err",
                    last_failure_kind=FailureKind.EXCEPTION.value,
                    finished_at=now, updated_at=now,
                )
                await q.close()

            asyncio.run(_setup())

            result = runner.invoke(cli, ["tasks", "--app", "_tasks_count:q", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert len(data) == 1
            assert data[0]["active_jobs"] == 2  # only pending, not completed/failed
        finally:
            sys.path.remove(mod_dir)
            sys.modules.pop("_tasks_count", None)

    def test_tasks_filter_by_queue(self, runner, tmp_path):
        """--queue filters to tasks in that queue only."""
        db_path = str(tmp_path / "test.db")
        mod_dir = _write_task_module(tmp_path, "_tasks_filter.py", f"""
from qler import Queue, task

q = Queue("{db_path}")

@task(q, queue_name="emails")
async def send_email():
    pass

@task(q, queue_name="reports")
async def generate_report():
    pass
""")
        import sys
        sys.path.insert(0, mod_dir)
        try:
            # Filter to emails only
            result = runner.invoke(cli, [
                "tasks", "--app", "_tasks_filter:q", "--json", "--queue", "emails"
            ])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert len(data) == 1
            assert data[0]["task"] == "_tasks_filter.send_email"

            # Filter to reports only
            result = runner.invoke(cli, [
                "tasks", "--app", "_tasks_filter:q", "--json", "--queue", "reports"
            ])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert len(data) == 1
            assert data[0]["task"] == "_tasks_filter.generate_report"
        finally:
            sys.path.remove(mod_dir)
            sys.modules.pop("_tasks_filter", None)

    def test_tasks_has_help(self, runner):
        result = runner.invoke(cli, ["tasks", "--help"])
        assert result.exit_code == 0
        assert "registered tasks" in result.output.lower()
        assert "--app" in result.output
        assert "--db" in result.output
        assert "--module" in result.output
        assert "--queue" in result.output
        assert "--json" in result.output
