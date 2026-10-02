from hashlib import sha256
from pathlib import Path

from calco_erp import __version__


app_name = "calco_erp"
app_title = "Calco PolyTechnik Pvt Ltd ERP"
app_publisher = "Codex"
app_description = "Calco PolyTechnik Pvt Ltd manufacturing ERP on ERPNext"
app_email = "support@example.com"
app_license = "MIT"
_branding_public = Path(__file__).resolve().parent / "public"
_branding_asset_files = (
    _branding_public / "css/calco_branding.css",
    _branding_public / "js/calco_branding.js",
    _branding_public / "images/calco-logo-official.svg",
    _branding_public / "images/calco-polymer-pellets-banner.png",
)
_branding_asset_digest = sha256(
    b"\0".join(asset.read_bytes() for asset in _branding_asset_files)
).hexdigest()[:16]
_branding_asset_version = f"{__version__}-{_branding_asset_digest}"
_branding_css = f"/assets/calco_erp/css/calco_branding.css?v={_branding_asset_version}"
_branding_js = f"/assets/calco_erp/js/calco_branding.js?v={_branding_asset_version}"
_branding_logo = f"/assets/calco_erp/images/calco-logo-official.svg?v={_branding_asset_version}"

_enterprise_digest = sha256(b"\0".join((_branding_public / p).read_bytes() for p in (
    "css/calco_enterprise.css", "js/calco_enterprise.js"))).hexdigest()[:16]
_enterprise_css = f"/assets/calco_erp/css/calco_enterprise.css?v={_enterprise_digest}"
_enterprise_js = f"/assets/calco_erp/js/calco_enterprise.js?v={_enterprise_digest}"
# Enterprise assets retained above but not activated: Classic is the approved UI.
app_include_css = [_branding_css, "/assets/calco_erp/css/workspace_presentation.css"]
web_include_css = _branding_css
app_include_js = [
    _branding_js,
    "/assets/calco_erp/js/workspace_presentation.js",
    "/assets/calco_erp/js/production_consumption_entry.js",
]
web_include_js = _branding_js
app_logo_url = _branding_logo
brand_html = f"""<span class="calco-brand-inline"><img src="{_branding_logo}" alt="Calco PolyTechnik Pvt Ltd ERP"><span>Calco PolyTechnik Pvt Ltd ERP</span></span>"""

after_install = "calco_erp.workspace_setup.after_install_setup"

after_migrate = [
    "calco_erp.calco_production.fg_schedule_preview.setup",
    "calco_erp.calco_production.forecast_period.setup",
    "calco_erp.calco_production.fg_planning_authority.ensure_setup",
    "calco_erp.branding_setup.ensure_branding_setup",
    "calco_erp.workspace_setup.sync_workspace_ui",
    "calco_erp.calco_customer_approval.sales_order_journey.ensure_sales_order_journey_setup",
    "calco_erp.calco_purchase.purchase_journey.ensure_purchase_journey_setup",
    "calco_erp.calco_purchase.import_landed_cost.ensure_import_landed_cost_setup",
    "calco_erp.calco_purchase.purchase_closure.ensure_purchase_closure_setup",
    "calco_erp.calco_purchase.supplier_approval_matrix.ensure_supplier_approval_setup",
    "calco_erp.calco_purchase.master_data_governance.ensure_master_data_governance_setup",
    "calco_erp.calco_purchase.doctype.purchase_commercial_approval.purchase_commercial_approval.ensure_purchase_commercial_approval_setup",
    "calco_erp.machine_setup.ensure_machine_tracking_setup",
    "calco_erp.calco_production.material_readiness_work_order.ensure_work_order_material_readiness_setup",
    "calco_erp.calco_maintenance.machine_master_sync.sync_machine_master_after_migrate",
    "calco_erp.calco_maintenance.spare_mapping_sync.sync_machine_spare_mapping_after_migrate",
    "calco_erp.calco_maintenance.pm_schedule_sync.refresh_pm_schedule_tracking",
    "calco_erp.foundation_setup.ensure_foundation_records",
    "calco_erp.calco_quality.rm_quality_setup.ensure_rm_quality_setup",
    "calco_erp.calco_quality.rm_purchase_flow_setup.ensure_rm_purchase_flow_setup",
    "calco_erp.calco_quality.fg_quality_setup.ensure_fg_quality_setup",
    "calco_erp.calco_production.production_execution.ensure_production_execution_setup",
    "calco_erp.calco_production.work_order_lifecycle.ensure_work_order_lifecycle_setup",
    "calco_erp.calco_production.job_card_execution.ensure_job_card_execution_setup",
    "calco_erp.calco_production.stopped_execution.setup",
    "calco_erp.calco_production.execution_policy.setup",
    "calco_erp.calco_production.in_process_quality_setup.ensure_setup",
    "calco_erp.calco_production.grade_change_control.ensure_grade_change_control_setup",
    "calco_erp.calco_production.doctype.production_consumption_entry.production_consumption_entry.ensure_production_consumption_setup",
    "calco_erp.calco_production.report_setup.ensure_rm_batch_consumption_cost_report_link",
    "calco_erp.hrms_setup.ensure_hrms_integration_records",
    "calco_erp.barcode_setup.ensure_barcode_setup",
    "calco_erp.coa_setup.ensure_coa_setup",
    "calco_erp.thermal_label_setup.ensure_thermal_label_setup",
]

