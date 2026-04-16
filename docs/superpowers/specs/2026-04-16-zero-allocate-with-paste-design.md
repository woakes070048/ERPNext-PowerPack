# Zero Allocate with Paste — Design Spec

**Date:** 2026-04-16
**App:** cecypo_powerpack
**Feature area:** Payment Reconciliation PowerUp
**Status:** Design approved; implementation plan pending

## Problem

A single supplier credit (return Purchase Invoice or Payment Entry) often needs to be distributed across many outstanding Purchase Invoices — sometimes 30+. The existing Zero Allocate workflow requires selecting every payment and every invoice row in the Payment Reconciliation grid, which is tedious and error-prone when the supplier hands us a list of bill numbers and amounts.

## Goal

Add a new "Zero Allocate with Paste" button next to Zero Allocate. The user pastes a two-column list (`bill_no`, amount), the system resolves the bills to Purchase Invoices, the user reviews/adjusts amounts against each PI's outstanding, and clicks Proceed. The existing `allocation` child table gets populated with correctly-shaped rows. The user then clicks the existing Zero Reconcile button (unchanged) to commit.

## Non-goals

- Customer-side equivalent (credit notes crediting Sales Invoices). Ship Supplier first; Customer is a potential follow-up.
- Reconciling across multiple credits in a single dialog invocation. One credit per paste session.
- Any change to the existing Zero Allocate or Zero Reconcile behavior.
- Automatic commit — user still clicks Zero Reconcile.

## User flow

1. User opens a Payment Reconciliation, sets `party_type = Supplier`, picks a party, clicks **Get Unreconciled Entries** to load the payments and invoices tables.
2. User clicks **Zero Allocate with Paste** (inner button under "Powerup" group, next to Zero Allocate).
3. Single modal opens, three sections:
   - **Credit**: select from the already-loaded payments.
   - **Paste**: textarea for 2-column data + **Parse & Match** button.
   - **Review** (revealed after parse): editable table of matched PIs with live totals, skipped panel for ambiguous/not-found rows, Auto Distribute / Reset / Proceed.
4. User adjusts amounts (inline edit, per-row "match outstanding" helper, or Auto Distribute).
5. User clicks **Proceed**. If existing allocation rows exist, a Replace/Append picker appears. Allocation table is populated.
6. User clicks the existing **Zero Reconcile** button to commit.

## Architecture

No new DocTypes. No `hooks.py` changes. Feature-gated by existing `enable_payment_reconciliation_powerup` flag.

### New client code

Added to `cecypo_powerpack/public/js/payment_reconciliation_powerup.js`:

