import sqlite3

from reset_analytics_data import reset_analytics_data


def _create_db(path):
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE ads_data (id INTEGER PRIMARY KEY, product_sku TEXT);
            CREATE TABLE products (master_sku TEXT PRIMARY KEY, title TEXT);
            INSERT INTO ads_data (product_sku) VALUES ('100000001'), ('100000002');
            INSERT INTO products (master_sku, title) VALUES ('100000001', 'Тестовый товар');
            """
        )


def test_reset_analytics_data_creates_backup_and_preserves_catalog(tmp_path):
    db_path = tmp_path / "kaspi_monitor.db"
    backups_dir = tmp_path / "backups"
    _create_db(db_path)

    result = reset_analytics_data(db_path, backups_dir)

    assert result.deleted_rows == 2
    assert result.backup_path.exists()

    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM ads_data").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 1

    with sqlite3.connect(result.backup_path) as db:
        assert db.execute("SELECT COUNT(*) FROM ads_data").fetchone()[0] == 2
