(function () {
    function setMsg(el, text) {
      if (!el) return;
      el.textContent = text || "";
    }
  
    function show(el) { el && el.classList.remove("hidden"); }
    function hide(el) { el && el.classList.add("hidden"); }
  
    function is6digits(v) {
      return /^\d{6}$/.test((v || "").trim());
    }
  
    document.addEventListener("DOMContentLoaded", () => {
      const p1 = document.getElementById("newPassword");
      const p2 = document.getElementById("confirmPassword");
  
      const mismatch = document.getElementById("passwordMismatch");
      const sendBtn = document.getElementById("sendPasswordCodeBtn");
  
      const codeBlock = document.getElementById("codeBlock");
      const codeInput = document.getElementById("passwordCodeInput");
      const confirmBtn = document.getElementById("confirmPasswordChangeBtn");
  
      const msg = document.getElementById("passwordChangeMessage");
  
      if (!p1 || !p2 || !sendBtn || !confirmBtn) return;
  
      function passwordsMatch() {
        const a = (p1.value || "").trim();
        const b = (p2.value || "").trim();
        return a.length > 0 && b.length > 0 && a === b;
      }
  
      function refresh() {
        const ok = passwordsMatch();
        if (ok) hide(mismatch); else show(mismatch);
  
        sendBtn.disabled = !ok;
  
        // confirm enabled only when: step2 visible + code valid + passwords still match
        const canConfirm = !codeBlock.classList.contains("hidden") && ok && is6digits(codeInput?.value);
        confirmBtn.disabled = !canConfirm;
      }
  
      p1.addEventListener("input", () => { setMsg(msg, ""); refresh(); });
      p2.addEventListener("input", () => { setMsg(msg, ""); refresh(); });
      codeInput?.addEventListener("input", () => { setMsg(msg, ""); refresh(); });
  
      // Step 1: request code
      sendBtn.addEventListener("click", async () => {
        setMsg(msg, "");
        refresh();
        if (sendBtn.disabled) return;
  
        const new_password = (p1.value || "").trim();
  
        try {
          const resp = await fetch("/settings/security/send-code", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ new_password }),
            credentials: "same-origin",
          });
  
          if (!resp.ok) {
            const t = await resp.text().catch(() => "");
            setMsg(msg, t || `Ошибка (HTTP ${resp.status}).`);
            return;
          }
  
          // success => show code input
          show(codeBlock);
          setMsg(msg, "OK");
          refresh();
          if (codeInput) codeInput.focus();
  
        } catch {
          setMsg(msg, "Сетевая ошибка. Повторите попытку.");
        }
      });
  
      // Step 2: change password
      confirmBtn.addEventListener("click", async () => {
        setMsg(msg, "");
        refresh();
        if (confirmBtn.disabled) return;
  
        const new_password = (p1.value || "").trim();
        const code = (codeInput?.value || "").trim();
  
        try {
          const resp = await fetch("/settings/security/change-password", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ new_password, code }),
            credentials: "same-origin",
          });
  
          if (!resp.ok) {
            const t = await resp.text().catch(() => "");
            setMsg(msg, t || `Ошибка (HTTP ${resp.status}).`);
            return;
          }
  
          // success
          // бэк возвращает {status:"ok"} (и может вернуть redirect — если появится, легко добавить)
          setMsg(msg, document.documentElement.lang === "en" ? "Password changed" : "Пароль изменён");
  
          // по желанию: window.location.href = "/";
        } catch {
          setMsg(msg, "Сетевая ошибка. Повторите попытку.");
        }
      });
  
      // init state
      hide(codeBlock);
      refresh();
    });
  })();
  