scheduler_events = {
    "daily": [
        "calco_erp.calco_maintenance.automation.run_daily_maintenance_automation",
    ]
}

doc_events = {
    "Material Request": {"validate": "calco_erp.planning_upgrade.procurement.protect_provenance"},
    "Production Requirement": {"validate": "calco_erp.planning_upgrade.planning.protect_snapshot"},
    "Landed Cost Voucher": {
        "validate": "calco_erp.calco_production.fg_true_up_draft.validate",
        "before_submit": "calco_erp.calco_production.fg_true_up_draft.before_submit",
        "on_trash": "calco_erp.calco_production.fg_true_up_draft.prevent_delete",
    },
    "Supplier": {
        "before_insert": "calco_erp.calco_purchase.supplier_creation_guard.validate_supplier_not_created_from_request_form",
    },
    "Item": {
        "validate": [
            "calco_erp.barcode_setup.ensure_item_barcode_on_validate",
            "calco_erp.calco_purchase.existing_rm_readiness.validate_existing_rm_technical_fields",
            "calco_erp.calco_production.grade_change_control.validate_item_grade_classification",
        ],
    },
    "Batch": {
        "validate": "calco_erp.barcode_setup.ensure_batch_barcode_on_validate",
    },
    "Purchase Receipt": {
        "validate": [
            "calco_erp.rm_batch_setup.ensure_purchase_receipt_batch_numbers",
            "calco_erp.calco_quality.rm_warehouse_flow.apply_purchase_receipt_quarantine",
            "calco_erp.calco_quality.purchase_receipt_qc.validate_rejected_qty_purchase_return",
            "calco_erp.calco_purchase.import_shipment.validate_purchase_receipt_import_shipment_gate",
            "calco_erp.calco_purchase.import_landed_cost.sync_import_purchase_receipt",
        ],
        "before_submit": [
            "calco_erp.calco_quality.purchase_receipt_qc.validate_supplier_documents_and_rm_storage",
            "calco_erp.calco_purchase.import_landed_cost.validate_import_purchase_receipt_submission",
        ],
        "on_submit": [
            "calco_erp.thermal_label_setup.handle_purchase_receipt_submit",
        ],
    },
    "Purchase Invoice": {
        "validate": "calco_erp.utils.dependencies.validate_purchase_invoice_chain",
    },
    "Request for Quotation": {
        "validate": "calco_erp.calco_purchase.supplier_approval_matrix.validate_request_for_quotation_supplier_matrix",
    },
    "Supplier Quotation": {
        "validate": [
            "calco_erp.calco_purchase.supplier_approval_matrix.validate_supplier_quotation_supplier_matrix",
            "calco_erp.calco_purchase.supplier_last_purchase_price.apply_supplier_quotation_lpp",
        ],
        "before_submit": "calco_erp.calco_purchase.supplier_last_purchase_price.snapshot_supplier_quotation_lpp",
        "on_submit": "calco_erp.calco_purchase.commercial_approval.ensure_supplier_quotation_commercial_approvals",
    },
    "Purchase Order": {
        "validate": "calco_erp.calco_purchase.commercial_approval.validate_purchase_order_commercial_approval_gate",
        "on_submit": "calco_erp.calco_purchase.purchase_order_supplier_dispatch.schedule_automatic_supplier_dispatch",
    },
    "Production Plan": {
        "validate": "calco_erp.calco_production.production_plan_control.validate_production_plan",
    },
    "Sales Forecast": {
        "validate": "calco_erp.calco_production.forecast_period.validate_periods",
        "before_update_after_submit": "calco_erp.calco_production.forecast_period.validate_periods",
        "before_submit": "calco_erp.calco_production.sales_forecast_import.validate_unique_submitted_forecast_month",
    },
    "BOM": {"validate": "calco_erp.calco_production.integrated_packing.validate_new_bom",
        "before_validate": "calco_erp.planning_upgrade.formulation.validate",
        "before_update_after_submit": "calco_erp.planning_upgrade.formulation.guard_after_submit"},
    "Work Order": {
        "on_trash": ["calco_erp.calco_production.fg_planning_authority.protect_delete", "calco_erp.calco_production.missed_in_process_quality.protect_history"],
        "before_insert": ["calco_erp.calco_production.integrated_packing.validate_new_work_order", "calco_erp.calco_production.fg_planning_authority.initialize", "calco_erp.calco_production.in_process_quality.initialize_work_order"],
        "before_update_after_submit": ["calco_erp.calco_production.integrated_packing.protect", "calco_erp.calco_production.fg_planning_authority.validate"],
        "on_update_after_submit": "calco_erp.calco_production.in_process_quality.protect_work_order",
        "validate": [
            "calco_erp.calco_production.integrated_packing.protect",
            "calco_erp.calco_production.in_process_quality.protect_work_order",
            "calco_erp.calco_production.fg_planning_authority.validate",
            "calco_erp.calco_production.work_order_control.validate_work_order_plan_context",
            "calco_erp.machine_setup.validate_work_order_machine",
            "calco_erp.calco_production.production_execution.sync_work_order_execution_context",
            "calco_erp.calco_production.work_order_lifecycle.validate_work_order_lifecycle",
            "calco_erp.calco_production.production_readiness.clear_stale_partial_approval",
        ],
        "after_insert": "calco_erp.calco_production.work_order_control.initialize_work_order_from_production_plan",
        "on_submit": "calco_erp.calco_production.fg_planning_authority.after_submit",
        "before_submit": [
            "calco_erp.calco_production.integrated_packing.validate_new_work_order",
            "calco_erp.calco_production.fg_planning_authority.before_submit",
            "calco_erp.machine_setup.validate_work_order_machine_required",
            "calco_erp.calco_production.production_execution.sync_work_order_execution_context",
        ],
        "before_cancel": ["calco_erp.calco_production.material_reservation_submission.prevent_work_order_with_active_reservation_cancel", "calco_erp.calco_production.fg_planning_authority.before_cancel"],
    },
    "Stock Reservation Entry": {
        "before_submit": "calco_erp.calco_production.material_reservation_submission.protect_calco_sre_set_operation",
        "before_cancel": "calco_erp.calco_production.material_reservation_submission.protect_calco_sre_set_operation",
    },
    "Job Card": {
        "on_trash": ["calco_erp.calco_production.stopped_execution.prevent_closure_deletion", "calco_erp.calco_production.execution_policy.prevent_delete", "calco_erp.calco_production.physical_completion.prevent_delete"],
        "before_save": ["calco_erp.calco_production.stopped_execution.protect_audit", "calco_erp.calco_production.execution_policy.protect", "calco_erp.calco_production.physical_completion.protect"],
        "before_update_after_submit": ["calco_erp.calco_production.shift_output_snapshot.protect_activation", "calco_erp.calco_production.stopped_execution.protect_audit", "calco_erp.calco_production.execution_policy.protect", "calco_erp.calco_production.physical_completion.protect"],
        "onload": "calco_erp.calco_production.job_card_execution.sync_job_card_grade_change_display",
        "validate": [
            "calco_erp.calco_production.job_card_execution.sync_job_card_execution_context",
            "calco_erp.calco_production.in_process_quality.validate_job_card_execution",
            "calco_erp.calco_production.grade_change_control.validate_rm_loading_gate",
        ],
        "before_submit": "calco_erp.calco_production.compounding_execution.validate_compounding_completion_extension",
    },
    "Stock Entry": {
        "before_validate": [
            "calco_erp.calco_production.stock_entry_warehouse_defaults.apply_work_order_transfer_warehouses",
            "calco_erp.calco_production.production_readiness.validate_material_transfer_readiness",
            "calco_erp.calco_production.material_reservation_transfer.validate_material_transfer_reservation_gate",
        ],
        "validate": [
            "calco_erp.machine_setup.validate_stock_entry_machine",
            "calco_erp.calco_production.manufacture_entry.validate_controlled_manufacture",
            "calco_erp.calco_production.wip_consumption.validate_wip_consumption",
            "calco_erp.calco_production.wip_return.validate_unused_wip_return",
        ],
        "before_save": "calco_erp.fg_batch_setup.normalize_rm_batch_consumption_rows",
        "before_submit": [
            "calco_erp.calco_production.manufacture_entry.validate_controlled_manufacture_on_submit",
            "calco_erp.calco_production.wip_consumption.validate_wip_consumption_on_submit",
            "calco_erp.calco_production.wip_return.validate_unused_wip_return_on_submit",
            "calco_erp.calco_production.material_reservation_transfer.validate_reserved_material_transfer",
            "calco_erp.calco_production.work_order_lifecycle.validate_stock_entry_lifecycle",
            "calco_erp.fg_batch_setup.prepare_manufacture_stock_entry",
        ],
        "on_submit": [
            "calco_erp.calco_production.material_reservation_transfer.sync_reservation_after_material_transfer",
            "calco_erp.calco_production.wip_return.refresh_work_order_return_reconciliation",
            "calco_erp.thermal_label_setup.handle_stock_entry_submit",
            "calco_erp.calco_production.work_order_lifecycle.sync_stock_entry_lifecycle_on_submit",
        ],
        "before_cancel": "calco_erp.calco_production.partial_fg_lots.before_stock_cancel",
        "on_cancel": [
            "calco_erp.calco_production.partial_fg_lots.after_stock_cancel",
            "calco_erp.calco_quality.doctype.final_qc_release.final_qc_release.prevent_direct_release_stock_entry_cancel",
            "calco_erp.calco_production.material_reservation_transfer.sync_reservation_after_material_transfer",
            "calco_erp.calco_production.wip_return.refresh_work_order_return_reconciliation",
        ],
    },
    "Final QC Release": {"before_cancel": "calco_erp.calco_dispatch.partial_lot_dispatch.before_release_cancel"},
    "Delivery Note": {
        "before_submit": "calco_erp.utils.dependencies.validate_delivery_note_chain",
        "on_submit": "calco_erp.coa_setup.attach_coa_pdf_to_delivery_note",
    },
    "Maintenance Ticket": {
        "on_update": "calco_erp.calco_maintenance.automation.sync_pm_plan_completion_from_ticket",
    },
    "Quality Inspection": {
        "before_cancel": "calco_erp.calco_production.in_process_quality.protect_qi_history",
        "on_trash": "calco_erp.calco_production.in_process_quality.protect_qi_history",
        "before_update_after_submit": "calco_erp.calco_production.in_process_quality.validate_qi_identity",
        "before_validate": [
            "calco_erp.calco_production.in_process_quality.validate_qi_identity",
            "calco_erp.calco_quality.rm_quality_setup.apply_rm_testing_context",
            "calco_erp.calco_quality.fg_quality_setup.apply_fg_control_plan",
            "calco_erp.calco_quality.automatic_rm_release.prepare_incoming_qi_release_context",
        ],
        "validate": [
            "calco_erp.calco_quality.rm_quality_setup.apply_rm_testing_context",
            "calco_erp.calco_quality.fg_quality_setup.apply_fg_control_plan",
            "calco_erp.calco_production.work_order_lifecycle.validate_quality_inspection_lifecycle",
            "calco_erp.calco_quality.purchase_receipt_qc.initialize_auto_created_incoming_quality_inspection_status",
        ],
        "before_submit": [
            "calco_erp.calco_quality.fg_quality_setup.validate_fg_submission",
            "calco_erp.calco_production.in_process_quality.before_submit_qi",
            "calco_erp.calco_quality.automatic_rm_release.validate_incoming_qi_accepted_quantity",
        ],
        "on_update": "calco_erp.calco_quality.purchase_receipt_qc.sync_purchase_receipt_qc_status_from_quality_inspection",
        "on_update_after_submit": "calco_erp.calco_quality.purchase_receipt_qc.sync_purchase_receipt_qc_status_from_quality_inspection",
        "on_submit": [
            "calco_erp.calco_quality.purchase_receipt_qc.sync_purchase_receipt_qc_status_from_quality_inspection",
            "calco_erp.calco_quality.automatic_rm_release.process_accepted_quality_inspection",
            "calco_erp.calco_production.work_order_lifecycle.sync_quality_inspection_lifecycle_on_submit",
        ],
        "on_cancel": "calco_erp.calco_quality.purchase_receipt_qc.sync_purchase_receipt_qc_status_from_quality_inspection",
    },
    "Quality Goal": {
        "validate": "calco_erp.calco_quality.quality_governance.validate_quality_goal_phase",
    },
    "RM Deviation Approval": {
        "on_submit": "calco_erp.calco_quality.purchase_receipt_qc.sync_purchase_receipt_qc_status_from_rm_deviation",
        "on_cancel": "calco_erp.calco_quality.purchase_receipt_qc.sync_purchase_receipt_qc_status_from_rm_deviation",
    },
}

