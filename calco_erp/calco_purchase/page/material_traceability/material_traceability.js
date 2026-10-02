frappe.pages["material-traceability"].on_page_load = function (wrapper) {
  new CalcoMaterialTraceabilityPage(wrapper);
};

class CalcoMaterialTraceabilityPage {
  constructor(wrapper) {
    this.wrapper = $(wrapper);
    this.page = frappe.ui.make_app_page({
      parent: wrapper,
      title: __("Material Traceability"),
      single_column: true,
    });
    this.offset = 0; this.requestSequence = 0; this.statusFilter = "All"; this.lastData = null;
    this.searchOptions = ["Any", "Batch No", "PO", "PR", "MR", "Supplier", "Item Code"];
    this.buildLayout();
    this.bindEvents();
    this.loadFromRoute();
  }

  buildLayout() {
    this.page.main.html(`
      <section class="calco-material-traceability cmt">
        <style>
      .cmt { --cmt-bg:#f6f8fb; --cmt-card:#fff; --cmt-line:#e3e8ef; --cmt-ink:#0f172a; --cmt-ink2:#475569; --cmt-ink3:#94a3b8;
             --cmt-blue:#2563eb; --cmt-teal:#0d9488; --cmt-green:#16a34a; --cmt-amber:#d97706; --cmt-red:#dc2626; --cmt-grey:#cbd5e1;
             padding:18px; border-radius:16px; background:var(--cmt-bg); color:var(--cmt-ink); }
      .cmt-toolbar { display:grid; grid-template-columns: 200px 1fr auto; gap:12px; align-items:end; margin-bottom:16px; }
      .cmt-label { display:block; font-size:11px; letter-spacing:.06em; text-transform:uppercase; color:var(--cmt-ink2); margin-bottom:4px; }
      .cmt-kpis { display:grid; grid-template-columns: repeat(5, minmax(0,1fr)); gap:10px; }
      .cmt-kpi { background:var(--cmt-card); border:1px solid var(--cmt-line); border-radius:12px; padding:12px 14px; border-top:3px solid var(--cmt-grey); }
      .cmt-kpi--blue { border-top-color:var(--cmt-blue);} .cmt-kpi--teal{border-top-color:var(--cmt-teal);} .cmt-kpi--green{border-top-color:var(--cmt-green);} .cmt-kpi--red{border-top-color:var(--cmt-red);}
      .cmt-kpi__label { font-size:11px; text-transform:uppercase; letter-spacing:.06em; color:var(--cmt-ink2); }
      .cmt-kpi__value { font-size:24px; font-weight:700; font-variant-numeric:tabular-nums; margin-top:2px; }
      .cmt-kpi__sub { font-size:12px; color:var(--cmt-ink3); }
      .cmt-flow { background:var(--cmt-card); border:1px solid var(--cmt-line); border-radius:12px; padding:12px 14px; margin-top:10px; }
      .cmt-flow__bar, .cmt-card__bar { display:flex; height:10px; border-radius:999px; overflow:hidden; background:#eef2f6; gap:2px; }
      .cmt-seg--green{background:var(--cmt-green);} .cmt-seg--amber{background:var(--cmt-amber);} .cmt-seg--red{background:var(--cmt-red);} .cmt-seg--grey{background:var(--cmt-grey);}
      .cmt-legend { display:flex; flex-wrap:wrap; gap:16px; margin-top:8px; font-size:12px; color:var(--cmt-ink2); font-variant-numeric:tabular-nums; }
      .cmt-dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px; }
      .cmt-dot--green{background:var(--cmt-green);} .cmt-dot--amber{background:var(--cmt-amber);} .cmt-dot--red{background:var(--cmt-red);} .cmt-dot--grey{background:var(--cmt-grey);}
      .cmt-filterbar { display:flex; flex-wrap:wrap; gap:8px; margin:14px 0 10px; }
      .cmt-chip-filter { border:1px solid var(--cmt-line); background:var(--cmt-card); border-radius:999px; padding:5px 12px; font-size:12px; color:var(--cmt-ink2); cursor:pointer; }
      .cmt-chip-filter.is-active { background:var(--cmt-ink); border-color:var(--cmt-ink); color:#fff; }
      .cmt-chip-filter__n { margin-left:6px; opacity:.7; font-variant-numeric:tabular-nums; }
      .cmt-card { background:var(--cmt-card); border:1px solid var(--cmt-line); border-radius:14px; padding:14px 16px; margin-bottom:12px; }
      .cmt-card__head { display:flex; justify-content:space-between; align-items:center; gap:10px; }
      .cmt-card__id { display:flex; align-items:center; flex-wrap:wrap; gap:8px; }
      .cmt-link--title { font-size:16px; font-weight:700; color:var(--cmt-ink) !important; background:none !important; border:0 !important; padding:0 !important; }
      .cmt-item { font-size:11px; font-weight:600; background:#eef2ff; color:#3730a3; border-radius:6px; padding:2px 8px; }
      .cmt-card__supplier { font-size:13px; color:var(--cmt-ink2); margin:2px 0 12px; }
      .cmt-muted { color:var(--cmt-ink3); font-style:italic; }
      .cmt-status { font-size:11px; font-weight:600; border-radius:999px; padding:3px 10px; }
      .cmt-status--green{background:#dcfce7;color:#166534;} .cmt-status--blue{background:#dbeafe;color:#1e40af;} .cmt-status--grey{background:#f1f5f9;color:#475569;}
      .cmt-stepper { display:grid; grid-template-columns: repeat(5, minmax(0,1fr)); margin-bottom:12px; }
      .cmt-step { text-align:center; padding:0 4px; }
      .cmt-step__track { position:relative; height:28px; display:flex; justify-content:center; align-items:center; }
      .cmt-step__track::before { content:""; position:absolute; top:50%; left:0; right:0; height:2px; background:var(--cmt-line); transform:translateY(-50%); }
      .cmt-step:first-child .cmt-step__track::before { left:50%; } .cmt-step:last-child .cmt-step__track::before { right:50%; }
      .cmt-step--done .cmt-step__track::before { background:var(--cmt-green); }
      .cmt-step__node { position:relative; z-index:1; width:26px; height:26px; border-radius:50%; display:flex; align-items:center; justify-content:center;
                        font-size:12px; font-weight:700; background:#fff; border:2px solid var(--cmt-grey); color:var(--cmt-ink3); }
      .cmt-step--done .cmt-step__node { background:var(--cmt-green); border-color:var(--cmt-green); color:#fff; }
      .cmt-step--active .cmt-step__node { border-color:var(--cmt-blue); color:var(--cmt-blue); box-shadow:0 0 0 4px rgba(37,99,235,.15); }
      .cmt-step__label { font-size:11px; text-transform:uppercase; letter-spacing:.05em; color:var(--cmt-ink2); margin-top:4px; }
      .cmt-step__value { font-size:17px; font-weight:700; font-variant-numeric:tabular-nums; }
      .cmt-step--todo .cmt-step__value { color:var(--cmt-ink3); }
      .cmt-step__sub { font-size:11px; color:var(--cmt-amber); }
      .cmt-step__docs { display:flex; flex-direction:column; align-items:center; gap:3px; margin-top:4px; }
      .cmt-link { font-size:11px; border:1px solid var(--cmt-line); background:#f8fafc; border-radius:6px; padding:2px 8px; color:var(--cmt-blue); cursor:pointer; max-width:100%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
      .cmt-link:hover { border-color:var(--cmt-blue); }
      .cmt-flags { display:flex; flex-wrap:wrap; gap:6px; margin-top:10px; }
      .cmt-flag { font-size:11px; font-weight:600; border-radius:6px; padding:3px 8px; }
      .cmt-flag--warn{background:#fef3c7;color:#92400e;} .cmt-flag--bad{background:#fee2e2;color:#991b1b;} .cmt-flag--info{background:#e0f2fe;color:#075985;}
      .cmt-batches { display:flex; flex-wrap:wrap; align-items:center; gap:6px; margin-top:10px; }
      .cmt-batches__label { font-size:11px; text-transform:uppercase; letter-spacing:.05em; color:var(--cmt-ink2); margin-right:2px; }
      .cmt-batch { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:11px; background:#f1f5f9; border-radius:6px; padding:2px 8px; }
      .cmt-empty { background:var(--cmt-card); border:1px dashed var(--cmt-line); border-radius:12px; padding:28px; text-align:center; color:var(--cmt-ink2); }
      .cmt-performance { font-size:11px; color:var(--cmt-ink3); text-align:right; margin-top:6px; }
      @media (max-width: 900px) {
        .cmt-toolbar { grid-template-columns: 1fr; }
        .cmt-kpis { grid-template-columns: repeat(2, minmax(0,1fr)); }
        .cmt-stepper { grid-template-columns: repeat(5, minmax(64px,1fr)); overflow-x:auto; }
      }

          .calco-material-traceability__toolbar { display:grid;grid-template-columns:200px 1fr auto;gap:12px;align-items:end;margin-bottom:12px; }
          .calco-material-traceability__label { font-size:12px;color:var(--cmt-ink2);margin-bottom:4px; }
          .cmt-population { margin:12px 0;font-size:13px; }
          .cmt-population table { width:100%;font-variant-numeric:tabular-nums; }
          .cmt-population th,.cmt-population td { padding:6px 10px;text-align:right;white-space:nowrap; }
          .cmt-population th:first-child,.cmt-population td:first-child { text-align:left; }
          .cmt-scroll { overflow-x:auto; }
          .cmt-unit { border-top:1px solid var(--cmt-line);padding-top:10px;margin-top:10px; }
          .cmt-unit-label { font-size:12px;font-weight:600;margin-bottom:8px; }
          .cmt-docs { display:flex;flex-wrap:wrap;gap:8px;margin-top:10px; }
          .cmt-filter-label { font-size:12px;color:var(--cmt-ink2); }
          .cmt-card button,.cmt-card span { overflow-wrap:anywhere; }
          @media(max-width:768px) { .calco-material-traceability__toolbar { grid-template-columns:1fr; } }
        </style>
        <div class="calco-material-traceability__toolbar">
          <div>
            <div class="calco-material-traceability__label">${__("Search By")}</div>
            <select class="form-control calco-material-traceability__search-by"></select>
          </div>
          <div>
            <div class="calco-material-traceability__label">${__("Search Query")}</div>
            <input class="form-control calco-material-traceability__query" placeholder="${__("Batch No, PO, PR, MR, Supplier, Item Code")}" />
          </div>
          <button type="button" class="btn btn-primary calco-material-traceability__run">${__("Search")}</button>
        </div>
        <div class="calco-material-traceability__pager" style="display:flex;gap:8px;align-items:center;margin-bottom:12px;flex-wrap:wrap">
          <select class="form-control trace-page-size" style="width:90px" aria-label="Results per page"><option>20</option><option>25</option><option>50</option></select>
          <button class="btn btn-default trace-prev">${__("Previous")}</button>
          <button class="btn btn-default trace-next">${__("Next")}</button>
          <span class="calco-material-traceability__performance text-muted small"></span>
        </div>
        <div class="calco-material-traceability__totals" style="margin-bottom:16px"></div>
        <label class="cmt-filter-label">${__("Filter this page")}
          <select class="form-control cmt-status-filter" style="width:auto;display:inline-block;margin:0 8px 12px">
            ${["All","Not Started","In Progress","Completed","Attention"].map(v=>`<option value="${v}">${__(v)}</option>`).join("")}
          </select><span class="cmt-filter-count"></span></label>
        <div class="calco-material-traceability__results"></div>
      </section>
    `);

    this.$searchBy = this.page.main.find(".calco-material-traceability__search-by");
    this.$query = this.page.main.find(".calco-material-traceability__query");
    this.$results = this.page.main.find(".calco-material-traceability__results");
    this.$performance = this.page.main.find(".calco-material-traceability__performance");

    this.$searchBy.html(this.searchOptions.map((option) => `<option value="${frappe.utils.escape_html(option)}">${frappe.utils.escape_html(option)}</option>`).join(""));
  }

