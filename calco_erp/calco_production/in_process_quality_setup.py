from __future__ import annotations

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from calco_erp.calco_production.in_process_quality import MODEL_FIELD, SNAPSHOT, FINGERPRINT, STARTED, STAGE, AUDIT


def field(name, label, kind="Data", **kwargs):
    return {"fieldname": name, "label": label, "fieldtype": kind, **kwargs}


def ensure_setup():
    plan_fields = [
        field("custom_inspection_scope", "Inspection Scope", "Select", options="Final QC\nIn-Process QC", default="Final QC"),
        field("custom_ipqc_startup", "Startup Checkpoint", "Check"),
        field("custom_ipqc_stabilization", "Stabilization Checkpoint", "Check"),
        field("custom_ipqc_end_of_batch", "End-of-Batch Checkpoint", "Check"),
        field("custom_ipqc_period_basis", "Periodic Trigger", "Select", options="\nElapsed Minutes\nProduced Quantity", description="Elapsed Minutes uses active standard Job Card time logs. Produced Quantity uses Job Card completed quantity in Work Order stock UOM."),
        field("custom_ipqc_period_interval", "Periodic Interval", "Float"),
        field("custom_ipqc_sampling_window", "Permitted Sampling Window", "Float", default="0", description="After the due point, in active minutes or produced-quantity units matching the trigger. Zero means no additional tolerance. Frozen at first Start."),
        field("custom_ipqc_missed_escalation", "Missed Sample Requires Escalation", "Check", description="Critical mandatory parameters always require escalation, regardless of this setting."),
        field("custom_ipqc_mandatory", "Mandatory In-Process Requirement", "Check", default="1"),
        field("custom_ipqc_samples", "Samples per Checkpoint Parameter", "Int", default="1"),
    ]
    for df in plan_fields[1:]:
        df["depends_on"] = "eval:doc.custom_inspection_scope=='In-Process QC'"
    wo_fields = [field(MODEL_FIELD, "In-Process QC Model"), field(SNAPSHOT, "Frozen In-Process QC Plan", "Long Text"), field(FINGERPRINT, "QC Plan Fingerprint"), field(STARTED, "In-Process QC Available Since", "Datetime"), field("custom_ipqc_missed_events", "Missed Sample Audit Evidence", "Long Text", allow_on_submit=1)]
    qi_fields = [
        field("custom_ipqc_section", "In-Process QC Checkpoint", "Section Break", depends_on="eval:doc.custom_work_order_qc_stage=='In-Process QC'"),
        field("custom_ipqc_checkpoint", "Checkpoint", "Select", options="\nStartup\nStabilization\nPeriodic\nEnd-of-Batch"),
        field("custom_ipqc_sequence", "Checkpoint Sequence", "Int"),
        field("custom_ipqc_key", "Checkpoint Identity", search_index=1),
        field("custom_ipqc_batch", "Production Batch"),
        field(FINGERPRINT, "Frozen QC Plan Fingerprint"),
        field("custom_ipqc_specs", "Frozen Checkpoint Specifications", "Long Text"),
        field("custom_ipqc_sampled_on", "Sampled On", "Datetime"),
        field("custom_ipqc_sampled_by", "Sampled By", "Link", options="User"),
        field("custom_ipqc_shift", "Sample Shift"),
        field("custom_ipqc_shift_report", "Shift Report", "Link", options="Shift Report"),
        field("custom_ipqc_follow_up_of", "Follow-up Of", "Link", options="Quality Inspection", search_index=1),
        field(AUDIT, "Quality Recommendations and Production Actions", "Long Text", allow_on_submit=1),
    ]
    for df in wo_fields + qi_fields:
        df.update(read_only=1, no_copy=1)
    setup = {"FG Control Plan": plan_fields, "Work Order": wo_fields, "Quality Inspection": qi_fields, "Quality Inspection Reading": [field("custom_ipqc_spec_version", "Frozen Specification Version", read_only=1)]}
    for doctype, fields in setup.items():
        previous = "is_active" if doctype == "FG Control Plan" else "custom_work_order_qc_stage" if doctype == "Quality Inspection" else "custom_final_quality_inspection" if doctype == "Work Order" else "specification"
        for df in fields:
            df["insert_after"] = previous
            previous = df["fieldname"]
    create_custom_fields(setup, update=True)
    # The lifecycle setup owns this existing Select; no duplicate field or transaction DocType.
    frappe.db.set_value("Custom Field", "Quality Inspection-custom_work_order_qc_stage", "options", "\nInitial QC\nInitial QC Retest\nFinal QC\nIn-Process QC")
    for doctype in setup:
        frappe.clear_cache(doctype=doctype)
    frappe.clear_cache()


def execute():
    ensure_setup()
