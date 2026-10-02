"""MRP-only normalized expansion. No upstream module/class mutation.

Report extension retains native report permissions, prepared-report machinery and
all non-MRP reports. Stock/costing/order creation methods are never overridden.
Known fixed upstream delegates; unknown faulty upstream fails closed for review.
"""
import ast
import hashlib
import inspect
import textwrap
import io
import json
import tokenize

import frappe
from erpnext.manufacturing.report.material_requirements_planning_report import material_requirements_planning_report as native

REPORT = "Material Requirements Planning Report"
REVIEWED = {'update_rm_details': '24f198d38ea819ffb4f1be1e5c20027499373eb506f8eb303040a9b995457859', 'get_raw_materials': '938a912cc808d95f47bee20201b46b4200def54a2212d97babfccfa300d2d2e3'}


def method_fingerprint(method):
    tokens = [(t.type,t.string) for t in tokenize.generate_tokens(
        io.StringIO(textwrap.dedent(inspect.getsource(method))).readline)
        if t.type not in (tokenize.INDENT,tokenize.DEDENT,tokenize.NEWLINE,
                         tokenize.NL,tokenize.COMMENT,tokenize.ENDMARKER)]
    return hashlib.sha256(json.dumps(tokens,separators=(',',':')).encode()).hexdigest()


def compatibility():
    method = native.MaterialRequirementsPlanningReport.update_rm_details
    tree = ast.parse(textwrap.dedent(inspect.getsource(method)))
    expressions = [v for n in ast.walk(tree) if isinstance(n, ast.Dict)
                   for k,v in zip(n.keys,n.values) if isinstance(k,ast.Constant) and k.value == 'planned_qty']
    expected = ast.parse('material.qty * planned_qty', mode='eval').body
    if len(expressions)==1 and ast.dump(expressions[0])==ast.dump(expected):
        return 'upstream-fixed'
    for name, expected_hash in REVIEWED.items():
        if method_fingerprint(getattr(native.MaterialRequirementsPlanningReport,name)) != expected_hash:
            raise frappe.ValidationError('MRP upstream implementation changed; normalization compatibility review required.')
    return 'reviewed-defect'


class NormalizedMRP(native.MaterialRequirementsPlanningReport):
    def update_rm_details(self, raw_materials, delivery_date, planned_qty, bom_no, data):
        # Copy only this level. Recursion re-enters this method with original
        # child evidence; each level is normalized exactly once. qty was
        # calculated by native get_raw_materials using stock-UOM quantity.
        normalized = [frappe._dict(dict(row, stock_qty=row.qty)) for row in raw_materials]
        return super().update_rm_details(normalized, delivery_date, planned_qty, bom_no, data)


def execute(filters):
    if compatibility() == 'upstream-fixed':
        return native.execute(filters)
    obj = NormalizedMRP(frappe._dict(filters))
    data, chart = obj.generate_mrp()
    return obj.get_columns(), data, None, chart


class MRPReportMixin:
    def execute_module(self, filters):
        if self.name == REPORT:
            return execute(filters)
        return super().execute_module(filters)
