/* Presentation adapter only. Server month balances and signed creation context remain authoritative. */
const CalcoFGPlanningView = {
  group(data) {
    const groups = new Map();
    for (const period of data.rows || []) {
      const key = JSON.stringify([period.company, period.item_code, period.stock_uom]);
      if (!groups.has(key)) groups.set(key, {key, item_code: period.item_code, item_name: period.item_name, company: period.company, stock_uom: period.stock_uom, periods: []});
      groups.get(key).periods.push(period);
    }
    return [...groups.values()].map(item => {
      item.periods.sort((a, b) => a.key.localeCompare(b.key));
      item.target = item.periods.find(p => Number(p.balance_to_plan) > 0);
      item.net = Number(item.target?.balance_to_plan || 0);
      item.available = Number(item.periods[0]?.released_stock || 0);
      item.open = item.periods.reduce((n, p) => n + Number(p.draft || 0) + Number(p.not_started || 0), 0);
      item.running = item.periods.reduce((n, p) => n + Number(p.in_progress || 0), 0);
      return item;
    }).sort((a, b) => Number(b.net > 0) - Number(a.net > 0) || Number(b.running > 0) - Number(a.running > 0) || a.item_code.localeCompare(b.item_code));
  },
  filter(items, search, state, only) {
    const needle = search.trim().toLowerCase();
    return items.filter(item => `${item.item_code} ${item.item_name}`.toLowerCase().includes(needle)
      && (!only || item.net > 0)
      && (state === 'All' || (state === 'Production Required' && item.net > 0) || (state === 'Covered' && item.net === 0) || (state === 'In Production' && item.running > 0)));
  },
  totals(items) {
    const quantities = new Map();
    for (const item of items) quantities.set(item.stock_uom, (quantities.get(item.stock_uom) || 0) + item.net);
    return {count: items.length, required: items.filter(r => r.net > 0).length, covered: items.filter(r => !r.net).length, running: items.filter(r => r.running > 0).length, quantities};
  }
};

frappe.pages['fg-planning-dashboard'].on_page_load = wrapper => new CalcoFGPlanningDashboard(wrapper);

