# Phase 3A Production Architecture Decision

## Final User-Facing Flow

Approved Phase 3A production flow:

1. `Production Requirement`
2. `Work Order`
3. `Production Job Card`
4. `Material Availability & Batch Allocation`
5. `RM Requisition`
6. `Grade Change Clearance`
7. `Premix Preparation`
8. `Production Run`
9. `FG Delivery Note`
10. `FG Quarantine`

This is the only intended user-facing production path for Phase 3A.

## Work Order Decision

`Work Order` is now the canonical ERPNext manufacturing execution core.

- `Production Requirement` can seed or lead to a `Work Order` for execution.
- `Work Order` and `Production Job Card` are both visible in the normal journey and workspace.
- `Work Order` carries execution context and one-to-one FG batch mapping.
- `FG Batch No` is generated and stored per Work Order at validate time.

Decision:

- Use `Work Order` as a first-class execution control in Phase 3A.
- Keep `Production Job Card` as the detailed Calco control layer used before/around execution.

## Material Readiness Check Decision

`Material Readiness Check` is retained as legacy support only.

It remains in the Work Order setup for compatibility, but it is not a primary user-facing production stage.

## Transition Rule

Until deeper manufacturing integrations are added:

- Keep `Work Order` + `Production Job Card` as coupled execution documents.
- Continue building new Phase 3A controls on:
  - `Production Requirement`
  - `Work Order`
  - `Production Job Card`
  - `Production RM Requisition`
  - `Grade Change Clearance`
  - `Premix Preparation`
  - `FG Delivery Note`

## Workspace Guidance

Production workspace should prioritize:

- `Production Requirement`
- `Work Order`
- `Production Job Card`
- `Production RM Requisition`
- `Grade Change Clearance`
- `Premix Preparation`
- `FG Delivery Note`

This keeps the user path aligned with standard ERPNext manufacturing execution while preserving Calco control stages.
