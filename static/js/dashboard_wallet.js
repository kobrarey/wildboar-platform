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

          openWithdrawConfirmModal({ to_address, amount_gross });
        });
      }
  
      // init state
      setAddrUI("empty");
      updateReceiveAndValidation();
    }

    const withdrawState = {
      token: null,
      toAddress: null,
      amountGross: null,
      amountNet: null,
      emailSlot: null,
      codeSent: false,
      cooldownUntil: null,
      requestSeq: 0,
      confirmDone: false,
      emailOptions: null,
    };

    function initWithdrawConfirmModal() {
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

      if (!confirmModal) return;

      function setError(msg) {
        if (errEl) errEl.textContent = msg || "";
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

      function renderSummary() {
        if (elAddr && withdrawState.toAddress) elAddr.value = withdrawState.toAddress;
        if (elAmt && withdrawState.amountNet != null) elAmt.value = `${Number(withdrawState.amountNet).toFixed(2)} USDT`;
      }

      async function requestCode() {
        setError("");
        if (getCodeBtn) getCodeBtn.disabled = true;

        const amountGrossStr = Number(withdrawState.amountGross).toFixed(2);
        const slotToUse = Number(withdrawState.emailSlot || 1);

        const { ok, data } = await postJSON("/api/withdraw/request-code", {
          to_address: withdrawState.toAddress,
          amount_gross: amountGrossStr,
          email_slot: slotToUse,
        });

        if (!ok || !data || data.status !== "ok") {
          if (getCodeBtn) getCodeBtn.disabled = false;
          const msg = data?.message;
          if (!msg || (!msg.includes("minute") && !msg.includes("минут") && !msg.includes("раз"))) {
            setError(msg || L("Не удалось отправить код.", "Failed to send code."));
          }
          return;
        }

        withdrawState.token = data.token;
        withdrawState.emailSlot = data.email_slot;
        withdrawState.codeSent = true;

        withdrawState.amountNet = Number(data.amount_net ?? (withdrawState.amountGross - 1));
        renderSummary();
        if (codeInput) codeInput.value = "";
        if (confirmBtn) confirmBtn.disabled = true;

        startCooldownOnButton();
      }

      async function resendCode() {
        if (!withdrawState.token) return;
        if (resendBtn?.disabled) return;
        setError("");
        if (resendBtn) resendBtn.disabled = true;

        const { ok, data } = await postJSON("/api/withdraw/resend-code", { token: withdrawState.token });

        if (!ok || !data || data.status !== "ok") {
          if (resendBtn) resendBtn.disabled = false;
          const msg = data?.message;
          if (!msg || (!msg.includes("minute") && !msg.includes("минут") && !msg.includes("раз"))) {
            setError(msg || L("Не удалось отправить код повторно.", "Failed to resend code."));
          }
          return;
        }

        startCooldownOnButton();
      }

      async function confirmWithdraw() {
        if (!withdrawState.token) return;
        if (!isValidCode()) return;

        setError("");
        if (confirmBtn) confirmBtn.disabled = true;

        const code = codeInput?.value?.trim() || "";
        const { ok, data } = await postJSON("/api/withdraw/confirm", { token: withdrawState.token, code });

        if (confirmBtn) confirmBtn.disabled = false;

        if (!ok || !data || data.status === "error") {
          setError((data?.message) || L("Ошибка подтверждения.", "Confirmation error."));
          return;
        }

        withdrawState.confirmDone = true;
        closeModalLocal(confirmModal);
        openModalLocal(processingModal);
      }

      async function handleConfirmModalClose() {
        if (withdrawState.confirmDone) return;

        if (withdrawState.token) {
          try {
            await postJSON("/api/withdraw/cancel", { token: withdrawState.token });
          } catch (e) {
            console.warn("withdraw/cancel failed (network?), resetting UI anyway:", e);
          }
        }

        withdrawState.token = null;
        withdrawState.codeSent = false;
        withdrawState.emailSlot = null;
        if (codeInput) codeInput.value = "";
        if (resendBtn && typeof window.clearResendCooldown === "function") {
          window.clearResendCooldown(resendBtn, L("Получить код повторно", "Resend code"));
        } else if (resendBtn) {
          resendBtn.disabled = true;
        }
        setError("");
        if (selEmail && selEmail.options?.length) selEmail.selectedIndex = 0;
        if (getCodeBtn) getCodeBtn.disabled = false;
        if (confirmBtn) confirmBtn.disabled = true;
        closeModalLocal(confirmModal);
      }

      confirmModal._withdrawCloseHandler = handleConfirmModalClose;

      if (codeInput) {
        codeInput.addEventListener("input", () => {
          if (confirmBtn) confirmBtn.disabled = !isValidCode();
        });
      }
      if (resendBtn) {
        resendBtn.addEventListener("click", (e) => {
          e.preventDefault();
          resendCode();
        });
      }
      if (confirmBtn) {
        confirmBtn.addEventListener("click", confirmWithdraw);
      }
      if (selEmail) {
        selEmail.addEventListener("change", () => {
          withdrawState.emailSlot = Number(selEmail.value);
          withdrawState.token = null;
          withdrawState.codeSent = false;
          if (codeInput) codeInput.value = "";
          if (confirmBtn) confirmBtn.disabled = true;
          setError(L("Нажмите «Получить код» для отправки на выбранную почту.", "Press «Get code» to send to the selected email."));
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
    }

    function openWithdrawConfirmModal({ to_address, amount_gross }) {
      const confirmModal = document.getElementById("withdrawConfirmModal");
      const processingModal = document.getElementById("withdrawProcessingModal");
      const elAddr = document.getElementById("w2Address");
      const elAmt = document.getElementById("w2AmountNet");
      const codeInput = document.getElementById("w2Code");
      const resendBtn = document.getElementById("w2ResendBtn");
      const errEl = document.getElementById("w2Error");
      const getCodeBtn = document.getElementById("w2GetCodeBtn");
      const confirmBtn = document.getElementById("w2ConfirmBtn");
      const selEmail = document.getElementById("w2EmailSelect");

      withdrawState.token = null;
      withdrawState.toAddress = to_address;
      withdrawState.amountGross = amount_gross;
      withdrawState.amountNet = Math.max(amount_gross - 1, 0);
      withdrawState.emailSlot = null;
      withdrawState.codeSent = false;
      withdrawState.confirmDone = false;

      if (codeInput) codeInput.value = "";
      if (errEl) errEl.textContent = "";
      if (resendBtn && typeof window.clearResendCooldown === "function") {
        window.clearResendCooldown(resendBtn, L("Получить код повторно", "Resend code"));
      } else if (resendBtn) {
        resendBtn.disabled = true;
      }
      if (getCodeBtn) getCodeBtn.disabled = false;
      if (confirmBtn) confirmBtn.disabled = true;

      if (elAddr) elAddr.value = to_address;
      if (elAmt) elAmt.value = `${withdrawState.amountNet.toFixed(2)} USDT`;

      loadEmailOptionsForConfirm();

      if (confirmModal) {
        confirmModal.classList.add("is-open");
        confirmModal.setAttribute("aria-hidden", "false");
        document.body.style.overflow = "hidden";
      }
    }

    async function loadEmailOptionsForConfirm() {
      const selEmail = document.getElementById("w2EmailSelect");
      const elEmailText = document.getElementById("w2EmailText");
      const getCodeBtn = document.getElementById("w2GetCodeBtn");
      const resendBtn = document.getElementById("w2ResendBtn");
      const errEl = document.getElementById("w2Error");

      if (errEl) errEl.textContent = "";
      if (resendBtn) resendBtn.disabled = true;

      const seq = ++withdrawState.requestSeq;

      try {
        const r = await fetch("/api/withdraw/email-options", { method: "GET", credentials: "same-origin" });
        const opt = await r.json().catch(() => null);
        if (seq !== withdrawState.requestSeq) return;

        if (!r.ok || !opt || !Array.isArray(opt.options)) {
          if (errEl) errEl.textContent = L("Не удалось загрузить варианты почты.", "Failed to load email options.");
          return;
        }

        withdrawState.emailOptions = opt.options;
        const def = opt.default_slot;
        withdrawState.emailSlot = def ?? (opt.options[0]?.slot ?? 1);

        if (opt.options.length >= 2 && selEmail) {
          selEmail.classList.remove("hidden");
          selEmail.innerHTML = "";
          opt.options.forEach((o) => {
            const op = document.createElement("option");
            op.value = String(o.slot);
            op.textContent = o.email || o.email_masked || `slot ${o.slot}`;
            selEmail.appendChild(op);
          });
          selEmail.value = String(withdrawState.emailSlot);
          if (elEmailText) elEmailText.classList.add("hidden");
        } else {
          if (selEmail) selEmail.classList.add("hidden");
          if (elEmailText) {
            elEmailText.classList.remove("hidden");
            elEmailText.value = opt.options[0]?.email || opt.options[0]?.email_masked || "—";
          }
        }

        if (getCodeBtn) getCodeBtn.disabled = false;
      } catch (_) {
        if (errEl) errEl.textContent = L("Не удалось загрузить варианты почты.", "Failed to load email options.");
      }
    }

    function setupWithdrawConfirmCloseInterceptor() {
      const confirmModal = document.getElementById("withdrawConfirmModal");
      if (!confirmModal) return;

      document.addEventListener(
        "click",
        (e) => {
          const closeTarget = e.target.closest("[data-modal-close]");
          if (!closeTarget) return;
          const modal = closeTarget.closest(".modal");
          if (modal !== confirmModal) return;
          if (!confirmModal.classList.contains("is-open")) return;

          e.stopImmediatePropagation();
          const handler = confirmModal._withdrawCloseHandler;
          if (typeof handler === "function") {
            handler();
          } else {
            confirmModal.classList.remove("is-open");
            confirmModal.setAttribute("aria-hidden", "true");
            document.body.style.overflow = "";
          }
        },
        true
      );

      document.addEventListener(
        "keydown",
        (e) => {
          if (e.key !== "Escape") return;
          if (!confirmModal.classList.contains("is-open")) return;
          const opened = document.querySelector(".modal.is-open");
          if (opened !== confirmModal) return;

          e.stopImmediatePropagation();
          const handler = confirmModal._withdrawCloseHandler;
          if (typeof handler === "function") {
            handler();
          } else {
            confirmModal.classList.remove("is-open");
            confirmModal.setAttribute("aria-hidden", "true");
            document.body.style.overflow = "";
          }
        },
        true
      );
    }

    document.addEventListener("DOMContentLoaded", () => {
      initDepositModal();
      initWithdrawModal();
      initWithdrawConfirmModal();
      setupWithdrawConfirmCloseInterceptor();
    });
  })();
  