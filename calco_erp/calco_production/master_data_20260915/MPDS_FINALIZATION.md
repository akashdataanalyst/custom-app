# MPDS-only finalization, 15 September 2026

Scope: MPDS only. FG Items, FG Standards and Control Plans remain frozen.
The supplied Downloads file is MPDS (1).xlsx (not MPDS (1)(1).xlsx).
It is byte-identical to this package's MPDS (1).xlsx:
SHA256 cdb3f73599ee8e4dd1a1d9b893e980120cf17425b8314c465c70cf574d6ed1ec.
Authoritative sheet: Line2. 403 grade/line source rows.

Use `calco_erp.calco_production.manufacturing_master_sync.sync`, NOT `sync_all`.
`sync()` classifies the pinned package but writes MPDS documents only; all Quality
source records are excluded from its write path. No schema migration is required.
Dry-run first. Apply requires exact expected_site, controlled Manufacturing authority,
a real approval_reference, and caller-owned transaction commit after preservation checks.
Never call sync_all for an MPDS-only rollout; it also synchronizes Quality and timing.

Identity is FG + exact Line. Same FG across different Lines is valid.
L-1/L-2/L-3/L-5/L-6 map only to Line 1/2/3/5/6 and must exist as Workstations.
Do not normalize away Item suffixes. Do not choose duplicate same-line records by
Excel order or MPDS-number magnitude. Equal revision/date/status with conflicting
MPDS numbers remains ambiguous. Contradictory primary/secondary lines remain held.

After the approved exact Item additions, the three newly eligible source rows are
2 (500C0001F / Line 5), 246 (720D3031F4 / Line 5), and
338 (730C3083F1 / Line 6). Other eligible rows reuse frozen import identities.
No historical revision is rewritten. Source filename is pinned here/by manifest;
row hash, workbook hash, revision, imported user/time and all source specifications
are retained by the native MPDS import/review/approval path.

Verify all source process cells against stored specification rows. Unmapped means
not projected into an F-PRD measurement key, not discarded from MPDS.
Retain blank synthetic air-knife/vent/magnet placeholders as unconfigured.
Active MPDS establishes Production Ready for its exact grade/line only; it does not
establish QC or IPQC readiness. Existing frozen runs use their original snapshots.

Repeat apply must create zero revisions. Compare protected transaction, Item,
Quality-master and historical MPDS payload hashes before committing. No business
transaction, stock movement, QI or test production is authorized by this sync.
AWS remains undeployed; later target-site application needs separate authorization.