doctype_list_js = {"Material Request": "public/js/material_request_purchase_list.js"}

doctype_js = {
    "BOM": "public/js/bom_default_authority.js",
    "Item": "public/js/item_existing_rm_technical.js",
    "Material Request": "public/js/purchase_closure.js",
    "Purchase Order": [
        "public/js/purchase_closure.js",
        "public/js/purchase_order_supplier_dispatch.js",
    ],
    "Sales Order": "public/js/sales_order_journey_tracker.js",
    "Sales Forecast": "public/js/sales_forecast.js",
    "Request for Quotation": "public/js/request_for_quotation.js",
    "Purchase Receipt": [
        "public/js/barcode_transaction.js",
        "public/js/purchase_receipt_import_landed_cost.js",
    ],
    "Stock Entry": "public/js/barcode_transaction.js",
    "Delivery Note": "public/js/barcode_transaction.js",
    "Quality Inspection": "public/js/quality_inspection.js",
    "RM QC Decision": "public/js/rm_qc_decision.js",
    "RM Release Note": "public/js/rm_release_note.js",
    "RM Deviation Approval": "public/js/rm_deviation_approval.js",
    "New RM Request": "public/js/master_data_governance_journey.js",
    "New Supplier Request": "public/js/master_data_governance_journey.js",
    "Purchase Commercial Approval": "public/js/purchase_commercial_approval.js",
    "Production Requirement": "public/js/production_execution_journey.js",
    "Production Job Card": "public/js/production_execution_journey.js",
    "Job Card": [
        "public/js/parallel_production.js",
        "public/js/stopped_execution.js",
        "public/js/execution_policy.js",
        "public/js/partial_fg_lots.js",
        "public/js/physical_completion.js",
        "public/js/production_batch_closure.js",
        "public/js/fg_true_up.js",
        "public/js/production_execution_journey.js",
        "public/js/grade_change_control.js",
    ],
    "Work Order": [
        "public/js/fg_planning_work_order.js",
        "public/js/production_execution_journey.js",
        "public/js/grade_change_control.js",
        "public/js/production_readiness.js",
        "public/js/material_reservation.js",
        "public/js/wip_consumption.js",
        "public/js/wip_return.js",
    ],
    "Production Consumption Entry": "public/js/production_consumption_entry.js",
    "Grade Change Clearance": "public/js/grade_change_control.js",
}

