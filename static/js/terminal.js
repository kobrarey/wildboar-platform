(() => {
  const lang = (document.documentElement.lang || "en").toLowerCase();
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
      const isDark = (body.dataset.theme || "dark") === "dark";
      // requirement: light => moon, dark => sun
      btn.textContent = isDark ? "☀️" : "🌙";
    }

    btn.addEventListener("click", () => {
      const cur = body.dataset.theme || "dark";
      const next = cur === "dark" ? "light" : "dark";
      body.dataset.theme = next;
      localStorage.setItem(KEY, next);
      render();
    });

    render();
  }

  // ---------------- fund picker (popover over chart, Bybit-style bounds) ----------------
  function initFundPopover() {
    const popover = qs("#termFundPopover");
    const backdrop = qs("#termFundPopoverBackdrop");
    const burger = qs("#termBurger");
    const search = qs("#fundSearch");
    const list = qs("#fundList");
    if (!popover || !backdrop || !burger || !list) return;

    const rows = qsa(".term-drawer__tr", list);

    function open() {
      popover.classList.add("is-open");
      popover.setAttribute("aria-hidden", "false");
      burger.setAttribute("aria-expanded", "true");
      setTimeout(() => search && search.focus(), 50);
    }

    function close() {
      popover.classList.remove("is-open");
      popover.setAttribute("aria-hidden", "true");
      burger.setAttribute("aria-expanded", "false");
    }

    function toggle() {
      if (popover.classList.contains("is-open")) close();
      else open();
    }

    burger.addEventListener("click", toggle);
    backdrop.addEventListener("click", close);

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && popover.classList.contains("is-open")) close();
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

  /** Keeps only digits and one dot; fractional part at most maxDp chars. Preserves trailing "." while typing. */
  function limitDecimalPlaces(raw, maxDp) {
    let s = String(raw || "").replace(",", ".");
    s = s.replace(/[^\d.]/g, "");
    const firstDot = s.indexOf(".");
    if (firstDot === -1) return s;
    let intPart = s.slice(0, firstDot);
    const fracRaw = s.slice(firstDot + 1).replace(/\./g, "");
    const frac = fracRaw.slice(0, maxDp);
    const trailingDotOnly = s.endsWith(".") && fracRaw.length === 0;
    if (intPart === "" && (frac.length > 0 || trailingDotOnly)) intPart = "0";
    if (trailingDotOnly) return intPart + ".";
    return frac.length > 0 ? intPart + "." + frac : intPart;
  }

  function formatCappedAmount(maxValue, maxDp) {
    const t = Number(maxValue).toFixed(maxDp);
    if (!t.includes(".")) return t;
    return t.replace(/\.0+$/, "");
  }

  /**
   * Decimal places + at most maxIntDigits before "." + numeric cap at maxValue.
   */
  function limitOrderAmount(raw, maxDp, maxIntDigits, maxValue) {
    let s = limitDecimalPlaces(raw, maxDp);
    const trailingDot = s.endsWith(".");
    let intPart;
    let frac;
    if (trailingDot) {
      intPart = s.slice(0, -1);
      frac = "";
    } else {
      const idx = s.indexOf(".");
      if (idx === -1) {
        intPart = s;
        frac = undefined;
      } else {
        intPart = s.slice(0, idx);
        frac = s.slice(idx + 1);
      }
    }
    if (intPart.length > maxIntDigits) intPart = intPart.slice(0, maxIntDigits);
    if (trailingDot) {
      s = intPart + ".";
    } else if (frac !== undefined && frac.length > 0) {
      s = intPart + "." + frac;
    } else if (frac !== undefined) {
      s = intPart;
    } else {
      s = intPart;
    }
    const n = parseNum(s);
    if (n !== null && n > maxValue) return formatCappedAmount(maxValue, maxDp);
    return s;
  }

  function bindOrderAmountInput(el, maxDp, maxIntDigits, maxValue) {
    if (!el) return;
    const apply = () => {
      const cur = el.value;
      const next = limitOrderAmount(cur, maxDp, maxIntDigits, maxValue);
      if (next === cur) return;
      const pos = el.selectionStart ?? cur.length;
      el.value = next;
      const np = Math.min(pos, next.length);
      el.setSelectionRange(np, np);
    };
    el.addEventListener("input", apply);
    el.addEventListener("paste", (e) => {
      e.preventDefault();
      const t = (e.clipboardData || window.clipboardData).getData("text") || "";
      const start = el.selectionStart ?? 0;
      const end = el.selectionEnd ?? 0;
      const merged = el.value.slice(0, start) + t + el.value.slice(end);
      el.value = limitOrderAmount(merged, maxDp, maxIntDigits, maxValue);
      el.setSelectionRange(el.value.length, el.value.length);
    });
  }

  function initOrderPanel() {
    const wrap = qs(".term-order");
    if (!wrap) return;

    const isAuthenticated = wrap.dataset.authenticated === "1";
    const fundCode = wrap.dataset.fundCode || getCurrentFundCode();
    const fundName = wrap.dataset.fundName || "-";

    let availUSDT = Number(wrap.dataset.availUsdt || 0);
    let availShares = Number(wrap.dataset.availShares || 0);

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

    const availableUsdtEl = qs("[data-order-available-usdt]");
    const availableSharesEl = qs("[data-order-available-shares]");
    const tradeHistoryBody = qs("[data-terminal-trade-history-body]");

    let buyPending = false;
    let redeemPending = false;

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

    const BUY_MAX = 10_000_000;
    const BUY_MAX_INT_DIGITS = 8;
    const REDEEM_MAX = 1000;
    const REDEEM_MAX_INT_DIGITS = 4;

    bindOrderAmountInput(buyIn, 2, BUY_MAX_INT_DIGITS, BUY_MAX);
    bindOrderAmountInput(redIn, 4, REDEEM_MAX_INT_DIGITS, REDEEM_MAX);

    function fmt2(v) {
      const n = Number(v);
      return Number.isFinite(n) ? n.toFixed(2) : "0.00";
    }

    function fmt4(v) {
      const n = Number(v);
      return Number.isFinite(n) ? n.toFixed(4) : "0.0000";
    }

    function escapeHtml(value) {
      return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
    }

    function normalizeDisplayValue(value, fallback = "—") {
      if (value === null || value === undefined || value === "") return fallback;
      return String(value);
    }

    function statusClass(status, explicitClass = "") {
      const cls = String(explicitClass || "").toLowerCase();
      const st = String(status || "").toLowerCase();

      if (["success", "completed", "executed"].includes(cls)) return "success";
      if (["failed", "error"].includes(cls)) return "failed";
      if (["cancelled", "canceled"].includes(cls)) return "cancelled";
      if (["pending", "processing", "settling"].includes(cls)) return "processing";

      if (st === "success" || st === "completed" || st === "executed") return "success";
      if (st === "failed" || st === "error" || st === "failed_requires_review") return "failed";
      if (st === "cancelled" || st === "canceled") return "cancelled";

      return "processing";
    }

    function statusLabel(status, explicitLabel) {
      if (explicitLabel) return explicitLabel;

      const st = String(status || "").toLowerCase();

      if (st === "success" || st === "completed" || st === "executed") {
        return L("Выполнено", "Completed");
      }

      if (st === "failed" || st === "error" || st === "failed_requires_review") {
        return L("Ошибка", "Failed");
      }

      if (st === "cancelled" || st === "canceled") {
        return L("Отменено", "Cancelled");
      }

      return L("Обрабатывается", "Processing");
    }

    function sideLabel(side) {
      const s = String(side || "").toLowerCase();

      if (s === "redeem" || s === "redemption") {
        return L("Погашение", "Redeem");
      }

      return L("Покупка", "Buy");
    }

    function nowDisplay() {
      const d = new Date();
      const pad = (x) => String(x).padStart(2, "0");

      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    }

    function updateAvailableUsdt(value) {
      const n = Number(value);
      if (!Number.isFinite(n)) return;

      availUSDT = Math.max(n, 0);
      wrap.dataset.availUsdt = String(availUSDT);

      if (availableUsdtEl) {
        availableUsdtEl.textContent = fmt2(availUSDT);
      }

      if (buyBtn) {
        buyBtn.disabled = !canEnableBuyButton() || buyPending;
      }
    }

    function updateAvailableShares(value) {
      const n = Number(value);
      if (!Number.isFinite(n)) return;

      availShares = Math.max(n, 0);
      wrap.dataset.availShares = String(availShares);

      if (availableSharesEl) {
        availableSharesEl.textContent = fmt4(availShares);
      }

      if (redBtn) {
        redBtn.disabled = !canEnableRedeemButton() || redeemPending;
      }
    }

    function pickHistoryRow(data) {
      if (!data || typeof data !== "object") return null;

      return (
        data.trade_row ||
        data.history_row ||
        data.row ||
        data.order_row ||
        data.order ||
        null
      );
    }

    function buildFallbackBuyRow(amountUsdt) {
      return {
        name: fundName,
        side: L("Покупка", "Buy"),
        amount: `${fmt2(amountUsdt)} USDT`,
        shares: "—",
        price: "—",
        status: "pending",
        status_label: L("Обрабатывается", "Processing"),
        created: nowDisplay(),
        executed: "—",
      };
    }

    function buildFallbackRedeemRow(shares) {
      return {
        name: fundName,
        side: L("Погашение", "Redeem"),
        amount: "—",
        shares: `${fmt4(shares)} ${L("паёв", "shares")}`,
        price: "—",
        status: "pending",
        status_label: L("Обрабатывается", "Processing"),
        created: nowDisplay(),
        executed: "—",
      };
    }

    function normalizeHistoryRow(row, fallback) {
      const r = row && typeof row === "object" ? row : fallback;

      const sideRaw = r.side ?? r.direction ?? fallback.side;
      const statusRaw = r.status ?? "pending";

      return {
        name: r.name ?? r.fund_name ?? r.fund_code ?? fallback.name,
        side: r.side_label ?? sideLabel(sideRaw),
        amount:
          r.amount ??
          r.amount_text ??
          (r.amount_usdt !== undefined && r.amount_usdt !== null ? `${fmt2(r.amount_usdt)} USDT` : fallback.amount),
        shares:
          r.shares ??
          r.shares_text ??
          (r.shares_qty !== undefined && r.shares_qty !== null ? fmt4(r.shares_qty) : fallback.shares),
        price:
          r.price ??
          r.price_text ??
          (r.price_usdt !== undefined && r.price_usdt !== null ? `${fmt2(r.price_usdt)} USDT` : fallback.price),
        status: statusRaw,
        status_class: r.status_class ?? r.status_color ?? "",
        status_label: r.status_label ?? fallback.status_label,
        created: r.created ?? r.created_at ?? fallback.created,
        executed: r.executed ?? r.executed_at ?? fallback.executed,
      };
    }

    function renderHistoryRow(row) {
      const stClass = statusClass(row.status, row.status_class);

      return `
      <div class="term-tr term-tr--history">
        <div class="t-name">${escapeHtml(normalizeDisplayValue(row.name))}</div>
        <div class="t-center">${escapeHtml(normalizeDisplayValue(row.side))}</div>
        <div class="t-center">${escapeHtml(normalizeDisplayValue(row.amount))}</div>
        <div class="t-center">${escapeHtml(normalizeDisplayValue(row.shares))}</div>
        <div class="t-center">${escapeHtml(normalizeDisplayValue(row.price))}</div>
        <div class="t-center">
          <span class="term-status term-status--${stClass}">
            ${escapeHtml(statusLabel(row.status, row.status_label))}
          </span>
        </div>
        <div class="t-center">${escapeHtml(normalizeDisplayValue(row.created))}</div>
        <div class="t-right">${escapeHtml(normalizeDisplayValue(row.executed))}</div>
      </div>
    `;
    }

    function insertPendingHistoryRow(row) {
      if (!tradeHistoryBody) return;

      const empty = tradeHistoryBody.querySelector("[data-terminal-trade-empty]");
      if (empty) empty.remove();

      tradeHistoryBody.insertAdjacentHTML("afterbegin", renderHistoryRow(row));
    }

    function validateBuy() {
      if (!buyIn) return false;

      const raw = buyIn.value;
      const n = parseNum(raw);

      if (buyErr) buyErr.textContent = "";
      if (buyOk) buyOk.textContent = "";

      if (n === null) return false;
      if (!decimalsOk(raw, 2)) return false;
      if (n <= 0) return false;
      if (n > BUY_MAX) return false;

      const intLen = String(raw || "").trim().replace(",", ".").split(".")[0].length;
      if (intLen > BUY_MAX_INT_DIGITS) return false;

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
      if (n > REDEEM_MAX) return false;

      const intLenR = String(raw || "").trim().replace(",", ".").split(".")[0].length;
      if (intLenR > REDEEM_MAX_INT_DIGITS) return false;

      if (n > availShares) {
        if (redErr) redErr.textContent = L("Количество больше доступного баланса", "Quantity exceeds available balance");
        return false;
      }

      return true;
    }

    function canEnableBuyButton() {
      if (!buyIn) return false;

      const raw = buyIn.value;
      const n = parseNum(raw);

      if (n === null) return false;
      if (!decimalsOk(raw, 2)) return false;
      if (n <= 0) return false;
      if (n > BUY_MAX) return false;

      const intLen = String(raw || "").trim().replace(",", ".").split(".")[0].length;
      if (intLen > BUY_MAX_INT_DIGITS) return false;

      // Для неавторизованного пользователя кнопку надо дать нажать,
      // чтобы показать login-required message, но API request не отправлять.
      if (!isAuthenticated) return true;

      return n <= availUSDT;
    }

    function canEnableRedeemButton() {
      if (!redIn) return false;

      const raw = redIn.value;
      const n = parseNum(raw);

      if (n === null) return false;
      if (!decimalsOk(raw, 4)) return false;
      if (n <= 0) return false;
      if (n > REDEEM_MAX) return false;

      const intLenR = String(raw || "").trim().replace(",", ".").split(".")[0].length;
      if (intLenR > REDEEM_MAX_INT_DIGITS) return false;

      // Для неавторизованного пользователя кнопку надо дать нажать,
      // чтобы показать login-required message, но API request не отправлять.
      if (!isAuthenticated) return true;

      return n <= availShares;
    }

    function setBackendError(targetEl, data, fallback) {
      if (!targetEl) return;
      targetEl.textContent = data?.message || data?.detail || fallback;
    }

    async function postJSON(url, payload) {
      const resp = await fetch(url, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify(payload),
      });

      const ct = (resp.headers.get("content-type") || "").toLowerCase();
      const data = ct.includes("application/json")
        ? await resp.json().catch(() => null)
        : { message: await resp.text().catch(() => "") };

      return { ok: resp.ok, status: resp.status, data };
    }

    buyIn && buyIn.addEventListener("input", () => {
      if (buyErr) buyErr.textContent = "";
      if (buyOk) buyOk.textContent = "";
      if (buyBtn) buyBtn.disabled = !canEnableBuyButton() || buyPending;
    });

    redIn && redIn.addEventListener("input", () => {
      if (redErr) redErr.textContent = "";
      if (redOk) redOk.textContent = "";
      if (redBtn) redBtn.disabled = !canEnableRedeemButton() || redeemPending;
    });

    if (buyBtn) {
      buyBtn.disabled = true;

      buyBtn.addEventListener("click", async () => {
        if (buyPending) return;

        if (!isAuthenticated) {
          if (buyErr) buyErr.textContent = L("Войдите в аккаунт, чтобы купить паи.", "Log in to buy fund shares.");
          return;
        }

        if (!validateBuy()) return;

        const amount = parseNum(buyIn.value);
        if (amount === null) return;

        buyPending = true;
        buyBtn.disabled = true;
        if (buyErr) buyErr.textContent = "";
        if (buyOk) buyOk.textContent = "";

        try {
          const { ok, data } = await postJSON("/api/trading/orders/buy", {
            fund_code: fundCode,
            amount_usdt: buyIn.value.trim().replace(",", "."),
          });

          if (!ok || data?.status === "error") {
            setBackendError(buyErr, data, L("Не удалось создать заявку.", "Failed to create order."));
            return;
          }

          const fallback = buildFallbackBuyRow(amount);
          const row = normalizeHistoryRow(pickHistoryRow(data), fallback);

          insertPendingHistoryRow(row);

          const nextAvailable =
            data?.available_usdt ??
            data?.usdt_balance_available ??
            data?.available_balance_usdt ??
            (availUSDT - amount);

          updateAvailableUsdt(nextAvailable);

          buyIn.value = "";
          buyBtn.disabled = true;

          if (buyOk) {
            buyOk.textContent = data?.message || L("Заявка на покупку создана.", "Buy order created.");
          }
        } catch (e) {
          console.error(e);
          if (buyErr) buyErr.textContent = L("Ошибка сети.", "Network error.");
        } finally {
          buyPending = false;
          if (buyBtn) buyBtn.disabled = !canEnableBuyButton();
        }
      });
    }

    if (redBtn) {
      redBtn.disabled = true;

      redBtn.addEventListener("click", async () => {
        if (redeemPending) return;

        if (!isAuthenticated) {
          if (redErr) redErr.textContent = L("Войдите в аккаунт, чтобы погасить паи.", "Log in to redeem fund shares.");
          return;
        }

        if (!validateRedeem()) return;

        const shares = parseNum(redIn.value);
        if (shares === null) return;

        redeemPending = true;
        redBtn.disabled = true;
        if (redErr) redErr.textContent = "";
        if (redOk) redOk.textContent = "";

        try {
          const { ok, data } = await postJSON("/api/trading/orders/redeem", {
            fund_code: fundCode,
            shares: redIn.value.trim().replace(",", "."),
          });

          if (!ok || data?.status === "error") {
            setBackendError(redErr, data, L("Не удалось создать заявку.", "Failed to create order."));
            return;
          }

          const fallback = buildFallbackRedeemRow(shares);
          const row = normalizeHistoryRow(pickHistoryRow(data), fallback);

          insertPendingHistoryRow(row);

          const nextAvailable =
            data?.available_shares ??
            data?.shares_available ??
            data?.available_shares_current_fund ??
            (availShares - shares);

          updateAvailableShares(nextAvailable);

          redIn.value = "";
          redBtn.disabled = true;

          if (redOk) {
            redOk.textContent = data?.message || L("Заявка на погашение создана.", "Redeem order created.");
          }
        } catch (e) {
          console.error(e);
          if (redErr) redErr.textContent = L("Ошибка сети.", "Network error.");
        } finally {
          redeemPending = false;
          if (redBtn) redBtn.disabled = !canEnableRedeemButton();
        }
      });
    }
  }

  // ---------------- live terminal summary ----------------
  function format2(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return "-";
    return n.toFixed(2);
  }

  function format4(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return "-";
    return n.toFixed(4);
  }

  function format0(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return "-";
    return String(Math.round(n));
  }

  function setSignedPct(el, value) {
    if (!el) return;

    const n = Number(value);
    el.classList.remove("pos", "neg");

    if (!Number.isFinite(n)) {
      el.textContent = "-";
      return;
    }

    if (n > 0) el.classList.add("pos");
    if (n < 0) el.classList.add("neg");

    const sign = n > 0 ? "+" : "";
    el.textContent = `${sign}${n.toFixed(2)}%`;
  }

  function getCurrentFundCode() {
    const cfgEl = document.getElementById("terminalChartConfig");
    if (!cfgEl) return null;

    try {
      const cfg = JSON.parse(cfgEl.textContent || "{}");
      return cfg.fund_code || cfg.symbol_code || null;
    } catch (_) {
      return null;
    }
  }

  function applyTerminalLivePayload(payload) {
    if (!payload) return;

    const current = payload.current_fund || {};
    const info = payload.fund_info || {};

    const priceEl = qs("[data-live-current-price]");
    const chEl = qs("[data-live-change-24h]");
    const highEl = qs("[data-live-day-high]");
    const lowEl = qs("[data-live-day-low]");
    const aumEl = qs("[data-live-aum]");
    const sharesEl = qs("[data-live-shares-outstanding]");

    if (priceEl) priceEl.textContent = format2(current.current_price_usdt);
    setSignedPct(chEl, current.change_24h_pct);

    if (highEl) highEl.textContent = current.day_high_usdt == null ? "-" : format2(current.day_high_usdt);
    if (lowEl) lowEl.textContent = current.day_low_usdt == null ? "-" : format2(current.day_low_usdt);

    if (aumEl) {
      aumEl.textContent = info.aum_usdt == null ? "-" : `${format0(info.aum_usdt)} USDT`;
    }

    if (sharesEl) {
      sharesEl.textContent = info.shares_outstanding == null ? "-" : format4(info.shares_outstanding);
    }
  }

  function initTerminalLiveSummary() {
    const fundCode = getCurrentFundCode();
    if (!fundCode) return;

    let lastPollTs = 0;

    const poll = async () => {
      lastPollTs = Date.now();
      try {
        const url = `/api/terminal/live/${encodeURIComponent(fundCode)}`;
        const resp = await fetch(url, {
          method: "GET",
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        });

        if (!resp.ok) return;

        const payload = await resp.json();
        applyTerminalLivePayload(payload);
      } catch (_) {
        /* keep terminal silent on transient polling errors */
      }
    };

    // Initial fetch + sync with chart polls so the top summary moves together with the chart price.
    poll();
    window.addEventListener("wb:chart-bar-poll", poll);

    // Fallback for funds without chart data: keep polling on our own clock if no chart events arrive.
    window.setInterval(() => {
      if (Date.now() - lastPollTs >= 10000) poll();
    }, 10000);
  }

  document.addEventListener("DOMContentLoaded", () => {
    initTheme();
    initFundPopover();
    initBottomTabs();
    initOrderPanel();
    initTerminalLiveSummary();
  });
})();
