"""Preview or apply a reviewed CRM catalog snapshot to the bot's SQLite database."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3


def read_catalog(payload):
    if payload.get("schema_version") != 1 or not payload.get("products"):
        raise ValueError("Expected a nonempty CRM catalog snapshot (schema version 1)")
    result = {}
    for item in payload["products"]:
        sku, url, title = item["sku"], item["url"], item["title"]
        if not re.fullmatch(r"[0-9]+", sku) or url != f"https://kaspi.kz/shop/p/{sku}/":
            raise ValueError("Every item must have a numeric Kaspi SKU and its canonical URL")
        if not isinstance(title, str) or not title.strip() or sku in result:
            raise ValueError("Catalog contains a duplicate SKU or missing title")
        result[sku] = (url, title.strip())
    return result


def import_catalog(
    db: Path, payload: dict, *, apply=False, reviewed_exclusions=None, expected_disables=None, backup=None,
):
    catalog = read_catalog(payload)
    exclusions = payload.get("excluded", [])
    if apply:
        if any(row.get("reason") == "unresolved_short_link" for row in exclusions):
            raise ValueError("Resolve or verify every unresolved short link before importing")
        if exclusions and reviewed_exclusions != len(exclusions):
            raise ValueError("Review the excluded rows and pass their count as --reviewed-exclusions")
        if backup is not None:
            backup_database(db, backup)
    uri = db.resolve().as_uri() + ("?mode=rw" if apply else "?mode=ro")
    with closing(sqlite3.connect(uri, uri=True)) as conn, conn:
        conn.execute("PRAGMA foreign_keys=ON")
        if apply:
            conn.execute("BEGIN IMMEDIATE")
        else:
            conn.execute("PRAGMA query_only=ON")
        fields = {column[1] for column in conn.execute("PRAGMA table_info(products)")}
        migrated = {"monitor_enabled", "monitor_baseline_pending"}.issubset(fields)
        if not migrated and apply:
            raise ValueError("Apply KaspiBot database migrations before importing")
        enabled_expr = "monitor_enabled" if migrated else "1"
        current = {
            sku: (url, title, enabled)
            for sku, url, title, enabled in conn.execute(
                f"SELECT master_sku, url, title, {enabled_expr} FROM products"
            )
        }
        existing = set(catalog) & set(current)
        plan = {
            "add": sorted(set(catalog) - set(current)),
            "update": sorted(sku for sku in existing if catalog[sku] != current[sku][:2]),
            "reactivate": sorted(sku for sku in existing if not current[sku][2]),
            "disable": sorted(sku for sku, (_, _, enabled) in current.items() if enabled and sku not in catalog),
        }
        if apply:
            if plan["disable"] and expected_disables != len(plan["disable"]):
                raise ValueError("Review the disable plan and pass its count as --expected-disables")
            conn.executemany(
                "INSERT INTO products (master_sku,url,title,monitor_baseline_pending) VALUES (?,?,?,1)",
                [(sku, *catalog[sku]) for sku in plan["add"]],
            )
            conn.executemany(
                """UPDATE products SET url=?,title=?,monitor_enabled=1,
                    monitor_baseline_pending=CASE WHEN monitor_enabled=0 THEN 1 ELSE monitor_baseline_pending END
                   WHERE master_sku=?""",
                [(*catalog[sku], sku) for sku in sorted(set(plan["update"]) | set(plan["reactivate"]))],
            )
            conn.executemany(
                "UPDATE products SET monitor_enabled=0 WHERE master_sku=?",
                [(sku,) for sku in plan["disable"]],
            )
        return plan


def backup_database(db: Path, backup: Path):
    if backup.exists():
        raise ValueError("Backup path already exists")
    uri = db.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as source:
        # Exclusive creation prevents accidentally replacing an existing backup.
        os.close(os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        with closing(sqlite3.connect(backup)) as copy:
            source.backup(copy)
            if copy.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("SQLite backup check failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--reviewed-exclusions", type=int)
    parser.add_argument("--expected-disables", type=int)
    args = parser.parse_args()
    payload = json.loads(args.catalog.read_text(encoding="utf-8-sig"))
    try:
        if args.apply and not args.backup:
            parser.error("--apply requires --backup")
        plan = import_catalog(
            args.db, payload, apply=args.apply, reviewed_exclusions=args.reviewed_exclusions,
            expected_disables=args.expected_disables, backup=args.backup,
        )
    except (ValueError, sqlite3.Error) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps({"applied": args.apply, "counts": {key: len(skus) for key, skus in plan.items()}, **plan}))


if __name__ == "__main__":
    main()
