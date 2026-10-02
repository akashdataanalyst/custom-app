/* Purchase-only native ListView presentation. Formatters cannot add child columns
 * or reorder the subject header. Guarded instance methods customize only left and
 * header HTML; native rows, assignments, comments, likes, queries and events remain.
 */
(() => {
  const settings = frappe.listview_settings["Material Request"] ||= {};
  if (settings.__calco_purchase_columns) return;
  settings.__calco_purchase_columns = true;
  settings.add_fields = [...new Set([...(settings.add_fields || []), "material_request_type", "transaction_date"])];
  const esc = v => frappe.utils.escape_html(v == null ? "" : String(v));
  const api = {
    active(lv) {
      return (lv.filter_area?.get() || []).some(f => f[0] === "Material Request" && f[1] === "material_request_type" &&
        (f[2] === "=" && f[3] === "Purchase" || f[2] === "in" &&
          (Array.isArray(f[3]) ? f[3] : String(f[3]).split(",")).length > 0 &&
          (Array.isArray(f[3]) ? f[3] : String(f[3]).split(",")).every(v => v === "Purchase")));
    },
    quantity(value, uom) {
      if (value === null || value === undefined || value === "" || !uom || !Number.isFinite(Number(value))) return esc(__("Review required"));
      return `${esc(Number(value).toLocaleString(frappe.boot?.lang || "en", {maximumFractionDigits:20}))} ${esc(uom)}`;
    },
    date(summary) {
      if (!summary || summary.date_mode === "review") return esc(__("Date requires review"));
      return `${summary.date_mode === "earliest" ? esc(__("Earliest")) + ": " : ""}${esc(frappe.datetime.str_to_user(summary.required_by))}`;
    },
    cells(summary, state = "Loading…") {
      if (!summary) return `<span class="calco-mr-items">${esc(__(state))}</span><span class="calco-mr-qty">—</span><span class="calco-mr-date calco-mr-required">—</span>`;
      // Each pair shares a CSS grid row, even when a long Item Code wraps.
      const rows = summary.items.map((r, i) => `<span class="calco-mr-items" style="grid-row:${i+1}" title="${esc(r.item_code)}">${esc(r.item_code)}</span><span class="calco-mr-qty" style="grid-row:${i+1}">${api.quantity(r.stock_qty, r.stock_uom)}</span>`).join("");
      return `${rows || '<span class="calco-mr-items">'+esc(__("No item rows"))+'</span>'}<span class="calco-mr-date calco-mr-required" style="grid-row:1 / span ${Math.max(1,summary.items.length)}" title="${esc(summary.required_dates.join(' · '))}">${api.date(summary)}</span>`;
    },
    install(lv) {
      if (lv.__calco_purchase_columns) return;
      lv.__calco_purchase_columns = true;
      lv.__calco_summaries = {};
      // Native setup_view renders/caches its header BEFORE settings.onload.
      // Invalidate that cache on first render and each Purchase/native transition.
      // Keep native rendering, selection, paging and event delegation intact.
      const renderHeader = lv.render_header;
      lv.render_header = function(force = false) {
        const active = api.active(this);
        const changed = this.__calco_header_mode !== active;
        if (changed || force) {
          this.$list_head_subject = null;
          this.$checkbox_actions = null;
        }
        const result = renderHeader.call(this, force || changed);
        this.__calco_header_mode = active;
        return result;
      };
      const left = lv.get_left_html, header = lv.get_header_html;
      lv.get_left_html = function(doc) {
        if (!api.active(this) || doc.material_request_type !== "Purchase") return left.call(this, doc);
        return `<div class="calco-mr-grid" data-mr="${esc(doc.name)}"><div class="calco-mr-date calco-mr-transaction">${esc(frappe.datetime.str_to_user(doc.transaction_date))}</div><div class="calco-mr-id list-subject level">${this.get_subject_element(doc, doc.name).innerHTML}</div><div class="calco-mr-body">${api.cells(this.__calco_summaries[doc.name])}</div><div class="calco-mr-status">${this.get_indicator_html(doc)}</div></div>${this.generate_button_html(doc)}${this.generate_dropdown_html(doc)}`;
      };
      lv.get_header_html = function() {
        const active = api.active(this);
        this.$result.toggleClass("calco-purchase-mr", active);
        const html = header.call(this);
        if (!active) return html;
        const $header = $(html);
        const $original = $header.find(".list-header-subject");
        const $checkbox = $original.find(".list-check-all").first().closest(".select-like").detach();
        $original.html(`<div class="calco-mr-grid calco-mr-head"><span class="calco-mr-date" data-sort-by="transaction_date">${esc(__("Transaction Date"))}</span><span class="calco-mr-head-id"><span data-sort-by="name">${esc(__("ID"))}</span></span><span>${esc(__("Item Code"))}</span><span>${esc(__("Qty"))}</span><span class="calco-mr-date" title="${esc(__("Item required dates; earliest when different"))}">${esc(__("Required By"))}</span><span data-sort-by="status">${esc(__("Status"))}</span></div>`);
        $original.find(".calco-mr-head-id").prepend($checkbox);
        return $header.prop("outerHTML");
      };
      if (!document.getElementById("calco-purchase-mr-style")) {
        $('<style id="calco-purchase-mr-style">').text(`
          .calco-purchase-mr .list-row{height:auto;min-height:42px;align-items:flex-start}
          .calco-purchase-mr .list-row>.level-left,.calco-purchase-mr .list-header-subject{flex:1;min-width:0;overflow:visible}
          .calco-mr-grid{display:grid;grid-template-columns:minmax(88px,.8fr) minmax(155px,1.6fr) minmax(100px,1.3fr) minmax(88px,1fr) minmax(92px,1fr) minmax(92px,1fr);gap:8px;width:100%;font-size:12px;align-items:start;white-space:normal;padding:8px 0}
          .calco-mr-body{display:grid;grid-template-columns:subgrid;grid-column:3 / 6;gap:5px 8px;align-items:start}
          .calco-mr-items{grid-column:1;overflow-wrap:anywhere}.calco-mr-qty{grid-column:2;overflow-wrap:anywhere}.calco-mr-required{grid-column:3}
          .calco-mr-id{min-width:0;max-width:none!important;align-self:start}.calco-mr-id>div{display:flex;min-width:0;gap:4px}.calco-mr-id a{white-space:normal;overflow-wrap:anywhere;font-weight:600}
          .calco-mr-head-id{display:flex;gap:4px}.calco-mr-status{min-width:0}.calco-mr-status .indicator-pill{max-width:100%}
          .calco-purchase-mr .list-row>.level-right{flex-shrink:0;align-self:start;padding-top:8px}
          @media(max-width:1000px){.calco-mr-grid{grid-template-columns:minmax(130px,1.3fr) minmax(90px,1fr) minmax(70px,.8fr) minmax(85px,.9fr)}.calco-mr-date{display:none}.calco-mr-body{grid-column:2 / 4}.calco-mr-status{grid-column:4}.calco-mr-id{grid-column:1}}
          @media(max-width:600px){.calco-mr-grid{grid-template-columns:minmax(100px,1fr) minmax(85px,1fr) minmax(65px,.8fr);gap:5px}.calco-mr-status{grid-column:1;grid-row:2}.calco-mr-body{grid-row:1 / span 2}.calco-mr-head>span:last-child{display:none}.calco-purchase-mr .list-row{flex-wrap:wrap}.calco-purchase-mr .list-row>.level-right{margin-left:auto}}
        `).appendTo(document.head);
      }
    },
    async refresh(lv) {
      const seq = lv.__calco_sequence = (lv.__calco_sequence || 0) + 1;
      if (!api.active(lv)) { lv.__calco_summaries = {}; return; }
      const names = (lv.data || []).filter(d => d.material_request_type === "Purchase").map(d => d.name);
      if (!names.length) return;
      lv.__calco_summaries = {};
      const paint = (data, state) => lv.$result.find(".calco-mr-grid[data-mr]").each((_, el) => {
        $(el).find(".calco-mr-body").html(api.cells(data[el.dataset.mr], state));
      });
      paint({}, "Loading…");
      try {
        const r = await frappe.call({method:"calco_erp.calco_purchase.material_request_list.get_purchase_summaries", args:{names}, freeze:false});
        if (seq !== lv.__calco_sequence || !api.active(lv)) return;
        lv.__calco_summaries = r.message?.summaries || {};
        paint(lv.__calco_summaries, "Unavailable");
      } catch (error) {
        if (seq === lv.__calco_sequence && api.active(lv)) paint({}, "Unable to load items");
      }
    },
  };
  const onload = settings.onload, refresh = settings.refresh;
  settings.onload = function(lv) { onload?.call(this, lv); api.install(lv); };
  settings.refresh = function(lv) { refresh?.call(this, lv); return api.refresh(lv); };
  settings.__calco_purchase_api = api;
})();
