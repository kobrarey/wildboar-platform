(() => {
    const lang = (document.documentElement.lang || "ru").toLowerCase();
    const L = (ru, en) => (lang === "en" ? en : ru);
  
    async function copyToClipboard(text) {
      try {
        if (navigator.clipboard && window.isSecureContext) {
          await navigator.clipboard.writeText(text);
          return true;
        }
      } catch (_) {}
  
      // fallback
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
  
    function debounce(fn, ms) {
      let t = null;
      return (...args) => {
        if (t) clearTimeout(t);
        t = setTimeout(() => fn(...args), ms);
      };
    }
  
    function initDepositModal() {
      const modal = document.getElementById("depositModal");
      if (!modal) return;
  
      const addrInput = document.getElementById("depositAddress");
      const copyBtn = document.getElementById("depositCopyBtn");
      const statusEl = document.getElementById("depositCopyStatus");
  
      const openBtns = document.querySelectorAll('[data-modal-open="depositModal"]');
      openBtns.forEach((b) =>
        b.addEventListener("click", () => {
          if (statusEl) statusEl.textContent = "";
        })
      );
  
      if (!copyBtn || !addrInput) return;
  
      copyBtn.addEventListener("click", async () => {
        const address = (addrInput.value || "").trim();
        if (!address) return;
  
        const ok = await copyToClipboard(address);
        if (!statusEl) return;
  
        statusEl.textContent = ok
          ? L("Скопировано", "Copied")
          : L("Не удалось скопировать", "Copy failed");
  
        window.setTimeout(() => {
          statusEl.textContent = "";
        }, 1500);
      });
    }
  
    function initWithdrawModal() {
      const modal = document.getElementById("withdrawModal");
      if (!modal) return;
  
      const balance = parseFloat(modal.dataset.usdtBalance || "0") || 0;
  
      const addrInput = document.getElementById("withdrawAddress");
      const addrStatusWrap = document.getElementById("withdrawAddrStatus");
      const addrStatusText = document.getElementById("withdrawAddrStatusText");
  
      const amountInput = document.getElementById("withdrawAmount");
      const amountMaxBtn = document.getElementById("withdrawMaxBtn");
  
      const receiveInput = document.getElementById("withdrawReceive");
      const confirmBtn = document.getElementById("withdrawConfirmBtn");
  
      const msgEl = document.getElementById("withdrawMessage");
      const amountErr = document.getElementById("withdrawAmountError");
  
      const openBtns = document.querySelectorAll('[data-modal-open="withdrawModal"]');
  
      let addrStatus = "invalid";

      function openModalLocal(m) {
        if (!m) return;
        m.classList.add("is-open");
        m.setAttribute("aria-hidden", "false");
        document.body.style.overflow = "hidden";
      }
      function closeModalLocal(m) {
        if (!m) return;
        m.classList.remove("is-open");
        m.setAttribute("aria-hidden", "true");
        document.body.style.overflow = "";
      }

      function setAddrUI(status) {
        addrStatus = status;
  
        if (!addrStatusWrap || !addrStatusText) return;
  
        addrStatusWrap.classList.remove(
          "addr-status--valid",
          "addr-status--checksum",
          "addr-status--invalid",
          "addr-status--empty"
        );
  
        if (!status || status === "empty") {
          addrStatusWrap.classList.add("addr-status--empty");
          addrStatusText.textContent = "";
          return;
        }
  
        if (status === "valid") {
          addrStatusWrap.classList.add("addr-status--valid");
          addrStatusText.textContent = L("Адрес корректен", "Address looks correct");
          return;
        }
        if (status === "checksum") {
          addrStatusWrap.classList.add("addr-status--checksum");
          addrStatusText.textContent = L(
            "Неверная контрольная сумма",
            "Checksum mismatch"
          );
          return;
        }
  
        addrStatusWrap.classList.add("addr-status--invalid");
        addrStatusText.textContent = L("Адрес указан неверно", "Address is invalid");
      }
  
      function sanitizeAmount(raw) {
        let s = (raw || "").replace(/[^\d.,]/g, "");
  
        // оставляем только один разделитель (точку)
        const m = s.match(/[.,]/);
        if (m) {
          const idx = s.search(/[.,]/);
          const before = s.slice(0, idx);
          const after = s
            .slice(idx + 1)
            .replace(/[.,]/g, "");
          s = before + "." + after;
        }
        return s;
      }
  
      function getAmount() {
        const s = sanitizeAmount(amountInput ? amountInput.value : "");
        const n = parseFloat((s || "").replace(",", "."));
        return Number.isFinite(n) ? n : 0;
      }
  
      function updateReceiveAndValidation() {
        const amount = getAmount();
  
        // сумма к получению = max(amount - 1, 0)
        const receive = Math.max(amount - 1, 0);
  
        if (receiveInput) receiveInput.value = receive.toFixed(2);
  
        // amount errors
        if (amountErr) {
          if (amount > balance) {
            amountErr.textContent = L(
              `Сумма превышает баланс (${balance.toFixed(2)} USDT)`,
              `Amount exceeds balance (${balance.toFixed(2)} USDT)`
            );
          } else {
            amountErr.textContent = "";
          }
        }
  
        const addrOk = addrStatus === "valid" || addrStatus === "checksum";
        const amountOk = amount > 0 && amount <= balance;
  
        if (confirmBtn) confirmBtn.disabled = !(addrOk && amountOk);
      }
  
      const validateAddressDebounced = debounce(async () => {
        if (!addrInput) return;
        const address = (addrInput.value || "").trim();
        addrInput.value = address;
  
        if (!address) {
          setAddrUI("empty");
          updateReceiveAndValidation();
          return;
        }
  
        try {
          const resp = await fetch("/api/wallet/validate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ address }),
          });
  
          if (!resp.ok) {
            setAddrUI("invalid");
            updateReceiveAndValidation();
            return;
          }
  
          const data = await resp.json();
          setAddrUI(data.status || "invalid");
          updateReceiveAndValidation();
        } catch (e) {
          setAddrUI("invalid");
          updateReceiveAndValidation();
        }
      }, 300);
  
      function resetWithdrawForm() {
        if (addrInput) addrInput.value = "";
        if (amountInput) amountInput.value = "";
        if (receiveInput) receiveInput.value = "0.00";
        if (msgEl) msgEl.textContent = "";
        if (amountErr) amountErr.textContent = "";
        setAddrUI("empty");
        if (confirmBtn) confirmBtn.disabled = true;
      }
  
      openBtns.forEach((b) => b.addEventListener("click", resetWithdrawForm));
  
      if (addrInput) {
        addrInput.addEventListener("input", () => {
          // trim только по краям
          const v = addrInput.value;
          const trimmed = v.replace(/^\s+|\s+$/g, "");
          if (trimmed !== v) addrInput.value = trimmed;
          validateAddressDebounced();
        });
        addrInput.addEventListener("blur", () => {
          addrInput.value = (addrInput.value || "").trim();
          validateAddressDebounced();
        });
      }
  
      if (amountInput) {
        amountInput.addEventListener("input", () => {
          const s = sanitizeAmount(amountInput.value);
          if (s !== amountInput.value) amountInput.value = s;
          updateReceiveAndValidation();
        });
      }
  
      if (amountMaxBtn) {
        amountMaxBtn.addEventListener("click", () => {
          if (!amountInput) return;
          amountInput.value = balance.toFixed(2);
          updateReceiveAndValidation();
        });
      }
  
      if (confirmBtn) {
        confirmBtn.addEventListener("click", (e) => {
          e.preventDefault();

          const to_address = (addrInput?.value || "").trim();
          const amount_gross = getAmount(); // gross из первой плашки (НЕ меняем логику)

          if (!to_address || !(amount_gross > 0)) return;

          // закрыть 1-ю модалку и открыть 2-ю
          modal.classList.remove("is-open");
          modal.setAttribute("aria-hidden", "true");

          const m2 = document.getElementById("withdrawConfirmModal");
          if (m2) {
            m2.classList.add("is-open");
            m2.setAttribute("aria-hidden", "false");
            document.body.style.overflow = "hidden";
          }

          startWithdrawConfirmFlow({ to_address, amount_gross });
        });
      }
  
      // init state
      setAddrUI("empty");
      updateReceiveAndValidation();
    }

    function startWithdrawConfirmFlow({ to_address, amount_gross }) {
      const confirmModal = document.getElementById("withdrawConfirmModal");
      const processingModal = document.getElementById("withdrawProcessingModal");

      const elAddr = document.getElementById("w2Address");
      const elAmt = document.getElementById("w2AmountNet");

      const selEmail = document.getElementById("w2EmailSelect");
      const elEmailText = document.getElementById("w2EmailText");
      const getCodeBtn = document.getElementById("w2GetCodeBtn");

      const codeInput = document.getElementById("w2Code");
      const resendBtn = document.getElementById("w2ResendBtn");
      const errEl = document.getElementById("w2Error");
      const confirmBtn = document.getElementById("w2ConfirmBtn");

      const lang = (document.documentElement.lang || "ru").toLowerCase();
      const L = (ru, en) => (lang === "en" ? en : ru);

      let token = null;
      let currentSlot = null;
      let requestSeq = 0;

      function setError(msg) {
        if (!errEl) return;
        errEl.textContent = msg || "";
      }

      function openModalLocal(m) {
        if (!m) return;
        m.classList.add("is-open");
        m.setAttribute("aria-hidden", "false");
        document.body.style.overflow = "hidden";
      }
      function closeModalLocal(m) {
        if (!m) return;
        m.classList.remove("is-open");
        m.setAttribute("aria-hidden", "true");
        document.body.style.overflow = "";
      }

      function startCooldownOnButton() {
        if (resendBtn && typeof window.startResendCooldown === "function") {
          window.startResendCooldown(resendBtn, 60, L("Получить код повторно", "Resend code"));
        } else if (resendBtn) {
          resendBtn.disabled = false;
        }
      }

      function isValidCode() {
        const v = (codeInput?.value || "").trim();
        return /^\d{6}$/.test(v);
      }

      async function getJSON(url) {
        const r = await fetch(url, { method: "GET", credentials: "same-origin" });
        const data = await r.json().catch(() => null);
        return { ok: r.ok, data };
      }

      async function postJSON(url, body) {
        const r = await fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify(body),
        });
        const data = await r.json().catch(() => null);
        return { ok: r.ok, data };
      }

      function renderSummary(amountNet) {
        if (elAddr) elAddr.value = to_address;
        if (elAmt) elAmt.value = `${Number(amountNet).toFixed(2)} USDT`;
      }

      async function loadEmailOptions() {
        setError("");
        if (resendBtn) resendBtn.disabled = true;

        const seq = ++requestSeq;

        const { ok: okOpt, data: opt } = await getJSON("/api/withdraw/email-options");
        if (seq !== requestSeq) return;

        if (!okOpt || !opt || !Array.isArray(opt.options)) {
          setError(L("Не удалось загрузить варианты почты.", "Failed to load email options."));
          return;
        }

        const options = opt.options;
        const def = opt.default_slot;

        if (options.length >= 2) {
          if (selEmail) {
            selEmail.classList.remove("hidden");
            selEmail.innerHTML = "";
            options.forEach((o) => {
              const op = document.createElement("option");
              op.value = String(o.slot);
              op.textContent = o.email || o.email_masked || `slot ${o.slot}`;
              selEmail.appendChild(op);
            });
            currentSlot = currentSlot || def || options[0].slot;
            selEmail.value = String(currentSlot);
          }
          if (elEmailText) elEmailText.classList.add("hidden");
        } else {
          if (selEmail) selEmail.classList.add("hidden");
          if (elEmailText) {
            elEmailText.classList.remove("hidden");
            elEmailText.value = options[0]?.email || options[0]?.email_masked || "—";
          }
          currentSlot = def || (options[0] ? options[0].slot : 1);
        }

        if (getCodeBtn) getCodeBtn.disabled = false;
      }

      async function requestCode() {
        setError("");
        if (getCodeBtn) getCodeBtn.disabled = true;

        const amountGrossStr = Number(amount_gross).toFixed(2);
        const slotToUse = Number(currentSlot || 1);

        const { ok, data } = await postJSON("/api/withdraw/request-code", {
          to_address,
          amount_gross: amountGrossStr,
          email_slot: slotToUse,
        });

        if (!ok || !data || data.status !== "ok") {
          if (getCodeBtn) getCodeBtn.disabled = false;
          const msg = data && data.message;
          if (!msg || (!msg.includes("minute") && !msg.includes("минут") && !msg.includes("раз"))) {
            setError(msg || L("Не удалось отправить код.", "Failed to send code."));
          }
          return;
        }

        token = data.token;
        currentSlot = data.email_slot;

        renderSummary(Number(data.amount_net || (amount_gross - 1)));
        if (codeInput) codeInput.value = "";
        if (confirmBtn) confirmBtn.disabled = true;

        if (resendBtn) startCooldownOnButton();
      }

      async function resendCode() {
        if (!token) return;
        if (resendBtn?.disabled) return;
        setError("");
        if (resendBtn) resendBtn.disabled = true;

        const { ok, data } = await postJSON("/api/withdraw/resend-code", { token });

        if (!ok || !data || data.status !== "ok") {
          if (resendBtn) resendBtn.disabled = false;
          const msg = data && data.message;
          if (!msg || (!msg.includes("minute") && !msg.includes("минут") && !msg.includes("раз"))) {
            setError(msg || L("Не удалось отправить код повторно.", "Failed to resend code."));
          }
          return;
        }

        startCooldownOnButton();
      }

      async function confirmWithdraw() {
        if (!token) return;
        if (!isValidCode()) return;

        setError("");
        confirmBtn.disabled = true;

        const code = codeInput.value.trim();
        const { ok, data } = await postJSON("/api/withdraw/confirm", { token, code });

        confirmBtn.disabled = false;

        if (!ok || !data || data.status === "error") {
          setError((data && data.message) || L("Ошибка подтверждения.", "Confirmation error."));
          return;
        }

        // OK → закрыть confirm modal, открыть "выполняется"
        closeModalLocal(confirmModal);
        openModalLocal(processingModal);
      }

      // init UI
      if (confirmModal) openModalLocal(confirmModal);
      if (elAddr) elAddr.value = to_address;
      renderSummary(Math.max(amount_gross - 1, 0));

      // listeners
      if (codeInput) {
        codeInput.addEventListener("input", () => {
          confirmBtn.disabled = !isValidCode();
        });
      }
      if (resendBtn) {
        resendBtn.addEventListener("click", (e) => {
          e.preventDefault();
          resendCode();
        });
      }
      if (confirmBtn) confirmBtn.addEventListener("click", confirmWithdraw);

      if (selEmail) {
        selEmail.addEventListener("change", () => {
          currentSlot = Number(selEmail.value);
          token = null;
          if (resendBtn && typeof window.clearResendCooldown === "function") {
            window.clearResendCooldown(resendBtn, L("Получить код повторно", "Resend code"));
          } else if (resendBtn) {
            resendBtn.disabled = true;
          }
          if (getCodeBtn) getCodeBtn.disabled = false;
        });
      }

      if (getCodeBtn) {
        getCodeBtn.addEventListener("click", requestCode);
      }

      // старт: только загрузка email options, код НЕ отправляется автоматически
      loadEmailOptions();
    }

    document.addEventListener("DOMContentLoaded", () => {
      initDepositModal();
      initWithdrawModal();
    });
  })();
  