from calco_erp.calco_quality.quality_governance import install_quality_governance_permissions
from calco_erp.workspace_setup import ensure_quality_workspace_layout


def execute():
    install_quality_governance_permissions()
    ensure_quality_workspace_layout()
