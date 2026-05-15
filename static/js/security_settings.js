document.addEventListener("DOMContentLoaded", () => {
  initAccordions();
  initPasswordChange();
  initTotpSecurity();
  initEmailsManager();
});

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

  let left = seconds;
  btn.disabled = true;

  const baseText = btn.textContent;
  if (label) {
    btn.textContent = label;
  }
  const id = setInterval(() => {
    left -= 1;
    if (left <= 0) {
      clearInterval(id);
      btn.disabled = false;
      btn.textContent = baseText;
    } else {
      btn.textContent = document.documentElement.lang === "en"
        ? `Retry in ${left}s`
        : `Повторно через ${left}с`;
    }
  }, 1000);
}

function isTotpEnabledOnPage() {
  const root = document.querySelector("[data-totp-root]");
  return root?.dataset?.totpEnabled === "1";
}

function sanitizeTotpInput(input) {
  if (!input) return "";
  const clean = (input.value || "").replace(/\D/g, "").slice(0, 6);
  if (input.value !== clean) input.value = clean;
  return clean;
}

function initInlineTotpBlock(blockEl, inputEl, onChange) {
  if (!blockEl || !inputEl) return;

  inputEl.addEventListener("input", () => {
    sanitizeTotpInput(inputEl);
    if (typeof onChange === "function") onChange();
  });

  blockEl.querySelectorAll("[data-totp-help]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const msg = blockEl.querySelector("[data-totp-help-message]");
      if (msg) msg.classList.toggle("hidden");
    });
  });
}

function setInlineTotpVisible(blockEl, inputEl, visible) {
  if (!blockEl || !inputEl) return;

  blockEl.classList.toggle("hidden", !visible);

  if (!visible) {
    inputEl.value = "";
    const msg = blockEl.querySelector("[data-totp-help-message]");
    if (msg) msg.classList.add("hidden");
  }
}

function getInlineTotpCode(inputEl) {
  return (inputEl?.value || "").trim();
}

async function parseJsonOrText(resp) {
  const ct = (resp.headers.get("content-type") || "").toLowerCase();

  if (ct.includes("application/json")) {
    const data = await resp.json().catch(() => null);
    return {
      data,
      message: data?.message || data?.detail || "",
    };
  }

  const text = await resp.text().catch(() => "");
  return {
    data: null,
    message: text,
  };
}

function initAccordions() {
  const accordions = document.querySelectorAll("[data-accordion]");
  accordions.forEach((acc) => {
    const btn = acc.querySelector("[data-acc-toggle]");
    const body = acc.querySelector(".accordion__body");
    if (!btn || !body) return;

    const sync = () => {
      const isOpen = acc.classList.contains("is-open");
      btn.setAttribute("aria-expanded", isOpen ? "true" : "false");
    };

    sync();

    btn.addEventListener("click", () => {
      acc.classList.toggle("is-open");
      sync();
    });
  });
}

