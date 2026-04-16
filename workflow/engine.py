"""
Движок воронки (Workflow Engine) — основная бизнес-логика переходов
между статусами продавцов.

Отвечает за:
- Создание workflow при обнаружении нового продавца
- Отправку WARN1 / WARN2 через WhatsApp
- Обработку входящих сообщений (классификация + авто-ответ)
- Проверку отсоединения (микро-скан)
- Эскалацию до юрзаявки
- Закрытие workflow при отсоединении
- Рецидивы
"""
import asyncio
import json
import logging
import random
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from config import Config, now_kz
from database import (
    SellerWorkflowDB,
    MessageLogDB,
    LegalRequestsDB,
    SellersDB,
    ProductsDB,
    ProductSellersDB,
)
from whatsapp import (
    WhatsAppClientBase,
    MessageClassifier,
    ClassificationType,
    get_warn1_template,
    get_warn2_template,
    get_auto_reply_template,
    render_template,
    normalize_phone,
)
from bot.notifications import NotificationService

logger = logging.getLogger(__name__)

# Антиспам-лимиты
MAX_OUTGOING_PER_DAY = 3
MIN_HOURS_BETWEEN_MESSAGES = 1
WHATSAPP_SEND_DELAY_MIN = 5
WHATSAPP_SEND_DELAY_MAX = 10

# Название компании для шаблонов
OUR_COMPANY_NAME = "PKS Ltd"


