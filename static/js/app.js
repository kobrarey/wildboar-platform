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

    // Step 2 elements
    const step1 = document.getElementById("registerStep1");
    const step2 = document.getElementById("registerStep2");
    const codeInput = document.getElementById("registerCode");
    const confirmBtn = document.getElementById("registerConfirmBtn");
    const confirmErr = document.getElementById("registerConfirmError");
    const codeHint = document.getElementById("registerCodeHint");

    let pendingEmail = ""; // email, который ждёт подтверждения

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

    function showStep1() {
      if (step1) step1.classList.remove("is-hidden");
      if (step2) step2.classList.add("is-hidden");
      pendingEmail = "";
      if (codeInput) codeInput.value = "";
      if (confirmBtn) confirmBtn.disabled = true;
      setError(confirmErr, "");
      setError(errorEl, "");
      if (codeHint) codeHint.textContent = "Мы отправили код на вашу почту.";
      refreshButton();
    }

    function showStep2(emailValue) {
      pendingEmail = (emailValue || "").trim();
      if (step1) step1.classList.add("is-hidden");
      if (step2) step2.classList.remove("is-hidden");
      if (codeHint && pendingEmail) codeHint.textContent = `Мы отправили код на почту: ${pendingEmail}`;
      if (codeInput) codeInput.focus();
      setError(confirmErr, "");
    }

    // live updates (step1)
    form.addEventListener("input", () => {
      setError(errorEl, "");
      refreshButton();
    });

    terms?.addEventListener("change", () => {
      setError(errorEl, "");
      refreshButton();
    });

    // intercept submit => /register returns JSON now
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

        // ожидаем JSON
        const ct = (resp.headers.get("content-type") || "").toLowerCase();
        let payload = null;
        if (ct.includes("application/json")) {
          payload = await resp.json().catch(() => null);
        } else {
          const txt = await resp.text().catch(() => "");
          payload = txt;
        }

        if (!resp.ok) {
          const msg =
            (typeof payload === "string" && payload) ||
            payload?.message ||
            payload?.detail ||
            "Ошибка регистрации.";
          setError(errorEl, msg);
          return;
        }

        // Успех: {"status":"ok","next":"enter_code","email":...}
        const next = payload?.next;
        const em = payload?.email || (email?.value || "").trim();

        if (payload?.status === "ok" && next === "enter_code") {
          showStep2(em);
          return;
        }

        // fallback (на случай если бэк когда-то вернёт redirect)
        const redirect = payload?.redirect || payload?.next_url;
        if (redirect) {
          window.location.href = redirect;
          return;
        }

        // если ничего не пришло — просто покажем step2
        showStep2(em);

      } catch {
        setError(errorEl, "Сетевая ошибка. Повторите попытку.");
      }
    });

    // step2: enable confirm button only for 6-8 digits (под код)
    function refreshConfirm() {
      const v = (codeInput?.value || "").trim();
      const ok = /^\d{6,8}$/.test(v);
      if (confirmBtn) confirmBtn.disabled = !ok;
      setError(confirmErr, "");
    }

    codeInput?.addEventListener("input", refreshConfirm);

    confirmBtn?.addEventListener("click", async () => {
      setError(confirmErr, "");
      const code = (codeInput?.value || "").trim();
      if (!/^\d{6,8}$/.test(code)) {
        setError(confirmErr, "Введите код (6–8 цифр).");
        return;
      }
      const em = pendingEmail || (email?.value || "").trim();
      if (!em) {
        setError(confirmErr, "Не найден email для подтверждения.");
        return;
      }

      try {
        const resp = await fetch("/register/confirm", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email: em, code }),
          credentials: "same-origin",
        });

        const ct = (resp.headers.get("content-type") || "").toLowerCase();
        const payload = ct.includes("application/json")
          ? await resp.json().catch(() => null)
          : await resp.text().catch(() => "");

        if (!resp.ok) {
          const msg =
            (typeof payload === "string" && payload) ||
            payload?.message ||
            payload?.detail ||
            "Неверный код.";
          setError(confirmErr, msg);
          return;
        }

        // success: {"status":"ok","redirect":"/dashboard"} + cookie
        const redirect = payload?.redirect || "/dashboard";
        window.location.href = redirect;

      } catch {
        setError(confirmErr, "Сетевая ошибка. Повторите попытку.");
      }
    });

    // при открытии модалки регистрации всегда возвращаем на шаг 1
    // (это важно, чтобы после закрытия/открытия не оставаться на шаге кода)
    const registerModal = document.getElementById("registerModal");
    if (registerModal) {
      const obs = new MutationObserver(() => {
        if (registerModal.classList.contains("is-open")) showStep1();
      });
      obs.observe(registerModal, { attributes: true, attributeFilter: ["class"] });
    }

    // init
    showStep1();
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

        // Успех может быть redirect, или JSON с redirect
        if (resp.redirected) {
          window.location.href = resp.url;
          return;
        }

        const ct = (resp.headers.get("content-type") || "").toLowerCase();
        const payload = ct.includes("application/json")
          ? await resp.json().catch(() => null)
          : await resp.text().catch(() => "");

        if (resp.ok) {
          const redirect = payload?.redirect || "/dashboard";
          window.location.href = redirect;
          return;
        }

        // 400: "Email не подтверждён..."
        // 401: invalid credentials
        const msg =
          (typeof payload === "string" && payload) ||
          payload?.message ||
          payload?.detail ||
          `Ошибка входа (HTTP ${resp.status}).`;

        setError(errorEl, msg);

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

  // отключаем любые обработчики маски телефона (если были)
  const ph = document.getElementById("regPhone");
  if (ph) { ph.oninput = null; ph.onkeydown = null; ph.onkeyup = null; }
})();


