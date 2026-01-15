(function () {
  // ---------- helpers ----------
  function qs(sel, root = document) { return root.querySelector(sel); }
  function qsa(sel, root = document) { return Array.from(root.querySelectorAll(sel)); }
  function setError(el, text) { if (el) el.textContent = text || ""; }

  // ---------- modal helpers ----------
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

  // ---------- password rules ----------
  const RULE_META = {
    len: "Длина ≥ 8",
    digit: "Минимум 1 цифра",
    lower: "Минимум 1 строчная буква",
    upper: "Минимум 1 заглавная буква",
    special: "Минимум 1 спецсимвол",
    nospace: "Без пробелов/табов/переводов строки",
  };

  function getPasswordRules(pw) {
    const s = pw || "";
    return {
      len: s.length >= 8,
      digit: /\d/.test(s),
      lower: /[a-z]/.test(s),
      upper: /[A-Z]/.test(s),
      special: /[^A-Za-z0-9]/.test(s),
      nospace: !/\s/.test(s),
    };
  }

  function updateRulesUI(rulesListEl, state) {
    if (!rulesListEl) return;
    qsa("[data-rule]", rulesListEl).forEach((li) => {
      const key = li.getAttribute("data-rule");
      const ok = !!state[key];
      li.classList.toggle("rule-ok", ok);
      li.classList.toggle("rule-bad", !ok);
    });
  }

  function allRulesOk(state) {
    return Object.values(state).every(Boolean);
  }

  // ---------- registration ----------
  function initRegistration() {
    const form = document.getElementById("registerForm");
    if (!form) return;

    const firstName = qs('input[name="first_name"]', form);
    const lastName  = qs('input[name="last_name"]', form);
    const email     = qs('input[name="email"]', form);
    const password  = document.getElementById("registerPassword") || qs('input[name="password"]', form);

    const rulesList = document.getElementById("passwordRules");
    const terms     = document.getElementById("termsCheck");
    const submitBtn = document.getElementById("registerSubmit");
    const errorEl   = document.getElementById("registerError");

    function requiredOk() {
      const a = (firstName?.value || "").trim();
      const b = (lastName?.value || "").trim();
      const c = (email?.value || "").trim();
      const d = (password?.value || "").trim();
      return a.length > 0 && b.length > 0 && c.length > 0 && d.length > 0;
    }

    function validate() {
      const state = getPasswordRules(password?.value || "");
      updateRulesUI(rulesList, state);

      const ok = !!terms?.checked && requiredOk() && allRulesOk(state);
      const missingRules = Object.keys(state).filter((k) => !state[k]);
      return { ok, missingRules };
    }

    function refreshButton() {
      const v = validate();
      if (submitBtn) submitBtn.disabled = !v.ok;
    }

    form.addEventListener("input", () => {
      setError(errorEl, "");
      refreshButton();
    });

    terms?.addEventListener("change", () => {
      setError(errorEl, "");
      refreshButton();
    });

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      setError(errorEl, "");

      const v = validate();
      refreshButton();

      if (!v.ok) {
        const parts = [];
        if (!requiredOk()) parts.push("Заполните обязательные поля.");
        if (!terms?.checked) parts.push("Подтвердите принятие условий.");
        if (v.missingRules.length) {
          parts.push("Не выполнены условия пароля: " + v.missingRules.map(k => RULE_META[k]).join(", "));
        }
        setError(errorEl, parts.join(" "));
        return;
      }

      try {
        const resp = await fetch("/register", {
          method: "POST",
          body: new FormData(form),
          credentials: "same-origin",
        });

        if (resp.redirected) {
          window.location.href = resp.url;
          return;
        }
        if (resp.ok) {
          window.location.href = "/dashboard";
          return;
        }
        if (resp.status === 400) {
          setError(errorEl, await resp.text());
          return;
        }
        setError(errorEl, (await resp.text().catch(() => "")) || `Ошибка регистрации (HTTP ${resp.status}).`);
      } catch {
        setError(errorEl, "Сетевая ошибка. Повторите попытку.");
      }
    });

    refreshButton();
  }

  // ---------- login ----------
  function initLogin() {
    const form = document.getElementById("loginForm");
    if (!form) return;

    const errorEl = document.getElementById("loginError");
    form.addEventListener("input", () => setError(errorEl, ""));

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      setError(errorEl, "");

      const email = (qs('input[name="email"]', form)?.value || "").trim();
      const pass  = (qs('input[name="password"]', form)?.value || "").trim();
      if (!email || !pass) {
        setError(errorEl, "Введите email и пароль.");
        return;
      }

      try {
        const resp = await fetch("/login", {
          method: "POST",
          body: new FormData(form),
          credentials: "same-origin",
        });

        if (resp.redirected) {
          window.location.href = resp.url;
          return;
        }
        if (resp.ok) {
          window.location.href = "/dashboard";
          return;
        }
        if (resp.status === 401) {
          setError(errorEl, await resp.text());
          return;
        }
        setError(errorEl, (await resp.text().catch(() => "")) || `Ошибка входа (HTTP ${resp.status}).`);
      } catch {
        setError(errorEl, "Сетевая ошибка. Повторите попытку.");
      }
    });
  }

  // ---------- modal open/close (ВОЗВРАЩАЕМ, это и было потеряно) ----------
  document.addEventListener("click", (e) => {
    const openBtn = e.target.closest("[data-modal-open]");
    if (openBtn) {
      const id = openBtn.getAttribute("data-modal-open");
      const modal = document.getElementById(id);
      openModal(modal);

      if (id === "registerModal") setError(document.getElementById("registerError"), "");
      if (id === "loginModal") setError(document.getElementById("loginError"), "");
      return;
    }

    const closeBtn = e.target.closest("[data-modal-close]");
    if (closeBtn) {
      const modal = e.target.closest(".modal");
      if (modal) closeModal(modal);
      return;
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    const opened = document.querySelector(".modal.is-open");
    if (opened) closeModal(opened);
  });

  // ---------- sidebar (если на странице есть /dashboard) ----------
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
    if (!toggleBtn) return;

    if (isMobileView()) {
      if (sidebar && sidebar.classList.contains("is-open")) closeSidebarMobile();
      else openSidebarMobile();
    } else {
      document.body.classList.toggle("sidebar-collapsed");
      if (sidebar) sidebar.classList.remove("is-open");
      if (backdrop) backdrop.hidden = true;
      document.body.style.overflow = "";
    }
  });

  document.addEventListener("click", (e) => {
    if (backdrop && e.target === backdrop) closeSidebarMobile();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && sidebar && sidebar.classList.contains("is-open")) closeSidebarMobile();
  });

  // ---------- init ----------
  initRegistration();
  initLogin();
})();


