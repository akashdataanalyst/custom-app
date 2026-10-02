# BOM Default Authority v1 — prospective application policy

Normal new BOMs use **Automatic Default**. Explicit alternates use **Keep Non-Default** and require a reason. Native Manufacturing submit permissions continue to apply. Actor, timestamp, prior default and revision are frozen on submission. No Item/BOM identifiers are encoded in policy logic.

Existing submitted BOMs with blank policy retain native legacy authority and are not backfilled. Opening them does not classify them. The approved Recovery default/alternate pair and historical Work Order bindings remain unchanged. A deliberate future mapping requires its own reviewed record list; it is not part of schema synchronization.

New Drafts capture current Item default authority. Before automatic promotion, the Item row is locked and that baseline revalidated. Concurrent stale promotions are rejected for review, not silently applied in succession. Revision allocation uses locking reads of submitted **and cancelled** revisions under the same lock. Alternate submissions receive distinct revisions without changing Item/default authority. Cancellation and native default management take the same Item lock.

The narrow `manage_default_bom` override prevents native sole-BOM fallback from promoting an explicit alternate. It otherwise delegates promotion/cancellation to ERPNext. Submitted policy/revision/evidence changes are prohibited. Amendments copy alternate policy; changing it is an explicit Draft action recorded on new submission.

## Deployment and legacy script

Use targeted `planning_upgrade_20260927` metadata PLAN, fingerprint review, APPLY and retry. No broad migrate. The metadata package no longer installs the blanket Server Script.

Before enabling submissions on a site which contains the old script, inspect its exact source and event. The app fails closed while an enabled BOM Before Submit Server Script remains. Stage/test app behavior in isolation, then disable the verified equivalent script in the same controlled cutover; do not leave competing mechanisms active. Never blanket-disable unrelated scripts. Recovery has no such script. The isolated earlier test copy was disabled after exact SHA-256 verification: 4b8fc4590afd867e608693747b43ca8fbae4ff02af767094a4c173dae9c0591a.

AWS migration is documentation only in this task. No AWS access or build.

## Planning

Planning selects the explicit Item default and freezes it. An unsuitable operation-enabled route raises `Default BOM requires manufacturing-master review for this production route.` No alternate substitution or historical rebinding is permitted.

## Verification

Tests: `test_bom_default_authority.py`; updated `test_planning_upgrade.py`. The independent two-session runner/evidence is under the local reconciliation evidence directory. Test counts and actual deployment status must be taken from the final report, not inferred from this design document.