override_whitelisted_methods = {
    "erpnext.stock.doctype.material_request.material_request.make_request_for_quotation": "calco_erp.calco_purchase.supplier_approval_matrix.make_request_for_quotation_with_supplier_matrix",
    "erpnext.stock.doctype.material_request.material_request.update_status": "calco_erp.calco_purchase.purchase_closure.update_material_request_status",
    "erpnext.buying.doctype.purchase_order.purchase_order.update_status": "calco_erp.calco_purchase.purchase_closure.update_purchase_order_status",
    "erpnext.manufacturing.doctype.work_order.work_order.make_stock_entry": "calco_erp.calco_production.production_readiness.make_stock_entry_with_readiness",
    "erpnext.manufacturing.doctype.work_order.work_order.stop_unstop": "calco_erp.calco_production.fg_planning_authority.stop_unstop",
    "erpnext.manufacturing.doctype.work_order.work_order.close_work_order": "calco_erp.calco_production.fg_planning_authority.close_work_order",
}

extend_doctype_class = {
    "BOM": ["calco_erp.planning_upgrade.bom.BOMAuthorityMixin"],
    "Report": ["calco_erp.planning_upgrade.mrp.MRPReportMixin"],
    "Master Production Schedule": ["calco_erp.planning_upgrade.mps.MPSSubmissionMixin"],
    "Serial and Batch Bundle": ["calco_erp.calco_quality.rm_release_bundle_authority.ReleaseBundleMixin"],
    "Work Order": ["calco_erp.calco_production.stopped_execution.StopInvariantWorkOrderMixin", "calco_erp.calco_production.partial_fg_lots.PartialFGWorkOrderMixin"],
    "Sales Forecast": ["calco_erp.calco_production.forecast_period.ForecastPeriodMixin"],
    "Material Request": [
        "calco_erp.calco_purchase.purchase_closure.MaterialRequestClosureAuditMixin",
    ],
    "Purchase Order": [
        "calco_erp.calco_purchase.purchase_closure.PurchaseOrderClosureAuditMixin",
    ],
    "Job Card": [
        "calco_erp.calco_production.production_readiness.ProductionReadinessJobCardMixin",
        "calco_erp.calco_production.stopped_execution.StoppedExecutionJobCardMixin",
        "calco_erp.calco_production.execution_policy.ExecutionPolicyJobCardMixin",
        "calco_erp.calco_production.physical_completion.PhysicalCompletionJobCardMixin",
    ],
    "Stock Entry": [
        "calco_erp.calco_production.wip_consumption.ControlledWIPConsumptionStockEntryMixin",
        "calco_erp.calco_production.partial_fg_lots.PartialFGStockEntryMixin",
    ],
}

