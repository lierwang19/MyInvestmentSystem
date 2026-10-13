"""Offline backend tests with disposable databases, including during collection."""
import os
from pathlib import Path
import socket
import sqlite3
import tempfile
from urllib.parse import unquote, urlsplit

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def pytest_configure(config):
    # Collection imports API modules which capture these paths as constants.
    # Redirect them before importing any application modules or collecting tests.
    temp = tempfile.TemporaryDirectory(prefix="myinvestment-tests-")
    config._investment_test_temp = temp
    root = Path(temp.name)
    from web.backend import config as app_config
    app_config.DATA_DIR = str(root)
    app_config.USER_DB_PATH = str(root / "user_data.db")
    app_config.MARKET_DB_PATH = str(root / "market.db")

    from web.backend import database
    database.DB_PATH = app_config.USER_DB_PATH
    database.MARKET_DB_PATH = app_config.MARKET_DB_PATH

    connect = sqlite3.connect

    def safe_connect(path, *args, **kwargs):
        name = os.fsdecode(path)
        if name != ":memory:" and not (kwargs.get("uri") and name.startswith("file:") and
                                       ("mode=memory" in name or name.startswith("file::memory:"))):
            target = unquote(urlsplit(name).path) if kwargs.get("uri") and name.startswith("file:") else name
            resolved = Path(target).resolve()
            coverage_artifact = (
                resolved.parent == PROJECT_ROOT / "test-results"
                and (resolved.name == ".coverage" or resolved.name.startswith(".coverage."))
            )
            if (resolved == PROJECT_ROOT or PROJECT_ROOT in resolved.parents) and not coverage_artifact:
                raise RuntimeError("测试禁止连接工作区数据库；请使用 tmp_path 或内存数据库")
        return connect(path, *args, **kwargs)

    config._investment_original_connect = connect
    sqlite3.connect = safe_connect

    def no_network(*args, **kwargs):
        raise RuntimeError("后端测试默认离线；请 mock 外部行情、HTTP 或其他网络调用")

    config._investment_original_socket_connect = socket.socket.connect
    config._investment_original_socket_connect_ex = socket.socket.connect_ex
    socket.socket.connect = no_network
    socket.socket.connect_ex = no_network


@pytest.fixture(autouse=True)
def isolated_user_database(tmp_path, monkeypatch):
    from web.backend import database
    path = tmp_path / "user_data.db"
    monkeypatch.setattr(database, "DB_PATH", str(path))
    monkeypatch.setenv("SINGLE_USER_MODE", "")
    monkeypatch.setenv("ADMIN_TOKEN", "")
    conn = sqlite3.connect(path)
    try:
        database._create_tables(conn)
    finally:
        conn.close()


def pytest_unconfigure(config):
    if hasattr(config, "_investment_original_connect"):
        sqlite3.connect = config._investment_original_connect
        socket.socket.connect = config._investment_original_socket_connect
        socket.socket.connect_ex = config._investment_original_socket_connect_ex
    if hasattr(config, "_investment_test_temp"):
        config._investment_test_temp.cleanup()
