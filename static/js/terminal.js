(() => {
  const lang = (document.documentElement.lang || "ru").toLowerCase();
  const L = (ru, en) => (lang === "en" ? en : ru);

  const qs = (s, r = document) => r.querySelector(s);
  const qsa = (s, r = document) => Array.from(r.querySelectorAll(s));

  // ---------------- theme toggle ----------------
  function initTheme() {
    const body = document.body;
    const btn = qs("#termThemeBtn");
    if (!btn) return;

    // keep it simple + reusable for other pages later
    const KEY = "wb_theme";
    const saved = localStorage.getItem(KEY);
    if (saved === "dark" || saved === "light") {
      body.dataset.theme = saved;
    }

    function render() {
      const isDark = (body.dataset.theme || "light") === "dark";
      // requirement: light => moon, dark => sun
      btn.textContent = isDark ? "☀️" : "🌙";
    }

    btn.addEventListener("click", () => {
      const cur = body.dataset.theme || "light";
      const next = cur === "dark" ? "light" : "dark";
      body.dataset.theme = next;
      localStorage.setItem(KEY, next);
      render();
    });

    render();
  }

  // ---------------- drawer (fund menu) ----------------
  function initDrawer() {
    const drawer = qs("#termDrawer");
    const overlay = qs("#termDrawerOverlay");
    const burger = qs("#termBurger");
    const search = qs("#fundSearch");
    const list = qs("#fundList");
    if (!drawer || !overlay || !burger || !list) return;

    const rows = qsa(".term-drawer__row", list);

    function open() {
      drawer.classList.add("is-open");
      drawer.setAttribute("aria-hidden", "false");
      document.body.style.overflow = "hidden";
      setTimeout(() => search && search.focus(), 50);
    }

    function close() {
      drawer.classList.remove("is-open");
      drawer.setAttribute("aria-hidden", "true");
      document.body.style.overflow = "";
    }

    burger.addEventListener("click", open);
    overlay.addEventListener("click", close);

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && drawer.classList.contains("is-open")) close();
    });

    if (search) {
      search.addEventListener("input", () => {
        const q = (search.value || "").trim().toLowerCase();
        rows.forEach((r) => {
          const name = (r.dataset.name || "").toLowerCase();
          r.style.display = !q || name.includes(q) ? "" : "none";
        });
      });
    }
  }

  // ---------------- bottom tabs (history / assets) ----------------
  function initBottomTabs() {
    const btns = qsa("[data-term-bottom]");
    const panels = qsa("[data-term-panel]");
    if (!btns.length || !panels.length) return;

    function set(name) {
      btns.forEach((b) => b.classList.toggle("is-active", b.dataset.termBottom === name));
      panels.forEach((p) => p.classList.toggle("hidden", p.dataset.termPanel !== name));
    }

    btns.forEach((b) => b.addEventListener("click", () => set(b.dataset.termBottom)));
    set("history");
  }

  // ---------------- buy/redeem tabs + validation (no execution) ----------------
  function parseNum(s) {
    const t = String(s || "").trim().replace(",", ".");
    if (!t) return null;
    if (!/^\d+(\.\d+)?$/.test(t)) return null;
    const n = Number(t);
    return Number.isFinite(n) ? n : null;
  }

  function decimalsOk(raw, maxDecimals) {
    const v = String(raw || "").trim().replace(",", ".");
    const parts = v.split(".");
    return !(parts[1] && parts[1].length > maxDecimals);
  }

  function initOrderPanel() {
    const wrap = qs(".term-order");
    if (!wrap) return;

    const availUSDT = Number(wrap.dataset.availUsdt || 0);
    const availShares = Number(wrap.dataset.availShares || 0);

    const tabBuy = qs('[data-term-side="buy"]');
    const tabRed = qs('[data-term-side="redeem"]');
    const pBuy = qs('[data-term-side-panel="buy"]');
    const pRed = qs('[data-term-side-panel="redeem"]');

    const buyIn = qs("#buyAmount");
    const buyBtn = qs("#buyBtn");
    const buyErr = qs("#buyError");
    const buyOk = qs("#buyOk");

    const redIn = qs("#redeemAmount");
    const redBtn = qs("#redeemBtn");
    const redErr = qs("#redeemError");
    const redOk = qs("#redeemOk");

    function resetMsgs() {
      if (buyErr) buyErr.textContent = "";
      if (buyOk) buyOk.textContent = "";
      if (redErr) redErr.textContent = "";
      if (redOk) redOk.textContent = "";
    }

    function setSide(name) {
      tabBuy && tabBuy.classList.toggle("is-active", name === "buy");
      tabRed && tabRed.classList.toggle("is-active", name === "redeem");
      pBuy && pBuy.classList.toggle("hidden", name !== "buy");
      pRed && pRed.classList.toggle("hidden", name !== "redeem");
      resetMsgs();
    }

    tabBuy && tabBuy.addEventListener("click", () => setSide("buy"));
    tabRed && tabRed.addEventListener("click", () => setSide("redeem"));
    setSide("buy");

    function validateBuy() {
      if (!buyIn) return false;
      const raw = buyIn.value;
      const n = parseNum(raw);

      if (buyErr) buyErr.textContent = "";
      if (buyOk) buyOk.textContent = "";

      if (n === null) return false;
      if (!decimalsOk(raw, 2)) return false;
      if (n <= 0) return false;

      if (n > availUSDT) {
        if (buyErr) buyErr.textContent = L("Сумма больше доступного баланса", "Amount exceeds available balance");
        return false;
      }
      return true;
    }

    function validateRedeem() {
      if (!redIn) return false;
      const raw = redIn.value;
      const n = parseNum(raw);

      if (redErr) redErr.textContent = "";
      if (redOk) redOk.textContent = "";

      if (n === null) return false;
      if (!decimalsOk(raw, 4)) return false;
      if (n <= 0) return false;

      if (n > availShares) {
        if (redErr) redErr.textContent = L("Количество больше доступного баланса", "Quantity exceeds available balance");
        return false;
      }
      return true;
    }

    buyIn && buyIn.addEventListener("input", () => {
      if (buyBtn) buyBtn.disabled = !validateBuy();
    });

    redIn && redIn.addEventListener("input", () => {
      if (redBtn) redBtn.disabled = !validateRedeem();
    });

    if (buyBtn) {
      buyBtn.disabled = true;
      buyBtn.addEventListener("click", () => {
        if (!validateBuy()) return;
        if (buyOk) buyOk.textContent = L("Форма подготовлена (исполнения в этом этапе нет).", "Form is ready (no execution in this stage).");
      });
    }

    if (redBtn) {
      redBtn.disabled = true;
      redBtn.addEventListener("click", () => {
        if (!validateRedeem()) return;
        if (redOk) redOk.textContent = L("Форма подготовлена (исполнения в этом этапе нет).", "Form is ready (no execution in this stage).");
      });
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    initTheme();
    initDrawer();
    initBottomTabs();
    initOrderPanel();
  });
})();
