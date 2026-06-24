# -*- coding: utf-8 -*-
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
import sys
import os
import time
import logging

# ── Logging Configuration ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("invest")

# Add project root and scripts dirs to path so we can import scripts if needed
_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(_root)
sys.path.append(os.path.join(_root, "scripts", "etl"))
sys.path.append(os.path.join(_root, "scripts", "scoring"))
sys.path.append(os.path.join(_root, "scripts", "strategy"))
sys.path.append(os.path.join(_root, "scripts", "tools"))

from web.backend.api import upload, portfolio, analysis, system, brokers, accounts_db, transactions, transactions_archive, market, market_kline, settings, assets, quotes, stock_pool, risk, import_flow, strategy, backtest, t_trading, screenshot_import, hk_stocks, watchlist, ipo, ai_analysis, ai_briefing
from web.backend.database import init_db
from web.backend.migrate import add_stamp_tax_column, migrate_brokers_transfer_fee, migrate_stock_industry_columns, migrate_strategy_columns, migrate_holdings_buy_pb, migrate_t_trading_table, migrate_t_trading_trade_side, migrate_hk_stocks_table, migrate_watchlist_table, migrate_ipo_calendar_table, migrate_archived_holdings_unique
from web.backend.security import ADMIN_TOKEN_HEADER, is_protected_write_request, validate_admin_token

# Initialize database on startup (creates tables if not exist)
init_db()

# Run migrations to add any missing columns
add_stamp_tax_column()
migrate_brokers_transfer_fee()  # add transfer_fee_rate column to brokers (idempotent)
migrate_stock_industry_columns()
migrate_strategy_columns()
migrate_holdings_buy_pb()  # add buy_pb column to holdings (idempotent)
migrate_t_trading_table()  # ensure t_trading_items table + removed_at column exist (idempotent)
migrate_t_trading_trade_side()  # add trade_side column + rebuild UNIQUE constraint (idempotent)
migrate_hk_stocks_table()  # ensure hk_stocks table exists (idempotent)
migrate_watchlist_table()  # ensure watchlist_stocks table exists (idempotent)
migrate_ipo_calendar_table()  # ensure ipo_calendar table exists (idempotent)
migrate_archived_holdings_unique()  # add UNIQUE(account_id, ts_code) to archived_holdings (idempotent)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Modern lifecycle: startup and shutdown logic"""
    from web.backend.scheduler import start_scheduler
    from web.backend.api.quotes import start_poller, stop_poller

    logger.info("🚀 Starting MyInvestmentSystem backend...")
    scheduler = start_scheduler()
    start_poller()
    logger.info("✅ All services started")
    yield
    logger.info("🛑 Shutting down services...")
    stop_poller()
    try:
        from web.backend.drip_sync import shutdown_drip
        shutdown_drip()
    except Exception:
        pass
    scheduler.shutdown()
    logger.info("✅ Clean shutdown complete")

app = FastAPI(
    title="MyInvestmentSystem API",
    description="Backend for Quantitative Investment System",
    version="1.0.0",
    lifespan=lifespan
)

# ── Global Exception Handlers ──
from web.backend.response import APIError, api_error_handler, http_exception_handler, unhandled_exception_handler
app.add_exception_handler(APIError, api_error_handler)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)

# ── Request Logging Middleware ──
class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        duration_ms = (time.time() - start) * 1000

        # Skip noisy polling endpoints
        path = request.url.path
        if path in ("/api/quotes/realtime", "/api/quotes/status"):
            return response

        logger.info(
            f"{request.method} {path} → {response.status_code} ({duration_ms:.0f}ms)"
        )
        return response

app.add_middleware(RequestLoggingMiddleware)


class AdminWriteAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if is_protected_write_request(request.method, request.url.path):
            try:
                validate_admin_token(request.headers.get(ADMIN_TOKEN_HEADER))
            except HTTPException as exc:
                return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        return await call_next(request)


app.add_middleware(AdminWriteAuthMiddleware)

# CORS (configurable via env var CORS_ORIGINS, default localhost only)
cors_origins = os.environ.get("CORS_ORIGINS", "http://localhost:8080,http://localhost:8001").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(upload.router, prefix="/api", tags=["Files"])
app.include_router(portfolio.router, prefix="/api", tags=["Portfolio"])
app.include_router(analysis.router, prefix="/api", tags=["Analysis"])
app.include_router(system.router, prefix="/api", tags=["System"])
app.include_router(brokers.router, prefix="/api", tags=["Brokers"])
app.include_router(accounts_db.router, prefix="/api", tags=["AccountsDB"])
app.include_router(transactions.router, prefix="/api", tags=["Transactions"])
app.include_router(transactions_archive.router, prefix="/api", tags=["Archive"])
app.include_router(market.router, prefix="/api", tags=["Market"])
app.include_router(market_kline.router, prefix="/api", tags=["Kline"])
app.include_router(settings.router, prefix="/api", tags=["Settings"])
app.include_router(assets.router, prefix="/api", tags=["Assets"])
app.include_router(quotes.router, prefix="/api", tags=["Quotes"])
app.include_router(stock_pool.router, prefix="/api", tags=["StockPool"])
app.include_router(risk.router, prefix="/api", tags=["Risk"])
app.include_router(import_flow.router, prefix="/api", tags=["Import"])
app.include_router(strategy.router, prefix="/api", tags=["Strategy"])
app.include_router(backtest.router, prefix="/api", tags=["Backtest"])
app.include_router(t_trading.router, prefix="/api", tags=["TTrading"])
app.include_router(screenshot_import.router, prefix="/api", tags=["ScreenshotImport"])
app.include_router(hk_stocks.router, prefix="/api", tags=["HKStocks"])
app.include_router(watchlist.router, prefix="/api", tags=["Watchlist"])
app.include_router(ipo.router, prefix="/api", tags=["IPO"])
app.include_router(ai_analysis.router, prefix="/api", tags=["AI"])
app.include_router(ai_briefing.router, prefix="/api", tags=["AIBriefing"])

@app.get("/")
def read_root():
    return {
        "system": "MyInvestmentSystem",
        "status": "running", 
        "backend": "FastAPI"
    }
