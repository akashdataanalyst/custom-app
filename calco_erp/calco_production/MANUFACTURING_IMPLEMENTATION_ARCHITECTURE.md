# Calco Manufacturing Implementation Architecture

Status: Approved for phased implementation

## Customization Boundary

ERPNext remains the authority for Production Plan, Work Order, Job Card, Stock Entry, Quality Inspection, BOM, and Batch. Calco may add planning annotations, controlled evidence, validations, workflows, journeys, and reports, but must not create a parallel transaction authority, modify ERPNext core, invalidate legacy records, or disable Production Consumption Entry before approved cutover.

Production Planning Review is a Planning Center review step. It is not a separate ERP transaction, DocType, workflow document, or production authority. It only determines whether a calculated recommendation may be added to a Draft Production Plan.

## Definition of Done

Every phase is complete only when its authority boundary is documented; changes are isolated in one reviewable commit; no ERPNext core file is modified; schema, migration, rollback, and permissions are reported; syntax and focused automated tests pass; standard and legacy regressions pass; browser UAT passes with evidence; no duplicate authority is introduced; and business approval is received before the next phase.

## Phase Completion Criteria

1. Production Planning Review: lineage, blockers, review decision, reviewed quantity, audit identity, persistence, filtering, and draft-plan eligibility pass; review creates no ERP transaction.
2. Production Plan: reviewed recommendations create or update one Draft Production Plan without duplicate source lines.
3. Work Order: submitted Production Plan creates standard Work Orders; no alternate route is added.
4. Production Readiness: BOM, machine, material, governance, and ownership entry criteria pass.
5. Material Reservation: full released requirements, multiple batches, FIFO, and justified override are auditable.
6. Material Issue: standard Stock Entry moves material to common WIP; Production Consumption Entry remains operational.
7. Job Card Execution: standard Job Cards own operation execution and Calco evidence has no quantity authority.
8. Grade Change: transition category, checklist, cleaning code, inspection, and approval are preserved.
9. Premix and Blending: BOM owns plan, Stock Entry owns actual stock, and preparation evidence is complete.
10. In Process Quality: required readings use Quality Inspection and frozen quality rules remain intact.
11. Packing: operation, bags, labels, and checks link to Job Card, Batch, and Quality Inspection.
12. Manufacture Entry: one Manufacture Stock Entry owns FG, consumption, loss, by-products, and batch creation.
13. FG Quarantine: manufactured FG enters only quarantine and remains traceable.
14. Final QC: Quality Inspection alone owns disposition and blocks incomplete or rejected release.
15. FG Release: standard Stock Entry moves approved batches from quarantine to released FG.

## Phase 4 Approved Refinements

Production Readiness is an automatic Work Order control evaluation, not a separate transaction. It evaluates on Work Order load and changes to machine, BOM, or quantity, and again when Material Transfer is opened. A manual **Refresh Readiness** action remains available.

The four checks are Material, Machine, BOM, and Planning. Every BOM component must be enabled. The compact Work Order summary shows each check, the overall result, exact blocker, and responsible owner.

Full BOM material is required by default. Under the approved Partial Production ADR, only a Production Head-approved quantity with reason and audit identity may be evaluated instead. Work Order submission remains allowed. Material Transfer and initial Job Card Start are blocked until the result is **Ready for Material Issue**.

Phase 4 adds no DocType and modifies no ERPNext core file. Work Order remains manufacturing authority; Stock Entry remains material-movement authority; Job Card remains execution authority.