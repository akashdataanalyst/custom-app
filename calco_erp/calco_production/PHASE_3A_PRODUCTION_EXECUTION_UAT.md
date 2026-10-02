# Phase 3A Production Execution UAT

## Scope Boundary

Included:

- Weekly Production Requirement planning
- Production Job Card creation with FG batch generation
- BOM vs RM availability with FIFO batch allocation
- Partial production handling
- RM Requisition and Stores confirmation gate
- Grade Change Clearance
- Premix Preparation
- Production Run readings and downtime capture
- FG Packing & Labeling tracking on Job Card
- FG Delivery Note and FG Quarantine stop-point
- Production Execution Journey Tracker

Excluded:

- FG Quality
- FG Release
- COA
- Dispatch Clearance
- Dispatch Scanning
- QR / Barcode traceability
- Rework workflow

## UAT Steps

### 1. Production Requirement

1. Create a `Production Requirement` for a target week.
2. Keep `Pull Sales Orders` enabled and add one manual forecast line.
3. Save.
4. Verify sales order lines are pulled.
5. Verify `Sales Order` priority consumes FG inventory before `Forecast`.
6. Verify `Total Net Required Qty` is calculated.

Expected:

- Weekly requirement lines populate.
- `Net Required Qty` reflects FG inventory offset.
- Sales Orders rank above Forecast.

### 2. Job Card

1. Use `create_job_card_from_requirement` or create a `Production Job Card` against an open requirement line.
2. Save.

Expected:

- `FG Batch No` auto-generates.
- `Grade Code`, `Grade Name`, `Planned Qty`, `Target Date` persist.
- Journey Tracker appears on the Job Card.

### 3. Material Availability and Allocation

1. Open the Job Card after save.
2. Verify `Materials` populate from BOM.
3. Confirm FIFO batches are allocated.
4. Test one item with insufficient stock.

Expected:

- Material rows show `Required Qty`, `Available Qty`, `Allocated Qty`, `Batch No`, `FIFO Sequence`.
- `Material Availability` becomes `Available`, `Partially Available`, or `Blocked`.

### 4. Partial Production

1. Set `Execution Decision = Run Available Qty`.
2. Close the Job Card with `Actual Qty < Planned Qty`.
3. Enter `Production Head Reason`.

Expected:

- Job Card closes successfully.
- Balance quantity returns to linked `Production Requirement` as `Balance Return`.
- A follow-up Job Card created later gets a new FG batch number.

### 5. RM Requisition

1. Create a `Production RM Requisition` linked to the Job Card.
2. Save and verify requisition items are copied from allocated batches.
3. Run `confirm_rm_issue`.

Expected:

- Requisition captures `RM Code`, `Batch No`, `Required Qty`, `Issued Qty`.
- Stock Entry is created only on Stores confirmation.
- Requisition status becomes `Issued`.

### 6. Grade Change Clearance

1. Create `Grade Change Clearance` for the Job Card.
2. Mark a critical grade change.
3. Try to approve without purging confirmation.
4. Add purging confirmation and approve.

Expected:

- Approval is blocked until purging confirmation exists for critical grade changes.
- Job Card cannot move to production without approved clearance.

### 7. Premix Preparation

1. Create `Premix Preparation` linked to the Job Card.
2. Add item rows with allocated vs actual qty.
3. Mark as `Verified`.

Expected:

- Verification requires `Verified By`.
- Job Card can continue only after Premix Preparation is verified.

### 8. Production Run

1. Add run readings every two hours.
2. Add downtime entries across categories.
3. Move Job Card to `In Progress`.

Expected:

- Run readings save with shift and process parameters.
- Downtime rows capture category and remarks.

### 9. FG Delivery Note and Quarantine

1. Create `FG Delivery Note` for the Job Card.
2. Enter Prime FG, SPY, TPY, Metal Separator, PMX, Samples.
3. Move Job Card / FG Delivery Note to FG Quarantine.

Expected:

- Settlement summary shows:
  - `SPY -> L/OLD`
  - `TPY -> WX`
  - `Metal Separator -> WX`
  - `PMX -> PMX`
- Production execution stops at `FG Quarantine`.

## PASS / FAIL Matrix

| Area | Expected Result | Status |
| --- | --- | --- |
| Production Requirement planning | Weekly demand, sales-order priority, FG offset | Pending UAT |
| Job Card batch generation | One Job Card creates one FG Batch | Pending UAT |
| Material allocation | FIFO allocation with partial handling | Pending UAT |
| Partial production | Balance returns to requirement queue | Pending UAT |
| RM Requisition | Stock movement only on Stores confirmation | Pending UAT |
| Grade Change Clearance | Hard gate before production | Pending UAT |
| Premix Preparation | Verification before run | Pending UAT |
| Production Run capture | Shift-based readings and downtime | Pending UAT |
| FG Delivery Note | Output settlement recorded | Pending UAT |
| Journey Tracker | Visible on Requirement and Job Card | Pending UAT |