- `setup_zero_allocate_paste_button(frm)` — adds inner button under "Powerup" group. Visible only when `party_type === "Supplier"` AND `frm.doc.payments` contains at least one row with `amount > 0` (same "allocatable credit" criterion used by Section A's Credit selector). Called from the same `refresh` / `party_type` event chain as the existing 2% button.
- `zero_allocate_with_paste(frm)` — opens the main modal.
- `parse_paste(text)` — pure function, returns `{rows: [{bill_no, amount}], skipped: [{line, reason}]}`.
- `render_review_section(dialog, matched, ambiguous, not_found, credit_available)` — renders the Review HTML, wires input handlers.
- `run_auto_distribute(rows, credit_available)` — in-place cap-then-absorb.
- `proceed_zero_allocate_paste(frm, dialog, state)` — call `zero_allocate_entries`, override amounts, add to grid.

All helpers module-local (IIFE-scoped), nothing on `window`.

### New server code

One new whitelisted endpoint in `cecypo_powerpack/api.py`:

```python
@frappe.whitelist()
def resolve_bill_numbers_for_credit(company: str, supplier: str, bill_numbers: str) -> dict:
    """Resolve pasted bill_no strings to open Purchase Invoices for a given supplier/company."""
```

Returns `{"matched": [...], "ambiguous": [...], "not_found": [...]}`.

### Reused, unchanged

- `zero_allocate_entries` (`api.py`) — called with synthesized inputs (single payment, N invoices from matched bill_nos); returns correctly-shaped allocation dicts. Client overwrites `allocated_amount` before grid insert.
- `CustomPaymentReconciliation.zero_reconcile` (`custom_payment_reconciliation.py`) — user's next click; handles both PE and return-PI credit types.

### Documentation-only change

One-line comment above `_reconcile_without_validation` in `custom_payment_reconciliation.py` noting the process-level monkey-patch is safe under preforked Gunicorn but risky under gevent. No behavioral change.

## Dialog UI specification

`frappe.ui.Dialog`, size `large`, title "Zero Allocate with Paste".

### Section A — Credit (always visible)

- Field `credit`: Select, required.
- Options built from `frm.doc.payments`, filtered to rows with `amount > 0`.
- Label format: `${reference_name} — ${reference_type} — ${format_currency(amount, currency)}`.
- Value: `${reference_type}|${reference_name}` (composite key — stable across grid mutations).
- Help text: *"Shown: credits loaded from this document. Run 'Get Unreconciled Entries' first if the credit you want is missing."*

### Section B — Paste (always visible)

- Field `paste`: Small Text (textarea, 8 rows), monospace font.
- Placeholder:
  ```
  BILL/2025/001	150000
  BILL/2025/002	75000.50
  BILL/2025/003	1,200.00
  ```
- Button `parse_button`: label "Parse & Match". Disabled until credit is selected.

### Section C — Review (HTML field, hidden until parse runs)

- **Summary strip**: Credit Available | Total Allocated | Remaining | Matched count | Skipped count.
  - "Remaining" color: green (≥0), red (<0).
- **Matched table**: Bill No | PI Name | Outstanding | Amount to Allocate (editable `<input type="number" step="0.01">`) | Diff (Amount − Outstanding, colored red/green/neutral) | Actions (`×` remove, `↔` set amount = outstanding).
- **Skipped panel** (collapsible, default collapsed if empty): ambiguous + not-found rows with reason badges ("Ambiguous: 2 candidates", "Not found", "Invalid amount").
- Footer: **Auto Distribute** | **Reset** (restore pasted values) | **Proceed** (primary).

### Flow

1. User picks credit → paste input enabled.
2. User pastes → clicks Parse & Match → client parses, posts bill_nos to `resolve_bill_numbers_for_credit` → renders Review.
3. User edits amounts inline; summary recomputes on every input event.
4. User optionally clicks Auto Distribute or per-row `↔`.
5. User clicks Proceed:
   - Guard: `remaining_credit >= 0`. If not, pinned red "Over-allocated by X" above button; close blocked.
   - Guard: credit still exists in `frm.doc.payments` (re-looked-up by `reference_type|reference_name`). If not, red error; dialog stays open.
   - If `frm.doc.allocation.length > 0` → Replace/Append picker (same pattern as existing `show_replace_append_dialog`).
   - Call `zero_allocate_entries` with synthesized inputs → overlay `allocated_amount` map → add rows to grid.
   - Dialog closes. Toast: *"Added {N} allocation rows. Click Zero Reconcile to commit."*
   - `setup_zero_reconcile_button(frm)` called with 100ms `setTimeout` (matches existing flow).

### Cancel

At any point discards dialog state; `frm.doc.allocation` untouched.

## Paste parser

Flexible, Excel-friendly.

**Accepted delimiters** (per line, first delimiter wins): tab, multi-space (`\s{2,}`), comma.

**Amount normalization**: strip currency symbols (`KES`, `$`, `USD`, etc.), strip thousands commas (`1,234.56` → `1234.56`), trim whitespace.

**Rules**:
- Blank lines skipped silently.
- If first line's amount column fails to parse as a number, it's treated as a header row and skipped (auto-detect).
- Extra columns beyond the first two are ignored.
- Unparseable amounts → the line goes into the `skipped` list with reason `"Invalid amount"`.
- `bill_no` column: trimmed, but case preserved.
- Zero parseable rows after filtering → client-side error before server call.

## Matching logic (`resolve_bill_numbers_for_credit`)

**Signature**:
```python
@frappe.whitelist()
def resolve_bill_numbers_for_credit(
    company: str,
    supplier: str,
    bill_numbers: str  # JSON-encoded list of strings
) -> dict
```

**Returns**:
```python
{
    "matched":   [{"bill_no","pi_name","outstanding_amount","currency","conversion_rate","posting_date"}, ...],
    "ambiguous": [{"bill_no","candidates":[{"pi_name","posting_date","outstanding_amount"}, ...]}, ...],
    "not_found": ["BILL/XXX", ...],
}
```

**Query** (single batch):
```python
rows = frappe.db.get_all(
    "Purchase Invoice",
    filters={
        "supplier": supplier,
        "company": company,
        "docstatus": 1,
        "outstanding_amount": [">", 0],
        "bill_no": ["in", bill_numbers],
    },
    fields=["name","bill_no","outstanding_amount","currency",
            "conversion_rate","posting_date","grand_total"],
)
```

**Partitioning**:
- Group rows by `bill_no`.
- Each input `bill_no`:
  - 1 match → `matched`.
  - >1 matches → `ambiguous` (candidates listed; no auto-pick).
  - 0 matches → `not_found`.
- Order preserved to match paste order.

**Gates**:
- `is_feature_enabled('enable_payment_reconciliation_powerup')` — throw if disabled.
- `frappe.has_permission("Purchase Invoice", "read")` — throw if denied.
- Max 200 bill numbers per call — throw `_("Too many bill numbers in one paste (max 200)")` otherwise.

**Input sanitation**:
- JSON-decode, coerce to list.
- Strip each element, drop blanks.
- Uniquify preserving first-seen order.

## Auto-distribute algorithm (cap-then-absorb)

Pure client-side. Runs on the review table's current editable state.

```
1. For each row in display order:
     row.amount = max(0, min(row.amount, row.outstanding))

2. total = sum(row.amount)

3. If total > credit_available:
     overflow = total - credit_available
     for row in reversed(rows):
         if overflow <= 0: break
         take = min(row.amount, overflow)
         row.amount -= take
         overflow -= take

4. Round each row.amount to 2 decimals; nudge the last non-zero row by
   the rounding residual so sum(amounts) is exact.

5. Re-render Review table; summary strip updates.
```

**Non-goals** (explicit):
- No proportional scaling across rows.
- No reshuffling by outstanding size or posting date — paste order is sacred.
- No spill to new rows the user didn't paste.

**Per-row `↔` helper**: one click sets that row's `amount = outstanding`, for single-row cleanup without running auto-distribute across everything.

## Allocation-row population (on Proceed)

1. **Client guards**: credit picked; ≥1 row with amount > 0; `sum(amount) ≤ credit_available`; credit still exists in `frm.doc.payments`.
2. **Build inputs**:
   - `payments = [the selected frm.doc.payments row]` (full object, all tracking fields preserved).
   - `invoices = [{invoice_type: "Purchase Invoice", invoice_number: r.pi_name, outstanding_amount: r.outstanding, currency: r.currency} for r in matched_rows_with_amount_gt_0]`.
3. **Call `zero_allocate_entries`** via `frappe.call`, `freeze: true`. Returns list of zero-amount allocation dicts with exchange_rate, difference_account, accounting dimensions, gain_loss_posting_date populated.
4. **Overlay amounts**: build `pi_name → amount` map from review rows; set `allocation.allocated_amount = map[allocation.invoice_number]` on each returned dict. Every other field untouched.
5. **Replace / Append**: if `frm.doc.allocation.length > 0`, show existing picker; replace path calls `frm.clear_table('allocation')` first.
6. **Insert into grid**:
   ```js
   r.message.forEach(a => Object.assign(frm.add_child('allocation'), a));
   frm.refresh_field('allocation');
   ```
7. **Close dialog**; toast: `"Added {N} allocation rows. Click Zero Reconcile to commit."`
8. **Wire Zero Reconcile**: `setTimeout(() => setup_zero_reconcile_button(frm), 100);`

**Explicit non-actions**:
- No DB writes; `frm.doc.allocation` is in-memory dirty only.
- No call into `zero_reconcile` — user's next click.

## Credit-availability calculation

Displayed "Credit Available" is NOT raw `payment.amount`. It is:

```
credit_available = payment.amount
                 - sum(alloc.allocated_amount
                       for alloc in frm.doc.allocation
                       if alloc.reference_type == payment.reference_type
                       and alloc.reference_name == payment.reference_name)
```

Prevents silent double-allocation of the same credit when the user has already run Zero Allocate or manually added allocation rows.

## Error handling

### Parse-time (client only)
- Empty paste → "Please paste at least one row" inline; no server call.
- Unparseable amount → line → `skipped` with reason `"Invalid amount"`; parsing continues.
- Zero parseable rows after filtering → "Nothing to match"; no server call.

### Resolve-time (server response)
- Feature disabled / permission denied → standard `frappe.throw` → red msgprint; dialog stays open.
- >200 bill numbers → server throws before query; user sees limit and trims.
- All rows unmatched → Review opens with empty matched table, full skipped panel. Proceed disabled.

### Proceed-time
- `sum(amounts) > credit_available` → inline red "Over-allocated by X" pinned above Proceed; Proceed disabled.
- Selected credit gone from `frm.doc.payments` (user re-ran Get Unreconciled Entries while dialog was open) → re-lookup by `reference_type|reference_name` at Proceed. If missing: red error "The selected credit is no longer available — please reopen the dialog." Dialog stays open.
- Feature flipped off mid-dialog → `zero_allocate_entries` throws feature-disabled; dialog stays open, user sees message.

### Multi-currency
No paste-specific logic. `invoices` passes `currency` from matched PI; `zero_allocate_entries` already calls `get_invoice_exchange_map_for_zero_allocate` to compute `exchange_rate`.

### Credit is a return PI (debit/credit note)
Already handled downstream: `_reconcile_without_validation` dispatches `reference_type in ("Sales Invoice", "Purchase Invoice")` to `reconcile_dr_cr_note`. No changes needed.

## Testing

### Server-side unit tests
New file `cecypo_powerpack/tests/test_zero_allocate_paste.py`:

1. Feature disabled → throws.
2. Single clean match → `matched` populated; fields correct.
3. Two PIs same `bill_no`, same supplier → `ambiguous` with both candidates; nothing in `matched`.
4. `bill_no` not in DB → `not_found`.
5. Match exists but wrong supplier / wrong company → `not_found`.
6. Match exists but `docstatus = 0` → `not_found`.
7. Match exists but `outstanding_amount = 0` → `not_found`.
8. >200 bill numbers → throws limit error.
9. Blank / whitespace-only inputs dropped silently.
10. Input order preserved across all three partitions.

Fixtures: one Supplier, one Company, 3 PIs with known bill_nos. Direct inserts in `setUp` / `tearDown`.

### Client-side parser tests
Manual QA checklist only (no JS harness in this app today).

### Manual QA checklist
- Tab-separated paste from Excel parses.
- Comma-separated paste parses.
- Header row (`Bill No\tAmount`) auto-skipped.
- Amounts with thousands separators and currency symbols parse correctly.
- Ambiguous `bill_no` → shown in skipped panel.
- Not-found `bill_no` → shown in skipped panel.
- Over-allocation blocks Proceed.
- Auto Distribute with sum > credit → bottom rows shrink; total == credit.
- Auto Distribute with one row > outstanding → that row clamps; others untouched.
- Existing allocations → Replace / Append dialog.
- Selected credit has prior allocation rows → "Credit Available" reflects reduced amount.
- Credit = Payment Entry → Zero Reconcile succeeds via `reconcile_against_document`.
- Credit = return Purchase Invoice → Zero Reconcile succeeds via `reconcile_dr_cr_note`.
- Multi-currency credit vs invoice → `exchange_rate` populated per allocation row.
- Feature flag off → button hidden.

### Regression guard
Existing Zero Allocate and Zero Reconcile paths untouched. Run `bench --site site16.local run-tests --app cecypo_powerpack` after implementation.

## Open risks / future work

- **Customer-side parity**: Same workflow is useful for customer credit notes crediting Sales Invoices. Deferred; if built later, the match key differs (SIs don't carry a supplier-style `bill_no` — likely the SI `name` or a configurable field).
- **Zero Reconcile monkey-patch**: Safe under preforked Gunicorn (current bench16 config), risky under gevent/gthread. Comment added to document the constraint. If the deployment changes, revisit.
- **JS parser test coverage**: No automated tests for `parse_paste` today. Acceptable given the manual QA list; revisit if parse bugs start biting.
