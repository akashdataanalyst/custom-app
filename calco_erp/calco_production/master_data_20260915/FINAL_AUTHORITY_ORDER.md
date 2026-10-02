# Final manufacturing authority package (Recovery validation; AWS not deployed)

Frozen source authorities:
- FG Standard: corrected header-driven package `calco_quality/fg_standard_corrected_20260915`; SHA256 `74dbe59b6271ffbff5a4c07d3e10d18836683095782eee4856d4f3f5eed6e706`. Accepted commit `0d2a1023f51fcda7545ed2c4af24508c7300a1fc`.
- MPDS: this package's `MPDS (1).xlsx`, Line2; SHA256 `cdb3f73599ee8e4dd1a1d9b893e980120cf17425b8314c465c70cf574d6ed1ec`. Accepted commit `88daa2a959bda8ff1d732ad3788625112909c24a`.
- Control Plan: this package's `Control Plan Updated.xlsx`, Sheet1; SHA256 `22c5a967792f6c73484a8da76f4177ab2cf98b1656eb347f42591e2ca8cc47e2`.

The supplied filename variants `(1)` were not present in Downloads. Available file fingerprints match these pinned bytes.

For a Control Plan-only application:
1. Verify exact target site and live Item/Workstation identities.
2. Run `quality_master_versions.sync(master_kind='Control Plan')` dry-run.
3. Apply that same selected kind using `dry_run=False`, exact `expected_site`, and the approved source reference under existing Quality/System Manager authority.
4. Run/apply `manufacturing_master_authority.sync_timing` under Quality authority.
5. Recompute readiness against current frozen FG Standard/MPDS revisions, compare protected records, repeat both syncs, then commit the caller-owned transaction.

Do not use `sync_all` or the old mixed quality source sync for this phase: they include historical FG Standard source content. Future full-master deployment must explicitly apply the corrected FG Standard adapter, then MPDS-only sync, then Control Plan-only sync and approved timing. Never restore a Recovery database to deploy masters. Nothing is invoked by migration/startup/scheduler.

Control Plan scope is FG + exact Line. `control_plan_scope.json` retains all 660 exact source combinations, including unresolved source keys. No active source plan on a known source key remains a controlled hold. An FG Standard alone does not impose a source Control Plan on other Lines; native legacy QC gates remain in force. Existing active Control Plans also establish scope. Source scope is tied to the pinned manifest hash and must be regenerated when the approved source changes. MEGAMachine 1 is retained only where explicitly present in the approved existing source and actual Line 1 master; Lines 2/3/5/6 remain the production priority.

All applicable positive-sample/positive-final-frequency product tests receive the approved `ipqc-active-time-default-8h-v1` policy: Elapsed Minutes represents Active Production Time, interval480, window30; Pause/On Hold excluded. Existing numeric final-QC frequency remains a sample-count multiplier. Zero/absent applicability is not converted to a new mandatory test. Packing/storage/identification/dispatch controls keep their stages. Existing Startup/Stabilization/EOB flags are preserved independently; missing flags are not invented.

FG Standard is specification authority. Control Plan supplies tests, criticality and sampling. Prospective start freezes both source identities/hashes plus MPDS; QIs consume frozen specifications/conditions. Explicit standard N/A is separately reported; where the Control Plan still requires that test, Quality must approve a controlled applicability revision. Blank never becomes N/A or Pass. Ambiguous specifications and criticality remain blocked.

This phase imports40 newly eligible source plans and40 timing successors, reusing564 existing active plans. Historical Control Plan evidence, including the audited restoration/supersession, remains unchanged. Retry creates zero revisions. Automatic QI acceptance and stock/accounting postings are not part of master synchronization. AWS/8080 remains untouched.
