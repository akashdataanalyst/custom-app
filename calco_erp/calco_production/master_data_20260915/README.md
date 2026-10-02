# Manufacturing source package - 15 September 2026

The three workbooks are retained byte-for-byte and pinned by manifest.json.
Nothing runs automatically during migration or startup.

## Controlled application

Install only the targeted schema using
`calco_erp.calco_quality.quality_master_versions.ensure_schema()` under System Manager
before applying quality versions. This adds Manufacturing Quality Master Version
and the read-only Quality Inspection Reading Test Condition field.

Use `manufacturing_master_sync.sync_all()` for an exhaustive dry run. Apply requires
`dry_run=False`, the exact target `expected_site`, and an actual source-review
`approval_reference`. The caller owns the transaction and must roll back on error.
MPDS uses existing Manufacturing approval authority; quality versions require
System Manager or Quality Manager. Do not run broad historical import utilities.

- MPDS: exact FG/Line mapping; duplicate keys, inconsistent lines, missing Items
  and missing revision/date remain held. Explicit Excel m-d display on Air Pressure,
  Vacuum Valve On Time, Granule Size and Die is preserved as text alongside the raw
  date and number format. Month-name formats are not guessed into ranges.
- FG Standard: immutable source versions retain all cells, bounds, ratings,
  conditions, text and N/A. Ambiguous properties are held individually. Blank
  business revisions remain blank; workbook/row fingerprints supply technical
  version identity. The exact approved 760C0001 source row 1415 (DD / PreLaunch)
  is active; row 814 is preserved as an inactive historical source version.
- Control Plan: exact FG/Line versions preserve all source fields. Positive numeric
  frequencies retain existing final-QC sample-count multiplication. Operational
  Batch/Consignment labels are retained without inventing sampling cadence.
  Applicable product-quality tests receive the approved prospective
  `ipqc-active-time-default-8h-v1` policy: every 480 active production minutes,
  30-minute permitted sampling window. Pause/On Hold gaps do not advance the clock.
  Source sample size supplies IPQC sample quantity; numeric final-QC frequency is
  never converted to an IPQC interval or multiplier. Packing, dispatch, storage,
  identification and process controls retain their source semantics.
  Existing explicit Startup/Stabilization/EOB flags remain independent; absent
  flags are not invented. Ambiguous criticality remains a Quality-review blocker.

## Prospective runtime use

A successful first parallel Compounding start freezes applicable MPDS and quality
source identities, payloads and hashes in the existing run snapshot. Process
Parameter Monitor reads frozen MPDS targets. Final FG QI reads the frozen FG
Standard + line-specific Control Plan projection for that run. Missing required
specifications remain Quality-review blockers; importing a grade does not imply
that every required test has a usable specification. No automatic QI acceptance.

Historical start snapshots, QIs, native FG Control Plan rows and transactions are
not backfilled. References without a new frozen quality source snapshot retain
existing behavior. Timing revisions are prospective and do not change already-frozen plans.
New managed runs require resolved MPDS, FG specifications and Control Plan timing
before their first controlled start. Unmanaged legacy paths retain their behavior.

## Idempotence and preservation

Per-Item locks serialize source application. Immutable source identity prevents
repeat creation; retries verify stored payload hashes. Supersession creates a new
version and deactivates only its previous current version through document APIs.
Source evidence cannot be edited or deleted through ordinary forms.

No source workbook is rewritten; no stock/accounting transaction is posted.
Unresolved source conflicts remain in dry-run reports. This is not an assertion
that unresolved grades/lines are production-ready. AWS deployment requires separate
approval and target-site verification.

## Approved timing synchronization

`sync_all` now includes `manufacturing_master_authority.sync_timing`. Apply as
Administrator (or an authorized user with both required Manufacturing and Quality
roles); the timing action requires Quality Manager authority. It creates immutable
successor versions and leaves original source payloads, final-QC multipliers and
operational controls intact. A repeated application creates zero new revisions.
The caller commits only after verifying the returned results and preservation.
No migration hook, scheduler, automatic QI creation or AWS activation is added.

Read-only `manufacturing_master_authority.get_master_readiness(fg_item, line)`
returns Production Ready, QC Ready, IPQC Ready and Fully Manufacturing Ready.
IPQC Ready describes timing configuration; Fully Manufacturing Ready additionally
requires all mandatory specifications and production authority. A blank FG Standard
criterion cannot pass QC. Quality Manager may use **Revise Not Applicable** on an
active Control Plan master version for eligible unresolved criteria, with mandatory
reason and immutable actor/time/reference evidence. No criterion is automatically
marked Not Applicable. Old run fingerprints and restoration audit are retained.
