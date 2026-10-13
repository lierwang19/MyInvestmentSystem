"""Standard-library regression tests; never open a workspace business database.

API/scheduler functions are compiled from source with isolated service stubs so
these tests also run in review environments without FastAPI/APScheduler.
"""
import ast
import asyncio
import contextlib
import io
import logging
import os
from pathlib import Path, PurePosixPath
import sqlite3
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
import zipfile

from web.backend import database


ROOT = Path(__file__).resolve().parents[2]


def load_functions(relative_path, names, namespace, keep_decorators=False):
    tree = ast.parse((ROOT / relative_path).read_text())
    selected = [node for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    if not keep_decorators:
        for node in selected:
            node.decorator_list = []
    exec(compile(ast.Module(body=selected, type_ignores=[]), relative_path, "exec"), namespace)
    return namespace


def make_db(path, balance):
    conn = sqlite3.connect(path)
    try:
        database._create_tables(conn)
        conn.execute("INSERT INTO accounts(id, name, cash_balance) VALUES ('test', 'test', ?)", (balance,))
        conn.commit()
    finally:
        conn.close()


def read_balance(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT cash_balance FROM accounts WHERE id='test'").fetchone()[0]
    finally:
        conn.close()


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "data").mkdir()
        self.live = self.root / "data/user_data.db"
        self.snapshot = self.root / "snapshot.db"
        make_db(self.live, 1)
        make_db(self.snapshot, 2)
        self.env = load_functions("web/backend/api/system.py", {
            "restore_system_data", "_collect_restore_pairs", "_normalize_restore_member_name",
            "_resolve_restore_target", "backup_system_data",
        }, {"os": os, "Path": Path, "PurePosixPath": PurePosixPath,
            "PROJECT_ROOT": str(self.root), "USER_DB_PATH": str(self.live), "UploadFile": object,
            "sh_compact_timestamp": lambda: "test"})

    def upload(self, payload, name="user_data.db"):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(name, payload)

        class Upload:
            filename = "backup.zip"
            async def read(self):
                return archive.getvalue()
        return asyncio.run(self.env["restore_system_data"](Upload()))

    def test_restore_stages_old_zip_without_touching_live_wal(self):
        active = sqlite3.connect(self.live)
        self.addCleanup(active.close)
        active.execute("PRAGMA journal_mode=WAL")
        active.execute("UPDATE accounts SET cash_balance=3")
        active.commit()
        wal = Path(str(self.live) + "-wal")
        wal_bytes = wal.read_bytes()
        result = self.upload(self.snapshot.read_bytes())
        self.assertEqual(result["status"], "success")
        self.assertEqual(read_balance(self.live), 3)
        self.assertEqual(wal.read_bytes(), wal_bytes)
        self.assertTrue(Path(database.pending_restore_path(self.live)).is_file())

    def test_startup_applies_snapshot_without_replacing_live_inode(self):
        self.upload(self.snapshot.read_bytes(), "data/user_data.db")
        inode = self.live.stat().st_ino
        with patch.object(database, "DB_PATH", str(self.live)):
            database.init_db()
        self.assertEqual(read_balance(self.live), 2)
        self.assertEqual(self.live.stat().st_ino, inode)
        self.assertFalse(Path(database.pending_restore_path(self.live)).exists())

    def test_startup_refuses_active_reader_and_retains_snapshot(self):
        conn = sqlite3.connect(self.live)
        self.addCleanup(conn.close)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("BEGIN")
        conn.execute("SELECT * FROM accounts").fetchall()
        self.upload(self.snapshot.read_bytes())
        connect = sqlite3.connect

        def fast_connect(path, *args, **kwargs):
            if str(path) == str(self.live):
                kwargs["timeout"] = 0
            return connect(path, *args, **kwargs)

        with patch.object(database, "DB_PATH", str(self.live)), patch.object(database.sqlite3, "connect", fast_connect):
            with self.assertRaises(sqlite3.OperationalError):
                database.apply_pending_restore()
        self.assertTrue(Path(database.pending_restore_path(self.live)).is_file())
        self.assertEqual(conn.execute("SELECT cash_balance FROM accounts").fetchone()[0], 1)

    def test_corrupt_integrity_result_is_rejected(self):
        conn = sqlite3.connect(self.snapshot)
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute("UPDATE sqlite_master SET rootpage=(SELECT rootpage FROM sqlite_master WHERE name='accounts') WHERE name='brokers'")
        conn.commit()
        conn.close()
        result = self.upload(self.snapshot.read_bytes())
        self.assertEqual(result["status"], "error")
        self.assertEqual(read_balance(self.live), 1)
        self.assertFalse(Path(database.pending_restore_path(self.live)).exists())

    def test_empty_and_unrelated_databases_are_rejected(self):
        for payload in (b"", b"not SQLite"):
            with self.subTest(payload=payload):
                self.assertEqual(self.upload(payload)["status"], "error")
        other = self.root / "other.db"
        conn = sqlite3.connect(other)
        conn.execute("CREATE TABLE unrelated(v)")
        conn.close()
        self.assertEqual(self.upload(other.read_bytes())["status"], "error")
        self.assertEqual(read_balance(self.live), 1)

    def test_legacy_active_path_is_rejected(self):
        self.env["USER_DB_PATH"] = str(self.root / "user_data.db")
        self.assertEqual(self.upload(self.snapshot.read_bytes())["status"], "error")

    def test_duplicate_alias_targets_are_rejected(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("user_data.db", b"first")
            zf.writestr("data/user_data.db", b"second")
        with zipfile.ZipFile(archive) as zf:
            with self.assertRaises(ValueError):
                self.env["_collect_restore_pairs"](zf)

    def test_backup_includes_wal_commits_with_pinned_reader(self):
        writer = sqlite3.connect(self.live)
        self.addCleanup(writer.close)
        writer.execute("PRAGMA journal_mode=WAL")
        reader = sqlite3.connect(self.live)
        self.addCleanup(reader.close)
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM accounts").fetchall()
        writer.execute("UPDATE accounts SET cash_balance=5")
        writer.commit()

        class Response:
            def __init__(self, content, **kwargs):
                self.content = content
        responses = types.ModuleType("fastapi.responses")
        responses.StreamingResponse = Response
        with patch.dict(sys.modules, {"fastapi.responses": responses}):
            response = self.env["backup_system_data"]()
        with zipfile.ZipFile(response.content) as zf:
            self.snapshot.write_bytes(zf.read("data/user_data.db"))
        self.assertEqual(read_balance(self.snapshot), 5)


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.calendar = types.ModuleType("web.backend.services.trading_calendar")
        self.calendar.is_a_trading_day = lambda: True
        self.calendar.is_hk_trading_day = lambda: True
        scoring = types.ModuleType("scoring")
        scoring.run_scoring = lambda **kwargs: (self.calls.append("scoring") or {"pool_count": 1})
        engine = types.ModuleType("engine")
        engine.run_strategy = lambda **kwargs: (self.calls.append("strategy") or types.SimpleNamespace(error=None, signals=[], decisions=[]))
        modules = {self.calendar.__name__: self.calendar, "scoring": scoring, "engine": engine}
        patcher = patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.env = {"os": os, "sys": sys, "PROJECT_ROOT": "/tmp/test", "USER_DB_PATH": "/tmp/unused",
                    "logger": logging.getLogger("test"), "SCHEDULER_LAST_RUN": {},
                    "_scheduler_day_key": lambda: "20261008", "_scheduler_hm": lambda: "18:05"}
        for name in ("_market_update_done", "_valuation_done", "_factor_done", "_scoring_done"):
            event = threading.Event()
            event.set()
            self.env[name] = event
        load_functions("web/backend/scheduler.py", {"_upstream_ok_today", "_complete_job", "_wait_for_upstream",
                       "run_valuation_build", "run_daily_scoring", "run_daily_strategy", "run_daily_asset_snapshot"}, self.env, keep_decorators=True)

    def test_stale_upstream_skips_valuation_scoring_and_strategy(self):
        self.env["SCHEDULER_LAST_RUN"]["market_update"] = {"date": "20261007", "status": "ok"}
        for name in ("run_valuation_build", "run_daily_scoring", "run_daily_strategy", "run_daily_asset_snapshot"):
            self.env[name]()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.env["SCHEDULER_LAST_RUN"]["asset_snapshot"]["status"], "skipped")
        self.assertTrue(self.env["_valuation_done"].is_set())
        self.assertTrue(self.env["_scoring_done"].is_set())

    def test_fresh_factor_runs_scoring_and_records_date(self):
        self.env["SCHEDULER_LAST_RUN"]["factor_build"] = {"date": "20261008", "status": "ok"}
        self.env["run_daily_scoring"]()
        self.assertEqual(self.calls, ["scoring"])
        self.assertTrue(self.env["_upstream_ok_today"]("scoring"))

    def test_scoring_failure_blocks_strategy_and_releases_event(self):
        self.env["SCHEDULER_LAST_RUN"]["factor_build"] = {"date": "20261008", "status": "ok"}
        with patch.object(sys.modules["scoring"], "run_scoring", side_effect=RuntimeError("failed")):
            self.env["run_daily_scoring"]()
        self.env["run_daily_strategy"]()
        self.assertEqual(self.calls, [])
        self.assertTrue(self.env["_scoring_done"].is_set())

    def test_completion_event_released_on_exception(self):
        event = threading.Event()
        @self.env["_complete_job"](event)
        def fail():
            raise RuntimeError("failed")
        with self.assertRaises(RuntimeError):
            fail()
        self.assertTrue(event.is_set())

    def test_strategy_runs_for_fresh_scoring_and_hk_only_day(self):
        @contextlib.contextmanager
        def get_db():
            conn = sqlite3.connect(":memory:")
            conn.execute("CREATE TABLE accounts(id, created_at)")
            try:
                yield conn
            finally:
                conn.close()
        with patch.object(database, "get_db", get_db):
            self.env["SCHEDULER_LAST_RUN"]["scoring"] = {"date": "20261008", "status": "ok"}
            self.env["run_daily_strategy"]()
            self.calendar.is_a_trading_day = lambda: False
            self.env["SCHEDULER_LAST_RUN"].clear()
            self.env["run_daily_strategy"]()
        self.assertEqual(self.calls, ["strategy", "strategy"])


class MigrationTests(unittest.TestCase):
    def run_migration(self, root):
        documentation = (ROOT / "CLAUDE.md").read_text().split("### Docker 升级迁移", 1)[1]
        script = documentation.split("```bash\n", 1)[1].split("```", 1)[0]
        commands = root / "bin"
        commands.mkdir()
        compose = commands / "docker-compose"
        compose.write_text('#!/bin/sh\ncase "$1" in\nup) touch started;;\nlogs) echo "user_data.db -> /app/data/user_data.db（已存在）";;\nesac\n')
        compose.chmod(0o755)
        env = dict(os.environ, PATH=str(commands) + os.pathsep + os.environ["PATH"])
        return subprocess.run(["bash", "-c", script], cwd=root, env=env,
                              capture_output=True, encoding="utf-8", errors="replace", timeout=30)

    def test_migration_moves_optional_user_sidecars(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_db(root / "market.db", 1)
            make_db(root / "user_data.db", 2)
            writer = sqlite3.connect(root / "user_data.db")
            try:
                writer.execute("PRAGMA journal_mode=WAL")
                writer.execute("UPDATE accounts SET cash_balance=3")
                writer.commit()
                # Copy a committed WAL fixture into the offline migration root.
                fixture = root / "fixture"
                fixture.mkdir()
                for suffix in ("", "-wal", "-shm"):
                    (fixture / ("user_data.db" + suffix)).write_bytes((root / ("user_data.db" + suffix)).read_bytes())
            finally:
                writer.close()
            for suffix in ("", "-wal", "-shm"):
                os.replace(fixture / ("user_data.db" + suffix), root / ("user_data.db" + suffix))
            result = self.run_migration(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((root / "started").exists())
            self.assertEqual(read_balance(root / "data/user_data.db"), 3)
            self.assertFalse((root / "user_data.db-wal").exists())

    def test_existing_target_aborts_before_moving_or_starting(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_db(root / "market.db", 1)
            make_db(root / "user_data.db", 2)
            (root / "data").mkdir()
            make_db(root / "data/user_data.db", 9)
            result = self.run_migration(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue((root / "market.db").exists())
            self.assertTrue((root / "user_data.db").exists())
            self.assertFalse((root / "started").exists())
            self.assertEqual(read_balance(root / "data/user_data.db"), 9)


if __name__ == "__main__":
    unittest.main()
