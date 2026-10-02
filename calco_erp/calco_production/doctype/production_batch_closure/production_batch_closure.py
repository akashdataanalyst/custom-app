from calco_erp.calco_production.receipt_policy_infrastructure import InfrastructureRecord

class ProductionBatchClosure(InfrastructureRecord):
    def validate(self):
        new = self.is_new()
        super().validate()
        if new:
            from calco_erp.calco_production.production_batch_closure import validate_record
            validate_record(self)
