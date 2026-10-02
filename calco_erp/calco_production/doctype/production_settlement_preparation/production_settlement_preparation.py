from calco_erp.calco_production.receipt_policy_infrastructure import InfrastructureRecord

class ProductionSettlementPreparation(InfrastructureRecord):
    def validate(self):
        new=self.is_new()
        super().validate()
        if new:
            from calco_erp.calco_production.production_settlement import validate_record
            validate_record(self)
