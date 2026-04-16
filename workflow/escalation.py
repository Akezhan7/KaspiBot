"""
Планировщик эскалации — автоматические переходы воронки по таймеру.

Задачи (каждые 30 минут):
1. process_new_sellers — NEW_SELLER_ATTACH → отправить WARN1
2. process_warn1_expiry — WARN1_SENT > 24ч → проверка + WARN2
3. process_warn2_expiry — WARN2_SENT > 24ч → проверка + LEGAL
4. process_dialog_timeout — DIALOG_ACTIVE без ответа 24ч → вернуть к warn-этапу

Защита от race conditions:
- APScheduler: max_instances=1 + coalesce=True на каждой задаче
- Оптимистичная блокировка: update_status_if() проверяет, что статус
  не изменился между выборкой и действием (например, webhook мог
  перевести workflow в DIALOG_ACTIVE пока мы итерируем)
"""
import logging
from typing import TYPE_CHECKING

from config import Config
from database import MessageLogDB

if TYPE_CHECKING:
    from workflow.engine import WorkflowEngine

logger = logging.getLogger(__name__)

# Таймауты эскалации (часы) — из конфигурации
WARN1_TIMEOUT_HOURS = Config.WARN1_TIMEOUT_HOURS
WARN2_TIMEOUT_HOURS = Config.WARN2_TIMEOUT_HOURS
DIALOG_TIMEOUT_HOURS = Config.DIALOG_TIMEOUT_HOURS


