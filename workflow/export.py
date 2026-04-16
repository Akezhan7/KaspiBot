"""
Экспорт доказательной базы по юридическим заявкам.

Формирует JSON-пакеты, текстовые логи диалогов, таймлайны событий
и ZIP-архивы со всей документацией для подготовки к судебным искам.
"""
import csv
import io
import json
import logging
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from database import (
    LegalRequestsDB,
    MessageLogDB,
    ProductsDB,
    SellersDB,
    SellerWorkflowDB,
)

logger = logging.getLogger(__name__)

# Максимальный размер ZIP для отправки через Telegram (45 МБ с запасом)
MAX_ZIP_SIZE_BYTES = 45 * 1024 * 1024


class EvidenceExporter:
    """
    Экспорт доказательной базы для юридических заявок.

    Собирает данные из workflow, message_log, legal_requests, sellers, products
    и формирует пакет документов в различных форматах.
    """

    def __init__(
        self,
        legal_db: LegalRequestsDB,
        workflow_db: SellerWorkflowDB,
        message_log_db: MessageLogDB,
        sellers_db: SellersDB,
        products_db: ProductsDB,
    ) -> None:
        self._legal_db = legal_db
        self._workflow_db = workflow_db
        self._message_log_db = message_log_db
        self._sellers_db = sellers_db
        self._products_db = products_db

    async def export_legal_request(
        self, request_id: int, fmt: str = "json"
    ) -> bytes:
        """
        Экспорт юрзаявки в JSON или CSV.

        Args:
            request_id: ID юрзаявки
            fmt: формат — "json" или "csv"

        Returns:
            Байтовое содержимое файла
        """
        request = await self._legal_db.get_request(request_id)
        if not request:
            raise ValueError(f"Юрзаявка {request_id} не найдена")

        workflow = await self._workflow_db.get_workflow(request["workflow_id"])
        seller = await self._sellers_db.get_seller(request["seller_id"])
        products = await self._workflow_db.get_workflow_products(
            request["workflow_id"]
        )
        messages = await self._message_log_db.get_messages_for_workflow(
            request["workflow_id"]
        )

        package = self._build_legal_package_data(
            request, workflow, seller, products, messages
        )

        if fmt == "csv":
            return self._package_to_csv(package)

        return json.dumps(package, ensure_ascii=False, indent=2).encode("utf-8")

    async def export_dialog_log(self, workflow_id: int) -> str:
        """
        Экспорт переписки в текстовом формате.

        Returns:
            Текстовый лог вида:
            [2026-03-15 18:00] → WARN1: Текст...
            [2026-03-16 10:30] ← Ответ продавца (DIDNT_KNOW): Текст...
        """
        messages = await self._message_log_db.get_messages_for_workflow(
            workflow_id
        )
        workflow = await self._workflow_db.get_workflow(workflow_id)
        if not workflow:
            raise ValueError(f"Workflow {workflow_id} не найден")

        seller = await self._sellers_db.get_seller(workflow["seller_id"])
        seller_name = seller.get("merchant_name", "Неизвестный") if seller else "?"

        lines = [
            f"=== Лог переписки ===",
            f"Workflow: #{workflow_id}",
            f"Магазин: {seller_name}",
            f"Дата создания: {workflow.get('created_at', '—')}",
            f"Статус: {workflow.get('status', '—')}",
            "",
        ]

        for msg in messages:
            timestamp = self._format_timestamp(msg.get("sent_at", ""))
            direction = msg["direction"]
            text = msg.get("message_text", "")
            classification = msg.get("classification")
            template = msg.get("template_code")

            if direction == "OUT":
                prefix = "→"
                label = f" [{template}]" if template else ""
            else:
                prefix = "←"
                label = f" {seller_name}"

            lines.append(f"[{timestamp}] {prefix}{label}: {text}")

        lines.append("")
        lines.append(f"=== Всего сообщений: {len(messages)} ===")

        return "\n".join(lines)

    async def export_timeline(self, workflow_id: int) -> Dict[str, Any]:
        """
        Экспорт хронологии событий workflow.

        Returns:
            Dict с полями: workflow_id, seller, events[]
        """
        workflow = await self._workflow_db.get_workflow(workflow_id)
        if not workflow:
            raise ValueError(f"Workflow {workflow_id} не найден")

        seller = await self._sellers_db.get_seller(workflow["seller_id"])
        products = await self._workflow_db.get_workflow_products(workflow_id)
        messages = await self._message_log_db.get_messages_for_workflow(
            workflow_id
        )

        events: List[Dict[str, Any]] = []

        # Создание workflow
        events.append({
            "event": "DETECTED",
            "at": workflow.get("created_at", ""),
            "details": f"Обнаружен на {len(products)} товарах",
        })

        # Обнаружение каждого товара
        for p in products:
            events.append({
                "event": "PRODUCT_DETECTED",
                "at": p.get("detected_at", ""),
                "details": p.get("title", p["product_id"]),
                "product_id": p["product_id"],
            })

        # WARN1
        if workflow.get("warn1_sent_at"):
            warn1_msg = self._find_first_message(messages, "OUT", "WARN1")
            events.append({
                "event": "WARN1_SENT",
                "at": workflow["warn1_sent_at"],
                "message": warn1_msg,
            })

        # WARN2
        if workflow.get("warn2_sent_at"):
            warn2_msg = self._find_first_message(messages, "OUT", "WARN2")
            events.append({
                "event": "WARN2_SENT",
                "at": workflow["warn2_sent_at"],
                "message": warn2_msg,
            })

        # Входящие сообщения продавца
        for msg in messages:
            if msg["direction"] == "IN":
                events.append({
                    "event": "SELLER_RESPONSE",
                    "at": msg.get("sent_at", ""),
                    "classification": msg.get("classification"),
                    "message": msg.get("message_text", "")[:200],
                })

        # Юрзаявка
        legal = await self._legal_db.get_request_by_workflow(workflow_id)
        if legal:
            events.append({
                "event": "LEGAL_REQUEST",
                "at": legal.get("created_at", ""),
                "request_id": legal["id"],
            })

        # Отсоединение
        if workflow.get("detached_at"):
            events.append({
                "event": "DETACHED",
                "at": workflow["detached_at"],
            })

        # Закрытие
        if workflow.get("closed_at"):
            events.append({
                "event": "CLOSED",
                "at": workflow["closed_at"],
            })

        # Сортировка по времени
        events.sort(key=lambda e: e.get("at", "") or "")

        return {
            "workflow_id": workflow_id,
            "seller": {
                "name": seller.get("merchant_name", "?") if seller else "?",
                "merchant_id": workflow["seller_id"],
                "phone": seller.get("phone") if seller else None,
            },
            "status": workflow.get("status"),
            "events": events,
        }

    async def generate_legal_package(self, request_id: int) -> bytes:
        """
        Генерация полного ZIP-архива с доказательной базой.

        Содержимое архива:
        - заявка_{id}.txt — основные данные заявки (читаемый формат)
        - переписка_{id}.txt — полный лог переписки
        - хронология_{id}.txt — хронология событий
        - documents/ — документы контрольной закупки (если есть)

        Returns:
            Байты ZIP-архива
        """
        request = await self._legal_db.get_request(request_id)
        if not request:
            raise ValueError(f"Юрзаявка {request_id} не найдена")

        workflow_id = request["workflow_id"]

        # Собрать все данные
        legal_text = await self._export_legal_request_text(request_id)
        dialog_text = await self.export_dialog_log(workflow_id)
        timeline = await self.export_timeline(workflow_id)
        timeline_text = self._timeline_to_text(timeline)

        # Собрать пути к документам закупки
        purchase_docs = self._parse_purchase_documents(request)

        # Создать ZIP
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"заявка_{request_id}.txt", legal_text)
            zf.writestr(f"переписка_{request_id}.txt", dialog_text)
            zf.writestr(f"хронология_{request_id}.txt", timeline_text)

            # Добавить документы контрольной закупки
            for doc_path in purchase_docs:
                path = Path(doc_path)
                if path.exists() and path.is_file():
                    arc_name = f"documents/{path.name}"
                    zf.write(path, arc_name)
                else:
                    logger.warning(
                        f"Документ не найден: {doc_path} "
                        f"(юрзаявка {request_id})"
                    )

        zip_bytes = zip_buffer.getvalue()

        if len(zip_bytes) > MAX_ZIP_SIZE_BYTES:
            logger.warning(
                f"ZIP юрзаявки {request_id} превышает лимит: "
                f"{len(zip_bytes) / 1024 / 1024:.1f} МБ > 45 МБ. "
                f"Документы будут разбиты на части."
            )
            return self._split_large_zip(request_id, zip_bytes, purchase_docs,
                                         legal_text, dialog_text, timeline_text)

        logger.info(
            f"ZIP юрзаявки {request_id}: "
            f"{len(zip_bytes) / 1024:.1f} КБ"
        )
        return zip_bytes

    # ------------------------------------------------------------------
    # Приватные методы
    # ------------------------------------------------------------------

    def _build_legal_package_data(
        self,
        request: Dict,
        workflow: Optional[Dict],
        seller: Optional[Dict],
        products: List[Dict],
        messages: List[Dict],
    ) -> Dict[str, Any]:
        """Собрать структуру данных для юрпакета (формат из ROADMAP)."""
        return {
            "seller": {
                "name": seller.get("merchant_name", "?") if seller else "?",
                "phone": seller.get("phone") if seller else None,
                "merchant_id": request["seller_id"],
            },
            "products": [
                {
                    "product_id": p["product_id"],
                    "url": p.get("url", ""),
                    "title": p.get("title", ""),
                    "detected_at": p.get("detected_at", ""),
                    "still_attached": bool(p.get("still_attached", 1)),
                }
                for p in products
            ],
            "timeline": self._build_timeline_from_workflow(
                request, workflow, messages
            ),
            "dialog": [
                {
                    "direction": m["direction"],
                    "text": m.get("message_text", ""),
                    "at": m.get("sent_at", ""),
                    "classification": m.get("classification"),
                    "template_code": m.get("template_code"),
                }
                for m in messages
            ],
            "legal_request": {
                "id": request["id"],
                "created_at": request.get("created_at", ""),
                "control_purchase_status": request.get(
                    "control_purchase_status", "PENDING"
                ),
                "bin_iin": request.get("bin_iin"),
                "purchase_order_number": request.get("purchase_order_number"),
                "ready_for_lawsuit": bool(request.get("ready_for_lawsuit", 0)),
            },
        }

    def _build_timeline_from_workflow(
        self,
        request: Dict,
        workflow: Optional[Dict],
        messages: List[Dict],
    ) -> List[Dict[str, Any]]:
        """Собрать хронологический таймлайн из данных workflow."""
        events: List[Dict[str, Any]] = []

        if workflow:
            events.append({
                "event": "DETECTED",
                "at": workflow.get("created_at", ""),
            })

            if workflow.get("warn1_sent_at"):
                warn1_msg = self._find_first_message(messages, "OUT", "WARN1")
                events.append({
                    "event": "WARN1_SENT",
                    "at": workflow["warn1_sent_at"],
                    "message": warn1_msg,
                })

            if workflow.get("warn2_sent_at"):
                warn2_msg = self._find_first_message(messages, "OUT", "WARN2")
                events.append({
                    "event": "WARN2_SENT",
                    "at": workflow["warn2_sent_at"],
                    "message": warn2_msg,
                })

        events.append({
            "event": "LEGAL_REQUEST",
            "at": request.get("created_at", ""),
        })

        return events

    def _package_to_csv(self, package: Dict[str, Any]) -> bytes:
        """Конвертировать пакет данных в CSV."""
        output = io.StringIO()
        writer = csv.writer(output)

        # Заголовок — информация о продавце
        writer.writerow(["=== Информация о продавце ==="])
        writer.writerow(["Магазин", package["seller"]["name"]])
        writer.writerow(["Телефон", package["seller"]["phone"] or "—"])
        writer.writerow(["Merchant ID", package["seller"]["merchant_id"]])
        writer.writerow([])

        # Товары
        writer.writerow(["=== Товары ==="])
        writer.writerow(["ID", "Название", "URL", "Дата обнаружения", "На карточке"])
        for p in package["products"]:
            writer.writerow([
                p["product_id"],
                p["title"],
                p["url"],
                p["detected_at"],
                "Да" if p["still_attached"] else "Нет",
            ])
        writer.writerow([])

        # Таймлайн
        writer.writerow(["=== Хронология ==="])
        writer.writerow(["Событие", "Дата/время", "Сообщение"])
        for event in package["timeline"]:
            writer.writerow([
                event["event"],
                event["at"],
                event.get("message", ""),
            ])
        writer.writerow([])

        # Переписка
        writer.writerow(["=== Переписка ==="])
        writer.writerow(["Направление", "Дата/время", "Текст", "Классификация", "Шаблон"])
        for msg in package["dialog"]:
            writer.writerow([
                msg["direction"],
                msg["at"],
                msg["text"],
                msg.get("classification", ""),
                msg.get("template_code", ""),
            ])

        return output.getvalue().encode("utf-8-sig")

    def _parse_purchase_documents(self, request: Dict) -> List[str]:
        """Извлечь пути к документам закупки из JSON-поля."""
        docs_json = request.get("purchase_documents")
        if not docs_json:
            return []
        try:
            docs = json.loads(docs_json)
            if isinstance(docs, list):
                return [str(d) for d in docs]
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                f"Невалидный JSON в purchase_documents "
                f"юрзаявки {request.get('id')}"
            )
        return []

    def _split_large_zip(
        self,
        request_id: int,
        full_zip_bytes: bytes,
        purchase_docs: List[str],
        legal_text: str,
        dialog_text: str,
        timeline_text: str,
    ) -> bytes:
        """
        Разбить большой ZIP на части, чтобы уложиться в лимит Telegram.
        Первая часть содержит текстовые данные, последующие — документы.

        В текущей реализации возвращает первую часть (текстовые данные),
        т.к. документы большого объёма — это Фаза 7 (контрольные закупки).
        """
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"заявка_{request_id}.txt", legal_text)
            zf.writestr(f"переписка_{request_id}.txt", dialog_text)
            zf.writestr(f"хронология_{request_id}.txt", timeline_text)

        logger.info(
            f"Создана первая часть ZIP юрзаявки {request_id} "
            f"(без документов): {len(zip_buffer.getvalue()) / 1024:.1f} КБ"
        )
        return zip_buffer.getvalue()

    async def _export_legal_request_text(self, request_id: int) -> str:
        """Экспорт юрзаявки в человекочитаемом текстовом формате."""
        request = await self._legal_db.get_request(request_id)
        if not request:
            raise ValueError(f"Юрзаявка {request_id} не найдена")

        workflow = await self._workflow_db.get_workflow(request["workflow_id"])
        seller = await self._sellers_db.get_seller(request["seller_id"])
        products = await self._workflow_db.get_workflow_products(
            request["workflow_id"]
        )

        seller_name = seller.get("merchant_name", "?") if seller else "?"
        seller_phone = seller.get("phone", "—") if seller else "—"
        merchant_id = request["seller_id"]

        lines = [
            "=" * 50,
            f"ЮРИДИЧЕСКАЯ ЗАЯВКА #{request_id}",
            "=" * 50,
            "",
            "--- Информация о продавце ---",
            f"Магазин: {seller_name}",
            f"Телефон: {seller_phone or '—'}",
            f"Merchant ID: {merchant_id}",
            "",
            "--- Товары ---",
        ]

        for i, p in enumerate(products, 1):
            title = p.get("title", p["product_id"])
            url = p.get("url", "")
            detected = self._format_timestamp(p.get("detected_at", ""))
            attached = "Да" if p.get("still_attached", 1) else "Нет"
            lines.append(f"  {i}. {title}")
            if url:
                lines.append(f"     Ссылка: {url}")
            lines.append(f"     Обнаружен: {detected}")
            lines.append(f"     Ещё на карточке: {attached}")

        lines.append("")
        lines.append("--- Хронология уведомлений ---")

        if workflow:
            if workflow.get("warn1_sent_at"):
                lines.append(f"WARN1 отправлен: {self._format_timestamp(workflow['warn1_sent_at'])}")
            if workflow.get("warn2_sent_at"):
                lines.append(f"WARN2 отправлен: {self._format_timestamp(workflow['warn2_sent_at'])}")

        lines.append(f"Юрзаявка создана: {self._format_timestamp(request.get('created_at', ''))}")

        status = request.get("control_purchase_status", "PENDING")
        status_labels = {
            "PENDING": "Ожидает",
            "ASSIGNED": "Назначена",
            "COMPLETED": "Выполнена",
        }
        lines.append("")
        lines.append("--- Контрольная закупка ---")
        lines.append(f"Статус: {status_labels.get(status, status)}")

        if request.get("bin_iin"):
            lines.append(f"БИН/ИИН: {request['bin_iin']}")
        if request.get("purchase_order_number"):
            lines.append(f"Номер заказа: {request['purchase_order_number']}")

        lines.append("")
        lines.append("=" * 50)
        lines.append("PKS Ltd")

        return "\n".join(lines)

    @staticmethod
    def _timeline_to_text(timeline: Dict[str, Any]) -> str:
        """Конвертировать таймлайн в человекочитаемый текст."""
        seller = timeline.get("seller", {})
        events = timeline.get("events", [])

        event_labels = {
            "DETECTED": "Обнаружен прилепала",
            "PRODUCT_DETECTED": "Обнаружен на товаре",
            "WARN1_SENT": "Отправлено первое предупреждение (WARN1)",
            "WARN2_SENT": "Отправлено повторное предупреждение (WARN2)",
            "SELLER_RESPONSE": "Ответ продавца",
            "LEGAL_REQUEST": "Создана юридическая заявка",
            "DETACHED": "Продавец отсоединился",
            "CLOSED": "Воронка закрыта",
        }

        lines = [
            "=" * 50,
            "ХРОНОЛОГИЯ СОБЫТИЙ",
            "=" * 50,
            "",
            f"Магазин: {seller.get('name', '?')}",
            f"Телефон: {seller.get('phone', '—')}",
            f"Статус: {timeline.get('status', '—')}",
            "",
            "-" * 40,
        ]

        for event in events:
            at = event.get("at", "—")
            if at and at != "—":
                try:
                    dt = datetime.fromisoformat(at)
                    at = dt.strftime("%d.%m.%Y %H:%M")
                except (ValueError, TypeError):
                    pass

            event_type = event.get("event", "")
            label = event_labels.get(event_type, event_type)

            line = f"[{at}] {label}"

            details = event.get("details", "")
            if details:
                line += f" — {details}"

            message = event.get("message", "")
            if message and event_type == "SELLER_RESPONSE":
                classification = event.get("classification", "")
                if classification:
                    line += f" ({classification})"
                line += f"\n           \"{message}\""

            lines.append(line)

        lines.append("-" * 40)
        lines.append(f"\nВсего событий: {len(events)}")

        return "\n".join(lines)

    @staticmethod
    def _find_first_message(
        messages: List[Dict], direction: str, template_prefix: str
    ) -> str:
        """Найти первое сообщение с указанным направлением и префиксом шаблона."""
        for msg in messages:
            if msg["direction"] == direction:
                template = msg.get("template_code", "") or ""
                if template.upper().startswith(template_prefix.upper()):
                    return msg.get("message_text", "")[:300]
        return ""

    @staticmethod
    def _format_timestamp(dt_str: str) -> str:
        """Форматировать timestamp из БД в читаемый вид."""
        if not dt_str:
            return "—"
        try:
            dt = datetime.fromisoformat(dt_str)
            return dt.strftime("%d.%m.%Y %H:%M")
        except (ValueError, TypeError):
            return dt_str
