import frappe
from frappe.model.document import Document
from calco_erp.calco_quality import quality_master_versions as q
FIELDS=('revision_key','alias','alias_key','target_parameter','reason','reference','approved_by','approved_on','supersedes')
class QualityParameterMappingRevision(Document):
    def validate(self):
        if not getattr(frappe.flags,'quality_mapping_review',False):frappe.throw('Use Parameter Mapping Review; approval evidence is immutable.')
        fingerprint=q.source.fingerprint({k:str(self.get(k) or '') for k in FIELDS})
        if self.fingerprint!=fingerprint:frappe.throw('Mapping approval fingerprint mismatch.')
        old=self.get_doc_before_save()
        if old and any(old.get(k)!=self.get(k) for k in (*FIELDS,'fingerprint')):frappe.throw('Historical mapping evidence cannot be rewritten.')
    def on_trash(self):
        frappe.throw('Approved mapping history cannot be deleted.')
