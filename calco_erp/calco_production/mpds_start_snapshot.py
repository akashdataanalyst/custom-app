"""Prospective MPDS evidence within the existing immutable IPQC start snapshot."""
from __future__ import annotations
import frappe
from calco_erp.calco_production.mpds_master import CURRENT_STATUS, MPDS_DOCTYPE, SPECIFICATION_AUTHORITY_FIELDS, get_authority_payload_hash
from calco_erp.calco_production.manufacturing_master_sync import IDENTITY_PREFIX


def prepare(fg_item, production_line):
    name = frappe.db.get_value(MPDS_DOCTYPE, {'fg_item': fg_item, 'production_line': production_line, 'status': CURRENT_STATUS}, 'name')
    if not isinstance(name, str) or not name:
        return None
    doc = frappe.get_doc(MPDS_DOCTYPE, name)
    if not str(doc.get('import_identity_key') or '').startswith(IDENTITY_PREFIX):
        return None
    return {
        'name': doc.name, 'fg_item': doc.fg_item, 'production_line': doc.production_line,
        'mpds_no': doc.mpds_no, 'revision': doc.revision,
        'source_revision_date': str(doc.get('source_revision_date') or ''),
        'effective_date': str(doc.get('effective_date') or ''),
        'source_line_identifier': doc.source_line_identifier,
        'material_status_snapshot': doc.material_status_snapshot,
        'imported_payload_hash': get_authority_payload_hash(doc),
        'specifications': [{field: row.get(field) for field in SPECIFICATION_AUTHORITY_FIELDS} for row in doc.get('specifications') or []],
    }


def from_frozen_plan(work_order, fg_item, production_line):
    from calco_erp.calco_production.in_process_quality import frozen_plan
    if not work_order.get('custom_ipqc_plan_snapshot'):
        return None
    plan = frozen_plan(work_order)
    snapshot = plan.get('mpds_snapshot') if plan else None
    if not snapshot:
        return None
    if snapshot['fg_item'] != fg_item or snapshot['production_line'] != production_line:
        frappe.throw('Frozen MPDS item/line does not match the production run. Use controlled production adjustment.')
    doc = frappe._dict(snapshot)
    doc.specifications = [frappe._dict(row) for row in snapshot['specifications']]
    if get_authority_payload_hash(doc) != snapshot['imported_payload_hash']:
        frappe.throw('Frozen MPDS fingerprint mismatch.')
    return doc