function initPasswordChange() {
  const newPassword = document.getElementById("newPassword");
  const confirmPassword = document.getElementById("confirmPassword");
  const mismatch = document.getElementById("passwordMismatch");
  const sendCodeBtn = document.getElementById("sendPasswordCodeBtn");
  const confirmBtn = document.getElementById("confirmPasswordChangeBtn");
  const codeBlock = document.getElementById("codeBlock");
  const codeInput = document.getElementById("passwordCodeInput");
  const resendBtn = document.getElementById("passwordCodeResendBtn");
  const msg = document.getElementById("passwordChangeMessage");
  const slotEl = document.getElementById("passwordEmailSlot");
  const passwordTotpBlock = document.getElementById("passwordChangeTotpBlock");
  const passwordTotpInput = document.getElementById("passwordChangeTotpCode");
  let passwordChangeTotpRequired = false;

  if (!newPassword || !confirmPassword || !sendCodeBtn || !confirmBtn || !codeBlock || !codeInput || !msg || !slotEl) return;

  const isEn = document.documentElement.lang === "en";
  const passwordChangedModal = document.getElementById("passwordChangedModal");
  const backdrop = document.getElementById("backdrop");
  const closePasswordChangedEls = document.querySelectorAll("[data-password-changed-close]");

  function openPasswordChangedModal() {
    if (!passwordChangedModal) return;
    passwordChangedModal.classList.add("is-open");
    passwordChangedModal.setAttribute("aria-hidden", "false");
    if (backdrop) {
      backdrop.hidden = false;
    }
  }

  function closePasswordChangedModal() {
    if (!passwordChangedModal) return;
    passwordChangedModal.classList.remove("is-open");
    passwordChangedModal.setAttribute("aria-hidden", "true");
    if (backdrop) {
      backdrop.hidden = true;
    }
  }

  closePasswordChangedEls.forEach((el) => {
    el.addEventListener("click", () => {
      closePasswordChangedModal();
    });
  });

  function setMsg(text) {
    msg.textContent = text || "";
  }

  function setError(text) {
    if (!mismatch) return;
    mismatch.textContent = text || (isEn ? "Passwords do not match" : "Пароли не совпадают");
    mismatch.classList.toggle("hidden", !text);
  }

  function passwordsMatch() {
    const a = (newPassword.value || "").trim();
    const b = (confirmPassword.value || "").trim();
    return a.length > 0 && a === b;
  }

  function refreshState() {
    const ok = passwordsMatch();
    if (mismatch) mismatch.classList.toggle("hidden", ok);
    sendCodeBtn.disabled = !ok;

    // до получения кода нельзя менять пароль
    if (codeBlock.classList.contains("hidden")) {
      confirmBtn.disabled = true;
    }
  }

  function refreshPasswordConfirmState() {
    if (codeBlock.classList.contains("hidden")) {
      confirmBtn.disabled = true;
      return;
    }

    const emailCode = (codeInput.value || "").trim();
    const totpCode = getInlineTotpCode(passwordTotpInput);

    const emailOk = isSixDigits(emailCode);
    const totpOk = !passwordChangeTotpRequired || isSixDigits(totpCode);

    confirmBtn.disabled = !(emailOk && totpOk);
  }

  newPassword.addEventListener("input", () => {
    setMsg("");
    refreshState();
  });

  confirmPassword.addEventListener("input", () => {
    setMsg("");
    refreshState();
  });

  codeInput.addEventListener("input", () => {
    setMsg("");
    setError("");
    refreshPasswordConfirmState();
  });

  initInlineTotpBlock(passwordTotpBlock, passwordTotpInput, () => {
    setMsg("");
    setError("");
    refreshPasswordConfirmState();
  });

  sendCodeBtn.addEventListener("click", async () => {
    setMsg("");
    if (!passwordsMatch()) {
      setError(isEn ? "Passwords do not match" : "Пароли не совпадают");
      return;
    }
    setError("");

    const slot = parseInt(slotEl.value, 10) || 1;

    if (!lockBtn(sendCodeBtn)) return;

    try {
      const resp = await fetch("/settings/security/send-code", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          new_password: newPassword.value,
          slot: slot,
        }),
      });

      if (!resp.ok) {
        const raw = await resp.text().catch(() => "");
        let msgText = raw || (isEn ? `Error (HTTP ${resp.status}).` : `Ошибка (HTTP ${resp.status}).`);
        // если backend вернул JSON {"status": "...", "message": "..."}, показываем только message
        if (raw) {
          try {
            const data = JSON.parse(raw);
            if (data && typeof data.message === "string") {
              msgText = data.message;
            }
          } catch {
            // не JSON — оставляем как есть
          }
        }
        // показываем текст как "красную" ошибку под полями пароля
        setError(msgText);
        unlockBtn(sendCodeBtn);
        return;
      }

      const parsed = await parseJsonOrText(resp);
      const data = parsed.data || {};
      passwordChangeTotpRequired = data.totp_required === true;
      setInlineTotpVisible(passwordTotpBlock, passwordTotpInput, passwordChangeTotpRequired);

      // ok
      codeBlock.classList.remove("hidden");
      refreshPasswordConfirmState();
      setMsg(isEn ? "Code sent. Please enter the code from email." : "Код отправлен. Введите код из письма.");
      codeInput.focus();

      unlockBtn(sendCodeBtn);
      startBtnCooldown(
        sendCodeBtn,
        60,
        isEn ? "Continue" : "Продолжить"
      );
    } catch (e) {
      console.error(e);
      setMsg(isEn ? "Network error" : "Ошибка сети");
      unlockBtn(sendCodeBtn);
    }
  });

  // повторная отправка кода для смены пароля
  resendBtn?.addEventListener("click", async () => {
    if (resendBtn.disabled) return;
    if (!lockBtn(resendBtn)) return;

    setMsg("");
    setError("");

    if (!passwordsMatch()) {
      setError(isEn ? "Passwords do not match" : "Пароли не совпадают");
      unlockBtn(resendBtn);
      return;
    }

    const slot = parseInt(slotEl.value, 10) || 1;

    const originalText = resendBtn.textContent;
    resendBtn.textContent = isEn ? "Sending..." : "Отправляем...";

    try {
      const resp = await fetch("/settings/security/send-code", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          new_password: newPassword.value,
          slot: slot,
        }),
      });

      if (resp.ok) {
        const parsed = await parseJsonOrText(resp);
        const data = parsed.data || {};
        passwordChangeTotpRequired = data.totp_required === true;
        setInlineTotpVisible(passwordTotpBlock, passwordTotpInput, passwordChangeTotpRequired);
        refreshPasswordConfirmState();
        unlockBtn(resendBtn);
        startBtnCooldown(
          resendBtn,
          60,
          isEn ? "Send code again" : "Отправить код ещё раз"
        );
        setMsg(isEn ? "Code sent. Please enter the code from email." : "Код отправлен. Введите код из письма.");
        return;
      }

      const raw = await resp.text().catch(() => "");
      let msgText = raw || `HTTP ${resp.status}`;
      try {
        if (raw) {
          const data = JSON.parse(raw);
          if (data && typeof data.message === "string") {
            msgText = data.message;
          }
        }
      } catch {
        // не JSON — оставляем как есть
      }
      setMsg(msgText);
      unlockBtn(resendBtn);
    } catch (e) {
      console.error(e);
      setMsg(isEn ? "Network error" : "Ошибка сети");
      unlockBtn(resendBtn);
    }
  });

  confirmBtn.addEventListener("click", async () => {
    setMsg("");
    setError("");

    if (!passwordsMatch()) {
      setError(isEn ? "Passwords do not match" : "Пароли не совпадают");
      return;
    }

    const code = (codeInput.value || "").trim();
    const totpCode = getInlineTotpCode(passwordTotpInput);
    if (!isSixDigits(code)) {
      setMsg(isEn ? "Enter a 6-digit code" : "Введите 6-значный код");
      return;
    }

    if (passwordChangeTotpRequired && !isSixDigits(totpCode)) {
      setMsg(isEn ? "Enter Google 2FA code" : "Введите код Google 2FA");
      return;
    }

    if (!lockBtn(confirmBtn)) return;

    try {
      const resp = await fetch("/settings/security/change-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          new_password: newPassword.value,
          code: code,
          totp_code: passwordChangeTotpRequired ? totpCode : "",
        }),
      });

      if (!resp.ok) {
        const errText = await resp.text();
        setMsg(errText || `HTTP ${resp.status}`);
        unlockBtn(confirmBtn);
        return;
      }

      // успех
      setMsg(isEn ? "Password changed" : "Пароль изменён");
      openPasswordChangedModal();

      // сбрасываем форму в исходное состояние
      newPassword.value = "";
      confirmPassword.value = "";
      codeInput.value = "";

      if (mismatch) {
        mismatch.classList.add("hidden");
      }
      // скрываем блок ввода кода и блокируем кнопку подтверждения,
      // пока пользователь снова не введёт новый пароль и повтор
      if (codeBlock) {
        codeBlock.classList.add("hidden");
      }
      confirmBtn.disabled = true;
      sendCodeBtn.disabled = true;

      passwordChangeTotpRequired = false;
      setInlineTotpVisible(passwordTotpBlock, passwordTotpInput, false);

      // обновим состояние, чтобы корректно пересчитать доступность кнопок
      refreshState();
    } catch (e) {
      console.error(e);
      setMsg(isEn ? "Network error" : "Ошибка сети");
      unlockBtn(confirmBtn);
    }
  });

  // init
  if (mismatch) mismatch.classList.add("hidden");
  refreshState();
}

