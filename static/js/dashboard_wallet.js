(() => {
    const lang = (document.documentElement.lang || "en").toLowerCase();
    const L = (ru, en) => (lang === "en" ? en : ru);

    function lockBtn(btn) {
      if (!btn) return false;
      if (typeof window.lockActionButton === "function") {
        return window.lockActionButton(btn);
      }
      if (btn.dataset.pending === "1") return false;
      btn.dataset.pending = "1";
      btn.disabled = true;
      return true;
    }

    function unlockBtn(btn) {
      if (!btn) return;
      if (typeof window.unlockActionButton === "function") {
        window.unlockActionButton(btn);
        return;
      }
      delete btn.dataset.pending;
      btn.disabled = false;
    }

    function startBtnCooldown(btn, seconds, label) {
      if (!btn) return;
      if (typeof window.startResendCooldown === "function") {
        window.startResendCooldown(btn, seconds, label);
        return;
      }

      const baseText = label || btn.textContent;
      let left = seconds;
      btn.disabled = true;
      btn.textContent = lang === "en" ? `Retry in ${left}s` : `Повторно через ${left}с`;

      const id = setInterval(() => {
        left -= 1;
        if (left <= 0) {
          clearInterval(id);
          btn.disabled = false;
          btn.textContent = baseText;
          return;
        }
        btn.textContent = lang === "en" ? `Retry in ${left}s` : `Повторно через ${left}с`;
      }, 1000);
    }

    function isSixDigitCode(value) {
      return /^\d{6}$/.test((value || "").trim());
    }

    function sanitizeDigitCodeInput(input) {
      if (!input) return "";
      const clean = (input.value || "").replace(/\D/g, "").slice(0, 6);
      if (input.value !== clean) input.value = clean;
      return clean;
    }

    function initTotpBlock(blockEl, inputEl, onChange) {
      if (!blockEl || !inputEl) return;

      inputEl.addEventListener("input", () => {
        sanitizeDigitCodeInput(inputEl);
        if (typeof onChange === "function") onChange();
      });

      blockEl.querySelectorAll("[data-totp-help]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const msg = blockEl.querySelector("[data-totp-help-message]");
          if (msg) msg.classList.toggle("hidden");
        });
      });
    }

    function setTotpBlockVisible(blockEl, inputEl, required) {
      if (!blockEl || !inputEl) return;

      blockEl.classList.toggle("hidden", !required);

      if (!required) {
        inputEl.value = "";
        const msg = blockEl.querySelector("[data-totp-help-message]");
        if (msg) msg.classList.add("hidden");
      }
    }

    function getTotpCode(inputEl) {
      return (inputEl?.value || "").trim();
    }

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
  
      function getLiveBalance() {
        return parseFloat(modal.dataset.usdtBalance || "0") || 0;
      }
  
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
  
        const balance = getLiveBalance();

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
          amountInput.value = getLiveBalance().toFixed(2);
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

      document.addEventListener("wb:dashboard-live-updated", () => {
        updateReceiveAndValidation();
      });

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
      cancelPending: false,
      totpRequired: false,
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

      const totpBlock = document.getElementById("w2TotpBlock");
      const totpInput = document.getElementById("w2TotpCode");

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
        const emailCode = (codeInput?.value || "").trim();
        const totpCode = getTotpCode(totpInput);

        const emailOk = isSixDigitCode(emailCode);
        const totpOk = !withdrawState.totpRequired || isSixDigitCode(totpCode);

        return emailOk && totpOk;
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

        if (!lockBtn(getCodeBtn)) return;

        const amountGrossStr = Number(withdrawState.amountGross).toFixed(2);
        const slotToUse = Number(withdrawState.emailSlot || 1);

        try {
          const { ok, data } = await postJSON("/api/withdraw/request-code", {
            to_address: withdrawState.toAddress,
            amount_gross: amountGrossStr,
            email_slot: slotToUse,
          });

          if (!ok || !data || data.status !== "ok") {
            unlockBtn(getCodeBtn);
            const msg = data?.message;
            if (!msg || (!msg.includes("minute") && !msg.includes("минут") && !msg.includes("раз"))) {
              setError(msg || L("Не удалось отправить код.", "Failed to send code."));
            }
            return;
          }

          withdrawState.token = data.token;
          withdrawState.emailSlot = data.email_slot;
          withdrawState.codeSent = true;
          withdrawState.totpRequired = data.totp_required === true;

          setTotpBlockVisible(totpBlock, totpInput, withdrawState.totpRequired);

          withdrawState.amountNet = Number(data.amount_net ?? (withdrawState.amountGross - 1));
          renderSummary();

          if (codeInput) codeInput.value = "";
          if (confirmBtn) confirmBtn.disabled = !isValidCode();

          unlockBtn(getCodeBtn);
          startBtnCooldown(getCodeBtn, 60, L("Получить код", "Get code"));

          // Отдельный независимый cooldown для resend-кнопки.
          startCooldownOnButton();
        } catch (e) {
          console.error(e);
          setError(L("Ошибка сети", "Network error"));
          unlockBtn(getCodeBtn);
        }
      }

      async function resendCode() {
        if (!withdrawState.token) return;
        if (resendBtn?.disabled) return;
        if (!lockBtn(resendBtn)) return;

        setError("");

        try {
          const { ok, data } = await postJSON("/api/withdraw/resend-code", {
            token: withdrawState.token,
          });

          if (!ok || !data || data.status !== "ok") {
            unlockBtn(resendBtn);
            const msg = data?.message;
            if (!msg || (!msg.includes("minute") && !msg.includes("минут") && !msg.includes("раз"))) {
              setError(msg || L("Не удалось отправить код повторно.", "Failed to resend code."));
            }
            return;
          }

          unlockBtn(resendBtn);
          startBtnCooldown(resendBtn, 60, L("Получить код повторно", "Resend code"));
        } catch (e) {
          console.error(e);
          setError(L("Ошибка сети", "Network error"));
          unlockBtn(resendBtn);
        }
      }

      async function confirmWithdraw() {
        if (!withdrawState.token) return;

        const code = codeInput?.value?.trim() || "";
        const totpCode = getTotpCode(totpInput);

        if (!isSixDigitCode(code)) {
          setError(L("Введите 6-значный код из письма.", "Enter the 6-digit email code."));
          return;
        }

        if (withdrawState.totpRequired && !isSixDigitCode(totpCode)) {
          setError(L("Введите код Google 2FA.", "Enter Google 2FA code."));
          return;
        }

        if (!lockBtn(confirmBtn)) return;

        setError("");

        try {
          const { ok, data } = await postJSON("/api/withdraw/confirm", {
            token: withdrawState.token,
            code,
            totp_code: withdrawState.totpRequired ? totpCode : "",
          });

          if (!ok || !data || data.status === "error") {
            setError((data?.message) || L("Ошибка подтверждения.", "Confirmation error."));
            unlockBtn(confirmBtn);
            return;
          }

          withdrawState.confirmDone = true;
          closeModalLocal(confirmModal);
          openModalLocal(processingModal);
        } catch (e) {
          console.error(e);
          setError(L("Ошибка сети", "Network error"));
          unlockBtn(confirmBtn);
        }
      }

      async function handleConfirmModalClose() {
        if (withdrawState.confirmDone) return;
        if (withdrawState.cancelPending) return;

        withdrawState.cancelPending = true;

        try {
          if (withdrawState.token) {
            try {
              await postJSON("/api/withdraw/cancel", { token: withdrawState.token });
            } catch (e) {
              console.warn("withdraw/cancel failed (network?), resetting UI anyway:", e);
            }
          }
        } finally {
          withdrawState.token = null;
          withdrawState.codeSent = false;
          withdrawState.emailSlot = null;
          withdrawState.totpRequired = false;
          setTotpBlockVisible(totpBlock, totpInput, false);

          if (codeInput) codeInput.value = "";

          if (resendBtn && typeof window.clearResendCooldown === "function") {
            window.clearResendCooldown(resendBtn, L("Получить код повторно", "Resend code"));
          } else if (resendBtn) {
            resendBtn.disabled = true;
          }

          setError("");

          if (selEmail && selEmail.options?.length) {
            selEmail.selectedIndex = 0;
          }

          if (getCodeBtn) getCodeBtn.disabled = false;
          if (confirmBtn) confirmBtn.disabled = true;

          closeModalLocal(confirmModal);

          withdrawState.cancelPending = false;
        }
      }

      confirmModal._withdrawCloseHandler = handleConfirmModalClose;

      if (codeInput) {
        codeInput.addEventListener("input", () => {
          if (confirmBtn) confirmBtn.disabled = !isValidCode();
        });
      }

      initTotpBlock(totpBlock, totpInput, () => {
        setError("");
        if (confirmBtn) confirmBtn.disabled = !isValidCode();
      });

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
          withdrawState.totpRequired = false;
          setTotpBlockVisible(totpBlock, totpInput, false);
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
      withdrawState.cancelPending = false;
      withdrawState.totpRequired = false;
      setTotpBlockVisible(document.getElementById("w2TotpBlock"), document.getElementById("w2TotpCode"), false);

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

    function initDashboardLivePolling() {
      if (window.location.pathname !== "/dashboard") return;

      const POLL_MS = 10000;
      let inFlight = false;

      function toNumber(value, fallback = 0) {
        const n = Number(value);
        return Number.isFinite(n) ? n : fallback;
      }

      function formatFixed(value, decimals = 2) {
        return toNumber(value, 0).toFixed(decimals);
      }

      function formatSigned(value, decimals = 2) {
        const n = toNumber(value, 0);
        const sign = n > 0 ? "+" : "";
        return `${sign}${n.toFixed(decimals)}`;
      }

      function formatDailyChange(data) {
        const symbol = data?.stable_symbol || "USDT";
        const mode = String(data?.daily_change_display_mode || "").toLowerCase();

        if (mode === "absolute" && data.daily_change_abs != null) {
          return `${formatSigned(data.daily_change_abs, 2)} ${symbol}`;
        }

        if (data.daily_change_pct != null) {
          return `${formatSigned(data.daily_change_pct, 2)}%`;
        }

        return "0.00%";
      }

      function getComplianceKind(data) {
        const userStatus = String(data?.user_compliance_status || "ok").toLowerCase();
        const walletStatus = String(data?.wallet_compliance_status || "ok").toLowerCase();

        if (userStatus === "blocked" || walletStatus === "blocked") return "blocked";
        if (userStatus !== "ok" || walletStatus !== "ok") return "pending";
        return "ok";
      }

      function setButtonAvailable(el, available) {
        if (!el) return;

        if (el.tagName === "A") {
          if (!el.dataset.liveOriginalHref && el.getAttribute("href")) {
            el.dataset.liveOriginalHref = el.getAttribute("href");
          }

          if (available) {
            const originalHref = el.dataset.liveOriginalHref;
            if (originalHref) el.setAttribute("href", originalHref);
            el.removeAttribute("aria-disabled");
            el.removeAttribute("tabindex");
            el.classList.remove("is-disabled");
          } else {
            el.removeAttribute("href");
            el.setAttribute("aria-disabled", "true");
            el.setAttribute("tabindex", "-1");
            el.classList.add("is-disabled");
          }

          return;
        }

        el.disabled = !available;
        el.setAttribute("aria-disabled", available ? "false" : "true");
      }

      function setDepositAvailable(available) {
        const btn = document.querySelector("[data-live-deposit-btn]");
        if (!btn) return;

        btn.disabled = !available;
        btn.setAttribute("aria-disabled", available ? "false" : "true");

        if (available) {
          btn.setAttribute("data-modal-open", "depositModal");
        } else {
          btn.removeAttribute("data-modal-open");
        }
      }

      function renderComplianceWarning(kind) {
        const box = document.querySelector("[data-live-compliance-warning]");
        if (!box) return;

        if (kind === "ok") {
          box.innerHTML = "";
          return;
        }

        const text = kind === "blocked"
          ? L(
              "На адрес поступили USDT с санкционными/ограниченными признаками. Пополнение и покупка паёв отключены. Пожалуйста, выведите средства обратно.",
              "USDT with sanctioned/restricted indicators has been received to the address. Deposits and fund share purchases are disabled. Please withdraw the funds back."
            )
          : L(
              "Идёт проверка комплаенса, дождитесь её завершения.",
              "Compliance check in progress, please wait for it to complete."
            );

        const extraClass = kind === "pending" ? " compliance-warning--pending" : "";

        box.innerHTML = `
      <div class="compliance-warning${extraClass}" role="alert">
        <div class="compliance-warning__icon" aria-hidden="true">
          <svg width="18" height="18" viewBox="0 0 24 24">
            <path d="M12 3L2 21h20L12 3z" fill="none" stroke="currentColor" stroke-width="2"></path>
            <path d="M12 9v5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"></path>
            <circle cx="12" cy="17" r="1" fill="currentColor"></circle>
          </svg>
        </div>
        <div class="compliance-warning__text">
          <strong>${text}</strong>
        </div>
      </div>
    `;
      }

      function updateTopCard(data) {
        const symbol = data?.stable_symbol || "USDT";

        const currentBalanceEl = document.querySelector("[data-live-current-balance]");
        if (currentBalanceEl) {
          currentBalanceEl.textContent = `${formatFixed(data.current_balance, 2)} ${symbol}`;
          currentBalanceEl.dataset.stableSymbol = symbol;
        }

        const dailyChangeEl = document.querySelector("[data-live-daily-change]");
        if (dailyChangeEl) {
          dailyChangeEl.textContent = formatDailyChange(data);
          dailyChangeEl.dataset.stableSymbol = symbol;
        }
      }

      function updateStablecoinBalance(data) {
        const total = formatFixed(data.usdt_balance_total, 2);
        const available = formatFixed(data.usdt_balance_available, 2);

        const stableEl = document.querySelector("[data-live-stablecoin-balance]");
        if (stableEl) {
          stableEl.textContent = total;
        }

        const withdrawModal = document.getElementById("withdrawModal");
        if (withdrawModal) {
          withdrawModal.dataset.usdtBalance = String(available);
        }

        const availableEl = document.querySelector("[data-live-withdraw-available-value]");
        if (availableEl) {
          availableEl.textContent = available;
        }
      }

      function updateFunds(data) {
        const funds = Array.isArray(data?.funds) ? data.funds : [];
        if (!funds.length) return;

        const byCode = new Map();
        funds.forEach((f) => {
          if (f && f.code) byCode.set(String(f.code), f);
        });

        document.querySelectorAll("[data-live-fund-row]").forEach((row) => {
          const code = row.dataset.fundCode;
          const fund = byCode.get(code);
          if (!fund) return;

          const priceEl = row.querySelector("[data-live-fund-price]");
          const sharesEl = row.querySelector("[data-live-fund-shares]");
          const valueEl = row.querySelector("[data-live-fund-value]");

          if (priceEl) priceEl.textContent = `${formatFixed(fund.price, 2)} USDT`;
          if (sharesEl) sharesEl.textContent = formatFixed(fund.shares, 4);
          if (valueEl) valueEl.textContent = `${formatFixed(fund.value, 2)} USDT`;
        });
      }

      function updateCompliance(data) {
        const kind = getComplianceKind(data);
        const available = kind === "ok";

        setDepositAvailable(available);

        document.querySelectorAll("[data-live-invest-btn]").forEach((btn) => {
          setButtonAvailable(btn, available);
        });

        renderComplianceWarning(kind);
      }

      function applyDashboardLive(data) {
        if (!data || data.status !== "ok") return;

        updateTopCard(data);
        updateStablecoinBalance(data);
        updateFunds(data);
        updateCompliance(data);

        document.dispatchEvent(new CustomEvent("wb:dashboard-live-updated", {
          detail: data,
        }));
      }

      async function pollDashboardLive() {
        if (inFlight) return;
        inFlight = true;

        try {
          const resp = await fetch("/api/dashboard/live", {
            method: "GET",
            credentials: "same-origin",
            headers: { "Accept": "application/json" },
          });

          if (!resp.ok) return;

          const data = await resp.json().catch(() => null);
          applyDashboardLive(data);
        } catch (err) {
          console.warn("[dashboard-live] polling failed:", err);
        } finally {
          inFlight = false;
        }
      }

      pollDashboardLive();
      window.setInterval(pollDashboardLive, POLL_MS);
    }

    document.addEventListener("DOMContentLoaded", () => {
      initDepositModal();
      initWithdrawModal();
      initWithdrawConfirmModal();
      setupWithdrawConfirmCloseInterceptor();
      initDashboardLivePolling();
    });
  })();
  