  bindEvents() {
    this.page.main.on("change", ".cmt-status-filter", event => {
      this.statusFilter = event.currentTarget.value;
      if (this.lastData) this.renderResults(this.lastData);
    });
    this.page.main.on("change", ".trace-page-size", () => this.runSearch());
    this.page.main.on("click", ".trace-prev", () => this.runSearch(Math.max(0, this.offset - Number(this.page.main.find(".trace-page-size").val()))));
    this.page.main.on("click", ".trace-next", () => this.runSearch(this.offset + Number(this.page.main.find(".trace-page-size").val())));
    this.page.main.on("click", ".calco-material-traceability__run", () => this.runSearch());
    this.page.main.on("keydown", ".calco-material-traceability__query", (event) => {
      if (event.key === "Enter") {
        this.runSearch();
      }
    });
    this.page.main.on("click", ".calco-material-traceability__link, .cmt-link", (event) => {
      const target = event.currentTarget.dataset;
      if (target.doctype && target.name) {
        frappe.set_route("Form", target.doctype, target.name);
      }
    });
  }

  loadFromRoute() {
    const routeOptions = frappe.route_options || {};
    if (routeOptions.search_by) {
      this.$searchBy.val(routeOptions.search_by);
    }
    if (routeOptions.query) {
      this.$query.val(routeOptions.query);
      this.runSearch();
      return;
    }
    this.runSearch();
  }

