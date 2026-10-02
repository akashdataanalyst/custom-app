from frappe.model.document import Document

from calco_erp.calco_production.production_execution import ProductionRequirementMixin


class ProductionRequirement(ProductionRequirementMixin, Document):
    pass