fixtures = [
    {
        "dt": "Custom Field",
        "filters": [
            ["dt", "=", "RM Planning Parameter"],
            ["fieldname", "=", "manual_maximum_inventory_level"],
        ],
    },
    {
        "dt": "Custom Field",
        "filters": [
            ["dt", "=", "Production Plan"],
            ["fieldname", "in", [
                "custom_calco_release_to_production",
                "custom_release_key",
                "custom_release_requirement_key",
                "custom_release_authority",
                "custom_release_item_code",
                "custom_release_qty",
                "custom_release_priority",
                "custom_release_required_date",
                "custom_release_period_start",
                "custom_release_period_end",
                "custom_release_source_details",
                "custom_released_by",
                "custom_released_on",
                "custom_release_reviewed_by",
                "custom_release_reviewed_on",
                "custom_release_review_remarks",
            ]],
        ],
    },
    {
        "dt": "Custom Field",
        "filters": [
            ["dt", "=", "Production Requirement"],
            ["fieldname", "in", [
                "custom_calco_release_to_production",
                "custom_release_key",
                "custom_release_requirement_key",
                "custom_release_company",
                "custom_release_priority",
                "custom_release_source_details",
                "custom_released_by",
                "custom_released_on",
                "custom_release_reviewed_by",
                "custom_release_reviewed_on",
                "custom_release_review_remarks",
            ]],
        ],
    },
    {
        "dt": "Custom Field",
        "filters": [
            ["dt", "=", "Production Plan Item"],
            ["fieldname", "in", [
                "custom_planning_source_type",
                "custom_planning_source_doctype",
                "custom_planning_source_name",
                "custom_planning_source_row",
                "custom_required_delivery_date",
                "custom_planning_review_status",
                "custom_planning_reviewed_qty",
                "custom_planning_reviewed_by",
                "custom_planning_reviewed_on",
                "custom_planning_review_remarks",
            ]],
        ],
    },
    {
        "dt": "Custom Field",
        "filters": [
            ["dt", "in", ["Sales Order Item", "Sales Forecast Item", "Work Order"]],
            ["fieldname", "in", [
                "custom_planning_review_status",
                "custom_planning_reviewed_qty",
                "custom_planning_reviewed_by",
                "custom_planning_reviewed_on",
                "custom_planning_review_remarks",
            ]],
        ],
    },
]

