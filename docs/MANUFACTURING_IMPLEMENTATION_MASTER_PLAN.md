# Manufacturing Implementation Master Plan

Architecture: `calco_erp/calco_production/MANUFACTURING_IMPLEMENTATION_ARCHITECTURE.md`

## Authority Boundary

Production Planning Review is a Planning Center review step, not an ERP transaction. ERPNext Production Plan remains the first planning transaction authority. Work Order, Job Card, Stock Entry, Quality Inspection, BOM, and Batch retain their standard authority.

## Phase Register

| Phase | Status | Database Changes | UAT | Commit |
| --- | --- | --- | --- | --- |
| 1. Production Planning Review | Accepted and closed | 20 review annotation Custom Fields | 11 focused tests passed; business acceptance recorded | `Phase 1 - Production Planning Review` |
| 2. Production Plan | Implementation complete; automated regression passed; awaiting manual browser UAT | None | 17 focused/regression tests and controlled non-writing UAT passed; manual browser UAT pending | `Phase 2 - Production Plan` |
| 3. Work Order | Implementation complete; automated regression passed; awaiting manual browser UAT | None | 25 focused/regression tests and live non-writing validation passed | `Phase 3 - Work Order` |
| 4-15 | Not started | None approved | Not started | - |

## Phase 1 Record

- Scope: planner decision, reviewed quantity, reviewer identity/time, remarks, stale-review protection, filtering, and Draft Production Plan eligibility.
- Files changed: planning service, Planning Center endpoint/UI, fixture configuration/data, focused tests, architecture, and this log.
- Custom Fields: five review fields each on Sales Order Item, Sales Forecast Item, Work Order, and Production Plan Item.
- Database backup: `20260727_234117-recovery120120_localhost-database.sql.gz`.
- Database migration: completed on `recovery120120.localhost`; all 20 fields are present.
- Workflow changes: none.
- New DocTypes: none.
- Automated validation: Python compilation passed; fixture integrity passed; 11 focused tests passed; live RPC target resolved; all recovery service source hashes matched.
- Browser UAT: not validated. Browser control could not create a session, and the recovery database has no submitted Sales Order or Sales Forecast demand.
- Legacy impact: Production Consumption Entry, Production Job Card, Production RM Requisition, and FG Delivery Note remain active and unchanged.
- Rollback: revert the isolated Phase 1 commit and restore the five runtime files from `recovery_refresh_backups/manufacturing_phase1_20260727_234038`; remove only the 20 Phase 1 Custom Fields if schema rollback is explicitly approved.
- Known risk: the existing `after_migrate` maintenance setup scans approximately 21,455 Maintenance Spare Mapping rows and made this migration take about 20 minutes.
- Closure: Phase 1 accepted by the business and closed on 2026-07-28.

## Phase 2 Record

- Scope: protect Planning Center-managed Production Plan rows while preserving standard ERPNext Production Plan authority and compatibility.
- Files changed: scoped Production Plan validator, hook registration, focused tests, Phase 2 report/UAT evidence, and this log.
- Custom Fields: none; Phase 2 reuses the approved Phase 1 lineage and review fields.
- Database backup: `20260728_220922-recovery120120_localhost-database.sql.gz`.
- Database migration: not required.
- Workflow changes: none.
- New DocTypes: none.
- Automated validation: Python compilation passed; hook resolved; 6 Phase 2 tests and 11 Production Planning regression tests passed.
- Controlled UAT: submitted Production Plan `MFG-PP-2026-00010` passed lineage validation; duplicate source and excess quantity were blocked in memory; all transaction counts remained unchanged.
- Browser UAT: not validated because browser control failed before navigation with Windows sandbox error 1344. Manual checklist prepared.
- Legacy impact: Production Consumption Entry, Production Job Card, Production RM Requisition, and FG Delivery Note remain active and unchanged.
- Rollback: revert the isolated Phase 2 commit and restore `hooks.py.pre_phase2` from `recovery_refresh_backups/manufacturing_phase2_20260728_220921`; no schema rollback is required.
- Known limitation: Phase 2 does not submit Production Plans or create Work Orders. Work Order creation remains Phase 3.
- Pending decision: business user must complete manual browser UAT and approve Phase 2 before Phase 3 begins.
## Phase 3 Record

- Scope: preserve the standard submitted Production Plan to Work Order path and synchronize Planning Center review context skipped by ERPNext's `ignore_validate` insertion.
- Files changed: Work Order controller, hook registration, focused tests, Phase 3 report, and this log.
- Custom Fields: none; Phase 3 reuses standard Production Plan/Work Order links and approved Phase 1 review fields.
- Database migration: not required.
- Workflow and permission changes: none.
- New DocTypes: none.
- Automated validation: Python compilation, 8 focused tests, 6 Production Plan regressions, and 11 Production Planning regressions passed.
- Controlled UAT: live Work Order `MFG-WO-2026-00018` resolved submitted Production Plan item `pv0athnmr1`, confirmed quantity 1000, reviewer, and required delivery date without writing data.
- Browser UAT: not validated because browser control remains unavailable; manual checklist prepared.
- Legacy impact: Work Orders without Planning Center-managed plan lineage remain unchanged; Production Consumption Entry and other legacy production documents remain active.
- Rollback: revert the isolated Phase 3 commit and restore `/tmp/hooks.py.pre-manufacturing-phase3-20260729` in each recovery application container.
- Deferred: Work Order UX redesign after Job Card implementation; richer batch dropdown display after manufacturing execution is stable.