class WorkflowEngine:
    """
    Движок воронки — state machine для работы с продавцами.

    Управляет жизненным циклом:
    NEW_SELLER_ATTACH → WARN1_SENT → WARN2_SENT → LEGAL_REQUEST_CREATED
    В любой момент → DETACHED → CLOSED при отсоединении.
    CLOSED → RECIDIVE при повторном прилеплении.
    """

    def __init__(
        self,
        workflow_db: SellerWorkflowDB,
        message_log_db: MessageLogDB,
        legal_db: LegalRequestsDB,
        sellers_db: SellersDB,
        products_db: ProductsDB,
        product_sellers_db: ProductSellersDB,
        whatsapp_client: WhatsAppClientBase,
        classifier: MessageClassifier,
        notification_service: NotificationService,
        scanner=None,
    ) -> None:
        self._workflow_db = workflow_db
        self._message_log_db = message_log_db
        self._legal_db = legal_db
        self._sellers_db = sellers_db
        self._products_db = products_db
        self._product_sellers_db = product_sellers_db
        self._whatsapp = whatsapp_client
        self._classifier = classifier
        self._notifications = notification_service
        self._scanner = scanner

    # ------------------------------------------------------------------
    # Публичные методы
    # ------------------------------------------------------------------

    async def on_new_seller_detected(
        self, seller_id: str, product_ids: List[str]
    ) -> int | None:
        """
        Обработка обнаружения нового/вернувшегося продавца.

        Если активный workflow уже есть — добавляет товары к нему.
        Если недавно завершённый workflow — пропускает (cooldown).
        Если нет — создаёт новый.

        Returns:
            workflow_id или None если cooldown
        """
        existing = await self._workflow_db.get_active_workflow_for_seller(seller_id)

        if existing:
            workflow_id = existing["id"]
            for pid in product_ids:
                await self._workflow_db.add_product_to_workflow(workflow_id, pid)
            logger.info(
                f"Добавлено {len(product_ids)} товаров к workflow {workflow_id} "
                f"(продавец {seller_id})"
            )
            return workflow_id

        # Проверяем cooldown: не создавать новую воронку,
        # если предыдущая была завершена недавно
        recently_completed = await self._workflow_db.has_completed_workflow_recently(
            seller_id, Config.WORKFLOW_COOLDOWN_DAYS
        )
        if recently_completed:
            logger.info(
                f"Пропуск продавца {seller_id}: воронка завершена менее "
                f"{Config.WORKFLOW_COOLDOWN_DAYS} дней назад (cooldown)"
            )
            return None

        workflow_id = await self._workflow_db.create_workflow(seller_id)

        # Добавить ВСЕ активные товары продавца, а не только текущий
        all_product_ids = await self._product_sellers_db.get_active_product_ids_for_seller(
            seller_id
        )
        # Объединяем с переданными (на случай если они ещё не в product_sellers)
        all_ids = list(dict.fromkeys(all_product_ids + product_ids))
        for pid in all_ids:
            await self._workflow_db.add_product_to_workflow(workflow_id, pid)

        logger.info(
            f"Создан workflow {workflow_id} для продавца {seller_id}, "
            f"товаров: {len(all_ids)}"
        )
        return workflow_id

    async def send_warn1(self, workflow_id: int) -> bool:
        """
        Отправить WARN1 через WhatsApp.

        Returns:
            True если сообщение отправлено, False при ошибке
        """
        workflow = await self._workflow_db.get_workflow(workflow_id)
        if not workflow:
            logger.error(f"Workflow {workflow_id} не найден")
            return False

        seller_id = workflow["seller_id"]
        seller = await self._sellers_db.get_seller(seller_id)
        if not seller:
            logger.error(f"Продавец {seller_id} не найден")
            return False

        phone = seller.get("phone")
        if not phone:
            logger.warning(
                f"Нет телефона для продавца {seller_id} "
                f"({seller.get('merchant_name')}), пропускаем WARN1"
            )
            return False

        # Антиспам (только проверка дневного лимита, т.к. планировщик
        # эскалации сам контролирует интервал)
        if not await self._can_send_message(seller_id, skip_interval_check=True):
            return False

        # Подтянуть все активные товары продавца в workflow
        await self._sync_workflow_products(workflow_id, seller_id)

        # Подготовка контекста для шаблона
        context = await self._build_template_context(workflow_id, seller)

        # Выбор и рендеринг шаблона
        template = get_warn1_template()
        text = render_template(template, context)

        # Отправка текста
        try:
            await asyncio.sleep(random.uniform(
                WHATSAPP_SEND_DELAY_MIN, WHATSAPP_SEND_DELAY_MAX
            ))
            result = await self._whatsapp.send_text(phone, text)
            wa_message_id = result.get("idMessage")
        except Exception as e:
            logger.error(
                f"Ошибка отправки WARN1 для workflow {workflow_id}: {e}"
            )
            await self._notifications.send_to_admins(
                f"⚠️ <b>Ошибка отправки WARN1</b>\n\n"
                f"Продавец: {seller.get('merchant_name')}\n"
                f"Ошибка: {e}"
            )
            return False

        # Отправка документов-вложений (свидетельства об авторском праве)
        await self._send_warn_documents(
            phone, Config.WARN1_DOCUMENTS, workflow_id, "WARN1"
        )

        # Обновить статус
        await self._workflow_db.update_status(workflow_id, "WARN1_SENT")

        # Логировать сообщение
        await self._message_log_db.log_message(
            workflow_id=workflow_id,
            seller_id=seller_id,
            direction="OUT",
            text=text,
            template_code=template.code,
            wa_message_id=wa_message_id,
        )

        logger.info(
            f"WARN1 отправлен: workflow={workflow_id}, "
            f"продавец={seller.get('merchant_name')}"
        )

        # Уведомить админов
        await self._notifications.notify_warn1_sent(workflow_id, seller)

        return True

    async def send_warn2(self, workflow_id: int) -> bool:
        """
        Отправить WARN2 через WhatsApp.

        Returns:
            True если сообщение отправлено, False при ошибке
        """
        workflow = await self._workflow_db.get_workflow(workflow_id)
        if not workflow:
            logger.error(f"Workflow {workflow_id} не найден")
            return False

        seller_id = workflow["seller_id"]
        seller = await self._sellers_db.get_seller(seller_id)
        if not seller:
            logger.error(f"Продавец {seller_id} не найден")
            return False

        phone = seller.get("phone")
        if not phone:
            logger.warning(
                f"Нет телефона для продавца {seller_id}, пропускаем WARN2"
            )
            return False

        if not await self._can_send_message(seller_id, skip_interval_check=True):
            return False

        # Подтянуть все активные товары продавца в workflow
        await self._sync_workflow_products(workflow_id, seller_id)

        context = await self._build_template_context(workflow_id, seller)

        # Добавить дату WARN1 для шаблонов WARN2
        if workflow.get("warn1_sent_at"):
            context["warn1_date"] = self._format_datetime(workflow["warn1_sent_at"])

        template = get_warn2_template()
        text = render_template(template, context)

        try:
            await asyncio.sleep(random.uniform(
                WHATSAPP_SEND_DELAY_MIN, WHATSAPP_SEND_DELAY_MAX
            ))
            result = await self._whatsapp.send_text(phone, text)
            wa_message_id = result.get("idMessage")
        except Exception as e:
            logger.error(
                f"Ошибка отправки WARN2 для workflow {workflow_id}: {e}"
            )
            await self._notifications.send_to_admins(
                f"⚠️⚠️ <b>Ошибка отправки WARN2</b>\n\n"
                f"Продавец: {seller.get('merchant_name')}\n"
                f"Ошибка: {e}"
            )
            return False

        # Отправка документов-вложений (решение суда)
        await self._send_warn_documents(
            phone, Config.WARN2_DOCUMENTS, workflow_id, "WARN2"
        )

        await self._workflow_db.update_status(workflow_id, "WARN2_SENT")

        await self._message_log_db.log_message(
            workflow_id=workflow_id,
            seller_id=seller_id,
            direction="OUT",
            text=text,
            template_code=template.code,
            wa_message_id=wa_message_id,
        )

        logger.info(
            f"WARN2 отправлен: workflow={workflow_id}, "
            f"продавец={seller.get('merchant_name')}"
        )

        await self._notifications.notify_warn2_sent(workflow_id, seller)

        return True

    async def handle_incoming_message(
        self, sender_phone: str, text: str, sender_name: str
    ) -> None:
        """
        Обработка входящего сообщения от продавца.

        1. Найти продавца по телефону
        2. Найти активный workflow
        3. Классифицировать сообщение
        4. Записать в лог
        5. Сформировать и отправить авто-ответ
        6. При ALREADY_REMOVED — запустить микро-скан
        7. Уведомить админов
        """
        normalized = normalize_phone(sender_phone)
        if not normalized:
            logger.warning(f"Невалидный номер входящего: {sender_phone}")
            return

        # Найти продавца по телефону
        seller = await self._find_seller_by_phone(normalized)
        if not seller:
            logger.info(
                f"Входящее от неизвестного номера {normalized[:4]}***, "
                f"пропускаем"
            )
            return

        seller_id = seller["merchant_id"]
        merchant_name = seller.get("merchant_name", "Неизвестный")

        # Найти активный workflow
        workflow = await self._workflow_db.get_active_workflow_for_seller(seller_id)
        if not workflow:
            logger.info(
                f"Нет активного workflow для {merchant_name} ({seller_id}), "
                f"пропускаем входящее сообщение"
            )
            return

        workflow_id = workflow["id"]

        # Классифицировать сообщение
        classification_result = await self._classifier.classify(text)
        classification = classification_result.classification.value

        # Записать входящее в лог
        await self._message_log_db.log_message(
            workflow_id=workflow_id,
            seller_id=seller_id,
            direction="IN",
            text=text,
            classification=classification,
        )

        logger.info(
            f"Входящее от {merchant_name}: "
            f"тип={classification}, "
            f"confidence={classification_result.confidence:.2f}, "
            f"workflow={workflow_id}"
        )

        # DIALOG_ACTIVE — если workflow был в WARN1/WARN2
        if workflow["status"] in ("WARN1_SENT", "WARN2_SENT"):
            await self._workflow_db.update_status(workflow_id, "DIALOG_ACTIVE")

        # Обработка ALREADY_REMOVED — микро-скан
        if classification == ClassificationType.ALREADY_REMOVED.value:
            await self._handle_already_removed(workflow_id, seller, text)
            return

        # Сформировать авто-ответ
        await self._send_auto_reply(
            workflow_id, seller, classification
        )

        # Уведомить админов
        await self._notifications.notify_incoming_message(
            workflow_id, seller, text, classification
        )

    async def check_detachment(self, workflow_id: int) -> bool:
        """
        Проверить, отсоединился ли продавец от всех товаров.

        Делает точечный запрос к Kaspi API для каждого товара в workflow.

        Returns:
            True если продавец полностью отсоединился
        """
        workflow = await self._workflow_db.get_workflow(workflow_id)
        if not workflow:
            return False

        seller_id = workflow["seller_id"]
        products = await self._workflow_db.get_workflow_products(workflow_id)

        if not products:
            logger.warning(f"Нет товаров в workflow {workflow_id}")
            return False

        all_detached = True
        any_detached = False

        for product in products:
            product_id = product["product_id"]
            still_attached = await self._check_seller_on_product(
                seller_id, product_id
            )

            await self._workflow_db.update_product_attached(
                workflow_id, product_id, 1 if still_attached else 0
            )

            if still_attached:
                all_detached = False
            else:
                any_detached = True

        if all_detached:
            logger.info(
                f"Продавец {seller_id} полностью отсоединился "
                f"(workflow {workflow_id})"
            )
            return True

        if any_detached:
            logger.info(
                f"Продавец {seller_id} частично отсоединился "
                f"(workflow {workflow_id})"
            )

        return False

    async def escalate_to_legal(self, workflow_id: int) -> Optional[int]:
        """
        Эскалация до юрзаявки.

        Собирает все данные из workflow, сообщений, товаров
        и создаёт запись в legal_requests.

        Returns:
            request_id при успехе, None при ошибке
        """
        workflow = await self._workflow_db.get_workflow(workflow_id)
        if not workflow:
            logger.error(f"Workflow {workflow_id} не найден для эскалации")
            return None

        seller_id = workflow["seller_id"]
        seller = await self._sellers_db.get_seller(seller_id)
        if not seller:
            logger.error(f"Продавец {seller_id} не найден для эскалации")
            return None

        # Собрать данные
        products = await self._workflow_db.get_workflow_products(workflow_id)
        messages = await self._message_log_db.get_messages_for_workflow(workflow_id)

        product_links = json.dumps([
            {
                "product_id": p["product_id"],
                "title": p.get("title", ""),
                "url": p.get("url", ""),
            }
            for p in products
        ], ensure_ascii=False)

        detection_dates = json.dumps([
            {
                "product_id": p["product_id"],
                "detected_at": p.get("detected_at", ""),
            }
            for p in products
        ], ensure_ascii=False)

        warn_timeline = json.dumps({
            "created_at": workflow.get("created_at", ""),
            "warn1_sent_at": workflow.get("warn1_sent_at", ""),
            "warn2_sent_at": workflow.get("warn2_sent_at", ""),
        }, ensure_ascii=False)

        dialog_log = json.dumps([
            {
                "direction": m["direction"],
                "text": m["message_text"],
                "at": m.get("sent_at", ""),
                "classification": m.get("classification"),
            }
            for m in messages
        ], ensure_ascii=False)

        request_id = await self._legal_db.create_request(
            workflow_id=workflow_id,
            seller_id=seller_id,
            shop_name=seller.get("merchant_name"),
            phone=seller.get("phone"),
            product_links=product_links,
            detection_dates=detection_dates,
            warn_timeline=warn_timeline,
            dialog_log=dialog_log,
        )

        await self._workflow_db.update_status(
            workflow_id, "LEGAL_REQUEST_CREATED"
        )

        logger.info(
            f"Юрзаявка #{request_id} создана: workflow={workflow_id}, "
            f"продавец={seller.get('merchant_name')}"
        )

        # Уведомить админов
        await self._notifications.notify_legal_request(
            request_id=request_id,
            workflow_id=workflow_id,
            seller=seller,
            products_count=len(products),
            workflow=workflow,
        )

        return request_id

    async def close_workflow(
        self, workflow_id: int, reason: str = "detached"
    ) -> None:
        """
        Закрыть workflow (продавец отсоединился).
        """
        workflow = await self._workflow_db.get_workflow(workflow_id)
        if not workflow:
            return

        await self._workflow_db.update_status(workflow_id, "DETACHED")
        await self._workflow_db.update_status(workflow_id, "CLOSED")

        seller = await self._sellers_db.get_seller(workflow["seller_id"])
        merchant_name = seller.get("merchant_name", "?") if seller else "?"

        logger.info(
            f"Workflow {workflow_id} закрыт: {merchant_name}, причина: {reason}"
        )

        await self._notifications.notify_detached(workflow_id, seller or {}, reason)

    async def handle_recidive(
        self, seller_id: str, product_ids: List[str]
    ) -> int:
        """
        Рецидив: продавец вернулся после CLOSED.
        Создаёт новый workflow со статусом RECIDIVE → сразу отправляет WARN2.
        """
        workflow_id = await self._workflow_db.create_workflow(seller_id)
        for pid in product_ids:
            await self._workflow_db.add_product_to_workflow(workflow_id, pid)

        await self._workflow_db.update_status(workflow_id, "RECIDIVE")

        seller = await self._sellers_db.get_seller(seller_id)
        merchant_name = seller.get("merchant_name", "?") if seller else "?"

        logger.warning(
            f"Рецидив: продавец {merchant_name} ({seller_id}), "
            f"workflow={workflow_id}"
        )

        await self._notifications.send_to_admins(
            f"🔄 <b>Рецидив!</b>\n\n"
            f"Магазин: {merchant_name}\n"
            f"Ранее был в воронке, вернулся.\n"
            f"Workflow: #{workflow_id}\n"
            f"Сразу отправляем WARN2."
        )

        # Сразу WARN2
        await self.send_warn2(workflow_id)

        return workflow_id

    # ------------------------------------------------------------------
    # Приватные методы
    # ------------------------------------------------------------------

    async def _send_warn_documents(
        self,
        phone: str,
        document_paths: List[Path],
        workflow_id: int,
        warn_type: str,
    ) -> None:
        """Отправить документы-вложения к WARN-сообщению."""
        existing_files = [p for p in document_paths if p.exists()]
        if not existing_files:
            logger.warning(
                f"Нет файлов-вложений для {warn_type} "
                f"(workflow {workflow_id})"
            )
            return

        try:
            await asyncio.sleep(3)
            await self._whatsapp.send_files(phone, existing_files)
            logger.info(
                f"{warn_type} документы отправлены: workflow={workflow_id}, "
                f"файлов={len(existing_files)}"
            )
        except Exception as e:
            logger.error(
                f"Ошибка отправки документов {warn_type} "
                f"для workflow {workflow_id}: {e}"
            )

    async def _can_send_message(
        self, seller_id: str, *, skip_interval_check: bool = False
    ) -> bool:
        """
        Антиспам: проверить, можно ли отправить сообщение продавцу.

        - Макс 3 исходящих в день
        - Минимум 1 час между сообщениями
        """
        count_today = await self._message_log_db.count_messages_today(
            seller_id, "OUT"
        )
        if count_today >= MAX_OUTGOING_PER_DAY:
            logger.info(
                f"Антиспам: продавец {seller_id} — "
                f"{count_today}/{MAX_OUTGOING_PER_DAY} сообщений сегодня"
            )
            return False

        # Проверка интервала — последнее исходящее
        if skip_interval_check:
            return True
        messages = await self._message_log_db.get_messages_for_seller(seller_id)
        if messages:
            last_out = None
            for msg in reversed(messages):
                if msg["direction"] == "OUT":
                    last_out = msg
                    break

            if last_out and last_out.get("sent_at"):
                try:
                    sent_at = datetime.fromisoformat(last_out["sent_at"])
                    now_local = now_kz().replace(tzinfo=None)
                    elapsed = (now_local - sent_at).total_seconds() / 3600
                    if elapsed < MIN_HOURS_BETWEEN_MESSAGES:
                        logger.info(
                            f"Антиспам: продавец {seller_id} — "
                            f"прошло {elapsed:.1f}ч (мин {MIN_HOURS_BETWEEN_MESSAGES}ч)"
                        )
                        return False
                except (ValueError, TypeError):
                    pass

        return True

    async def _sync_workflow_products(self, workflow_id: int, seller_id: str) -> None:
        """Синхронизировать товары workflow с актуальными из product_sellers."""
        all_product_ids = await self._product_sellers_db.get_active_product_ids_for_seller(
            seller_id
        )
        for pid in all_product_ids:
            await self._workflow_db.add_product_to_workflow(workflow_id, pid)

    async def _build_template_context(
        self, workflow_id: int, seller: Dict
    ) -> Dict[str, str]:
        """Собрать контекст для подстановки в шаблон."""
        products = await self._workflow_db.get_workflow_products(workflow_id)

        product_lines = []
        for p in products:
            title = p.get("title", "Товар")
            url = p.get("url", "")
            if url:
                product_lines.append(f"• {title}\n  {url}")
            else:
                product_lines.append(f"• {title}")

        return {
            "shop_name": seller.get("merchant_name", "Магазин"),
            "product_links": "\n".join(product_lines),
            "our_company": OUR_COMPANY_NAME,
            "deadline": "24 часа",
            "detection_date": now_kz().strftime("%d.%m.%Y"),
        }

    async def _find_seller_by_phone(self, phone: str) -> Optional[Dict]:
        """Найти продавца по нормализованному телефону."""
        sellers = await self._sellers_db.get_all_sellers()
        normalized_target = normalize_phone(phone)

        for seller in sellers:
            seller_phone = seller.get("phone")
            if seller_phone and normalize_phone(seller_phone) == normalized_target:
                return seller

        return None

    async def _send_auto_reply(
        self,
        workflow_id: int,
        seller: Dict,
        classification: str,
    ) -> None:
        """Сформировать и отправить авто-ответ на входящее сообщение."""
        seller_id = seller["merchant_id"]

        if not await self._can_send_message(seller_id, skip_interval_check=True):
            logger.info(
                f"Антиспам: пропуск авто-ответа для {seller_id}"
            )
            return

        phone = seller.get("phone")
        if not phone:
            return

        context = await self._build_template_context(workflow_id, seller)
        template = get_auto_reply_template(classification)
        text = render_template(template, context)

        try:
            await asyncio.sleep(random.uniform(
                WHATSAPP_SEND_DELAY_MIN, WHATSAPP_SEND_DELAY_MAX
            ))
            result = await self._whatsapp.send_text(phone, text)
            wa_message_id = result.get("idMessage")
        except Exception as e:
            logger.error(
                f"Ошибка отправки авто-ответа для workflow {workflow_id}: {e}"
            )
            await self._notifications.send_to_admins(
                f"⚠️ <b>Ошибка отправки авто-ответа</b>\n\n"
                f"Продавец: {seller.get('merchant_name')}\n"
                f"Тип: {classification}\n"
                f"Ошибка: {e}"
            )
            return

        await self._message_log_db.log_message(
            workflow_id=workflow_id,
            seller_id=seller_id,
            direction="OUT",
            text=text,
            template_code=template.code,
            wa_message_id=wa_message_id,
        )

        logger.info(
            f"Авто-ответ отправлен: workflow={workflow_id}, "
            f"тип={classification}, шаблон={template.code}"
        )

    async def _handle_already_removed(
        self, workflow_id: int, seller: Dict, original_text: str
    ) -> None:
        """
        Обработка ответа ALREADY_REMOVED — микро-скан.

        1. Отправить промежуточный ответ
        2. Проверить товары через Kaspi API
        3. Если все убраны → закрыть workflow
        4. Если нет → сообщить, что проверка не подтвердила
        """
        seller_id = seller["merchant_id"]
        phone = seller.get("phone")

        # Отправить промежуточный ответ «проверяем»
        if phone and await self._can_send_message(seller_id, skip_interval_check=True):
            template = get_auto_reply_template("ALREADY_REMOVED")
            context = await self._build_template_context(workflow_id, seller)
            text = render_template(template, context)

            try:
                await asyncio.sleep(random.uniform(
                    WHATSAPP_SEND_DELAY_MIN, WHATSAPP_SEND_DELAY_MAX
                ))
                result = await self._whatsapp.send_text(phone, text)
                await self._message_log_db.log_message(
                    workflow_id=workflow_id,
                    seller_id=seller_id,
                    direction="OUT",
                    text=text,
                    template_code=template.code,
                    wa_message_id=result.get("idMessage"),
                )
            except Exception as e:
                logger.error(f"Ошибка отправки ответа ALREADY_REMOVED: {e}")

        # Уведомить админов
        await self._notifications.send_to_admins(
            f"🔍 <b>Продавец заявил об отсоединении</b>\n\n"
            f"Магазин: {seller.get('merchant_name')}\n"
            f"Workflow: #{workflow_id}\n"
            f"Запускаем проверку..."
        )

        # Микро-скан
        detached = await self.check_detachment(workflow_id)

        if detached:
            await self.close_workflow(workflow_id, reason="seller_confirmed_removal")
        else:
            # Продавец всё ещё на карточке
            if phone and await self._can_send_message(seller_id, skip_interval_check=True):
                still_text = (
                    "Мы проверили — ваши предложения всё ещё размещены "
                    "на наших карточках товаров.\n\n"
                    "Просим завершить отсоединение. Спасибо."
                )
                try:
                    await asyncio.sleep(random.uniform(
                        WHATSAPP_SEND_DELAY_MIN, WHATSAPP_SEND_DELAY_MAX
                    ))
                    result = await self._whatsapp.send_text(phone, still_text)
                    await self._message_log_db.log_message(
                        workflow_id=workflow_id,
                        seller_id=seller_id,
                        direction="OUT",
                        text=still_text,
                        wa_message_id=result.get("idMessage"),
                    )
                except Exception as e:
                    logger.error(
                        f"Ошибка отправки уведомления о неотсоединении: {e}"
                    )

            await self._notifications.send_to_admins(
                f"❌ <b>Проверка не подтвердила отсоединение</b>\n\n"
                f"Магазин: {seller.get('merchant_name')}\n"
                f"Workflow: #{workflow_id}\n"
                f"Продавец всё ещё на карточках."
            )

    async def _check_seller_on_product(
        self, seller_id: str, product_id: str
    ) -> bool:
        """
        Проверить, есть ли продавец на конкретном товаре.

        Если доступен scanner — делает реальный запрос к Kaspi API.
        Иначе — проверяет по кешированным данным в БД.

        Returns:
            True если продавец всё ещё на карточке
        """
        if self._scanner:
            return await self._scanner.check_seller_on_product(
                seller_id, product_id
            )

        # Fallback: проверка по кешу в БД
        sellers = await self._product_sellers_db.get_sellers_for_product(
            product_id, active_only=True
        )
        for s in sellers:
            if s.get("seller_id") == seller_id:
                return True
        return False

    @staticmethod
    def _format_datetime(dt_str: str) -> str:
        """Форматировать datetime-строку из БД в читаемый вид."""
        try:
            dt = datetime.fromisoformat(dt_str)
            return dt.strftime("%d.%m.%Y %H:%M")
        except (ValueError, TypeError):
            return dt_str or "—"
