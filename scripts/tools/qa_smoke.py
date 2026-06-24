#!/usr/bin/env python3
"""
L1 smoke QA for MyInvestmentSystem.

Usage:
  python3 scripts/tools/qa_smoke.py
  python3 scripts/tools/qa_smoke.py --skip-frontend

Design:
  - Frontend check uses host URL because the frontend is exposed on :8080.
  - Backend checks run inside the running `invest_backend` container to avoid
    flaky host port mapping on :8001.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import textwrap
import urllib.error
import urllib.request


FRONTEND_URL = "http://127.0.0.1:8080/"
BACKEND_CONTAINER = "invest_backend"


def _check_frontend(url: str) -> None:
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            body = resp.read(4096).decode("utf-8", "ignore")
    except Exception as exc:
        raise RuntimeError(f"frontend check failed: {exc}") from exc

    if resp.status != 200:
        raise RuntimeError(f"frontend returned HTTP {resp.status}")
    if "<div id=\"app\"></div>" not in body:
        raise RuntimeError("frontend index.html missing app mount node")
    print(f"[ok] frontend {url}")


def _run_backend_smoke(container: str) -> None:
    script = textwrap.dedent(
        r"""
        import json
        import os
        import sqlite3
        import time
        import urllib.error
        import urllib.request
        import uuid

        BASE = "http://127.0.0.1:8000"
        USER_DB = "/app/user_data.db"
        TEMP_ACCOUNT = "QA-SMOKE-" + uuid.uuid4().hex[:6].upper()
        ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "").strip()

        def req(method, path, data=None, headers=None, timeout=60):
            headers = headers or {}
            if ADMIN_TOKEN:
                headers["X-Admin-Token"] = ADMIN_TOKEN
            req_obj = urllib.request.Request(
                BASE + path,
                data=data,
                method=method,
                headers=headers,
            )
            t0 = time.perf_counter()
            try:
                with urllib.request.urlopen(req_obj, timeout=timeout) as resp:
                    body = resp.read().decode("utf-8", "ignore")
                    elapsed = time.perf_counter() - t0
                    return resp.status, json.loads(body), elapsed
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "ignore")
                elapsed = time.perf_counter() - t0
                try:
                    payload = json.loads(body)
                except Exception:
                    payload = {"raw": body}
                return exc.code, payload, elapsed

        def multipart(fields, files):
            boundary = "----WebKitFormBoundary" + uuid.uuid4().hex
            parts = []
            for name, value in fields.items():
                parts.append(f"--{boundary}\r\n".encode())
                parts.append(
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
                )
                parts.append(str(value).encode())
                parts.append(b"\r\n")
            for name, filename, content, content_type in files:
                parts.append(f"--{boundary}\r\n".encode())
                parts.append(
                    f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode()
                )
                parts.append(f"Content-Type: {content_type}\r\n\r\n".encode())
                parts.append(content)
                parts.append(b"\r\n")
            parts.append(f"--{boundary}--\r\n".encode())
            return b"".join(parts), f"multipart/form-data; boundary={boundary}"

        def assert_http_200(path, timeout=60):
            status, body, elapsed = req("GET", path, timeout=timeout)
            if status != 200:
                raise AssertionError(f"{path} returned {status}: {body}")
            print(f"[ok] GET {path} {elapsed:.3f}s")

        def cleanup():
            conn = sqlite3.connect(USER_DB)
            cur = conn.cursor()
            cur.execute("DELETE FROM t_trading_items WHERE account_id = ?", (TEMP_ACCOUNT,))
            cur.execute("DELETE FROM transactions WHERE account_id = ?", (TEMP_ACCOUNT,))
            cur.execute("DELETE FROM capital_flows WHERE account_id = ?", (TEMP_ACCOUNT,))
            cur.execute("DELETE FROM holdings WHERE account_id = ?", (TEMP_ACCOUNT,))
            cur.execute("DELETE FROM archived_holdings WHERE account_id = ?", (TEMP_ACCOUNT,))
            cur.execute("DELETE FROM accounts WHERE id = ?", (TEMP_ACCOUNT,))
            conn.commit()
            conn.close()

        try:
            assert_http_200("/api/accounts-db")
            assert_http_200("/api/analysis/holdings/A-02")
            assert_http_200("/api/analysis/stock-pool")
            assert_http_200("/api/market/current")
            assert_http_200("/api/system/status")
            assert_http_200("/api/quotes/status")

            status, body, elapsed = req(
                "POST",
                "/api/accounts-db",
                data=json.dumps({"name": TEMP_ACCOUNT, "remarks": "qa smoke temp"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            if status != 200:
                raise AssertionError(f"create account failed: {status} {body}")
            print(f"[ok] POST /api/accounts-db {elapsed:.3f}s")

            holdings_csv = (
                "证券代码,证券名称,证券数量,成本价\n"
                "000001,平安银行,100,10.50\n"
                "600000,浦发银行,200,9.80\n"
            ).encode("utf-8")
            body_bytes, content_type = multipart(
                {},
                [("file", "holdings.csv", holdings_csv, "text/csv")],
            )
            status, body, elapsed = req(
                "POST",
                f"/api/upload/holdings?account_id={TEMP_ACCOUNT}",
                data=body_bytes,
                headers={"Content-Type": content_type},
                timeout=120,
            )
            if status != 200 or body.get("migrated_to_db") != 2:
                raise AssertionError(f"upload holdings failed: {status} {body}")
            print(f"[ok] POST /api/upload/holdings {elapsed:.3f}s")

            conn = sqlite3.connect(USER_DB)
            cur = conn.cursor()
            rows = cur.execute(
                "SELECT ts_code, quantity FROM holdings WHERE account_id = ? ORDER BY ts_code",
                (TEMP_ACCOUNT,),
            ).fetchall()
            conn.close()
            if rows != [("000001.SZ", 100), ("600000.SH", 200)]:
                raise AssertionError(f"holdings rows mismatch: {rows}")
            print("[ok] holdings migrated to DB")

            flow_csv = (
                "记账日期,业务摘要,资产账户,股东帐号,证券代码,证券名称,成交股数,成交价格,成交金额,利息金额,发生金额,资金余额,手续费,印花税,过户费等,财汇局交易征费,币种,港股汇率\n"
                "2026-04-15,买入,acct,holder,600000,浦发银行,100,10,1000,0,-1002,0,2,0,0,0,CNY,1\n"
                "2026-04-16,银证转入,acct,holder,,,,,,0,5000,0,0,0,0,0,CNY,1\n"
            ).encode("utf-8")
            body_bytes, content_type = multipart(
                {"account_id": TEMP_ACCOUNT, "broker_type": "pingan"},
                [("file", "pingan.csv", flow_csv, "text/csv")],
            )
            status, body, elapsed = req(
                "POST",
                "/api/import/flow",
                data=body_bytes,
                headers={"Content-Type": content_type},
                timeout=120,
            )
            if status != 200:
                raise AssertionError(f"import flow failed: {status} {body}")
            result = body.get("result", {})
            if result.get("transactions", {}).get("inserted") != 1:
                raise AssertionError(f"unexpected import tx result: {body}")
            if result.get("capital_flows", {}).get("inserted") != 1:
                raise AssertionError(f"unexpected import flow result: {body}")
            print(f"[ok] POST /api/import/flow {elapsed:.3f}s")

            payload = {
                "transactions": [
                    {
                        "ts_code": "000001.SZ",
                        "name": "平安银行",
                        "trans_type": "BUY",
                        "price": 10.0,
                        "amount": 100,
                        "date": "2026-04-15",
                        "time": "10:31",
                    },
                    {
                        "ts_code": "000001.SZ",
                        "name": "平安银行",
                        "trans_type": "BUY",
                        "price": 10.0,
                        "amount": 100,
                        "date": "2026-04-15",
                        "time": "10:31",
                    },
                ]
            }
            status, body, elapsed = req(
                "POST",
                f"/api/screenshot/import/{TEMP_ACCOUNT}",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                timeout=120,
            )
            if status != 200:
                raise AssertionError(f"screenshot import failed: {status} {body}")
            results = body.get("results", [])
            statuses = [item.get("status") for item in results]
            if statuses != ["created", "duplicate_skipped"]:
                raise AssertionError(f"unexpected screenshot import statuses: {statuses}")
            print(f"[ok] POST /api/screenshot/import/{TEMP_ACCOUNT} {elapsed:.3f}s")

            cleanup()
            print("[ok] backend smoke complete")
        except Exception:
            cleanup()
            raise
        """
    )

    result = subprocess.run(
        ["docker", "exec", "-i", container, "python", "-u", "-"],
        input=script,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError("backend smoke failed")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run L1 smoke QA checks.")
    parser.add_argument(
        "--skip-frontend",
        action="store_true",
        help="Skip host-side frontend index check.",
    )
    parser.add_argument(
        "--frontend-url",
        default=FRONTEND_URL,
        help=f"Frontend URL to check. Default: {FRONTEND_URL}",
    )
    parser.add_argument(
        "--backend-container",
        default=BACKEND_CONTAINER,
        help=f"Backend container name. Default: {BACKEND_CONTAINER}",
    )
    args = parser.parse_args()

    try:
        if not args.skip_frontend:
            _check_frontend(args.frontend_url)
        _run_backend_smoke(args.backend_container)
    except Exception as exc:
        print(f"[fail] {exc}", file=sys.stderr)
        return 1

    print("[done] L1 smoke QA passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