  async runSearch(offset = 0) {
    this.offset = offset;
    const sequence = ++this.requestSequence;
    this.lastData = null;
    this.page.main.find(".trace-prev, .trace-next").prop("disabled", true);
    this.$results.html(`<div class="cmt-empty">${__("Loading…")}</div>`);
    try {
      const response = await frappe.call({
        method: "calco_erp.calco_purchase.purchase_journey.search_material_traceability",
        args: {
          query: this.$query.val(),
          search_by: this.$searchBy.val(),
          limit: Number(this.page.main.find(".trace-page-size").val()),
          offset,
        },
        freeze: false,
      });
      if (sequence === this.requestSequence) this.renderResults(response.message || {});
    } catch (error) {
      if (sequence !== this.requestSequence) return;
      this.page.main.find(".calco-material-traceability__totals, .cmt-filter-count").empty();
      this.$performance.text("");
      this.$results.html(`<div class="calco-material-traceability__empty">${frappe.utils.escape_html(error.message || __("Unable to load traceability results."))}</div>`);
    }
  }

  renderResults(data) {
    const results = data.results || [];
    this.offset = data.offset || 0;
    const first = results.length ? this.offset + 1 : 0;
    this.$performance.text(__("Showing {0}–{1} of {2} journeys · {3} ms", [first, results.length ? this.offset + results.length : 0, data.total_count || 0, data.performance_ms || 0]));
    this.page.main.find(".trace-prev").prop("disabled", this.offset === 0);
    this.page.main.find(".trace-next").prop("disabled", !data.has_more);


    this.lastData = data;
    this.page.main.find(".calco-material-traceability__totals").html(this.renderPopulation(data));
    const shown = results.filter(row => this.statusFilter === "All" ||
      (this.statusFilter === "Attention" ? this.hasAttention(row) : row.status === this.statusFilter));
    this.page.main.find(".cmt-filter-count").text(__("{0} of {1} loaded journeys", [shown.length, results.length]));
    this.$results.html(shown.length ? shown.map(row => this.renderCard(row)).join("") :
      `<div class="cmt-empty">${results.length ? __("No journeys match this page filter. Other pages may contain matches.") : __("No matching Material Request journeys found.")}</div>`);
  }

