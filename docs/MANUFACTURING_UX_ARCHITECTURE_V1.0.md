# Manufacturing UX Architecture v1.0

Status: Approved and Frozen

## Purpose

The Work Order remains the ERPNext manufacturing lifecycle authority. Its primary user experience identifies the production order, shows the Production Journey, shows the current stage, and presents the next business action.

Execution details remain available without competing with the Work Order:

- material movement is performed through Stock Entry;
- operation execution is performed through Job Card;
- quality execution is performed through Quality Inspection;
- grade change and premix evidence remain linked production evidence;
- batch genealogy remains authoritative in ERPNext Batch and stock transactions.

## Customization Boundary

- Extend ERPNext through Calco client scripts, hooks, custom fields, tabs, and linked actions.
- Do not modify ERPNext core.
- Do not create another production lifecycle authority.
- Do not remove existing fields or historical data.
- Do not change business logic, validation, workflow, permissions, or document lifecycle in Phase 4A.

## Work Order First View

The first view keeps:

- standard Work Order identity and status;
- finished good item, BOM, planned quantity, Production Plan, and Sales Order;
- machine or production line;
- Production Journey;
- Current Stage;
- Current Owner;
- Next Action;
- compact Production Readiness and exact blocker.

## Progressive Disclosure

The following remain available in collapsed sections or linked documents:

- warehouse and material requirement details;
- operations and time details;
- grade change, premix, and production quality controls;
- partial-production exception evidence;
- costing, references, and additional information;
- legacy compatibility fields.

Operator and shift execution belong to Job Card. Material issue belongs to Stock Entry. Quality activity belongs to Quality Inspection.

## Definition of Done

- The Work Order opens with one Production Journey.
- Current Stage, Current Owner, Next Action, and blocker are immediately visible.
- Production Readiness is presented inside the journey.
- Detailed sections are collapsed by default without deleting or changing data.
- Existing Work Orders remain readable and editable.
- Existing lifecycle actions continue to invoke the same backend methods.
- No ERPNext core, schema, workflow, or backend business logic changes occur.
- Browser UAT verifies new, draft, submitted, refreshed, and reloaded Work Orders.
