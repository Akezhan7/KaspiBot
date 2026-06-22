"""Safely clear only Kaspi ads analytics data from a local SQLite database."""
from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "kaspi_monitor.db"
DEFAULT_BACKUPS_DIR = BASE_DIR / "data" / "backups"


@dataclass(frozen=True)
class ResetResult:
    deleted_rows: int
    backup_path: Path


def get_analytics_row_count(db_path: Path) -> int:
    with sqlite3.connect(db_path, timeout=10) as db:
        return int(db.execute("SELECT COUNT(*) FROM ads_data").fetchone()[0])


def reset_analytics_data(db_path: Path, backups_dir: Path) -> ResetResult:
    """Back up the database, then delete only rows from ads_data."""
    db_path = Path(db_path)
    backups_dir = Path(backups_dir)
    if not db_path.is_file():
        raise FileNotFoundError(f"Database file not found: {db_path}")

    backups_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backups_dir / f"kaspi_monitor-before-analytics-reset-{timestamp}.db"

    with sqlite3.connect(db_path, timeout=10) as db:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"Database integrity check failed: {integrity}")

        with sqlite3.connect(backup_path) as backup:
            db.backup(backup)

        deleted_rows = int(db.execute("SELECT COUNT(*) FROM ads_data").fetchone()[0])
        db.execute("DELETE FROM ads_data")
        db.commit()

        remaining_rows = int(db.execute("SELECT COUNT(*) FROM ads_data").fetchone()[0])
        if remaining_rows != 0:
            raise RuntimeError(f"Analytics reset incomplete: {remaining_rows} rows remain")

    return ResetResult(deleted_rows=deleted_rows, backup_path=backup_path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Back up and clear only the ads_data table. Stop the bot first."
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--backups-dir", type=Path, default=DEFAULT_BACKUPS_DIR)
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required to create a backup and delete analytics rows.",
    )
    args = parser.parse_args()

    try:
        row_count = get_analytics_row_count(args.db_path)
        if not args.confirm:
            print(f"Dry run: {row_count} rows in ads_data would be deleted.")
            print("Stop the bot, then rerun with --confirm to proceed.")
            return 0

        result = reset_analytics_data(args.db_path, args.backups_dir)
    except Exception as exc:
        print(f"Analytics reset failed: {exc}")
        return 1

    print(f"Analytics rows deleted: {result.deleted_rows}")
    print(f"Database backup created: {result.backup_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
