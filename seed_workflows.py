"""Seed fresh workflows for testing WARN1 sending."""
import sqlite3
from config import now_kz_str

db = sqlite3.connect("data/kaspi_monitor.db")
now = now_kz_str()

# Clean any leftovers
db.execute("DELETE FROM seller_workflows")
db.execute("DELETE FROM workflow_products")
db.execute("DELETE FROM message_log")

# Create workflows for both sellers with NEW_SELLER_ATTACH status
db.execute(
    "INSERT INTO seller_workflows (seller_id, status, created_at, updated_at) "
    "VALUES ('14916088', 'NEW_SELLER_ATTACH', ?, ?)",
    (now, now),
)
db.execute(
    "INSERT INTO seller_workflows (seller_id, status, created_at, updated_at) "
    "VALUES ('30441662', 'NEW_SELLER_ATTACH', ?, ?)",
    (now, now),
)

# Get workflow IDs
wfs = db.execute("SELECT id, seller_id FROM seller_workflows").fetchall()
print(f"Created workflows: {wfs}")

# Link product 148499355 to both workflows
for wf_id, _ in wfs:
    db.execute(
        "INSERT INTO workflow_products (workflow_id, product_id, detected_at, still_attached) "
        "VALUES (?, '148499355', ?, 1)",
        (wf_id, now),
    )

db.commit()

# Verify
wfs = db.execute("SELECT * FROM seller_workflows").fetchall()
print(f"Workflows: {wfs}")
wps = db.execute("SELECT * FROM workflow_products").fetchall()
print(f"Workflow products: {wps}")
sellers = db.execute("SELECT merchant_id, merchant_name, phone FROM sellers").fetchall()
print(f"Sellers: {sellers}")
db.close()
print("Done! Ready for testing.")