class CalcoFGPlanningDashboard {
  constructor(wrapper) {
    this.page = frappe.ui.make_app_page({parent: wrapper, title: __('FG Planning Dashboard'), single_column: true});
    this.state = 'All'; this.rows = []; this.data = {}; this.pageIndex = 0; this.pageSize = 50;
    this.page.set_primary_action(__('Refresh Calculations'), () => this.refresh(), 'refresh');
    this.company = this.page.add_field({fieldname: 'company', label: __('Company'), fieldtype: 'Link', options: 'Company', default: frappe.defaults.get_user_default('Company'), change: () => { if (this.ready) this.refresh(); }});
    this.layout(); this.ready = true; this.refresh();
  }
  escape(value) { return frappe.utils.escape_html(String(value ?? '')); }
  qty(value) { return Number(value || 0).toLocaleString('en-IN', {maximumFractionDigits: 3}); }
  date(value) { return value ? new Date(value + 'T12:00:00').toLocaleDateString('en-GB', {day: 'numeric', month: 'short'}) : '—'; }
  layout() {
    this.page.main.html(`<div class="calco-fg-planning"><div class="calco-fg-planning__cards"></div>
      <div class="calco-fg-planning__toolbar"><span>Requirements by month · Quantities in each FG's stock UOM</span><span class="calco-fg-planning__summary"></span></div>
      <div class="calco-fg-planning__filter-card"><div class="calco-fg-planning__filter-row">
        <label>Search FG Code / Name<input class="form-control fg-search" placeholder="Search by FG Code or FG Name" type="search"></label>
        <div><label>Planning Status</label><div class="calco-fg-planning__pills">${['All','Production Required','Covered','In Production'].map(s => `<button type="button" class="fg-state ${s === 'All' ? 'is-active' : ''}" data-state="${s}">${__(s)}</button>`).join('')}</div></div>
        <label class="calco-fg-planning__check"><input type="checkbox" class="fg-only"> Only Requiring Production</label>
        <div class="calco-fg-planning__filter-actions"><button class="btn btn-sm btn-primary fg-apply">Apply Filters</button><button class="btn btn-sm btn-default fg-clear">Clear Filters</button></div>
      </div></div><div class="calco-fg-planning__table-wrap"><table class="calco-fg-planning__table"><colgroup>${[12,18,6.5,6.5,6.5,7,6.5,7,8.5,8,13.5].map(w => `<col style="width:${w}%">`).join('')}</colgroup><thead></thead><tbody></tbody></table></div>
      <div class="calco-fg-planning__pager"><button class="btn btn-sm btn-default fg-prev">Previous</button><span class="fg-page-number"></span><button class="btn btn-sm btn-default fg-next">Next</button></div>
      <details class="calco-fg-planning__notices"><summary></summary><div></div></details></div>`);
    this.root = this.page.main.find('.calco-fg-planning');
    if (!document.getElementById('calco-fg-planning-compact-style')) {
      const style = document.createElement('style'); style.id = 'calco-fg-planning-compact-style'; style.textContent = CalcoFGPlanningDashboard.styles; document.head.appendChild(style);
    }
    this.root.find('.fg-state').on('click', e => { this.state = e.currentTarget.dataset.state; this.pageIndex = 0; this.render(); });
    this.root.find('.fg-apply').on('click', () => { this.pageIndex = 0; this.render(); });
    this.root.find('.fg-search').on('keydown', e => { if (e.key === 'Enter') { this.pageIndex = 0; this.render(); } });
    this.root.find('.fg-clear').on('click', () => { this.state = 'All'; this.root.find('.fg-search').val(''); this.root.find('.fg-only').prop('checked', false); this.pageIndex = 0; this.render(); });
    this.root.find('.fg-prev').on('click', () => { this.pageIndex--; this.render(); });
    this.root.find('.fg-next').on('click', () => { this.pageIndex++; this.render(); });
  }
  async refresh() {
    if (!this.company.get_value()) return;
    const request = this.request = (this.request || 0) + 1;
    this.root.find('tbody').html('<tr><td colspan="11">Loading FG planning…</td></tr>');
    try {
      const result = await frappe.call({method: 'calco_erp.calco_production.fg_dashboard.get_dashboard_data', args: {company: this.company.get_value()}});
      if (request !== this.request) return;
      this.data = result.message; this.rows = CalcoFGPlanningView.group(this.data); this.pageIndex = 0; this.render();
    } catch (error) { this.root.find('tbody').html('<tr><td colspan="11">Unable to load planning. Resolve the reported error and refresh.</td></tr>'); throw error; }
  }
  render() {
    const e = value => this.escape(value), q = value => this.qty(value);
    const totals = CalcoFGPlanningView.totals(this.rows);
    const cards = [['Total FG Items',totals.count,'#2563eb','All'],['Production Required',totals.required,'#b42318','Production Required'],['Covered',totals.covered,'#15803d','Covered'],['In Production',totals.running,'#087e8b','In Production'],['Total Qty to Plan',[...totals.quantities].map(([u,n]) => `${q(n)} ${u}`).join(' · '),'#b42318',null]];
    this.root.find('.calco-fg-planning__cards').html(cards.map(([label,value,color,state]) => `<button class="calco-fg-planning__card" style="--card-accent:${color}" ${state ? `data-state="${state}"` : 'disabled'}><span class="calco-fg-planning__card-label">${label}</span><strong>${typeof value === 'number' ? q(value) : e(value)}</strong><small>${label === 'Total Qty to Plan' ? 'Next uncovered requirement per FG' : 'Across the planning horizon'}</small></button>`).join(''));
    this.root.find('.calco-fg-planning__card[data-state]').on('click', event => { this.state = event.currentTarget.dataset.state; this.pageIndex = 0; this.render(); });
    this.root.find('.fg-state').each((_, button) => $(button).toggleClass('is-active', button.dataset.state === this.state));
    const filtered = CalcoFGPlanningView.filter(this.rows, this.root.find('.fg-search').val() || '', this.state, this.root.find('.fg-only').prop('checked'));
    this.pageIndex = Math.max(0, Math.min(this.pageIndex, Math.ceil(filtered.length / this.pageSize) - 1));
    const visible = filtered.slice(this.pageIndex * this.pageSize, (this.pageIndex + 1) * this.pageSize);
    const months = this.data.months || [];
    this.root.find('thead').html(`<tr><th>FG Code</th><th>FG Name</th>${['This Month','Next Month','Next +1'].map((label,i) => `<th class="numeric">${label}<small>${e(months[i]?.label || '')}</small></th>`).join('')}<th class="numeric">FG Available</th><th class="numeric">Open WO</th><th class="numeric">In Progress WO</th><th class="numeric net">Net Qty to Plan</th><th>Required By</th><th>Action</th></tr>`);
    this.root.find('tbody').html(visible.map(item => {
      const index = this.rows.indexOf(item);
      const detail = (value, month = '') => `<button class="fg-detail" data-index="${index}" data-month="${month}">${value}</button>`;
      return `<tr class="${item.net > 0 ? 'production-required' : ''}" data-fg="${e(item.item_code)}"><td>${detail(e(item.item_code))}</td><td>${detail(e(item.item_name))}<small>${e(item.stock_uom)}</small></td>${months.map(m => `<td class="numeric">${detail(q(item.periods.find(p => p.key === m.key)?.remaining_demand), m.key)}${item.periods.find(p => p.key === m.key)?.uat_scenario_demand ? `<small>Recovery UAT Scenario</small>` : ''}</td>`).join('')}<td class="numeric">${q(item.available)}</td><td class="numeric">${q(item.open)}</td><td class="numeric ${item.running > 0 ? 'running' : ''}">${q(item.running)}</td><td class="numeric net">${detail(q(item.net),item.target?.key || '')}</td><td>${this.date(item.target?.end)}</td><td>${item.target ? `<button class="btn btn-sm btn-primary fg-add" data-index="${index}">Add Work Order</button>` : '<span class="covered">Covered</span>'}</td></tr>`;
    }).join('') || '<tr><td colspan="11">No FG items match these filters.</td></tr>');
    this.root.find('.calco-fg-planning__summary').text(`${filtered.length} FG items`);
    this.root.find('.fg-page-number').text(`${filtered.length ? this.pageIndex * this.pageSize + 1 : 0}–${Math.min((this.pageIndex + 1) * this.pageSize, filtered.length)} of ${filtered.length}`);
    this.root.find('.fg-prev').prop('disabled', this.pageIndex === 0);
    this.root.find('.fg-next').prop('disabled', (this.pageIndex + 1) * this.pageSize >= filtered.length);
    this.root.find('.fg-detail').on('click', event => this.details(this.rows[Number(event.currentTarget.dataset.index)],event.currentTarget.dataset.month));
    this.root.find('.fg-add').on('click', event => this.add(this.rows[Number(event.currentTarget.dataset.index)].target));
    const notices = this.data.exceptions || [], legacy = this.data.transition || [];
    this.root.find('.calco-fg-planning__notices summary').text(`Planning notices (${notices.length}) · Legacy releases to review (${legacy.length})`);
    this.root.find('.calco-fg-planning__notices > div').html(`<ul>${notices.map(n => `<li>${e(n.item_code)} — ${e(n.reason)} ${this.link(n.doctype,n.name)}</li>`).join('')}</ul><ul>${legacy.map(r => `<li>${this.link('Production Requirement',r.production_requirement)} — ${e(r.item_code)}: ${q(r.remaining_qty)} remaining</li>`).join('')}</ul>`);
  }
  link(doctype, name) { return doctype && name ? `<a href="/app/${frappe.router.slug(doctype)}/${encodeURIComponent(name)}">${this.escape(name)}</a>` : ''; }
  details(item, selectedMonth) {
    const metrics = [['forecast','Forecast'],['outstanding_so','Firm SO outstanding'],['fulfilled','Fulfilled sales'],['remaining_demand','Remaining demand'],['overdue_demand','Overdue demand'],['released_stock','Released FG'],['pending_release','Pending Final QC / release'],['draft','Draft WO allocation'],['not_started','Not Started WO'],['in_progress','In Progress WO'],['manufactured','Manufactured'],['process_loss','Process loss'],['short_or_cancelled','Stopped / cancelled / short quantity'],['projected_coverage','Projected coverage by month-end'],['balance_to_plan','Balance to Plan by month-end']];
    if (item.periods.some(p => p.uat_scenario_demand)) metrics.splice(2, 0, ['uat_scenario_demand','Recovery UAT Scenario']);
    const sources = [...new Map(item.periods.flatMap(p => p.sources || []).map(s => [JSON.stringify(s), s])).values()];
    const dialog = new frappe.ui.Dialog({title: `${item.item_code} — Planning Details`, size: 'extra-large', fields: [{fieldtype:'HTML',fieldname:'calculation'}]});
    dialog.fields_dict.calculation.$wrapper.html(`<p>${this.escape(item.item_name)} · ${this.escape(item.stock_uom)}</p><p>Net Qty to Plan shows the next uncovered month. Choose another month below when scheduling a later run.</p><table class="table table-bordered"><thead><tr><th>Calculation</th>${item.periods.map(p => `<th ${p.key === selectedMonth ? 'class="text-danger"' : ''}>${this.escape(p.label)}</th>`).join('')}</tr></thead><tbody>${metrics.map(([key,label]) => `<tr><td>${label}</td>${item.periods.map(p => `<td>${this.qty(p[key])}</td>`).join('')}</tr>`).join('')}<tr><td>Plan a run</td>${item.periods.map(p => `<td>${p.balance_to_plan > 0 ? `<button class="btn btn-sm btn-primary fg-period-add" data-month="${p.key}">Add Work Order</button>` : 'Covered'}</td>`).join('')}</tr></tbody></table><p>Calculation ${this.escape(this.data.version)} · Updated ${this.escape(this.data.as_of)}</p><details><summary>Contributing documents (${sources.length})</summary><table class="table table-bordered"><thead><tr><th>Document</th><th>Measure</th><th>Quantity</th><th>Reference / Date</th></tr></thead><tbody>${sources.map(s => `<tr><td>${this.escape(s.doctype)} ${this.link(s.doctype,s.name)}</td><td>${this.escape(s.metric)}</td><td>${this.qty(s.qty)}</td><td>${this.escape([s.row,s.date,s.batch,s.status].filter(Boolean).join(' · '))}</td></tr>`).join('')}</tbody></table></details>`);
    dialog.fields_dict.calculation.$wrapper.find('.fg-period-add').on('click', event => { dialog.hide(); this.add(item.periods.find(p => p.key === event.currentTarget.dataset.month)); });
    dialog.show();
  }
  async add(row) {
    const result = await frappe.call({method: 'calco_erp.calco_production.fg_planning_authority.creation_context', args: {company:row.company,item_code:row.item_code,month:row.key}, freeze:true});
    const context = result.message;
    const start = row.key > frappe.datetime.get_today() ? row.key : frappe.datetime.get_today();
    await frappe.new_doc('Work Order',{company:row.company,production_item:row.item_code,qty:context.row.balance_to_plan},doc => {
      doc.__run_link_triggers = 0; doc.custom_fg_planning_context = context.context; doc.bom_no = context.boms.suggested || '';
      doc.custom_fg_requested_start = context.requested_start || start + ' 08:00:00'; doc.planned_start_date = doc.custom_fg_requested_start; doc.planned_end_date = null; doc.expected_delivery_date = row.end;
      doc.fg_warehouse = context.warehouses['FG Quarantine']; doc.__fg_dashboard_context = context;
    });
  }
}

