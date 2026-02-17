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
          if (!msgEl) return;
          msgEl.textContent = L("Скоро будет доступно", "Coming soon");
        });
      }
  
      // init state
      setAddrUI("empty");
      updateReceiveAndValidation();
    }
  
    document.addEventListener("DOMContentLoaded", () => {
      initDepositModal();
      initWithdrawModal();
    });
  })();
  