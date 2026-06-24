# -*- coding: utf-8 -*-
"""
Shared configuration constants for the backend.
Centralizes PROJECT_ROOT and database paths to avoid redundant calculation
in every API module.
"""
import os

# Project root: go up 3 levels from web/backend/config.py
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Database paths
USER_DB_PATH = os.path.join(PROJECT_ROOT, "user_data.db")
MARKET_DB_PATH = os.path.join(PROJECT_ROOT, "market.db")

# Data file paths
STOCK_INDUSTRY_CSV = os.path.join(PROJECT_ROOT, "stock_industry_base.csv")
MARKET_ENRICHED_CSV = os.path.join(PROJECT_ROOT, "market_enriched.csv")

# Config directory
CONFIG_DIR = os.path.join(PROJECT_ROOT, "config")
SCORING_YAML = os.path.join(CONFIG_DIR, "scoring.yaml")

# Logs directory
LOGS_DIR = os.path.join(PROJECT_ROOT, "logs")
