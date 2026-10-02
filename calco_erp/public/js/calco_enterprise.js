/* Optional presentation only. Native workspace, search and notification authority. */
(function () {
  "use strict";
  const ROLE = "Calco New UI";
  function allowed(user, roles) { return !!user && user !== "Guest" && roles.includes(ROLE); }
  function enabled(user, roles, preference) { return allowed(user, roles) && preference !== "off"; }
  function preferenceKey(user) { return "calco_enterprise:" + user; }
  if (typeof module !== "undefined" && module.exports) { module.exports = {allowed, enabled, preferenceKey, notifications}; return; }
  if (window.__calcoEnterpriseUI) return;
  window.__calcoEnterpriseUI = true;
  const home = "/desk/calco-home";
  let started = false;
  function preference() { try { return localStorage.getItem(preferenceKey(frappe.session.user)); } catch (_) { return null; } }
  function switchMode(value) { try { localStorage.setItem(preferenceKey(frappe.session.user), value); } catch (_) { frappe.show_alert({message:__("Browser preferences are unavailable."),indicator:"orange"}); return; } location.reload(); }
  function el(tag, cls, text) { const n=document.createElement(tag); if(cls)n.className=cls; if(text)n.textContent=__(text); return n; }
  function button(label,cls,action) { const b=el("button",cls,label); b.type="button"; b.addEventListener("click",action); return b; }
  function routeLink(label,route,cls) { const a=el("a",cls,label); a.href=route; a.addEventListener("click",e=>{if(e.ctrlKey||e.metaKey||e.shiftKey)return;e.preventDefault();frappe.set_route(route);});return a; }
  function search() {
    if (frappe.searchdialog?.search?.show) { frappe.searchdialog.search.show(); return; }
    document.querySelector("#navbar-modal-search .item-anchor, .search-bar input")?.click();
  }
  function notifications(event) { event?.stopPropagation(); document.querySelector(".sidebar-notification .item-anchor, .dropdown-notifications .dropdown-toggle")?.click(); }
  function markActive() {
    const path=location.pathname.replace(/\/$/,"");
    document.querySelectorAll(".calco-ent-tabs a").forEach(a=>{const active=a.getAttribute("href")===path;a.classList.toggle("is-active",active);if(active)a.setAttribute("aria-current","page");else a.removeAttribute("aria-current");});
  }
  function start() {
    if(started || !window.frappe?.boot || !frappe.session)return;
    const roles=frappe.user_roles || [];
    if(!allowed(frappe.session.user,roles))return;
    started=true;
    if(!enabled(frappe.session.user,roles,preference())) {
      const restore=button("Enterprise View","btn btn-default btn-sm calco-enterprise-restore",()=>switchMode("on"));
      restore.title=__("Restore Calco Enterprise UI for this browser");document.body.append(restore);return;
    }
    document.documentElement.classList.add("calco-ent");
    const shell=el("div","calco-ent-shell");shell.setAttribute("aria-label",__("Calco Enterprise navigation"));
    const logo=el("img","ce-logo");logo.src="/assets/calco_erp/images/calco-logo-official.svg";logo.alt="Calco";
    shell.append(logo,routeLink("Calco PolyTechnik","/desk/calco-home","ce-name"),el("span","ce-product","Manufacturing ERP"),el("span","ce-grow"));
    const find=button("Search","ce-search",search);find.setAttribute("aria-label",__("Search ERP"));find.append(el("kbd",null,"Ctrl K"));shell.append(find);
    shell.append(button("Classic View","ce-text-btn",()=>switchMode("off")));
    if(frappe.boot.desk_settings?.notifications!==false) shell.append(button("Notifications","ce-text-btn",notifications));
    document.body.append(shell);
    const nav=el("nav","calco-ent-tabs");nav.setAttribute("aria-label",__("Accessible workspaces"));
    nav.append(routeLink("My Home",home,"ce-tab"));
    const host=document.querySelector(".main-section > header");(host || shell).append(nav);
    frappe.call({method:"calco_erp.enterprise_ui.home.get_navigation",callback:r=>{
      for(const workspace of r.message?.workspaces || []) {
        const slug=frappe.router.slug(workspace.name);nav.append(routeLink(workspace.title,"/desk/"+slug,"ce-tab"));
      }markActive();
    },error:()=>{nav.append(el("span","ce-navigation-error","Workspace navigation unavailable; use the sidebar."));}});
    frappe.router?.on("change",markActive);markActive();
  }
  function ready(tries=100) {if(window.frappe?.boot && document.querySelector(".main-section > header"))start();else if(tries>0)setTimeout(()=>ready(tries-1),100);}
  if(window.jQuery)jQuery(document).on("app_ready",()=>ready());
  if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",()=>ready());else ready();
})();
