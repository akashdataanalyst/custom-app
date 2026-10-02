/* Workspace-only presentation. Native routes, permissions and click handlers are retained. */
(() => {
  const scopes = new Set(['production','purchase','quality']);
  const descriptions = {
    'Work Order':'Plan and track production', 'Production Job Card':'Compounding / Extrusion execution',
    'Grade Change Clearance':'Controlled line clearance', 'Premix Run':'Controlled Premix execution',
    'FG Delivery Note':'Production handover to QA / Stores', 'Final RM Consumption':'Final confirmations and historical entries', 'Historical RM Consumption':'Legacy entries and posting history',
    'FG Planning Dashboard':'Forward-looking three-month demand', 'Production Dashboard':'Daily output and execution status',
    'Plant Production Dashboard':'Utilization and management reporting', 'RM Planning Dashboard':'Raw material requirements',
    'New RM Request':'Request and track material approval', 'New Supplier Request':'Request and track supplier approval',
    'Master Data Governance Center':'Controlled master approvals', 'Purchase Performance Dashboard':'Procurement performance',
    'Purchase Journey / Material Traceability':'MR to receipt, Quality disposition and release', 'RM Planning Parameter':'Planning master settings',
    'Quality Dashboard':'Quality overview and priorities', 'All Quality Inspections':'Incoming, in-process and FG records',
    'Inspection Parameters':'Quality parameter master', 'Inspection Templates':'Reusable inspection specifications',
    'Batch Quality Records':'Batch identity and quality links', 'RM Release Note':'Controlled raw material release',
    'Final QC Release':'Lot-specific finished goods release', 'RM QC Decision':'Review raw material disposition',
    'RM Testing Standard':'Approved raw material standards',
    'In-Process QC / Checkpoints':'Work Order journey: Startup, Stabilization, Periodic, EOB and Hold / OOS',
    'In-Process Quality Inspections':'Existing checkpoint inspections and follow-ups',
    'Incoming Quality Inspections':'Incoming inspection and disposition evidence',
    'Final FG Quality Inspection':'Inspect each partial FG lot independently',
    'Supplier Quotation / Acknowledgement':'Supplier acknowledgement through standard quotation evidence',
    'Certificates of Analysis':'Existing lot-specific COA records'
  };
  const counted = new Set(['Work Order','Production Job Card','FG Delivery Note']);
  const icons = {execution:'M4 21V9l5 3V7l5 3V3h6v18H4M8 17h1m4 0h1m4 0h1',planning:'M4 20V4m0 16h16M8 16v-5m5 5V6m5 10V9',quality:'M12 3l8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3m-4 9l3 3 5-6',attention:'M12 3L2 21h20L12 3m0 6v5m0 3v1',reporting:'M5 3h14v18H5V3m4 5h6m-6 4h6m-6 4h4'};
  function decorate() {
    const slug = location.pathname.replace(/\/$/,'').split('/').pop().toLowerCase();
    const scope = scopes.has(slug) ? slug : '';
    if (document.body.dataset.calcoWorkspace !== scope) document.body.dataset.calcoWorkspace = scope;
    if (!scope) return;
    document.querySelectorAll('.shortcut-widget-box').forEach(widget => {
      if (widget.dataset.calcoDecorated) return;
      const title = widget.getAttribute('aria-label') || widget.querySelector('.widget-title')?.textContent.trim();
      if (!title) return;
      widget.dataset.calcoDecorated='1';
      const category = /Planning/.test(title) ? 'planning' : /Dashboard|Report|Analytics|Traceability|Parameter|Template|Governance/.test(title) ? 'reporting' : /Consumption|Non Conformance|Decision|Action/.test(title) ? 'attention' : scope==='quality' || /Clearance/.test(title) ? 'quality' : 'execution';
      widget.dataset.category=category;widget.dataset.usefulCount=String(counted.has(title));
      const head=widget.querySelector('.widget-head');
      if(head)head.insertAdjacentHTML('afterbegin',`<span class="calco-card-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="${icons[category]}"/></svg></span>`);
      const subtitle=widget.querySelector('.widget-subtitle');if(subtitle)subtitle.textContent=descriptions[title] || (category==='reporting'?'Open report and drill-down':category==='quality'?'Quality records and controlled review':'Records and follow-up');
      widget.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();widget.click();}});
    });
  }
  let pending=false;
  function schedule(){if(pending)return;pending=true;requestAnimationFrame(()=>{pending=false;decorate();});}
  $(document).ready(()=>{new MutationObserver(schedule).observe(document.body,{childList:true,subtree:true});if(frappe.router)frappe.router.on('change',schedule);schedule();});
})();