CalcoFGPlanningDashboard.styles = `
.calco-fg-planning { color:var(--text-color,#344054); }
.calco-fg-planning__cards {display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px;margin-bottom:16px}
.calco-fg-planning__card {text-align:left;border:1px solid var(--border-color,#e2e8f0);border-left:6px solid var(--card-accent);border-radius:14px;padding:14px 16px;background:linear-gradient(180deg,#f8fbff 0%,var(--fg-color,#fff) 100%);box-shadow:0 8px 18px rgba(15,23,42,.04);color:inherit}
.calco-fg-planning__card:disabled {opacity:1}.calco-fg-planning__card:not(:disabled){cursor:pointer}.calco-fg-planning__card-label {display:block;color:var(--text-muted,#667085);font-size:11px;text-transform:uppercase;letter-spacing:.04em;margin-bottom:8px}.calco-fg-planning__card strong {font-size:26px;line-height:1.1;display:block;overflow-wrap:anywhere}.calco-fg-planning__card small{display:block;font-size:11px;color:var(--text-muted,#667085);margin-top:8px}
.calco-fg-planning__toolbar {display:flex;justify-content:space-between;gap:16px;margin-bottom:12px;color:var(--text-muted,#667085);font-size:12px}
.calco-fg-planning__filter-card {padding:14px 16px;margin-bottom:12px;border:1px solid var(--border-color,#e2e8f0);border-radius:14px;background:linear-gradient(180deg,#f8fbff,#fff);box-shadow:0 8px 18px rgba(15,23,42,.04)}
.calco-fg-planning__filter-row {display:grid;grid-template-columns:minmax(180px,1.2fr) minmax(350px,1.7fr) auto auto;gap:12px;align-items:end}.calco-fg-planning__filter-row label {display:block;font-size:12px;font-weight:700;color:#475467;margin:0}.calco-fg-planning__filter-row input[type=search] {margin-top:6px;border:1px solid #c3d2e6;border-radius:10px;min-height:38px;width:100%}
.calco-fg-planning__pills {display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}.calco-fg-planning__pills button {font-size:11px;border:1px solid #c3d2e6;border-radius:999px;padding:8px 10px;background:white;color:#475467}.calco-fg-planning__pills .is-active {background:#e8f0ff;color:#1d4ed8;border-color:#93b4f4;font-weight:700}.calco-fg-planning__check {padding-bottom:9px;white-space:nowrap}.calco-fg-planning__filter-actions {display:flex;gap:8px;padding-bottom:2px;white-space:nowrap}
.calco-fg-planning__table-wrap {overflow:auto;border:1px solid var(--border-color,#e2e8f0);border-radius:14px;background:var(--fg-color,#fff);max-height:calc(100vh - 360px);box-shadow:0 8px 18px rgba(15,23,42,.04)}
.calco-fg-planning__table {width:100%;table-layout:fixed;border-collapse:collapse}.calco-fg-planning__table th,.calco-fg-planning__table td {padding:9px 8px;border-bottom:1px solid var(--border-color,#e2e8f0);vertical-align:top;font-size:12px;overflow-wrap:anywhere;text-align:left}.calco-fg-planning__table th {position:sticky;top:0;background:white;z-index:2;font-size:10px;text-transform:uppercase;letter-spacing:.03em;color:#475467;line-height:1.3}.calco-fg-planning__table small {display:block;color:var(--text-muted,#667085);font-size:10px;margin-top:3px;text-transform:none}
.calco-fg-planning__table .numeric{text-align:right;font-variant-numeric:tabular-nums}.calco-fg-planning__table .net {color:#b42318;font-weight:700}.calco-fg-planning__table .production-required{background:#fff5f5}.calco-fg-planning__table tbody tr:hover{background:#f8fafc}.calco-fg-planning__table .running{color:#087e8b;font-weight:600}.calco-fg-planning__table .covered{color:#15803d;font-size:11px}.calco-fg-planning__table .fg-detail {border:0;background:transparent;padding:0;color:inherit;font:inherit;text-align:inherit;cursor:pointer;max-width:100%;overflow-wrap:anywhere;white-space:normal}.calco-fg-planning__table .fg-detail:hover{text-decoration:underline}.calco-fg-planning__table .fg-add{font-size:11px;padding:6px 8px;white-space:normal;line-height:1.3}
.calco-fg-planning__pager {display:flex;align-items:center;justify-content:flex-end;gap:10px;margin-top:10px;font-size:12px;color:var(--text-muted,#667085)}.calco-fg-planning__notices {margin-top:12px;font-size:12px;color:var(--text-muted,#667085)}.calco-fg-planning__notices summary{cursor:pointer}
@media(max-width:1400px){.calco-fg-planning__filter-row{grid-template-columns:1fr 1.3fr}.calco-fg-planning__table th,.calco-fg-planning__table td{padding:8px 6px}.calco-fg-planning__card{padding:12px}.calco-fg-planning__card strong{font-size:23px}}
@media(max-width:900px){.calco-fg-planning__cards{grid-template-columns:repeat(3,minmax(0,1fr))}.calco-fg-planning__table{min-width:950px}.calco-fg-planning__filter-row{grid-template-columns:1fr}.calco-fg-planning__toolbar{flex-direction:column}}
`;