# Recovery-scoped schema setup; no automatic costing activation.
after_migrate = list(after_migrate) + ["calco_erp.calco_production.fg_true_up_draft.setup"]

# Gate 1: additive evidence protection only; no receipt routing or activation.
after_migrate = list(after_migrate) + ["calco_erp.calco_production.receipt_policy_infrastructure.setup"]
doc_events.setdefault("Partial FG Lot", {})["validate"] = "calco_erp.calco_production.receipt_policy_infrastructure.protect_lot"
doc_events["Stock Entry"]["validate"] = list(doc_events["Stock Entry"]["validate"]) + ["calco_erp.calco_production.receipt_policy_infrastructure.protect_stock"]

# Gate 2 hooks apply only to explicitly marked provisional stock; flag stays OFF.
doc_events["Stock Entry"]["before_insert"] = "calco_erp.calco_production.provisional_receipts.bind_manufacture"
doc_events["Stock Entry"]["validate"] += ["calco_erp.calco_production.provisional_receipts.validate_issue"]
doc_events["Stock Entry"]["before_cancel"] = [doc_events["Stock Entry"]["before_cancel"], "calco_erp.calco_production.provisional_receipts.before_cancel"]
doc_events["Stock Entry"]["on_cancel"].insert(0, "calco_erp.calco_production.provisional_receipts.after_cancel")

