(function () {
  if (window.__calcoBrandingInitialized) {
    return;
  }
  window.__calcoBrandingInitialized = true;

  const ERP_TITLE = "Calco PolyTechnik Pvt Ltd ERP";
  const LOGIN_TITLE = "Calco PolyTechnik Pvt Ltd Manufacturing ERP";
  const brandingScript =
    document.currentScript ||
    Array.from(document.scripts).find((script) => script.src.includes("/calco_branding.js"));
  const ASSET_VERSION = brandingScript
    ? new URL(brandingScript.src, window.location.href).searchParams.get("v")
    : "";
  const versionAsset = (path) =>
    ASSET_VERSION ? path + "?v=" + encodeURIComponent(ASSET_VERSION) : path;
  const LOGO_URL = versionAsset("/assets/calco_erp/images/calco-logo-official.svg");
  const FAVICON_URL = versionAsset("/assets/calco_erp/images/calco-polytechnik-favicon.svg");
  const HOME_BANNER_URL = versionAsset(
    "/assets/calco_erp/images/calco-polymer-pellets-banner.png"
  );
  const APP_VERSION = (ASSET_VERSION || "")
    .replace(/-[a-f0-9]{16}$/i, "")
    .replace(/rc(\d+)$/i, " RC$1");
  const AUTH_VIEW_SELECTOR = [
    ".for-login",
    ".for-email-login",
    ".for-forgot",
    ".for-login-with-email-link",
    ".for-signup",
  ].join(",");
  let scheduled = false;
  let observer;

  function isLoginPage() {
    return document.body?.classList.contains("for-login") || !!document.querySelector(".for-login");
  }

  function setFavicon() {
    let favicon = document.querySelector("link[rel='icon']");
    if (!favicon) {
      favicon = document.createElement("link");
      favicon.rel = "icon";
      document.head.appendChild(favicon);
    }
    if (favicon.href !== window.location.origin + FAVICON_URL) {
      favicon.href = FAVICON_URL;
      favicon.type = "image/svg+xml";
    }
  }

  function setDocumentTitle() {
    const isLogin = isLoginPage();
    const nextTitle = isLogin ? LOGIN_TITLE : ERP_TITLE;
    if (document.title !== nextTitle) {
      document.title = nextTitle;
    }
  }

  function createBrandPanel() {
    const panel = document.createElement("aside");
    panel.className = "calco-login-brand-panel";
    panel.setAttribute("aria-label", "Calco Manufacturing ERP");
    panel.innerHTML = `
      <img class="calco-login-logo" src="${LOGO_URL}" alt="Calco PolyTechnik Pvt Ltd">
      <h1 class="calco-login-company">Calco PolyTechnik Pvt Ltd</h1>
      <div class="calco-login-product">Manufacturing ERP</div>
      <p class="calco-login-tagline">Engineering Excellence. Digitally Controlled.</p>
      <p class="calco-login-capabilities">Manufacturing &bull; Quality &bull; Traceability</p>
    `;
    return panel;
  }

  function createLoginFooter() {
    const footer = document.createElement("div");
    footer.className = "calco-login-footer";
    footer.innerHTML = `
      <span>Calco PolyTechnik Pvt Ltd &middot; Authorized Access Only</span>
      <span>Secure connection &middot; HTTPS</span>
    `;
    return footer;
  }

  function enhanceLoginViews() {
    if (!isLoginPage()) return;
    document.body.classList.add("calco-login-branded");

    const authViews = Array.from(document.querySelectorAll(AUTH_VIEW_SELECTOR));
    authViews.forEach((view) => view.classList.remove("calco-auth-active"));
    const activeView = authViews.find((view) => window.getComputedStyle(view).display !== "none");
    activeView?.classList.add("calco-auth-active");

    authViews.forEach((view) => {
      const loginCard = view.querySelector(":scope > .login-content.page-card");
      if (!loginCard) return;

      view.classList.add("calco-auth-view");
      if (!view.querySelector(":scope > .calco-login-brand-panel")) {
        view.prepend(createBrandPanel());
      }

      let formPanel = view.querySelector(":scope > .calco-login-form-panel");
      if (!formPanel) {
        formPanel = document.createElement("div");
        formPanel.className = "calco-login-form-panel";
        loginCard.before(formPanel);
        formPanel.appendChild(loginCard);
        formPanel.appendChild(createLoginFooter());
      }
    });

    const primaryHead = document.querySelector(".for-login .page-card-head");
    if (!primaryHead) return;

    const heading = primaryHead.querySelector("h4");
    if (heading) heading.textContent = "Welcome back";

    let subtitle = primaryHead.querySelector(".page-card-subtitle, .calco-login-subtitle");
    if (!subtitle) {
      subtitle = document.createElement("p");
      primaryHead.appendChild(subtitle);
    }
    subtitle.classList.add("calco-login-subtitle");
    subtitle.textContent = "Sign in to Calco Manufacturing ERP";
  }

  function currentRoute() {
    if (window.frappe && typeof frappe.get_route === "function") {
      return (frappe.get_route() || []).map((part) => String(part || "").toLowerCase());
    }
    return [];
  }

  function isDeskHome() {
    if (isLoginPage()) return false;
    const route = currentRoute();
    const pathname = window.location.pathname.replace(/\/$/, "").toLowerCase();
    return (
      pathname === "/desk" ||
      pathname === "/desk/desktop" ||
      pathname === "/app/desktop" ||
      route[0] === "desktop"
    );
  }

  function decorateDeskHeader() {
    if (isLoginPage()) return;
    const desktopNavbar = document.querySelector(".desktop-wrapper .desktop-navbar");
    if (desktopNavbar) {
      const nativeLogo = desktopNavbar.querySelector("#brand-logo");
      if (nativeLogo) {
        nativeLogo.src = LOGO_URL;
        nativeLogo.alt = "Calco PolyTechnik Pvt Ltd";
      }

      const nativeHome = desktopNavbar.querySelector(".navbar-home");
      if (nativeHome && !nativeHome.querySelector(".calco-desk-brand-copy")) {
        nativeHome.querySelector(".calco-desk-brand-label")?.remove();
        const copy = document.createElement("span");
        copy.className = "calco-desk-brand-copy";
        nativeHome.appendChild(copy);
      }

      const copy = nativeHome?.querySelector(".calco-desk-brand-copy");
      const home = isDeskHome();
      if (copy && copy.dataset.calcoSurface !== (home ? "home" : "standard")) {
        copy.innerHTML = home
          ? `
              <strong class="calco-desk-brand-label">Manufacturing ERP</strong>
              <small>Engineering Excellence. Digitally Controlled.</small>
              <span class="calco-home-nav-capabilities">Manufacturing <i>&bull;</i> Quality <i>&bull;</i> Traceability</span>
              <span class="calco-home-nav-description">Integrated manufacturing, quality and supply-chain operations.</span>
            `
          : `
              <strong class="calco-desk-brand-label">Manufacturing ERP</strong>
              <small>Engineering Excellence. Digitally Controlled.</small>
            `;
        copy.dataset.calcoSurface = home ? "home" : "standard";
      }

      if (home) {
        desktopNavbar.classList.add("calco-home-unified-banner");
        let image = desktopNavbar.querySelector(":scope > .calco-home-navbar-image");
        if (!image) {
          image = document.createElement("img");
          image.className = "calco-home-navbar-image";
          image.src = HOME_BANNER_URL;
          image.alt = "";
          image.setAttribute("aria-hidden", "true");
          desktopNavbar.prepend(image);
        }

        if (!desktopNavbar.querySelector(":scope > .calco-home-navbar-values")) {
          const values = document.createElement("div");
          values.className = "calco-home-navbar-values";
          values.setAttribute("aria-label", "Precision, People, Progress");
          values.innerHTML = "<span>Precision</span><span>People</span><span>Progress</span><i></i>";
          desktopNavbar.appendChild(values);
        }
      } else {
        desktopNavbar.classList.remove("calco-home-unified-banner");
        desktopNavbar
          .querySelectorAll(":scope > .calco-home-navbar-image, :scope > .calco-home-navbar-values")
          .forEach((element) => element.remove());
      }
      return;
    }

    const navbar = document.querySelector(
      "header.navbar .container, header.navbar .container-fluid, .navbar .container"
    );
    if (!navbar || navbar.querySelector(":scope > .calco-desk-brand")) return;

    const brand = document.createElement("div");
    brand.className = "calco-desk-brand";
    brand.setAttribute("aria-label", "Calco Manufacturing ERP");
    brand.innerHTML = `
      <img src="${LOGO_URL}" alt="Calco PolyTechnik Pvt Ltd">
      <span class="calco-desk-brand-copy">
        <strong class="calco-desk-brand-label">Manufacturing ERP</strong>
        <small>Engineering Excellence. Digitally Controlled.</small>
      </span>
    `;
    navbar.prepend(brand);
  }

  function createHomeIdentity() {
    const identity = document.createElement("section");
    identity.className = "calco-home-intro";
    identity.innerHTML = `
      <div class="calco-home-workspaces-bar">
        <h2>Your Workspaces</h2>
      </div>
    `;
    return identity;
  }

  function createHomeFooter() {
    const footer = document.createElement("footer");
    footer.className = "calco-home-footer";
    footer.innerHTML = `
      <div class="calco-home-footer-left">
        <strong>Calco PolyTechnik Pvt Ltd</strong>
        <span>Authorized Access Only</span>
        <span class="calco-secure-connection">
          <svg class="icon icon-sm" aria-hidden="true"><use href="#icon-lock"></use></svg>
          Secure connection &middot; HTTPS
        </span>
      </div>
      <div class="calco-home-footer-right">
        ${APP_VERSION ? `<span>v${APP_VERSION}</span>` : ""}
        <strong>Engineering Tomorrow. Together.</strong>
      </div>
    `;
    return footer;
  }

  function createOperationalBrandbar() {
    const bar = document.createElement("div");
    bar.className = "calco-operational-brandbar";
    bar.setAttribute("aria-label", "Calco Manufacturing ERP");
    bar.innerHTML = `
      <img src="${LOGO_URL}" alt="Calco PolyTechnik Pvt Ltd">
      <span class="calco-desk-brand-copy">
        <strong class="calco-desk-brand-label">Manufacturing ERP</strong>
        <small>Engineering Excellence. Digitally Controlled.</small>
      </span>
    `;
    return bar;
  }

  function syncOperationalBrandbar() {
    if (isLoginPage() || isDeskHome()) {
      document.body?.classList.remove("calco-operational-branded");
      document.querySelectorAll(".calco-operational-brandbar").forEach((bar) => bar.remove());
      return;
    }

    const activePage = Array.from(document.querySelectorAll(".page-container"))
      .find((page) => page.offsetParent !== null);
    if (!activePage) return;

    document.body.classList.add("calco-operational-branded");
    const activeBar = activePage.querySelector(":scope > .calco-operational-brandbar");
    document.querySelectorAll(".calco-operational-brandbar").forEach((bar) => {
      if (bar !== activeBar) bar.remove();
    });
    if (!activeBar) {
      activePage.prepend(createOperationalBrandbar());
    }
  }

  function syncWorkspaceCards() {
    if (!isDeskHome()) {
      document
        .querySelectorAll(".calco-home-intro, .calco-home-footer")
        .forEach((element) => element.remove());
      document.body?.classList.remove("calco-desk-home");
      return;
    }

    const wrapper = document.querySelector(".desktop-wrapper");
    const desktopContainer = wrapper?.querySelector(":scope > .desktop-container");
    const icons = Array.from(
      desktopContainer?.querySelectorAll(":scope > .icons-container > .icons > .desktop-icon") || []
    );
    if (!wrapper || !desktopContainer || !icons.length) return;

    document.body.classList.add("calco-desk-home");
    wrapper.classList.add("calco-desktop-branded");
    let existing = wrapper.querySelector(":scope > .calco-home-intro");
    document.querySelectorAll(".calco-home-intro").forEach((element) => {
      if (element !== existing) element.remove();
    });
    if (!existing) {
      existing = createHomeIdentity();
      desktopContainer.before(existing);
    }

    let footer = wrapper.querySelector(":scope > .calco-home-footer");
    document.querySelectorAll(".calco-home-footer").forEach((element) => {
      if (element !== footer) element.remove();
    });
    if (!footer) {
      wrapper.appendChild(createHomeFooter());
    }
    wrapper.dataset.workspaceCount = String(icons.length);
  }


  function loadProductionConsumptionScript() {
    if (window.__productionConsumptionEntryLoaded) return;

    window.__productionConsumptionEntryLoaded = true;
    const script = document.createElement("script");
    script.src = "/assets/calco_erp/js/production_consumption_entry.js?v=20260625_pce_rm_link_validated";
    script.async = false;
    document.head.appendChild(script);
  }

  function scheduleProductionConsumptionScriptLoad() {
    loadProductionConsumptionScript();
    if (!window.__productionConsumptionEntryLoaded) {
      window.setTimeout(loadProductionConsumptionScript, 1000);
    }
  }
  function applyBranding() {
    scheduled = false;
    setFavicon();
    setDocumentTitle();
    enhanceLoginViews();
    decorateDeskHeader();
    syncWorkspaceCards();
    syncOperationalBrandbar();
    scheduleProductionConsumptionScriptLoad();

  }

  function scheduleApply() {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(applyBranding);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", scheduleApply, { once: true });
  } else {
    scheduleApply();
  }

  window.addEventListener("load", scheduleApply, { once: true });
  window.addEventListener("hashchange", scheduleApply);
  document.addEventListener("page-change", scheduleApply);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      scheduleApply();
    }
  });

  if (window.frappe && frappe.router && typeof frappe.router.on === "function") {
    frappe.router.on("change", scheduleApply);
  }

  if (document.body && window.MutationObserver) {
    observer = new MutationObserver(scheduleApply);
    observer.observe(document.body, { childList: true, subtree: true });
  }
})();
