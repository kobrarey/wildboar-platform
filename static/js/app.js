(function () {
  // ---------- helpers ----------
  function qs(sel, root = document) { return root.querySelector(sel); }
  function qsa(sel, root = document) { return Array.from(root.querySelectorAll(sel)); }
  function setError(el, text) { if (el) el.textContent = text || ""; }

  const _resendTimers = new WeakMap();

  function startResendCooldown(btn, seconds = 60, baseText = "Отправить код ещё раз") {
    if (!btn) return;

    // сброс старого таймера
    const prev = _resendTimers.get(btn);
    if (prev?.intervalId) clearInterval(prev.intervalId);
    if (prev?.timeoutId) clearTimeout(prev.timeoutId);

    let left = seconds;
    btn.disabled = true;
    btn.textContent = `Повторно через ${left}с`;

    const endAt = Date.now() + seconds * 1000;

    function finish() {
      btn.disabled = false;
      btn.textContent = baseText;
      _resendTimers.delete(btn);
    }

    const intervalId = setInterval(() => {
      const diffMs = endAt - Date.now();
      left = Math.max(0, Math.round(diffMs / 1000));
      if (left <= 0) {
        clearInterval(intervalId);
        finish();
        return;
      }
      btn.textContent = `Повторно через ${left}с`;
    }, 1000);

    // страховочный таймер, если интервал притормозят в фоне
    const timeoutId = setTimeout(() => {
      clearInterval(intervalId);
      finish();
    }, seconds * 1000 + 500); // небольшой запас

    _resendTimers.set(btn, { intervalId, timeoutId });
  }

  async function postResend(endpoint, email, errEl, btn) {
    if (!email) {
      setError(errEl, "Не найден email.");
      return;
    }

    // по ТЗ: после клика дизейблим на 60 сек сразу
    startResendCooldown(btn, 60, "Отправить код ещё раз");

    try {
      const resp = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
        credentials: "same-origin",
      });

      if (resp.ok) return;

      // если 400 — показать текст и тоже оставить cooldown (он уже включён)
      const txt = await resp.text().catch(() => "");
      setError(errEl, txt || `Ошибка (HTTP ${resp.status}).`);

      // UX-совпадение: при 400 тоже гарантируем 60 сек (мы уже запустили)
      return;

    } catch {
      setError(errEl, "Сетевая ошибка. Повторите попытку.");
      // cooldown оставляем, чтобы не спамили
    }
  }

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
    const resendBtn = document.getElementById("registerResendBtn");

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

    // step2: enable confirm button only for 6 digits (под код)
    function refreshConfirm() {
      const v = (codeInput?.value || "").trim();
      const ok = /^\d{6}$/.test(v);
      if (confirmBtn) confirmBtn.disabled = !ok;
      setError(confirmErr, "");
    }

    codeInput?.addEventListener("input", refreshConfirm);

    resendBtn?.addEventListener("click", () => {
      setError(confirmErr, "");
      const em = pendingEmail || (email?.value || "").trim();
      postResend("/register/resend-code", em, confirmErr, resendBtn);
    });

    confirmBtn?.addEventListener("click", async () => {
      setError(confirmErr, "");
      const code = (codeInput?.value || "").trim();
      if (!/^\d{6}$/.test(code)) {
        setError(confirmErr, "Введите 6-значный код.");
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

    // Step 1 elements (inside form)
    const step1 = document.getElementById("loginStep1");
    const errorEl = document.getElementById("loginError");

    // Inputs inside Step 1
    const emailInput = qs('input[name="email"]', form);
    const passInput  = qs('input[name="password"]', form);

    // Step 2 elements (2FA)
    const step2 = document.getElementById("loginStep2");
    const hintEl = document.getElementById("login2faHint");
    const codeInput = document.getElementById("login2faCode");
    const confirmBtn = document.getElementById("login2faConfirmBtn");
    const confirmErr = document.getElementById("login2faError");
    const resendBtn = document.getElementById("login2faResendBtn");

    let pendingEmail = "";

    function showStep1() {
      if (step1) step1.classList.remove("is-hidden");
      if (step2) step2.classList.add("is-hidden");
      pendingEmail = "";
      setError(errorEl, "");
      setError(confirmErr, "");
      if (codeInput) codeInput.value = "";
      if (confirmBtn) confirmBtn.disabled = true;
    }

    function showStep2(email) {
      pendingEmail = (email || "").trim();
      if (step1) step1.classList.add("is-hidden");
      if (step2) step2.classList.remove("is-hidden");
      setError(confirmErr, "");
      // Подсказка уже задана в шаблоне по lang, не перезаписываем
      if (codeInput) codeInput.focus();
      refreshConfirm();
    }

    function refreshConfirm() {
      const v = (codeInput?.value || "").trim();
      const ok = /^\d{6}$/.test(v);
      if (confirmBtn) confirmBtn.disabled = !ok;
    }

    function payloadToMessage(payload, fallback) {
      if (!payload) return fallback;
      if (typeof payload === "string") return payload || fallback;
      return payload.message || payload.detail || fallback;
    }

    // очистка ошибок на ввод
    form.addEventListener("input", () => setError(errorEl, ""));
    codeInput?.addEventListener("input", () => {
      setError(confirmErr, "");
      refreshConfirm();
    });

    resendBtn?.addEventListener("click", () => {
      setError(confirmErr, "");
      const em = pendingEmail || (emailInput?.value || "").trim();
      postResend("/login/2fa/resend", em, confirmErr, resendBtn);
    });

    // Step 1 submit: /login
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      setError(errorEl, "");

      const email = (emailInput?.value || "").trim();
      const pass  = (passInput?.value || "").trim();
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

        // Если вдруг бэк вернул redirect (на будущее)
        if (resp.redirected) {
          window.location.href = resp.url;
          return;
        }

        const ct = (resp.headers.get("content-type") || "").toLowerCase();
        const payload = ct.includes("application/json")
          ? await resp.json().catch(() => null)
          : await resp.text().catch(() => "");

        if (!resp.ok) {
          // 400/401: показать текст/сообщение в модалке
          setError(errorEl, payloadToMessage(payload, `Ошибка входа (HTTP ${resp.status}).`));
          return;
        }

        // OK: возможны варианты
        // 1) {status:"ok", redirect:"/dashboard"}
        if (payload?.status === "ok" && payload?.redirect) {
          window.location.href = payload.redirect;
          return;
        }

        // 2) {status:"2fa_required"}
        if (payload?.status === "2fa_required") {
          showStep2(email);
          return;
        }

        // fallback
        setError(errorEl, "Некорректный ответ сервера.");
      } catch {
        setError(errorEl, "Сетевая ошибка. Повторите попытку.");
      }
    });

    // Step 2 confirm: /login/2fa
    confirmBtn?.addEventListener("click", async () => {
      setError(confirmErr, "");

      const email = pendingEmail || (emailInput?.value || "").trim();
      const code = (codeInput?.value || "").trim();

      if (!email) { setError(confirmErr, "Не найден email для подтверждения."); return; }
      if (!/^\d{6}$/.test(code)) { setError(confirmErr, "Введите 6-значный код."); return; }

      try {
        const resp = await fetch("/login/2fa", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email, code }),
          credentials: "same-origin",
        });

        const ct = (resp.headers.get("content-type") || "").toLowerCase();
        const payload = ct.includes("application/json")
          ? await resp.json().catch(() => null)
          : await resp.text().catch(() => "");

        if (!resp.ok) {
          // 400: текст
          setError(confirmErr, payloadToMessage(payload, `Ошибка (HTTP ${resp.status}).`));
          return;
        }

        if (payload?.status === "ok" && payload?.redirect) {
          window.location.href = payload.redirect;
          return;
        }

        setError(confirmErr, "Некорректный ответ сервера.");
      } catch {
        setError(confirmErr, "Сетевая ошибка. Повторите попытку.");
      }
    });

    // reset to step1 when modal is opened
    const loginModal = document.getElementById("loginModal");
    if (loginModal) {
      const obs = new MutationObserver(() => {
        if (loginModal.classList.contains("is-open")) showStep1();
      });
      obs.observe(loginModal, { attributes: true, attributeFilter: ["class"] });
    }

    showStep1();
  }

  // ---------- forgot password ----------
  function initForgotPage() {
    const emailEl = document.getElementById("forgotEmail");
    const sendBtn = document.getElementById("forgotSendBtn");
    const infoEl  = document.getElementById("forgotInfo");
    const codeEl  = document.getElementById("forgotCode");
    const verifyBtn = document.getElementById("forgotVerifyBtn");
    const errEl = document.getElementById("forgotError");
    const resendBtn = document.getElementById("forgotResendBtn");

    if (!emailEl || !sendBtn || !codeEl || !verifyBtn) return;

    const lang = (document.documentElement.getAttribute("lang") || "ru").toLowerCase();
    const isEn = lang === "en";
    const MSG = {
      enter_email: isEn ? "Enter email." : "Введите email.",
      enter_6digit: isEn ? "Enter 6-digit code." : "Введите 6-значный код.",
      error_http: (s) => (isEn ? `Error (HTTP ${s}).` : `Ошибка (HTTP ${s}).`),
      network: isEn ? "Network error. Please try again." : "Сетевая ошибка. Повторите попытку.",
      code_sent: isEn ? "If this email exists, a code has been sent." : "Если такой email существует, код отправлен на почту.",
      invalid_response: isEn ? "Invalid server response." : "Некорректный ответ сервера.",
    };

    function refreshVerify() {
      const email = (emailEl.value || "").trim();
      const code = (codeEl.value || "").trim();
      const ok = email.length > 0 && /^\d{6}$/.test(code);
      verifyBtn.disabled = !ok;
      setError(errEl, "");
    }

    emailEl.addEventListener("input", refreshVerify);
    codeEl.addEventListener("input", refreshVerify);

    sendBtn.addEventListener("click", async () => {
      setError(errEl, "");
      if (infoEl) infoEl.textContent = "";

      const email = (emailEl.value || "").trim();
      if (!email) {
        setError(errEl, MSG.enter_email);
        return;
      }

      try {
        const resp = await fetch("/forgot/send-code", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email }),
          credentials: "same-origin",
        });

        // По контракту: всегда 200 {"status":"ok"} (даже если email не существует)
        if (resp.ok) {
          if (infoEl) infoEl.textContent = MSG.code_sent;
          return;
        }

        // если всё же вернули ошибку — покажем текст
        const txt = await resp.text().catch(() => "");
        setError(errEl, txt || MSG.error_http(resp.status));
      } catch {
        setError(errEl, MSG.network);
      }
    });

    resendBtn?.addEventListener("click", async () => {
      setError(errEl, "");
      if (infoEl) infoEl.textContent = "";
      const email = (emailEl.value || "").trim();

      await postResend("/forgot/send-code", email, errEl, resendBtn);

      // на успех покажем нейтральное сообщение (как по контракту)
      if (infoEl && !errEl.textContent) {
        infoEl.textContent = MSG.code_sent;
      }
    });

    verifyBtn.addEventListener("click", async () => {
      setError(errEl, "");
      if (infoEl) infoEl.textContent = "";

      const email = (emailEl.value || "").trim();
      const code = (codeEl.value || "").trim();

      if (!email) { setError(errEl, MSG.enter_email); return; }
      if (!/^\d{6}$/.test(code)) { setError(errEl, MSG.enter_6digit); return; }

      try {
        const resp = await fetch("/forgot/verify", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email, code }),
          credentials: "same-origin",
        });

        if (resp.ok) {
          const data = await resp.json().catch(() => null);
          const redirect = data?.redirect;
          if (data?.status === "ok" && redirect) {
            window.location.href = redirect;
            return;
          }
          setError(errEl, MSG.invalid_response);
          return;
        }

        // ошибки: HTTP 400 текстом
        if (resp.status === 400) {
          setError(errEl, await resp.text());
          return;
        }

        const txt = await resp.text().catch(() => "");
        setError(errEl, txt || MSG.error_http(resp.status));
      } catch {
        setError(errEl, MSG.network);
      }
    });

    refreshVerify();
  }

  // ---------- forgot new password ----------
  function initForgotNewPasswordPage() {
    const tokenEl = document.getElementById("forgotToken");
    const passEl = document.getElementById("newPassword");
    const pass2El = document.getElementById("newPasswordConfirm");
    const rulesList = document.getElementById("newPasswordRules");
    const btn = document.getElementById("forgotNewPassBtn");
    const errEl = document.getElementById("forgotNewPassError");

    if (!passEl || !pass2El || !btn || !errEl) return;

    const lang = (document.documentElement.getAttribute("lang") || "ru").toLowerCase();
    const isEn = lang === "en";
    const MSG_NP = {
      invalid_link: isEn ? "Invalid link (token missing)." : "Некорректная ссылка (token отсутствует).",
      password_req: isEn ? "Password does not meet requirements." : "Пароль не соответствует требованиям.",
      passwords_match: isEn ? "Passwords do not match." : "Пароли не совпадают.",
      error_http: (s) => (isEn ? `Error (HTTP ${s}).` : `Ошибка (HTTP ${s}).`),
      network: isEn ? "Network error. Please try again." : "Сетевая ошибка. Повторите попытку.",
    };

    function getToken() {
      const hidden = (tokenEl?.value || "").trim();
      if (hidden) return hidden;
      const urlToken = new URLSearchParams(window.location.search).get("token");
      return (urlToken || "").trim();
    }

    function validate() {
      const token = getToken();
      const p1 = (passEl.value || "");
      const p2 = (pass2El.value || "");

      const state = getPasswordRules(p1);
      updateRulesUI(rulesList, state);

      const ok =
        token.length > 0 &&
        allRulesOk(state) &&
        p2.length > 0 &&
        p1 === p2;

      return { ok, token, p1, p2 };
    }

    function refresh() {
      const v = validate();
      btn.disabled = !v.ok;
      setError(errEl, "");
    }

    passEl.addEventListener("input", refresh);
    pass2El.addEventListener("input", refresh);

    btn.addEventListener("click", async () => {
      setError(errEl, "");

      const v = validate();
      if (!v.token) { setError(errEl, MSG_NP.invalid_link); return; }
      if (!allRulesOk(getPasswordRules(v.p1))) { setError(errEl, MSG_NP.password_req); return; }
      if (v.p1 !== v.p2) { setError(errEl, MSG_NP.passwords_match); return; }

      try {
        const resp = await fetch("/forgot/new-password", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token: v.token, password: v.p1, password_confirm: v.p2 }),
          credentials: "same-origin",
        });

        if (resp.ok) {
          const data = await resp.json().catch(() => null);
          const redirect = data?.redirect || "/";
          window.location.href = redirect;
          return;
        }

        if (resp.status === 400) {
          setError(errEl, await resp.text());
          return;
        }

        const txt = await resp.text().catch(() => "");
        setError(errEl, txt || MSG_NP.error_http(resp.status));
      } catch {
        setError(errEl, MSG_NP.network);
      }
    });

    refresh();
  }

  // ---------- language switcher ----------
  function initLanguageSwitcher() {
    const switchers = document.querySelectorAll(".lang-switcher");
    if (!switchers.length) return;

    function closeAll() {
      switchers.forEach((sw) => {
        const dd = sw.querySelector(".lang-dropdown");
        if (dd) dd.classList.add("hidden");
      });
    }

    // закрывать при клике вне
    document.addEventListener("click", (e) => {
      const inside = e.target.closest(".lang-switcher");
      if (!inside) closeAll();
    });

    switchers.forEach((sw) => {
      const btn = sw.querySelector("[data-lang-toggle]");
      const dropdown = sw.querySelector(".lang-dropdown");
      if (!btn || !dropdown) return;

      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        dropdown.classList.toggle("hidden");
      });

      dropdown.querySelectorAll("button[data-lang]").forEach((item) => {
        item.addEventListener("click", async () => {
          const lang = item.dataset.lang;
          try {
            const resp = await fetch("/set-language", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ lang }),
              credentials: "same-origin",
            });
            if (resp.ok) window.location.reload();
          } catch (e) {
            console.error(e);
          }
        });
      });
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

  // ---------- sidebar links ----------
  function initSidebarLinks() {
    document.querySelectorAll(".sidebar-item[data-href]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const href = btn.dataset.href;
        if (href) window.location.href = href;
      });
    });
  }

  // ---------- init ----------
  document.addEventListener("DOMContentLoaded", () => {
    initLanguageSwitcher();
    initSidebarLinks();
    initRegistration();
    initLogin();
    initForgotPage();
    initForgotNewPasswordPage();

    // отключаем любые обработчики маски телефона (если были)
    const ph = document.getElementById("regPhone");
    if (ph) { ph.oninput = null; ph.onkeydown = null; ph.onkeyup = null; }
  });
})();