# Gate 3 physical-only run closure; provisional activation remains disabled.
doctype_js['Final QC Release'] = 'public/js/production_batch_closure.js'

_gate3_job_validate = doc_events['Job Card'].get('validate', [])
if isinstance(_gate3_job_validate, str): _gate3_job_validate = [_gate3_job_validate]
doc_events['Job Card']['validate'] = list(_gate3_job_validate) + ['calco_erp.calco_production.production_batch_closure.protect_confirmed_run']

# Gate 4 restricted settlement preparation; all automatic activation stays OFF.
doctype_js['Production Batch Closure'] = 'public/js/production_settlement.js'
doctype_js['Production Settlement Preparation'] = 'public/js/production_settlement.js'

# FG handover reserves physical output without changing costing/stock engines.
doc_events.setdefault('Partial FG Lot', {})['before_insert'] = 'calco_erp.calco_production.fg_handover.protect_new_lot'

for _handover_dt in ('Stock Entry','Partial FG Lot'):
    for _handover_event in ('before_cancel','on_cancel'):
        _prior_handover_hooks=doc_events.setdefault(_handover_dt,{}).get(_handover_event,[])
        if isinstance(_prior_handover_hooks,str):_prior_handover_hooks=[_prior_handover_hooks]
        doc_events[_handover_dt][_handover_event]=list(_prior_handover_hooks)+['calco_erp.calco_production.fg_handover.referenced_reversal']

