(function () {
  // ---------- Modal helpers ----------
  function openModal(modalEl) {
    if (!modalEl) return;
    modalEl.classList.add("is-open");
    modalEl.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
  }

  function closeModal(modalEl) {
    if (!modalEl) return;
    modalEl.classList.remove("is-open");
    modalEl.setAttribute("aria-hidden", "true");
    document.body.style.overflow = "";
  }

  // Open/close modal by data-attributes
  document.addEventListener("click", (e) => {
    const openBtn = e.target.closest("[data-modal-open]");
    if (openBtn) {
      const id = openBtn.getAttribute("data-modal-open");
      openModal(document.getElementById(id));
      return;
    }

    const closeBtn = e.target.closest("[data-modal-close]");
    if (closeBtn) {
      const modal = e.target.closest(".modal");
      if (modal) closeModal(modal);
      return;
    }
  });

  // ESC closes any open modal
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    const opened = document.querySelector(".modal.is-open");
    if (opened) closeModal(opened);
  });

  // ---------- Terms checkbox enables register submit ----------
  const termsCheck = document.getElementById("termsCheck");
  const registerSubmit = document.getElementById("registerSubmit");
  if (termsCheck && registerSubmit) {
    const sync = () => {
      registerSubmit.disabled = !termsCheck.checked;
    };
    termsCheck.addEventListener("change", sync);
    sync();
  }

  // ---------- Sidebar toggle (mobile: overlay / desktop: collapse) ----------
  const sidebar = document.getElementById("sidebar");
  const backdrop = document.getElementById("backdrop");

  function openSidebarMobile() {
    if (!sidebar || !backdrop) return;
    sidebar.classList.add("is-open");
    backdrop.hidden = false;
    document.body.style.overflow = "hidden";
  }

  function closeSidebarMobile() {
    if (!sidebar || !backdrop) return;
    sidebar.classList.remove("is-open");
    backdrop.hidden = true;
    document.body.style.overflow = "";
  }

  function isMobileView() {
    return window.matchMedia("(max-width: 860px)").matches;
  }

  document.addEventListener("click", (e) => {
    const toggleBtn = e.target.closest("[data-sidebar-toggle]");
    if (toggleBtn) {
      if (isMobileView()) {
        // Mobile behavior: off-canvas + overlay
        if (sidebar && sidebar.classList.contains("is-open")) closeSidebarMobile();
        else openSidebarMobile();
      } else {
        // Desktop behavior: collapse/expand sidebar
        document.body.classList.toggle("sidebar-collapsed");

        // Ensure no conflict with mobile overlay state
        if (sidebar) sidebar.classList.remove("is-open");
        if (backdrop) backdrop.hidden = true;
        document.body.style.overflow = "";
      }
      return;
    }

    // Click on backdrop closes mobile sidebar
    if (backdrop && e.target === backdrop) {
      closeSidebarMobile();
      return;
    }
  });

  // ESC closes mobile sidebar if open
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (sidebar && sidebar.classList.contains("is-open")) closeSidebarMobile();
  });
})();

