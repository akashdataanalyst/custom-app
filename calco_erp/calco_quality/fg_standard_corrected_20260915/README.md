FG-only opt-in sync: calco_erp.calco_quality.fg_standard_candidate_sync.sync(path, expected_sha256, dry_run=True). Apply requires explicit site and approval; caller controls commit/rollback. No MPDS, Control Plan, timing or transaction writes. Source properties are header-driven. Original versions remain immutable; held rows retain prior authority. No automatic sync hook.

Targeted Item creation: `calco_erp.calco_quality.fg_item_candidate_sync.run(dry_run=True)`.
Scope is the 345 corrected-source rows pinned in missing_item_scope.json. Apply requires
an exact site and explicit approval reference, Item Create authority and a caller-owned
transaction. The current reference Item 720C0002 must still match the verified FG profile.
No migration/startup hook is installed. Run Item sync, then the unchanged FG Standard
sync from commit 619f85abae9110f3645499e8613632a89ae8740d. Existing Items are never rewritten.
Exact/normalized identity, recorded external Item references, descriptions and peer
collisions are held; no description-based merge. PreLaunch is retained in source evidence,
and all newly created Items retain Draft release status. Manufacturing readiness remains
separate. Native receipt routing enforces company FG Quarantine regardless of Item defaults.
No BOM, MPDS, Control Plan, Batch, stock or accounting document is created by Item sync.
The package is application/source based and does not require copying a Recovery database.
AWS execution requires separate authorization and target preflight.
