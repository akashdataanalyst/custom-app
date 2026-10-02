"""Explicit prospective BOM authority. No item/name exceptions or historical backfill.

The native default fallback would promote the sole alternate; overriding only
manage_default_bom prevents that fallback while retaining native promotion and
cancellation. Item row locks serialize submission, cancellation and promotion.
"""
import json
from contextvars import ContextVar
import frappe
from frappe.utils import now, today

POLICY = 'custom_default_authority_policy'
REASON = 'custom_default_authority_reason'
BASIS = 'custom_default_authority_basis'
EVIDENCE = 'custom_default_authority_evidence'
AUTO = 'Automatic Default'
KEEP = 'Keep Non-Default'
_adopting = ContextVar('bom_policy_adoption', default=None)


def lock_authority(item):
    rows = frappe.db.sql('select default_bom from `tabItem` where name=%s for update', (item,))
    if not rows:
        frappe.throw('BOM Item does not exist.')
    return rows[0][0] or ''


def policy(doc):
    value = doc.get(POLICY) or ''
    if value not in ('', AUTO, KEEP):
        frappe.throw('Invalid Default Authority Policy.')
    return value


class BOMAuthorityMixin:
    def save(self, *args, **kwargs):
        # Document._save locks the BOM in check_if_latest. Acquire Item first,
        # otherwise concurrent submissions can hold separate BOM locks while
        # native set_default needs the sibling row (BOM -> Item deadlock).
        if self.docstatus in (1, 2):
            lock_authority(self.item)
        return super().save(*args, **kwargs)

    def before_insert(self):
        # Also cover native insert().submit() within one transaction.
        current = lock_authority(self.item)
        parent = getattr(super(), 'before_insert', None)
        if parent:
            parent()
        if self.get('amended_from'):
            old = frappe.get_doc('BOM', self.amended_from)
            if not self.get(POLICY):
                self.set(POLICY, old.get(POLICY) or AUTO)
        elif not self.get(POLICY):
            self.set(POLICY, AUTO)
        self.set(BASIS, json.dumps({'item': self.item,
            'default': current,
            'captured_by': frappe.session.user, 'captured_on': now()}, sort_keys=True))
        self.set(EVIDENCE, None)

    def validate(self):
        value = policy(self)
        old = self.get_doc_before_save()
        if old:
            for field in (BASIS, EVIDENCE):
                if self.get(field) != old.get(field):
                    frappe.throw('BOM authority evidence cannot be edited.')
            if old.docstatus == 1:
                for field in (POLICY, REASON, 'custom_revision_no', 'custom_revision_date'):
                    if self.get(field) != old.get(field):
                        frappe.throw('Submitted BOM authority requires a controlled new revision.')
        if value == KEEP:
            if not (self.get(REASON) or '').strip():
                frappe.throw('Default Authority Reason is required for Keep Non-Default.')
            self.is_default = 0
        super().validate()

    def before_submit(self):
        parent = getattr(super(), 'before_submit', None)
        if parent:
            parent()
        # Fail closed if the legacy script is still enabled. Migration is a
        # separate reviewed targeted action; never silently disable live code.
        if frappe.db.exists('Server Script', {'reference_doctype': 'BOM',
                'doctype_event': 'Before Submit', 'disabled': 0}):
            frappe.throw('BOM submission script migration requires review before app authority can run.')
        current = lock_authority(self.item)
        value = policy(self)
        if not value:
            frappe.throw('Select Default Authority Policy explicitly for this existing Draft BOM.')
        basis = json.loads(self.get(BASIS) or '{}')
        if value == AUTO and (basis.get('item') != self.item or basis.get('default') != current):
            frappe.throw('Default BOM authority changed since this Draft was prepared. Review current authority and prepare a fresh Draft before promotion.')
        # Locking reads see the latest committed revision even under REPEATABLE
        # READ. Include cancelled revisions so their identity is never reused.
        rows = frappe.db.sql('''select custom_revision_no from `tabBOM`
            where item=%s and name!=%s and docstatus in (1,2) for update''', (self.item, self.name))
        self.custom_revision_no = max([int(r[0]) for r in rows if r[0] is not None], default=-1)+1
        self.custom_revision_date = today()
        self.is_active = 1
        self.is_default = int(value == AUTO)
        self.set(EVIDENCE, json.dumps({'version': 'bom-default-authority-v1',
            'policy': value, 'reason': self.get(REASON), 'previous_default': current,
            'revision': self.custom_revision_no, 'actor': frappe.session.user,
            'timestamp': now()}, sort_keys=True))

    def before_update_after_submit(self):
        old = self.get_doc_before_save()
        if old:
            for field in (POLICY, REASON, BASIS, EVIDENCE, "custom_revision_no", "custom_revision_date"):
                if self.get(field) != old.get(field) and not (_adopting.get() == self.name and field in (POLICY, REASON, EVIDENCE)):
                    frappe.throw("Submitted BOM authority requires a controlled new revision.")
        parent = getattr(super(), "before_update_after_submit", None)
        if parent:
            parent()

    def before_cancel(self):
        lock_authority(self.item)
        parent = getattr(super(), 'before_cancel', None)
        if parent:
            parent()

    def manage_default_bom(self):
        current = lock_authority(self.item)
        if policy(self) == KEEP:
            if current == self.name:
                frappe.throw('Alternate BOM unexpectedly holds Item default authority; controlled review required.')
            if self.is_default:
                frappe.throw('Keep Non-Default BOM cannot become the default.')
            return
        # Historical blanks retain native behavior; no classification/backfill.
        super().manage_default_bom()


def adopt_existing_alternate(name, expected_default, reason):
    """Explicit reviewed one-record mapping only; never called by synchronization.

    Not whitelisted. Administrator invokes a reviewed deployment mapping. Native
    save/Version supplies audit history; no formulation/default/stock changes.
    """
    if frappe.session.user != 'Administrator':
        frappe.throw('Administrator required for reviewed legacy authority mapping.', frappe.PermissionError)
    if not (reason or '').strip():
        frappe.throw('Reviewed mapping reason is required.')
    doc = frappe.get_doc('BOM', name)
    current = lock_authority(doc.item)
    if current != expected_default or current == name or doc.docstatus != 1 or doc.is_default or not doc.is_active:
        frappe.throw('Legacy alternate authority changed; mapping aborted.')
    if doc.get(POLICY) == KEEP:
        return {'name': name, 'changed': False}
    if doc.get(POLICY):
        frappe.throw('Existing explicit BOM policy cannot be remapped.')
    before = doc.as_dict()
    token = _adopting.set(name)
    try:
        doc.set(POLICY, KEEP)
        doc.set(REASON, reason)
        doc.set(EVIDENCE, json.dumps({'version':'bom-default-authority-v1',
            'action':'Reviewed historical alternate designation', 'policy':KEEP,
            'reason':reason, 'previous_default':current, 'actor':frappe.session.user,
            'timestamp':now()},sort_keys=True))
        doc.save()
        doc.reload()
        allowed = {POLICY, REASON, EVIDENCE, 'modified', 'modified_by'}
        def protected(value):
            if isinstance(value, dict):
                return {k:protected(v) for k,v in value.items() if k not in allowed}
            if isinstance(value, list):
                return [protected(v) for v in value]
            return value
        if protected(before) != protected(doc.as_dict()) or lock_authority(doc.item) != current:
            frappe.throw('Unexpected historical BOM mutation; mapping must roll back.')
        return {'name':name, 'changed':True}
    finally:
        _adopting.reset(token)
