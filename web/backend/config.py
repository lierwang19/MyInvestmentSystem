# -*- coding: utf-8 -*-
"""
Shared configuration constants for the backend.
Centralizes PROJECT_ROOT and database paths to avoid redundant calculation
in every API module.
"""
import os
import logging

logger = logging.getLogger(__name__)

# Project root: go up 3 levels from web/backend/config.py
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Runtime data directory (mounted as a Docker volume; keeps DB + WAL files together)
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
os.makedirs(DATA_DIR, exist_ok=True)


def resolve_db_path(filename: str, root: str = PROJECT_ROOT) -> str:
    """Resolve the canonical DB location under ``<root>/data``.

    New location is ``data/<filename>`` so the SQLite file and its WAL/SHM
    sidecars always live in the same (Docker-mounted) directory. Falls back
    to the legacy project-root location with a warning, so existing
    deployments keep working until the files are moved.
    """
    data_dir = os.path.join(root, "data")
    new_path = os.path.join(data_dir, filename)
    if os.path.exists(new_path):
        return new_path
    legacy_path = os.path.join(root, filename)
    if os.path.exists(legacy_path):
        logger.warning(
            "检测到旧位置数据库 %s，建议将其移动到 %s "
            "(data/ 目录已挂载为 Docker 卷，可避免 WAL 文件丢失)",
            legacy_path, new_path,
        )
        return legacy_path
    return new_path


# Database paths
USER_DB_PATH = resolve_db_path("user_data.db")
MARKET_DB_PATH = resolve_db_path("market.db")

# B1: 启动时打印实际使用的数据库路径。若看到"不存在，首次使用时创建"
# 而你明明有旧数据，说明升级时忘了把 DB 移到 data/（见 CLAUDE.md 迁移步骤），
# 此时不要写入，先停服迁移，否则会产生空库分叉。
for _label, _p in (("user_data.db", USER_DB_PATH), ("market.db", MARKET_DB_PATH)):
    logger.info("%s -> %s（%s）", _label, _p,
                "已存在" if os.path.exists(_p) else "不存在，首次使用时自动创建")

# Data file paths
STOCK_INDUSTRY_CSV = os.path.join(PROJECT_ROOT, "stock_industry_base.csv")
MARKET_ENRICHED_CSV = os.path.join(PROJECT_ROOT, "market_enriched.csv")

# Config directory
CONFIG_DIR = os.path.join(PROJECT_ROOT, "config")
SCORING_YAML = os.path.join(CONFIG_DIR, "scoring.yaml")

# Logs directory
LOGS_DIR = os.path.join(PROJECT_ROOT, "logs")