function initTotpSecurity() {
  const root = document.querySelector("[data-totp-root]");
  if (!root) return;

  const isEn = document.documentElement.lang === "en";
  const L = (ru, en) => (isEn ? en : ru);

  const disabledPanel = root.querySelector("[data-totp-disabled-panel]");
  const enabledPanel = root.querySelector("[data-totp-enabled-panel]");
  const statusBadge = root.querySelector("[data-totp-status-badge]");
  const confirmedAtEl = root.querySelector("[data-totp-confirmed-at]");

  const enableBtn = document.getElementById("totpEnableBtn");
  const setupPanel = document.getElementById("totpSetupPanel");
  const qrBox = document.getElementById("totpQrBox");
  const manualKey = document.getElementById("totpManualKey");
  const setupCode = document.getElementById("totpSetupCode");
  const confirmBtn = document.getElementById("totpConfirmBtn");

  const recoveryPanel = document.getElementById("totpRecoveryPanel");
  const recoveryCodesBox = document.getElementById("totpRecoveryCodes");
  const recoveryDoneBtn = document.getElementById("totpRecoveryDoneBtn");

  const disableCode = document.getElementById("totpDisableCode");
  const disableBtn = document.getElementById("totpDisableBtn");

  const errorEl = document.getElementById("totpError");
  const msgEl = document.getElementById("totpMessage");

  function setError(text) {
    if (errorEl) errorEl.textContent = text || "";
  }

  function setMsg(text) {
    if (msgEl) msgEl.textContent = text || "";
  }

  async function readResponse(resp) {
    const ct = (resp.headers.get("content-type") || "").toLowerCase();

    if (ct.includes("application/json")) {
      const data = await resp.json().catch(() => null);
      return {
        data,
        message:
          data?.message ||
          data?.detail ||
          (resp.ok ? "" : L("Ошибка запроса.", "Request error.")),
      };
    }

    const text = await resp.text().catch(() => "");
    return {
      data: null,
      message: text || (resp.ok ? "" : L("Ошибка запроса.", "Request error.")),
    };
  }

  async function postJSON(url, body = null) {
    const options = {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
    };

    if (body != null) {
      options.body = JSON.stringify(body);
    }

    const resp = await fetch(url, options);
    const parsed = await readResponse(resp);

    return {
      ok: resp.ok,
      status: resp.status,
      data: parsed.data,
      message: parsed.message,
    };
  }

  function resetSetupState() {
    if (qrBox) qrBox.innerHTML = "";
    if (manualKey) manualKey.value = "";
    if (setupCode) setupCode.value = "";
    if (confirmBtn) confirmBtn.disabled = true;
    if (setupPanel) setupPanel.classList.add("hidden");
  }

  function resetDisableState() {
    if (disableCode) disableCode.value = "";
    if (disableBtn) disableBtn.disabled = true;
  }

  function renderEnabledState(confirmedAtText = "") {
    root.dataset.totpEnabled = "1";

    if (statusBadge) {
      statusBadge.textContent = L("Включено", "Enabled");
      statusBadge.classList.remove("status-badge--warn");
      statusBadge.classList.add("status-badge--ok");
    }

    if (confirmedAtEl) {
      if (confirmedAtText) {
        confirmedAtEl.classList.remove("hidden");
        confirmedAtEl.innerHTML = `${L("Включено:", "Enabled at:")} <strong>${confirmedAtText}</strong>`;
      } else {
        confirmedAtEl.classList.add("hidden");
        confirmedAtEl.textContent = "";
      }
    }

    if (disabledPanel) disabledPanel.classList.add("hidden");
    if (enabledPanel) enabledPanel.classList.remove("hidden");
    if (recoveryPanel) recoveryPanel.classList.add("hidden");

    resetSetupState();
    resetDisableState();
  }

  function renderDisabledState() {
    root.dataset.totpEnabled = "0";

    if (statusBadge) {
      statusBadge.textContent = L("Отключено", "Disabled");
      statusBadge.classList.remove("status-badge--ok");
      statusBadge.classList.add("status-badge--warn");
    }

    if (confirmedAtEl) {
      confirmedAtEl.classList.add("hidden");
      confirmedAtEl.textContent = "";
    }

    if (disabledPanel) disabledPanel.classList.remove("hidden");
    if (enabledPanel) enabledPanel.classList.add("hidden");
    if (recoveryPanel) recoveryPanel.classList.add("hidden");

    resetSetupState();
    resetDisableState();
  }

  setupCode?.addEventListener("input", () => {
    setError("");
    const code = (setupCode.value || "").trim();
    if (confirmBtn) confirmBtn.disabled = !isSixDigits(code);
  });

  disableCode?.addEventListener("input", () => {
    setError("");
    const code = (disableCode.value || "").trim();
    if (disableBtn) disableBtn.disabled = !isSixDigits(code);
  });

  enableBtn?.addEventListener("click", async () => {
    setError("");
    setMsg("");

    if (!lockBtn(enableBtn)) return;

    try {
      const { ok, data, message } = await postJSON("/settings/security/totp/setup/start");

      if (!ok || !data || data.status !== "ok") {
        setError(message || L("Не удалось начать настройку аутентификатора.", "Failed to start authenticator setup."));
        unlockBtn(enableBtn);
        return;
      }

      if (qrBox) qrBox.innerHTML = data.qr_svg || "";
      if (manualKey) manualKey.value = data.manual_key || "";
      if (setupCode) setupCode.value = "";
      if (confirmBtn) confirmBtn.disabled = true;

      if (disabledPanel) disabledPanel.classList.add("hidden");
      if (setupPanel) setupPanel.classList.remove("hidden");
      if (recoveryPanel) recoveryPanel.classList.add("hidden");

      setMsg(L(
        "Отсканируйте QR-код в приложении-аутентификаторе и введите 6-значный код.",
        "Scan the QR code in your authenticator app and enter the 6-digit code."
      ));

      if (setupCode) setupCode.focus();
      unlockBtn(enableBtn);
    } catch (e) {
      console.error(e);
      setError(L("Ошибка сети.", "Network error."));
      unlockBtn(enableBtn);
    }
  });

  confirmBtn?.addEventListener("click", async () => {
    setError("");
    setMsg("");

    const code = (setupCode?.value || "").trim();
    if (!isSixDigits(code)) {
      setError(L("Введите 6-значный код.", "Enter a 6-digit code."));
      return;
    }

    if (!lockBtn(confirmBtn)) return;

    try {
      const { ok, data, message } = await postJSON("/settings/security/totp/setup/confirm", {
        code,
      });

      if (!ok || !data || data.status !== "ok") {
        setError(message || L("Не удалось подтвердить код.", "Failed to confirm the code."));
        unlockBtn(confirmBtn);
        return;
      }

      const recoveryCodes = Array.isArray(data.recovery_codes) ? data.recovery_codes : [];

      if (recoveryCodesBox) {
        recoveryCodesBox.textContent = recoveryCodes.join("\n");
      }

      if (setupPanel) setupPanel.classList.add("hidden");
      if (recoveryPanel) recoveryPanel.classList.remove("hidden");

      setMsg(L(
        "Google Authenticator включён. Сохраните recovery codes перед продолжением.",
        "Google Authenticator is enabled. Save the recovery codes before continuing."
      ));

      unlockBtn(confirmBtn);
    } catch (e) {
      console.error(e);
      setError(L("Ошибка сети.", "Network error."));
      unlockBtn(confirmBtn);
    }
  });

  recoveryDoneBtn?.addEventListener("click", () => {
    setError("");
    setMsg("");

    // После успешного включения проще и надёжнее обновить страницу,
    // чтобы backend заново отдал актуальные totp_enabled / totp_confirmed_at.
    window.location.reload();
  });

  disableBtn?.addEventListener("click", async () => {
    setError("");
    setMsg("");

    const code = (disableCode?.value || "").trim();
    if (!isSixDigits(code)) {
      setError(L("Введите 6-значный код.", "Enter a 6-digit code."));
      return;
    }

    if (!lockBtn(disableBtn)) return;

    try {
      const { ok, data, message } = await postJSON("/settings/security/totp/disable", {
        code,
      });

      if (!ok || !data || data.status !== "ok") {
        setError(message || L("Не удалось отключить аутентификатор.", "Failed to disable authenticator."));
        unlockBtn(disableBtn);
        return;
      }

      setMsg(L("Google Authenticator отключён.", "Google Authenticator disabled."));
      unlockBtn(disableBtn);
      renderDisabledState();
    } catch (e) {
      console.error(e);
      setError(L("Ошибка сети.", "Network error."));
      unlockBtn(disableBtn);
    }
  });

  // initial state sync
  if (root.dataset.totpEnabled === "1") {
    renderEnabledState(root.dataset.totpConfirmedAt || "");
  } else {
    renderDisabledState();
  }
}

