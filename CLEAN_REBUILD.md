# Calco ERP Clean Rebuild

## Source Of Truth
- Active custom app target: `calco_erp`
- Workspace creation method: standard ERPNext module workspace JSON files
- Do not use Workspace fixtures for this rebuild
- Do not add business DocTypes until workspace visibility is proven

## First Proof Target
- Workspace name: `Production`
- Source file:
  - `calco_erp/calco_erp/production_control/workspace/production/production.json`

## Remaining Workspace Targets
- `Quality`
- `Purchase`
- `Dispatch`
- `Complaint CAPA`
- `Maintenance`
- `NPD`
- `Vendor Approval`
- `Customer Approval`
- `Management Review`

## Old Artifacts To Ignore Or Remove
- old `calco_erp/calco_erp/fixtures/*.json` workspace-style exports
- old `doc_events` / business validation hooks in `calco_erp/calco_erp/hooks.py`
- old business DocType payload that existed before workspace proof

## Clean Install Order
1. copy `calco_erp` into `apps/calco_erp`
2. run `pip install -e .` from the app root
3. append `calco_erp` to `sites/apps.txt` if needed
4. run `bench --site frontend install-app calco_erp`
5. run `bench --site frontend migrate`
6. run `bench --site frontend clear-cache`
7. hard refresh and confirm `Production` appears in Workspaces
