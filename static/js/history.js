(() => {
  const lang = (document.documentElement.lang || "en").toLowerCase();
  const L = (ru, en) => (lang === "en" ? en : ru);
  const POLL_MS = 10000;

  async function copyToClipboard(text) {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch (_) {}

    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      ta.style.top = "-9999px";
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(ta);
      return ok;
    } catch (_) {
      return false;
    }
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function shortHex(value) {
    const s = String(value || "").trim();
    if (!s) return "—";
    return s.length > 12 ? `${s.slice(0, 5)}...${s.slice(-5)}` : s;
  }

  function normalizeSub(main, sub) {
    if (main === "trading") {
      if (sub === "buys") return "purchases";
      if (sub === "redeem") return "redemptions";
    }
    return sub || "all";
  }

  function copyIconSvg() {
    return `
      <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M8 8h11v11H8z" fill="none" stroke="currentColor" stroke-width="2"></path>
        <path d="M5 16H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h11a1 1 0 0 1 1 1v1" fill="none" stroke="currentColor" stroke-width="2"></path>
      </svg>
    `;
  }

  function renderCopyCell(value, extraClass = "") {
    const full = String(value || "").trim();

    return `
      <div class="tx-col-cell-center ${extraClass}">
        <div class="tx-col-copy">
          <span class="tx-hash">${escapeHtml(shortHex(full))}</span>
          ${
            full
              ? `<button type="button" class="copy-btn tx-copy-btn" data-copy="${escapeHtml(full)}" aria-label="${escapeHtml(L("Скопировать", "Copy"))}">
                   ${copyIconSvg()}
                 </button>`
              : ""
          }
        </div>
      </div>
    `;
  }

  function transferTypeLabel(type) {
    const t = String(type || "").toLowerCase();
    if (t === "withdraw" || t === "withdrawal") return L("Вывод", "Withdrawal");
    return L("Пополнение", "Deposit");
  }

  function transferStatusHtml(status) {
    const st = String(status || "").toLowerCase();

    if (st === "success") {
      return `<span class="tx-status tx-status--success">${escapeHtml(L("Завершено", "Completed"))}</span>`;
    }

    if (st === "failed") {
      return `<span class="tx-status tx-status--failed">${escapeHtml(L("Ошибка", "Failed"))}</span>`;
    }

    if (st === "processing" || st === "pending") {
      return `<span class="tx-status tx-status--pending">${escapeHtml(L("В обработке", "Pending"))}</span>`;
    }

    return `<span class="tx-status tx-status--pending">${escapeHtml(st || L("В обработке", "Pending"))}</span>`;
  }

  function tradingStatusHtml(rowOrStatus) {
    const row = rowOrStatus && typeof rowOrStatus === "object"
      ? rowOrStatus
      : { status: rowOrStatus };

    const st = String(row.status || "").toLowerCase();
    const explicitLabel = row.status_label || "";
    const explicitClass = String(row.status_class || "").toLowerCase();
    const explicitColor = String(row.status_color || "").toLowerCase();

    let cssClass = "tx-status--pending";
    let fallbackLabel = L("В обработке", "Processing");

    if (
      ["success", "completed", "executed"].includes(st) ||
      ["success", "completed", "executed"].includes(explicitClass) ||
      explicitColor === "green"
    ) {
      cssClass = "tx-status--success";
      fallbackLabel = L("Завершено", "Completed");
    } else if (
      ["failed", "error", "failed_requires_review"].includes(st) ||
      ["failed", "error"].includes(explicitClass) ||
      explicitColor === "red"
    ) {
      cssClass = "tx-status--failed";
      fallbackLabel = L("Ошибка", "Failed");
    } else if (
      ["cancelled", "canceled"].includes(st) ||
      ["cancelled", "canceled"].includes(explicitClass) ||
      explicitColor === "gray"
    ) {
      cssClass = "tx-status--cancelled";
      fallbackLabel = L("Отменено", "Cancelled");
    }

    return `<span class="tx-status ${cssClass}">${escapeHtml(explicitLabel || fallbackLabel)}</span>`;
  }

  function complianceStatusHtml(status) {
    const cs = String(status || "").toLowerCase();

    if (cs === "ok") return `<span class="status-ok cs-ok">ok</span>`;
    if (cs === "blocked") return `<span class="status-blocked cs-blocked">blocked</span>`;
    if (cs === "pending_check") return `<span class="status-pending">pending check</span>`;

    return "—";
  }

  function renderNoData() {
    return `
      <div class="muted-text" style="margin-top:14px;">
        ${escapeHtml(L("Пока нет данных.", "No data yet."))}
      </div>
    `;
  }

  function renderTransfersTable(sub, rows) {
    const isAll = sub === "all";
    const isDeposits = sub === "deposits";
    const tableClass = isAll
      ? "tx-table--all"
      : isDeposits
      ? "tx-table--deposits"
      : "tx-table--withdrawals";

    const header = isAll
      ? `
        <div class="tx-header">
          <div>${escapeHtml(L("Монета", "Coin"))}</div>
          <div class="tx-col-center">${escapeHtml(L("Вид сети", "Network"))}</div>
          <div class="tx-col-center">${escapeHtml(L("Количество", "Amount"))}</div>
          <div class="tx-col-center">${escapeHtml(L("Тип", "Type"))}</div>
          <div class="tx-col-center tx-col-addr">${escapeHtml(L("Адрес", "Address"))}</div>
          <div class="tx-col-center tx-col-txid">Txid</div>
          <div class="tx-col-center">${escapeHtml(L("Статус", "Status"))}</div>
          <div class="tx-col-center">Compliance status</div>
          <div class="tx-col-center">${escapeHtml(L("Дата/время", "Date/time"))}</div>
        </div>
      `
      : `
        <div class="tx-header">
          <div>${escapeHtml(L("Монета", "Coin"))}</div>
          <div class="tx-col-center">${escapeHtml(L("Вид сети", "Network"))}</div>
          <div class="tx-col-center">${escapeHtml(L("Количество", "Amount"))}</div>
          <div class="tx-col-center tx-col-addr">${escapeHtml(L("Адрес", "Address"))}</div>
          <div class="tx-col-center tx-col-txid">Txid</div>
          <div class="tx-col-center">${escapeHtml(L("Статус", "Status"))}</div>
          <div class="tx-col-center">Compliance status</div>
          <div class="tx-col-center">${escapeHtml(L("Дата/время", "Date/time"))}</div>
        </div>
      `;

    const safeRows = Array.isArray(rows) ? rows : [];

    const body = safeRows.length
      ? safeRows
          .map((tx) => {
            const type = String(tx.type || "").toLowerCase();
            const address = tx.full_address || tx.address || "";
            const txid = tx.full_tx_hash || tx.txid || "";
            const amount = tx.amount ?? "0.00";

            if (isAll) {
              return `
                <div class="tx-row">
                  <div class="tx-col-coin">${escapeHtml(tx.coin || "USDT")}</div>
                  <div class="tx-col-center">${escapeHtml(tx.network || "BSC (BEP20)")}</div>
                  <div class="tx-col-center">${escapeHtml(amount)}</div>
                  <div class="tx-col-center">${escapeHtml(transferTypeLabel(tx.type))}</div>
                  ${renderCopyCell(address, "tx-col-addr")}
                  ${renderCopyCell(txid, "tx-col-txid")}
                  <div class="tx-col-center">${transferStatusHtml(tx.status)}</div>
                  <div class="tx-col-center">${complianceStatusHtml(tx.compliance_status)}</div>
                  <div class="tx-col-dt tx-col-center">${escapeHtml(tx.date_time || "")}</div>
                </div>
              `;
            }

            return `
              <div class="tx-row">
                <div class="tx-col-coin">${escapeHtml(tx.coin || "USDT")}</div>
                <div class="tx-col-center">${escapeHtml(tx.network || "BSC (BEP20)")}</div>
                <div class="tx-col-center">${escapeHtml(amount)}</div>
                ${renderCopyCell(address, "tx-col-addr")}
                ${renderCopyCell(txid, "tx-col-txid")}
                <div class="tx-col-center">${transferStatusHtml(tx.status)}</div>
                <div class="tx-col-center">${complianceStatusHtml(tx.compliance_status)}</div>
                <div class="tx-col-dt tx-col-center">${escapeHtml(tx.date_time || "")}</div>
              </div>
            `;
          })
          .join("")
      : renderNoData();

    return `
      <div class="tx-table ${tableClass}">
        ${header}
        ${body}
      </div>
    `;
  }

  function tradingSideLabel(side) {
    const s = String(side || "").toLowerCase();
    if (s === "redeem" || s === "redemption") return L("Погашение", "Redemption");
    return L("Покупка", "Purchase");
  }

  function renderTradingTable(rows) {
    const safeRows = Array.isArray(rows) ? rows : [];
    const grid = "grid-template-columns:minmax(160px,1.4fr) 110px 120px 110px 100px 110px 160px 160px;";

    const header = `
      <div class="tx-header tx-header--trading" style="${grid}">
        <div>${escapeHtml(L("Название", "Name"))}</div>
        <div class="tx-col-center">${escapeHtml(L("Направление", "Side"))}</div>
        <div class="tx-col-center">${escapeHtml(L("Стоимость", "Amount"))}</div>
        <div class="tx-col-center">${escapeHtml(L("Паи", "Shares"))}</div>
        <div class="tx-col-center">${escapeHtml(L("Цена", "Price"))}</div>
        <div class="tx-col-center">${escapeHtml(L("Статус", "Status"))}</div>
        <div class="tx-col-center">${escapeHtml(L("Создано", "Created"))}</div>
        <div class="tx-col-center">${escapeHtml(L("Исполнено", "Executed"))}</div>
      </div>
    `;

    const body = safeRows.length
      ? safeRows
          .map((row) => {
            return `
              <div class="tx-row tx-row--trading" style="${grid}">
                <div class="tx-col-coin">${escapeHtml(row.fund_name || row.fund_code || "—")}</div>
                <div class="tx-col-center">${escapeHtml(tradingSideLabel(row.side))}</div>
                <div class="tx-col-center">${escapeHtml(row.amount ?? (row.amount_usdt != null ? `${row.amount_usdt} USDT` : "—"))}</div>
                <div class="tx-col-center">${escapeHtml(row.shares_display ?? (row.shares != null ? `${row.shares} ${L("паёв", "shares")}` : "—"))}</div>
                <div class="tx-col-center">${escapeHtml(row.price ?? (row.price_usdt != null ? `${row.price_usdt} USDT` : "—"))}</div>
                <div class="tx-col-center">${tradingStatusHtml(row)}</div>
                <div class="tx-col-dt tx-col-center">${escapeHtml(row.created_at || "")}</div>
                <div class="tx-col-dt tx-col-center">${escapeHtml(row.executed_at || "—")}</div>
              </div>
            `;
          })
          .join("")
      : renderNoData();

    return `
      <div class="tx-table tx-table--trading">
        ${header}
        ${body}
      </div>
    `;
  }

  function renderLiveTable(section, sub, rows) {
    if (section === "transfers") return renderTransfersTable(sub, rows);
    return renderTradingTable(rows);
  }

  function initHistoryTabs() {
    const root = document.getElementById("historyTabs");
    if (!root) return;

    const mainBtns = Array.from(root.querySelectorAll("[data-main]"));
    const subGroups = Array.from(root.querySelectorAll("[data-subgroup]"));
    const panels = Array.from(root.querySelectorAll("[data-panel]"));
    const exportButtons = Array.from(root.querySelectorAll("[data-history-export]"));

    let currentMain = "trading";
    let currentSub = "all";
    let inFlight = false;
    let pollTimer = null;

    function getPanel(mainName = currentMain, subName = currentSub) {
      return root.querySelector(`[data-panel="${mainName}/${subName}"]`);
    }

    function updateExportButtons() {
      const activeKey = `${currentMain}/${currentSub}`;

      exportButtons.forEach((btn) => {
        btn.classList.toggle("hidden", btn.dataset.historyExport !== activeKey);
      });
    }

    function updateQueryParams() {
      const url = new URL(window.location.href);
      url.searchParams.set("tab", currentMain);
      url.searchParams.set("sub", currentSub);
      window.history.replaceState({}, "", url.toString());
    }

    function showPanel(mainName, subName) {
      const key = `${mainName}/${subName}`;
      panels.forEach((p) => p.classList.toggle("hidden", p.dataset.panel !== key));
    }

    async function pollActiveHistory({ silent = true } = {}) {
      if (window.location.pathname !== "/history") return;
      if (inFlight) return;

      inFlight = true;

      try {
        const url = new URL("/api/history/live", window.location.origin);
        url.searchParams.set("section", currentMain);
        url.searchParams.set("sub", currentSub);

        const resp = await fetch(url.toString(), {
          method: "GET",
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        });

        if (!resp.ok) return;

        const data = await resp.json().catch(() => null);
        if (!data || data.status !== "ok") return;

        const panel = getPanel(data.section || currentMain, data.sub || currentSub);
        if (!panel) return;

        const html = renderLiveTable(data.section || currentMain, data.sub || currentSub, data.rows || []);

        if (panel.innerHTML !== html) {
          panel.innerHTML = html;
        }
      } catch (err) {
        if (!silent) console.warn("[history-live] polling failed:", err);
      } finally {
        inFlight = false;
      }
    }

    function restartPolling() {
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }

      pollActiveHistory({ silent: false });
      pollTimer = window.setInterval(() => {
        pollActiveHistory();
      }, POLL_MS);
    }

    function setSub(groupEl, subName, opts = {}) {
      if (!groupEl) return;

      const normalized = normalizeSub(currentMain, subName);
      const subBtns = Array.from(groupEl.querySelectorAll("[data-sub]"));

      subBtns.forEach((b) => {
        b.classList.toggle("is-active", normalizeSub(currentMain, b.dataset.sub) === normalized);
      });

      currentSub = normalized;
      showPanel(currentMain, currentSub);
      updateExportButtons();

      if (opts.updateUrl !== false) updateQueryParams();
      if (opts.poll !== false) restartPolling();
    }

    function setMain(name, opts = {}) {
      currentMain = name === "transfers" ? "transfers" : "trading";

      mainBtns.forEach((b) => b.classList.toggle("is-active", b.dataset.main === currentMain));
      subGroups.forEach((g) => g.classList.toggle("hidden", g.dataset.subgroup !== currentMain));

      const activeGroup = root.querySelector(`[data-subgroup="${currentMain}"]`);
      const requestedSub = opts.sub || "all";
      const subBtn = activeGroup && activeGroup.querySelector(`[data-sub="${requestedSub}"]`);
      const first = activeGroup ? activeGroup.querySelector("[data-sub]") : null;
      const nextSub = (subBtn && subBtn.dataset.sub) || (first && first.dataset.sub) || "all";

      setSub(activeGroup, nextSub, opts);
    }

    mainBtns.forEach((b) => {
      b.addEventListener("click", () => setMain(b.dataset.main));
    });

    subGroups.forEach((g) => {
      g.querySelectorAll("[data-sub]").forEach((b) => {
        b.addEventListener("click", () => setSub(g, b.dataset.sub));
      });
    });

    root.addEventListener("click", async (e) => {
      const btn = e.target.closest && e.target.closest("[data-copy]");
      if (!btn) return;

      const full = (btn.getAttribute("data-copy") || "").trim();
      if (!full) return;

      const ok = await copyToClipboard(full);
      if (ok) {
        const old = btn.getAttribute("aria-label") || "";
        btn.setAttribute("aria-label", L("Скопировано", "Copied"));
        btn.classList.add("is-copied");

        setTimeout(() => {
          btn.classList.remove("is-copied");
          if (old) btn.setAttribute("aria-label", old);
        }, 900);
      }
    });

    const params = new URLSearchParams(window.location.search);
    const tab = params.get("tab") || "trading";
    const sub = normalizeSub(tab, params.get("sub") || "all");

    setMain(tab, { sub, updateUrl: false, poll: false });
    updateExportButtons();
    restartPolling();
  }

  document.addEventListener("DOMContentLoaded", initHistoryTabs);
})();