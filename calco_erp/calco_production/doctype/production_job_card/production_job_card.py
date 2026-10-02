from frappe.model.document import Document

from calco_erp.calco_production.production_execution import ProductionJobCardMixin


class ProductionJobCard(ProductionJobCardMixin, Document):
    pass

