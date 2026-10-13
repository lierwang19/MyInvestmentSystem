"""Real routers, validation and auth middleware with disposable SQLite data.

The client deliberately does not enter lifespan: no live pollers or scheduled
network jobs are started. External market/production E2E is a separate task.
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from web.backend.main import app
    instance = TestClient(app)
    try:
        yield instance
    finally:
        instance.close()


@pytest.mark.parametrize("path", ["/api/accounts-db", "/api/brokers", "/api/settings",
    "/api/analysis/stock-pool", "/api/analysis/scoring-config", "/api/assets/history",
    "/api/quotes/status", "/api/watchlist", "/api/strategy/list"])
def test_registered_read_routes_return_json(client, path):
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert isinstance(response.json(), dict)


def test_account_deposit_buy_sell_http_contract(client):
    broker = client.post("/api/brokers", json={"name": "测试券商"})
    assert broker.status_code == 200, broker.text
    account = client.post("/api/accounts-db", json={"name": "http-test", "broker_id": broker.json()["id"]})
    assert account.status_code == 200, account.text
    account_id = account.json()["id"]
    deposit = client.post(f"/api/accounts-db/{account_id}/capital-flows", json={
        "flow_type": "DEPOSIT", "amount": 10000, "date": "2026-10-01"})
    assert deposit.status_code == 200, deposit.text
    for kind, date in (("BUY", "2026-10-02"), ("SELL", "2026-10-03")):
        response = client.post(f"/api/transactions/{account_id}", json={
            "ts_code": "000001.SZ", "name": "测试股票", "trans_type": kind,
            "price": 10, "amount": 100, "date": date})
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "success"
    history = client.get(f"/api/transactions/{account_id}")
    assert history.status_code == 200
    assert len(history.json()["data"]) == 2
    assert client.get(f"/api/accounts-db/{account_id}/capital-flows").status_code == 200


def test_admin_token_protects_writes_and_settings_reads(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "test-only-token")
    assert client.post("/api/accounts-db", json={"name": "blocked"}).status_code == 401
    assert client.get("/api/settings").status_code == 401
    assert client.get("/api/accounts-db").status_code == 200
    response = client.post("/api/accounts-db", json={"name": "authorized"},
                           headers={"X-Admin-Token": "test-only-token"})
    assert response.status_code == 200, response.text


def test_single_user_mode_keeps_existing_auth_contract(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "test-only-token")
    monkeypatch.setenv("SINGLE_USER_MODE", "1")
    assert client.post("/api/accounts-db", json={"name": "single-user"}).status_code == 200
    assert client.get("/api/settings").status_code == 200
