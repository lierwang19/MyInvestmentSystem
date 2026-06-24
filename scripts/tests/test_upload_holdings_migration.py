import os
import tempfile

from web.backend.api.upload import migrate_holdings_to_db
from web.backend.database import get_db


def test_migrate_holdings_to_db_persists_rows():
    account_id = "QA-UPLOAD-MIGRATE"
    csv_content = (
        "证券代码,证券名称,证券数量,成本价\n"
        "000001,平安银行,100,10.50\n"
        "600000,浦发银行,200,9.80\n"
    )

    fd, path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(csv_content)

        migrated = migrate_holdings_to_db(path, account_id)
        assert migrated == 2

        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT ts_code, quantity, cost_price, total_cost, created_at, updated_at
                FROM holdings
                WHERE account_id = ?
                ORDER BY ts_code
                """,
                (account_id,),
            ).fetchall()
            conn.execute("DELETE FROM holdings WHERE account_id = ?", (account_id,))
            conn.commit()

        assert len(rows) == 2
        assert rows[0]["ts_code"] == "000001.SZ"
        assert rows[0]["quantity"] == 100
        assert rows[0]["cost_price"] == 10.5
        assert rows[0]["total_cost"] == 1050.0
        assert rows[0]["created_at"]
        assert rows[0]["updated_at"]
    finally:
        os.remove(path)