# Presentation-only synchronization follows existing workspace setup.
after_migrate.append("calco_erp.workspace_presentation.sync")

# Final PCE quantity authority; legacy PCE and native valuation remain unchanged.
doc_events['Stock Entry']['validate'].append('calco_erp.calco_production.final_consumption.validate_stock')
doc_events['Stock Entry']['before_cancel'].append('calco_erp.calco_production.final_consumption.before_cancel_stock')

# Prevent unrestricted batch edits; normal automatic start allocation stays authoritative.
for _batch_dt in ('Work Order', 'Job Card'):
    _batch_events = doc_events[_batch_dt].get('validate', [])
    if isinstance(_batch_events, str): _batch_events = [_batch_events]
    doc_events[_batch_dt]['validate'] = list(_batch_events) + ['calco_erp.calco_production.parallel_production.protect_batch']

# Canonical Classic Desk entry; portal/public routes retain native resolution.
website_path_resolver = ['calco_erp.desk_routing.resolve_path']

# Prospective Purchase assignments are OFF unless the site-bound policy is enabled.
for _purchase_dt in ("Material Request", "Purchase Commercial Approval", "Purchase Order", "Quality Inspection", "Purchase Receipt"):
    for _purchase_event in ("on_update", "on_submit", "on_cancel", "on_update_after_submit", "on_change"):
        _purchase_prior = doc_events.setdefault(_purchase_dt, {}).get(_purchase_event, [])
        if isinstance(_purchase_prior, str): _purchase_prior = [_purchase_prior]
        doc_events[_purchase_dt][_purchase_event] = list(_purchase_prior) + ["calco_erp.planning_upgrade.purchase_assignments.sync"]
for _purchase_dt in ("Purchase Order", "Purchase Receipt", "Purchase Invoice", "Supplier Quotation"):
    for _purchase_event in ("on_submit", "on_cancel", "on_update_after_submit"):
        _purchase_prior = doc_events.setdefault(_purchase_dt, {}).get(_purchase_event, [])
        if isinstance(_purchase_prior, str): _purchase_prior = [_purchase_prior]
        doc_events[_purchase_dt][_purchase_event] = list(_purchase_prior) + ["calco_erp.planning_upgrade.purchase_assignments.on_related_change"]

# Restricted Management presentation only; never grant underlying data access.
has_permission = dict(globals().get("has_permission", {}))
has_permission["Dashboard"] = "calco_erp.holistic_ui.permissions.dashboard_permission"
permission_query_conditions = dict(globals().get("permission_query_conditions", {}))
permission_query_conditions["Dashboard"] = "calco_erp.holistic_ui.permissions.dashboard_query"

# Approved final held bundle; no historical replay or broad permission sync.
doc_events["Item"]["before_save"] = "calco_erp.final_four.item_revision.before_save"
doc_events["Job Offer"] = {"validate": "calco_erp.final_four.hr.job_offer_validate"}
doc_events.setdefault("Leave Application", {})["before_insert"] = "calco_erp.final_four.hr.leave_before_insert"
doc_events["Purchase Receipt"]["on_submit"].append("calco_erp.final_four.return_communication.on_submit")

doc_events["Calco Return Communication"] = {"validate": "calco_erp.final_four.return_communication.protect_event", "on_trash": "calco_erp.final_four.return_communication.protect_event"}

# Observe native/service completion; no scheduler or second assignment lifecycle.
doc_events.setdefault("ToDo", {})["validate"] = "calco_erp.task_timeliness.completion.protect_and_stamp"
extend_doctype_class["Task"] = ["calco_erp.task_timeliness.completion.TaskCompletionMixin"]

# Opt-in existing Task Timeliness KRA only; no adoption or score scheduler.
extend_doctype_class["Appraisal"] = ["calco_erp.task_timeliness.appraisal.AppraisalTimelinessMixin"]
doctype_js["Appraisal"] = "public/js/task_timeliness_appraisal.js"

doc_events["Appraisal KRA"] = {event: "calco_erp.task_timeliness.appraisal.protect_kra_child" for event in ("validate", "before_update_after_submit", "before_submit", "before_cancel", "on_trash")}
