# Production P2 Architecture Decision: Legacy RM Governance Evidence

## Status

Deferred for Production P2 architecture approval. No P1 runtime behavior is changed by this record.

## Context

Production P1 treats every Raw Material without an existing Master Data Governance
`Operational Ready` result as not ready. This is conservative and prevents a planning
recommendation from progressing when governance evidence is absent.

Live UAT identified two distinct states that must remain separate:

- Explicitly not ready: a governance package exists and is below Operational Ready.
- Missing evidence: no governance package currently resolves the RM's readiness.

## P2 Decision Required

Calco must approve how legacy Raw Materials with missing governance evidence are handled.
The decision must define whether they are:

- governed retrospectively through the existing Master Data Governance process;
- temporarily grandfathered through a controlled, time-bound exception register; or
- blocked until evidence is completed.

## Non-Negotiable Constraints

- Missing evidence must never be silently treated as Operational Ready.
- Any exception must identify owner, approver, reason, scope, effective date, and expiry.
- The exception must remain a recommendation input and must not become Work Order authority.
- Supplier Approval Matrix, RM quality, and RM planning authority remain unchanged.
- Existing P1 blocker behavior remains active until a separate P2 decision is approved and implemented.

