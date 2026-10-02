# Manufacturing Phase 3 - Work Order

Status: Implementation complete; automated regression passed; awaiting manual browser UAT

## Scope

- Keep ERPNext Work Order as manufacturing authority.
- Reuse ERPNext Production Plan `make_work_order`.
- Validate Planning Center-managed Production Plan lineage on generated Work Orders.
- Recover Calco context skipped by ERPNext's `ignore_validate` Work Order insert.
- Preserve standard and legacy Work Orders that do not carry Planning Center lineage.

## Implementation

- Added a Work Order validate hook for managed Production Plan rows.
- Added an `after_insert` initializer for Work Orders created by submitted Production Plans.
- Copied confirmed planning review status, quantity, reviewer, timestamp, remarks, and required delivery date.
- Persisted existing execution defaults and FG batch context after standard Work Order creation.
- Blocked mismatched plan rows, unsubmitted plans, inconsistent Item/BOM/Sales Order lineage, unconfirmed review, and quantity above planned coverage.

## Boundaries

- No ERPNext core modification.
- No schema or migration.
- No new DocType, workflow, permission, or Work Order creation API.
- Existing FG Planning and legacy Work Order behavior remains unchanged.
- Production Consumption Entry remains active.
- Work Order UX redesign is deferred until Job Card implementation is complete.
- Rich batch dropdown columns are deferred until manufacturing execution is stable.

## Validation

- 8 focused Work Order tests passed.
- 6 Production Plan regression tests passed.
- 11 Production Planning Center regression tests passed.
- Live non-writing validation passed for `MFG-WO-2026-00018`.
- All recovery services resolve the new hook and share identical source hashes.
- Recovery 8081 and existing local 8080 both return HTTP 200.

## Rollback

1. Revert the isolated Phase 3 commit.
2. Restore `/tmp/hooks.py.pre-manufacturing-phase3-20260729` in each recovery application container.
3. Remove `calco_erp/calco_production/work_order_control.py` from recovery containers.
4. Clear the recovery site cache and restart only recovery application services.

## Manual Browser UAT

1. Open a submitted Production Plan containing a confirmed Planning Center row.
2. Use the standard ERPNext Create Work Order action.
3. Confirm exactly the remaining eligible quantity is created.
4. Open the Work Order and verify Production Plan, Production Plan Item, Sales Order, Item, BOM, reviewed quantity, reviewer, required delivery date, stage, and FG batch context.
5. Re-run Create Work Order and confirm no duplicate quantity is created.
6. Confirm a standard legacy Work Order without Planning Center lineage still opens and saves.
7. Confirm Production Planning Center changes the recommendation to Released through Work Order.
8. Confirm no direct Work Order action was added to Production Planning Center.

Browser status: NOT VALIDATED. Manual business UAT is required.
