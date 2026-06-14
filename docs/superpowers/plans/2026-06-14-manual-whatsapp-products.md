# Manual WhatsApp Product Sending Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one admin button to a seller card that immediately sends all current products to WhatsApp and records progress without disrupting automatic escalation.

**Architecture:** Extend the existing seller workflow with a two-field snapshot and a single engine operation. Reuse `send_warn1()` for untouched workflows; send a short logged message for workflows that already passed WARN1. Keep Telegram handlers thin and calculate progress from the stored snapshot plus the current active product count.

**Tech Stack:** Python 3.11, aiogram, aiosqlite, pytest, Green API WhatsApp client

---

## File Map

- `database/schema.py`: include snapshot columns in fresh databases.
- `database/migrations.py`: migrate existing production databases.
- `database/seller_workflow.py`: read the latest workflow and record a successful manual snapshot.
- `workflow/engine.py`: coordinate manual sending without duplicating WARN1.
- `bot/handlers.py`: render progress and handle confirm/send callbacks.
- `tests/test_phase1_db.py`: cover snapshot persistence and migration version.
- `tests/test_phase4_workflow_engine.py`: cover manual send behavior.
- `tests/test_manual_whatsapp_ui.py`: cover card button and progress rendering.

### Task 1: Persist Manual Send Snapshot

**Files:**
- Modify: `database/schema.py`
- Modify: `database/migrations.py`
- Modify: `database/seller_workflow.py`
- Test: `tests/test_phase1_db.py`

- [ ] **Step 1: Write failing DAO tests**

Add tests that call:

```python
await wf_db.record_manual_products_sent(wf_id, 12)
workflow = await wf_db.get_workflow(wf_id)
assert workflow["manual_products_initial_count"] == 12
assert workflow["manual_products_sent_at"] is not None

latest = await wf_db.get_latest_workflow_for_seller("M001")
assert latest["id"] == wf_id
```

- [ ] **Step 2: Run the DAO tests and verify failure**

Run:

```bash
pytest tests/test_phase1_db.py -q
```

Expected: failure because the columns and DAO methods do not exist.

- [ ] **Step 3: Add migration 7 and DAO methods**

Add nullable columns:

```sql
ALTER TABLE seller_workflows ADD COLUMN manual_products_sent_at TIMESTAMP
ALTER TABLE seller_workflows ADD COLUMN manual_products_initial_count INTEGER
```

Implement:

```python
async def get_latest_workflow_for_seller(self, seller_id: str) -> Optional[Dict[str, Any]]:
    ...

async def record_manual_products_sent(
    self, workflow_id: int, product_count: int
) -> bool:
    ...
```

The snapshot update must not modify `updated_at`, so automatic WARN2 timing is unchanged.

- [ ] **Step 4: Run the DAO tests**

Run:

```bash
pytest tests/test_phase1_db.py -q
```

Expected: all tests pass.

### Task 2: Add the Manual Send Operation

**Files:**
- Modify: `database/seller_workflow.py`
- Modify: `workflow/engine.py`
- Test: `tests/test_phase4_workflow_engine.py`

- [ ] **Step 1: Write failing engine tests**

Cover these cases:

```python
result = await engine.send_products_to_seller("M001")
assert result.success is True
assert result.sent_warn1 is True
assert result.product_count == 2
```

For an existing `WARN1_SENT` workflow:

```python
status_before = (await wf_db.get_workflow(wf_id))["status"]
result = await engine.send_products_to_seller("M001")
status_after = (await wf_db.get_workflow(wf_id))["status"]
assert result.sent_warn1 is False
assert status_after == status_before
assert "SKU001" not in wa_client.send_text.call_args.args[1]
assert "https://kaspi.kz/" in wa_client.send_text.call_args.args[1]
```

Also verify that failed WhatsApp delivery, missing phone, and zero active products
do not write `manual_products_sent_at`.

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```bash
pytest tests/test_phase4_workflow_engine.py -q
```

Expected: failure because `send_products_to_seller()` and its result type do not exist.

- [ ] **Step 3: Implement the engine operation**

Add:

```python
@dataclass(frozen=True)
class ManualProductSendResult:
    success: bool
    reason: str
    workflow_id: Optional[int] = None
    product_count: int = 0
    sent_warn1: bool = False
```

Implement `send_products_to_seller(seller_id)`:

1. Load seller and active products.
2. Reject missing phone or empty product list.
3. Reuse the latest non-closed workflow, including `READY_FOR_LAWSUIT`;
   otherwise create a workflow and attach all active products.
4. If status is `NEW_SELLER_ATTACH`, call `send_warn1()`.
5. Otherwise send one short message containing current product titles and URLs,
   then write it to `message_log` as `MANUAL_PRODUCT_LIST`.
6. Record the snapshot only after successful WhatsApp delivery.

- [ ] **Step 4: Ensure templates only include active products**

Include `ps.is_active` in workflow product rows and filter inactive rows while
building WhatsApp template context. Do not remove historical workflow products,
because detachment checks and legal exports need them.

- [ ] **Step 5: Run engine tests**

Run:

```bash
pytest tests/test_phase4_workflow_engine.py -q
```

Expected: all tests pass.

### Task 3: Add the Seller Card Button and Progress

**Files:**
- Modify: `bot/handlers.py`
- Create: `tests/test_manual_whatsapp_ui.py`

- [ ] **Step 1: Write failing presentation tests**

Test `_build_seller_details()` with:

```python
tracking = {
    "manual_products_sent_at": "2026-06-14 15:30:00",
    "manual_products_initial_count": 12,
}
```

Assert the card contains `Было товаров: 12`, `Осталось: 5`,
`Откреплено: 7`, `частично открепился`, and exactly one
`Отправить товары в WhatsApp` action.

- [ ] **Step 2: Run the UI test and verify failure**

Run:

```bash
pytest tests/test_manual_whatsapp_ui.py -q
```

Expected: failure because the card does not accept tracking data or render the button.

- [ ] **Step 3: Render progress and the action**

Extend the card builder with optional `tracking` and `show_whatsapp_action`
arguments. Calculate:

```python
initial = tracking["manual_products_initial_count"]
current = len(products)
detached = max(initial - current, 0)
```

Render one action callback:

```python
    InlineKeyboardButton(
        text="Отправить товары в WhatsApp",
        callback_data=f"wa_products_send_{merchant_id}",
    )
```

- [ ] **Step 4: Add the send callback**

Add one admin-only callback:

```text
wa_products_send_<merchant_id>
```

The send callback calls `workflow_engine.send_products_to_seller()`, reports
success or a concrete validation failure, and redraws the seller card.

- [ ] **Step 5: Run UI and engine tests**

Run:

```bash
pytest tests/test_manual_whatsapp_ui.py tests/test_phase4_workflow_engine.py -q
```

Expected: all tests pass.

### Task 4: Regression Verification

**Files:**
- Verify only

- [ ] **Step 1: Run focused workflow and database tests**

```bash
pytest tests/test_phase1_db.py tests/test_phase3_templates_classifier.py tests/test_phase4_workflow_engine.py tests/test_phase5_escalation.py tests/test_manual_whatsapp_ui.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run the full test suite**

```bash
pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 3: Check formatting and diff**

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; only intended implementation and test files are modified.