  esc(value) { return frappe.utils.escape_html(value == null ? "" : String(value)); }
  known(value) { return value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value)); }
  qty(value) { return this.known(value) ? this.esc(value) : this.esc(__("Review required")); }
  links(doctype, names) {
    return (names || []).map(name => `<button type="button" class="cmt-link" data-doctype="${this.esc(doctype)}" data-name="${this.esc(name)}">${this.esc(name)}</button>`).join("");
  }
  hasAttention(row) {
    return row.qc_quantity_review_required || (row.deviations || []).length || (row.supplier_capa_requests || []).length ||
      (row.quantity_groups || []).some(g => g.qc_quantity_review_required || g.qc_pending === null || Number(g.qc_pending)>0 || Number(g.rejected)>0 || Number(g.returned)>0);
  }
  renderPopulation(data) {
    const cols = [["Requested","requested"],["Ordered","ordered"],["Received","received"],["QC Accepted","qc_accepted"],
      ["QC Pending","qc_pending"],["Rejected","rejected"],["Returned","returned"],["RM Released","released"],["PO Outstanding","outstanding_po"]];
    return `<details class="cmt-population"><summary>${__("Complete matching population")}: ${Number(data.total_count || 0)} ${__("journeys")} · ${__("Totals by stock UOM")}</summary>
      <div class="cmt-scroll"><table><thead><tr><th>${__("Stock UOM")}</th>${cols.map(([label])=>`<th>${__(label)}</th>`).join("")}</tr></thead><tbody>
      ${(data.quantities || []).map(g=>`<tr><th>${this.esc(g.stock_uom)}</th>${cols.map(([,key])=>`<td>${this.qty(g.qc_quantity_review_required && ["qc_accepted","qc_pending","rejected"].includes(key) ? null : g[key] === undefined ? 0 : g[key])}</td>`).join("")}</tr>`).join("")}</tbody></table></div></details>`;
  }
  renderUnit(row, group) {
    // Only the backend's stock-UOM groups and QC authority drive quantities.
    // Missing QC fields are never silently zeroed or inferred from receipt totals.
    const g = {requested:0, ordered:0, received:0, qc_accepted:0, rejected:0, returned:0, released:0, qc_pending:0, outstanding_po:0, ...group};
    if (g.qc_quantity_review_required) ["qc_accepted","rejected","qc_pending"].forEach(k => g[k]=null);
    const processed = this.known(g.qc_accepted) && this.known(g.rejected) ? Number(g.qc_accepted)+Number(g.rejected) : null;
    const reached = value => this.known(value) && Number(value)>0;
    const covered = (value, target) => reached(target) && this.known(value) && Number(value)>=Number(target);
    const steps = [
      ["Requested",g.requested,reached(g.requested),this.links("Material Request",[row.material_request]),""],
      ["Ordered",g.ordered,covered(g.ordered,g.requested),this.links("Purchase Order",row.purchase_orders),""],
      ["Received",g.received,covered(g.received,g.ordered),this.links("Purchase Receipt",row.purchase_receipts),""],
      ["QC",processed,reached(g.received) && this.known(g.qc_pending) && Number(g.qc_pending)===0,"",`${__("Awaiting QC")}: ${this.qty(g.qc_pending)}`],
      ["Accepted",g.qc_accepted,row.status === "Completed" && reached(g.qc_accepted),"",`${__("Rejected")}: ${this.qty(g.rejected)}`],
    ];
    const flags = [];
    if (g.qc_quantity_review_required || !this.known(g.qc_pending)) flags.push(__("QC UOM Review Required"));
    if (reached(g.qc_pending)) flags.push(`${__("Awaiting QC")}: ${this.qty(g.qc_pending)}`);
    if (reached(g.rejected)) flags.push(`${__("Rejected")}: ${this.qty(g.rejected)}`);
    if (reached(g.returned)) flags.push(`${__("Returned")}: ${this.qty(g.returned)}`);
    return `<section class="cmt-unit"><div class="cmt-unit-label">${__("Stock UOM")}: ${this.esc(g.stock_uom || __("Unknown UOM"))}</div>
      <div class="cmt-stepper">${steps.map(([label,value,done,docs,sub],i)=>{
        const state=done ? "done" : reached(value) ? "active" : "todo";
        return `<div class="cmt-step cmt-step--${state}"><div class="cmt-step__track"><div class="cmt-step__node">${done ? "✓" : i+1}</div></div>
        <div class="cmt-step__label">${__(label)}</div><div class="cmt-step__value">${this.qty(value)}</div>
        <div class="cmt-step__sub">${sub}</div><div class="cmt-step__docs">${docs}</div></div>`;
      }).join("")}</div>
      <div class="cmt-legend"><span>${__("RM Released")}: ${this.qty(g.released === undefined ? 0 : g.released)}</span><span>${__("PO Outstanding")}: ${this.qty(g.outstanding_po === undefined ? 0 : g.outstanding_po)}</span><span>${__("Returned")}: ${this.qty(g.returned === undefined ? 0 : g.returned)}</span></div>
      ${flags.length ? `<div class="cmt-flags">${flags.map(v=>`<span class="cmt-flag cmt-flag--warn">${v}</span>`).join("")}</div>` : ""}</section>`;
  }
  renderCard(row) {
    const tone = {Completed:"green", "In Progress":"blue"}[row.status] || "grey";
    // No fallback to legacy aggregate scalar fields: these are null for mixed UOMs.
    const groups = row.quantity_groups || [];
    return `<article class="cmt-card"><header class="cmt-card__head"><div class="cmt-card__id">${this.links("Material Request",[row.material_request])}
      ${(row.item_codes || []).map(item=>`<span class="cmt-item">${this.esc(item)}</span>`).join("")}</div>
      <span class="cmt-status cmt-status--${tone}">${this.esc(row.status || __("Unknown"))}</span></header>
      <div class="cmt-card__supplier">${this.esc(row.supplier || __("No supplier yet"))}</div>
      ${groups.length ? groups.map(g=>this.renderUnit(row,g)).join("") : `<div class="cmt-empty">${__("Stock UOM quantities require review")}</div>`}
      <div class="cmt-docs">${this.links("Batch",row.batches)}${this.links("RM Release Note",row.release_notes)}
      ${(row.deviations || []).length ? `<span>${__("Deviation")}</span>${this.links("RM Deviation Approval",row.deviations)}` : ""}
      ${(row.supplier_capa_requests || []).length ? `<span>${__("Supplier CAPA")}</span>${this.links("Supplier CAPA Request",row.supplier_capa_requests)}` : ""}</div></article>`;
  }
}