class EscalationScheduler:
    """
    Планировщик автоматической эскалации воронки.

    Каждый метод — отдельная задача для APScheduler.
    Использует оптимистичную блокировку (update_status_if)
    для защиты от race conditions.
    """

    def __init__(self, workflow_engine: "WorkflowEngine") -> None:
        self._engine = workflow_engine
        self._workflow_db = workflow_engine._workflow_db
        self._message_log_db = workflow_engine._message_log_db

    async def process_new_sellers(self) -> None:
        """
        Обработать новых продавцов: NEW_SELLER_ATTACH → WARN1.

        Находит все workflow в статусе NEW_SELLER_ATTACH и отправляет WARN1.
        """
        logger.info("Эскалация: обработка новых продавцов (NEW_SELLER_ATTACH)")

        try:
            workflows = await self._workflow_db.get_workflows_by_status(
                "NEW_SELLER_ATTACH"
            )

            if not workflows:
                logger.debug("Нет новых продавцов для обработки")
                return

            logger.info(f"Найдено {len(workflows)} новых продавцов для WARN1")

            processed = 0
            skipped = 0

            for wf in workflows:
                # Проверяем глобальный дневной лимит перед каждой отправкой
                if await self._is_daily_limit_reached():
                    remaining = len(workflows) - processed - skipped
                    logger.info(
                        f"Дневной лимит ({Config.DAILY_MESSAGE_LIMIT}) достигнут, "
                        f"отложено {remaining} WARN1 на завтра"
                    )
                    break

                workflow_id = wf["id"]
                merchant_name = wf.get("merchant_name", "?")

                try:
                    success = await self._engine.send_warn1(workflow_id)
                    if success:
                        processed += 1
                    else:
                        skipped += 1
                        logger.warning(
                            f"Не удалось отправить WARN1 для {merchant_name} "
                            f"(workflow {workflow_id})"
                        )

                except Exception as e:
                    logger.error(
                        f"Ошибка при обработке workflow {workflow_id} "
                        f"({merchant_name}): {e}"
                    )
                    skipped += 1

            logger.info(
                f"Обработка новых продавцов завершена: "
                f"отправлено={processed}, пропущено={skipped}"
            )

        except Exception as e:
            logger.error(f"Критическая ошибка process_new_sellers: {e}", exc_info=True)

    async def process_warn1_expiry(self) -> None:
        """
        Обработать просроченные WARN1: WARN1_SENT > 24ч.

        Для каждого:
        1. Оптимистичная блокировка (update_status_if)
        2. Повторная проверка (отсоединился?) → close
        3. Если нет → WARN2
        """
        logger.info(
            f"Эскалация: проверка просроченных WARN1 "
            f"(>{WARN1_TIMEOUT_HOURS}ч)"
        )

        try:
            workflows = await self._workflow_db.get_workflows_for_escalation(
                "WARN1_SENT", WARN1_TIMEOUT_HOURS
            )

            if not workflows:
                logger.debug("Нет просроченных WARN1")
                return

            logger.info(f"Найдено {len(workflows)} просроченных WARN1")

            for wf in workflows:
                if await self._is_daily_limit_reached():
                    logger.info(
                        f"Дневной лимит ({Config.DAILY_MESSAGE_LIMIT}) достигнут, "
                        f"отложена эскалация WARN1→WARN2"
                    )
                    break
                await self._escalate_warn1(wf)

        except Exception as e:
            logger.error(
                f"Критическая ошибка process_warn1_expiry: {e}", exc_info=True
            )

    async def process_warn2_expiry(self) -> None:
        """
        Обработать просроченные WARN2: WARN2_SENT > 24ч.

        Для каждого:
        1. Оптимистичная блокировка (update_status_if)
        2. Повторная проверка (отсоединился?) → close
        3. Если нет → escalate_to_legal
        """
        logger.info(
            f"Эскалация: проверка просроченных WARN2 "
            f"(>{WARN2_TIMEOUT_HOURS}ч)"
        )

        try:
            workflows = await self._workflow_db.get_workflows_for_escalation(
                "WARN2_SENT", WARN2_TIMEOUT_HOURS
            )

            if not workflows:
                logger.debug("Нет просроченных WARN2")
                return

            logger.info(f"Найдено {len(workflows)} просроченных WARN2")

            for wf in workflows:
                await self._escalate_warn2(wf)

        except Exception as e:
            logger.error(
                f"Критическая ошибка process_warn2_expiry: {e}", exc_info=True
            )

    async def process_dialog_timeout(self) -> None:
        """
        Обработать замолчавшие диалоги: DIALOG_ACTIVE > 24ч без ответа.

        Если продавец не отвечает 24 часа после последнего сообщения,
        возвращаем к предыдущему warn-этапу:
        - Если был WARN1 → повторная проверка → WARN2 или close
        - Если был WARN2 → повторная проверка → LEGAL или close
        """
        logger.info(
            f"Эскалация: проверка замолчавших диалогов "
            f"(>{DIALOG_TIMEOUT_HOURS}ч)"
        )

        try:
            workflows = await self._workflow_db.get_workflows_for_escalation(
                "DIALOG_ACTIVE", DIALOG_TIMEOUT_HOURS
            )

            if not workflows:
                logger.debug("Нет замолчавших диалогов")
                return

            logger.info(f"Найдено {len(workflows)} замолчавших диалогов")

            for wf in workflows:
                await self._handle_dialog_timeout(wf)

        except Exception as e:
            logger.error(
                f"Критическая ошибка process_dialog_timeout: {e}", exc_info=True
            )

    # ------------------------------------------------------------------
    # Приватные методы
    # ------------------------------------------------------------------

    async def _is_daily_limit_reached(self) -> bool:
        """Проверить, достигнут ли глобальный дневной лимит исходящих сообщений."""
        sent_today = await self._message_log_db.count_all_outgoing_today()
        return sent_today >= Config.DAILY_MESSAGE_LIMIT

    async def _escalate_warn1(self, wf: dict) -> None:
        """Эскалация одного WARN1_SENT → WARN2 (или close при отсоединении)."""
        workflow_id = wf["id"]
        merchant_name = wf.get("merchant_name", "?")

        try:
            # Проверка отсоединения перед эскалацией
            detached = await self._engine.check_detachment(workflow_id)
            if detached:
                await self._engine.close_workflow(
                    workflow_id, reason="detached_before_warn2"
                )
                logger.info(
                    f"Продавец {merchant_name} отсоединился до WARN2 "
                    f"(workflow {workflow_id})"
                )
                return

            # Оптимистичная блокировка: статус мог измениться пока шла
            # проверка отсоединения (например, webhook пришёл)
            locked = await self._workflow_db.update_status_if(
                workflow_id, "WARN2_SENT", "WARN1_SENT"
            )
            if not locked:
                logger.debug(
                    f"Workflow {workflow_id} ({merchant_name}) "
                    f"статус изменился, пропускаем"
                )
                return

            # send_warn2 ожидает текущий статус и обновит его,
            # но мы уже поставили WARN2_SENT — нужно откатить,
            # т.к. send_warn2 сам вызывает update_status.
            # Вместо этого: мы уже захватили блокировку, теперь
            # send_warn2 просто перезапишет тот же статус.
            success = await self._engine.send_warn2(workflow_id)
            if not success:
                # Откатить статус если отправка не удалась
                await self._workflow_db.update_status(workflow_id, "WARN1_SENT")
                logger.warning(
                    f"Не удалось отправить WARN2 для {merchant_name} "
                    f"(workflow {workflow_id}), статус откачен"
                )

        except Exception as e:
            logger.error(
                f"Ошибка эскалации WARN1→WARN2 для workflow "
                f"{workflow_id} ({merchant_name}): {e}"
            )

    async def _escalate_warn2(self, wf: dict) -> None:
        """Эскалация одного WARN2_SENT → LEGAL (или close при отсоединении)."""
        workflow_id = wf["id"]
        merchant_name = wf.get("merchant_name", "?")

        try:
            # Проверка отсоединения перед эскалацией
            detached = await self._engine.check_detachment(workflow_id)
            if detached:
                await self._engine.close_workflow(
                    workflow_id, reason="detached_before_legal"
                )
                logger.info(
                    f"Продавец {merchant_name} отсоединился до LEGAL "
                    f"(workflow {workflow_id})"
                )
                return

            # Оптимистичная блокировка
            locked = await self._workflow_db.update_status_if(
                workflow_id, "LEGAL_REQUEST_CREATED", "WARN2_SENT"
            )
            if not locked:
                logger.debug(
                    f"Workflow {workflow_id} ({merchant_name}) "
                    f"статус изменился, пропускаем"
                )
                return

            # escalate_to_legal сам ставит LEGAL_REQUEST_CREATED
            request_id = await self._engine.escalate_to_legal(workflow_id)
            if request_id:
                logger.info(
                    f"Юрзаявка #{request_id} создана для {merchant_name} "
                    f"(workflow {workflow_id})"
                )
            else:
                # Откат
                await self._workflow_db.update_status(workflow_id, "WARN2_SENT")
                logger.warning(
                    f"Не удалось создать юрзаявку для {merchant_name} "
                    f"(workflow {workflow_id}), статус откачен"
                )

        except Exception as e:
            logger.error(
                f"Ошибка эскалации WARN2→LEGAL для workflow "
                f"{workflow_id} ({merchant_name}): {e}"
            )

    async def _handle_dialog_timeout(self, wf: dict) -> None:
        """Обработка одного замолчавшего диалога DIALOG_ACTIVE."""
        workflow_id = wf["id"]
        merchant_name = wf.get("merchant_name", "?")

        try:
            # Повторная проверка отсоединения
            detached = await self._engine.check_detachment(workflow_id)
            if detached:
                await self._engine.close_workflow(
                    workflow_id, reason="detached_during_dialog_timeout"
                )
                logger.info(
                    f"Продавец {merchant_name} отсоединился "
                    f"(dialog timeout, workflow {workflow_id})"
                )
                return

            # Определить следующий шаг по тому, какой WARN уже был
            warn2_sent = wf.get("warn2_sent_at")

            if warn2_sent:
                # Уже был WARN2 → эскалация до юрзаявки
                locked = await self._workflow_db.update_status_if(
                    workflow_id, "LEGAL_REQUEST_CREATED", "DIALOG_ACTIVE"
                )
                if not locked:
                    logger.debug(
                        f"Workflow {workflow_id} ({merchant_name}) "
                        f"статус изменился, пропускаем"
                    )
                    return

                request_id = await self._engine.escalate_to_legal(workflow_id)
                if request_id:
                    logger.info(
                        f"Dialog timeout → LEGAL для {merchant_name} "
                        f"(workflow {workflow_id})"
                    )
                else:
                    await self._workflow_db.update_status(
                        workflow_id, "DIALOG_ACTIVE"
                    )
                    logger.warning(
                        f"Не удалось создать юрзаявку для "
                        f"{merchant_name} после dialog timeout"
                    )
            else:
                # Был только WARN1 → отправить WARN2
                locked = await self._workflow_db.update_status_if(
                    workflow_id, "WARN2_SENT", "DIALOG_ACTIVE"
                )
                if not locked:
                    logger.debug(
                        f"Workflow {workflow_id} ({merchant_name}) "
                        f"статус изменился, пропускаем"
                    )
                    return

                success = await self._engine.send_warn2(workflow_id)
                if not success:
                    await self._workflow_db.update_status(
                        workflow_id, "DIALOG_ACTIVE"
                    )
                    logger.warning(
                        f"Не удалось отправить WARN2 для "
                        f"{merchant_name} после dialog timeout"
                    )

        except Exception as e:
            logger.error(
                f"Ошибка обработки dialog timeout для workflow "
                f"{workflow_id} ({merchant_name}): {e}"
            )