function initEmailsManager() {
  const items = document.querySelectorAll("[data-email-item]");
  if (!items.length) return;

  const isEn = document.documentElement.lang === "en";
  const warningModal = document.getElementById("emailWarningModal");
  const successModal = document.getElementById("emailSuccessModal");
  const deleteSuccessModal = document.getElementById("emailDeleteSuccessModal");
  const backdrop = document.getElementById("backdrop");
  const closeWarningEls = document.querySelectorAll("[data-email-warning-close]");
  const closeSuccessEls = document.querySelectorAll("[data-email-success-close]");
  const closeDeleteSuccessEls = document.querySelectorAll("[data-email-delete-success-close]");
  const totpEnabled = isTotpEnabledOnPage();

  const deleteTotpModal = document.getElementById("emailDeleteTotpModal");
  const deleteTotpInput = document.getElementById("emailDeleteTotpCode");
  const deleteTotpError = document.getElementById("emailDeleteTotpError");
  const closeDeleteTotpEls = document.querySelectorAll("[data-email-delete-totp-close]");

  let pendingDeleteSlot = null;
  let pendingDeleteBtn = null;
  let pendingDeleteSetErr = null;
  let deleteTotpPending = false;

  function openLastEmailWarning() {
    if (!warningModal) return;
    warningModal.classList.add("is-open");
    warningModal.setAttribute("aria-hidden", "false");
    if (backdrop) {
      backdrop.hidden = false;
    }
  }

  function closeLastEmailWarning() {
    if (!warningModal) return;
    warningModal.classList.remove("is-open");
    warningModal.setAttribute("aria-hidden", "true");
    if (backdrop) {
      backdrop.hidden = true;
    }
  }

  closeWarningEls.forEach((el) => {
    el.addEventListener("click", () => {
      closeLastEmailWarning();
    });
  });

  function openEmailSuccess() {
    if (!successModal) return;
    successModal.classList.add("is-open");
    successModal.setAttribute("aria-hidden", "false");
    if (backdrop) {
      backdrop.hidden = false;
    }
  }

  function closeEmailSuccess() {
    if (!successModal) return;
    successModal.classList.remove("is-open");
    successModal.setAttribute("aria-hidden", "true");
    if (backdrop) {
      backdrop.hidden = true;
    }
  }

  closeSuccessEls.forEach((el) => {
    el.addEventListener("click", () => {
      closeEmailSuccess();
    });
  });

  function openEmailDeleteSuccess() {
    if (!deleteSuccessModal) return;
    deleteSuccessModal.classList.add("is-open");
    deleteSuccessModal.setAttribute("aria-hidden", "false");
    if (backdrop) {
      backdrop.hidden = false;
    }
  }

  function closeEmailDeleteSuccess() {
    if (!deleteSuccessModal) return;
    deleteSuccessModal.classList.remove("is-open");
    deleteSuccessModal.setAttribute("aria-hidden", "true");
    if (backdrop) {
      backdrop.hidden = true;
    }
  }

  function openEmailDeleteTotpModal(slot, btnDelete, setErr) {
    pendingDeleteSlot = slot;
    pendingDeleteBtn = btnDelete;
    pendingDeleteSetErr = setErr;
    deleteTotpPending = false;

    if (deleteTotpError) deleteTotpError.textContent = "";
    if (deleteTotpInput) deleteTotpInput.value = "";

    if (deleteTotpModal) {
      deleteTotpModal.classList.add("is-open");
      deleteTotpModal.setAttribute("aria-hidden", "false");
    }

    if (backdrop) backdrop.hidden = false;

    setTimeout(() => {
      if (deleteTotpInput) deleteTotpInput.focus();
    }, 50);
  }

  function closeEmailDeleteTotpModal() {
    if (deleteTotpModal) {
      deleteTotpModal.classList.remove("is-open");
      deleteTotpModal.setAttribute("aria-hidden", "true");
    }

    if (backdrop) backdrop.hidden = true;

    if (deleteTotpInput) deleteTotpInput.value = "";
    if (deleteTotpError) deleteTotpError.textContent = "";

    pendingDeleteSlot = null;
    pendingDeleteBtn = null;
    pendingDeleteSetErr = null;
    deleteTotpPending = false;
  }

  closeDeleteTotpEls.forEach((el) => {
    el.addEventListener("click", () => {
      if (pendingDeleteBtn) unlockBtn(pendingDeleteBtn);
      closeEmailDeleteTotpModal();
    });
  });

  closeDeleteSuccessEls.forEach((el) => {
    el.addEventListener("click", () => {
      closeEmailDeleteSuccess();
    });
  });

  // показать модалку после успешного добавления резервной почты (по флагу в localStorage)
  try {
    if (localStorage.getItem("wb_email_backup_added") === "1") {
      localStorage.removeItem("wb_email_backup_added");
      openEmailSuccess();
    }
    if (localStorage.getItem("wb_email_backup_deleted") === "1") {
      localStorage.removeItem("wb_email_backup_deleted");
      openEmailDeleteSuccess();
    }
  } catch (e) {
    console.warn("localStorage not available", e);
  }

  async function deleteEmailRequest(slot, totpCode, btnDelete, setErr) {
    try {
      const resp = await fetch("/settings/security/emails/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          slot: slot,
          totp_code: totpCode || "",
        }),
      });

      if (!resp.ok) {
        const parsed = await parseJsonOrText(resp);
        const errText = parsed.message || `HTTP ${resp.status}`;

        if (setErr) setErr(errText);
        if (deleteTotpError && totpCode) deleteTotpError.textContent = errText;

        unlockBtn(btnDelete);
        deleteTotpPending = false;
        return;
      }

      try {
        localStorage.setItem("wb_email_backup_deleted", "1");
      } catch (e) {
        console.warn("localStorage not available", e);
      }

      closeEmailDeleteTotpModal();
      window.location.reload();
    } catch (e) {
      console.error(e);
      const msg = isEn ? "Network error" : "Ошибка сети";

      if (setErr) setErr(msg);
      if (deleteTotpError && totpCode) deleteTotpError.textContent = msg;

      unlockBtn(btnDelete);
      deleteTotpPending = false;
    }
  }

  initInlineTotpBlock(
    document.getElementById("emailDeleteTotpBlock"),
    deleteTotpInput,
    async () => {
      const code = getInlineTotpCode(deleteTotpInput);

      if (!isSixDigits(code)) return;
      if (!pendingDeleteSlot || !pendingDeleteBtn) return;
      if (deleteTotpPending) return;

      deleteTotpPending = true;
      if (deleteTotpError) deleteTotpError.textContent = "";

      await deleteEmailRequest(
        pendingDeleteSlot,
        code,
        pendingDeleteBtn,
        pendingDeleteSetErr
      );
    }
  );

  items.forEach((item) => {
    const slot = parseInt(item.getAttribute("data-slot") || "0", 10);
    if (!slot) return;

    const btnDelete = item.querySelector("[data-email-delete]");
    const btnConfirm = item.querySelector("[data-email-confirm]");
    const codeBlock = item.querySelector("[data-email-code-block]");
    const codeInput = item.querySelector("[data-email-code-input]");
    const emailInput = item.querySelector("[data-email-input]");
    const emailValueEl = item.querySelector("[data-email-value]");
    const errEl = item.querySelector("[data-email-error]");
    const msgEl = item.querySelector("[data-email-msg]");
    const resendBtn = item.querySelector("[data-email-resend]");
    const emailTotpBlock = item.querySelector("[data-email-totp-block]");
    const emailTotpInput = item.querySelector("[data-email-totp-input]");

    const setErr = (t) => {
      if (!errEl) return;
      errEl.textContent = t || "";
    };

    const setMsg = (t) => {
      if (!msgEl) return;
      msgEl.textContent = t || "";
    };

    initInlineTotpBlock(emailTotpBlock, emailTotpInput, () => {
      setErr("");
    });

    const getEmail = () => {
      // если есть уже отображаемый email — берём его
      const text = (emailValueEl && emailValueEl.textContent ? emailValueEl.textContent : "").trim();
      if (text) return text;
      // иначе — из input (для слота 2, когда почта не добавлена)
      const v = (emailInput && emailInput.value ? emailInput.value : "").trim();
      return v;
    };

    btnConfirm?.addEventListener("click", async () => {
      if (btnConfirm.disabled) return;
      if (!lockBtn(btnConfirm)) return;

      setErr("");
      setMsg("");

      const email = getEmail();

      // 1) если код-блок скрыт => отправляем код
      const codeVisible = codeBlock && !codeBlock.classList.contains("hidden");

      if (!codeVisible) {
        if (!email) {
          setErr(isEn ? "Enter email" : "Введите e-mail");
          unlockBtn(btnConfirm);
          return;
        }

        try {
          const resp = await fetch("/settings/security/emails/send-code", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: "same-origin",
            body: JSON.stringify({ slot: slot, email: email }),
          });

          if (!resp.ok) {
            const raw = await resp.text().catch(() => "");
            let msg = raw || (isEn ? `Error (HTTP ${resp.status}).` : `Ошибка (HTTP ${resp.status}).`);
            if (raw) {
              try {
                const data = JSON.parse(raw);
                if (data && typeof data.message === "string") {
                  msg = data.message;
                }
              } catch {
                // не JSON — оставляем как есть
              }
            }
            setErr(msg);
            unlockBtn(btnConfirm);
            return;
          }

          // ok
          if (codeBlock) codeBlock.classList.remove("hidden");
          setInlineTotpVisible(emailTotpBlock, emailTotpInput, totpEnabled);
          setMsg(isEn ? "Code sent. Enter the code from email." : "Код отправлен. Введите код из письма.");
          if (codeInput) codeInput.focus();
          unlockBtn(btnConfirm);
        } catch (e) {
          console.error(e);
          setErr(isEn ? "Network error" : "Ошибка сети");
          unlockBtn(btnConfirm);
        }

        return;
      }

      // 2) если код-блок видим => подтверждаем код
      const code = (codeInput && codeInput.value ? codeInput.value : "").trim();
      if (!isSixDigits(code)) {
        setErr(isEn ? "Enter a 6-digit code" : "Введите 6-значный код");
        unlockBtn(btnConfirm);
        return;
      }

      const totpCode = getInlineTotpCode(emailTotpInput);

      if (totpEnabled && !isSixDigits(totpCode)) {
        setErr(isEn ? "Enter Google 2FA code" : "Введите код Google 2FA");
        unlockBtn(btnConfirm);
        return;
      }

      try {
        const resp = await fetch("/settings/security/emails/confirm", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({
            slot: slot,
            code: code,
            totp_code: totpEnabled ? totpCode : "",
          }),
        });

        if (!resp.ok) {
          const errText = await resp.text();
          setErr(errText || `HTTP ${resp.status}`);
          unlockBtn(btnConfirm);
          return;
        }

        // успех: для слота 2 ставим флаг, чтобы после перезагрузки показать модалку "резервная почта добавлена"
        try {
          if (slot === 2) {
            localStorage.setItem("wb_email_backup_added", "1");
          }
        } catch (e) {
          console.warn("localStorage not available", e);
        }
        // проще и корректнее перезагрузить, чтобы отрисовать обновлённые emails/verified и индикатор
        window.location.reload();
      } catch (e) {
        console.error(e);
        setErr(isEn ? "Network error" : "Ошибка сети");
        unlockBtn(btnConfirm);
      }
    });

    btnDelete?.addEventListener("click", async () => {
      if (btnDelete.disabled) return;
      if (!lockBtn(btnDelete)) return;

      if (btnDelete.dataset.onlyEmail === "1") {
        unlockBtn(btnDelete);
        openLastEmailWarning();
        return;
      }

      setErr("");
      setMsg("");

      if (totpEnabled) {
        openEmailDeleteTotpModal(slot, btnDelete, setErr);
        return;
      }

      await deleteEmailRequest(slot, "", btnDelete, setErr);
    });

    // Отдельная кнопка повторной отправки кода
    resendBtn?.addEventListener("click", async () => {
      if (resendBtn.disabled) return;
      if (!lockBtn(resendBtn)) return;

      setErr("");
      setMsg("");

      const email = getEmail();
      if (!email) {
        setErr(isEn ? "Enter email" : "Введите e-mail");
        unlockBtn(resendBtn);
        return;
      }

      // визуально показать, что что-то происходит
      const originalText = resendBtn.textContent;
      resendBtn.textContent = isEn ? "Sending..." : "Отправляем...";

      try {
        const resp = await fetch("/settings/security/emails/send-code", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({ slot: slot, email: email }),
        });

        if (resp.ok) {
          unlockBtn(resendBtn);
          startBtnCooldown(
            resendBtn,
            60,
            isEn ? "Send code again" : "Отправить код ещё раз"
          );
          setMsg(isEn ? "Code sent. Enter the code from email." : "Код отправлен. Введите код из письма.");
          return;
        }

        const raw = await resp.text().catch(() => "");
        let msg = raw || (isEn ? `Error (HTTP ${resp.status}).` : `Ошибка (HTTP ${resp.status}).`);
        if (raw) {
          try {
            const data = JSON.parse(raw);
            if (data && typeof data.message === "string") {
              msg = data.message;
            }
          } catch {
            // не JSON — оставляем
          }
        }
        setErr(msg);
        unlockBtn(resendBtn);
      } catch (e) {
        console.error(e);
        setErr(isEn ? "Network error" : "Ошибка сети");
        unlockBtn(resendBtn);
      }
    });
  });
}

function isSixDigits(code) {
  return typeof code === "string" && /^\d{6}$/.test(code);
}

  