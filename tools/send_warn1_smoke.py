"""One-off live WARN1 smoke test.

Creates a synthetic seller/product/workflow and immediately sends WARN1 to the
given phone number. This bypasses the bot scheduler, scanner, webhook, and
analytics jobs.
"""
import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bot.notifications import NotificationService
from config import Config
from database import (
    LegalRequestsDB,
    MessageLogDB,
    ProductSellersDB,
    ProductsDB,
    SellerWorkflowDB,
    SellersDB,
)
from database.migrations import DatabaseMigrations
from database.schema import DatabaseSchema
from whatsapp import GreenAPIClient
from workflow.engine import WorkflowEngine


class ConsoleNotifications(NotificationService):
    """Notification service that prints instead of calling Telegram."""

    def __init__(self) -> None:
        self.bot = None
        self.admin_ids = []

    async def send_to_admins(self, text: str, **_kwargs) -> None:
        print(f"[admin notification skipped]\n{text}")

    async def notify_warn1_sent(self, workflow_id: int, seller: dict) -> None:
        print(
            "[admin notification skipped] "
            f"WARN1 sent: workflow={workflow_id}, seller={seller.get('merchant_name')}"
        )


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send a real WARN1 smoke test to a WhatsApp phone number."
    )
    parser.add_argument(
        "--phone",
        default="+77054089839",
        help="Recipient phone number in Kazakhstan format.",
    )
    parser.add_argument(
        "--merchant-name",
        default="WARN1 smoke test seller",
        help="Synthetic seller name shown in local DB records.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not Config.GREEN_API_INSTANCE_ID or not Config.GREEN_API_TOKEN:
        raise RuntimeError("GREEN_API_INSTANCE_ID/GREEN_API_TOKEN are not configured")

    await DatabaseSchema.init_db(Config.DB_PATH)
    await DatabaseMigrations(Config.DB_PATH).run_migrations()

    suffix = str(int(time.time()))
    seller_id = f"TEST_WARN1_{suffix}"
    product_id = f"TEST_WARN1_SKU_{suffix}"
    product_url = f"https://kaspi.kz/shop/p/test-warn1-{suffix}"
    product_title = "Тестовый товар для проверки WARN1"

    sellers_db = SellersDB(Config.DB_PATH)
    products_db = ProductsDB(Config.DB_PATH)
    product_sellers_db = ProductSellersDB(Config.DB_PATH)
    workflow_db = SellerWorkflowDB(Config.DB_PATH)
    message_log_db = MessageLogDB(Config.DB_PATH)
    legal_db = LegalRequestsDB(Config.DB_PATH)

    await sellers_db.add_seller(seller_id, args.merchant_name, args.phone)
    await products_db.add_product(product_id, product_url, product_title)
    await product_sellers_db.add_or_update_link(product_id, seller_id, 1.0)

    workflow_id = await workflow_db.create_workflow(seller_id)
    await workflow_db.add_product_to_workflow(workflow_id, product_id)

    whatsapp_client = GreenAPIClient(
        Config.GREEN_API_URL,
        Config.GREEN_API_INSTANCE_ID,
        Config.GREEN_API_TOKEN,
        Config.GREEN_API_MEDIA_URL,
    )

    engine = WorkflowEngine(
        workflow_db=workflow_db,
        message_log_db=message_log_db,
        legal_db=legal_db,
        sellers_db=sellers_db,
        products_db=products_db,
        product_sellers_db=product_sellers_db,
        whatsapp_client=whatsapp_client,
        classifier=None,
        notification_service=ConsoleNotifications(),
        scanner=None,
    )

    print(f"DB: {Config.DB_PATH}")
    print(f"Seller: {seller_id} / {args.phone}")
    print(f"Product: {product_id}")
    print(f"Workflow: {workflow_id}")
    print("Sending WARN1 now...")

    success = await engine.send_warn1(workflow_id)
    workflow = await workflow_db.get_workflow(workflow_id)
    messages = await message_log_db.get_messages_for_workflow(workflow_id)

    print(f"Success: {success}")
    print(f"Workflow status: {workflow.get('status') if workflow else 'missing'}")
    print(f"Logged outgoing messages: {len(messages)}")
    for msg in messages:
        print(f"- {msg.get('template_code')}: {msg.get('wa_message_id')}")

    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
