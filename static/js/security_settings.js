document.addEventListener("DOMContentLoaded", () => {
  initAccordions();
  initPasswordChange();
  initEmailsManager();
});

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

  newPassword.addEventListener("input", () => {
    setMsg("");
    refreshState();
  });

  confirmPassword.addEventListener("input", () => {
    setMsg("");
    refreshState();
  });

  sendCodeBtn.addEventListener("click", async () => {
    setMsg("");
    if (!passwordsMatch()) {
      setError(isEn ? "Passwords do not match" : "Пароли не совпадают");
      return;
    }
    setError("");

    const slot = parseInt(slotEl.value, 10) || 1;

    sendCodeBtn.disabled = true;

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
        sendCodeBtn.disabled = false;
        return;
      }

      // ok
      codeBlock.classList.remove("hidden");
      confirmBtn.disabled = false;
      setMsg(isEn ? "Code sent. Please enter the code from email." : "Код отправлен. Введите код из письма.");
      codeInput.focus();
    } catch (e) {
      console.error(e);
      setMsg(isEn ? "Network error" : "Ошибка сети");
      sendCodeBtn.disabled = false;
    }
  });

  // повторная отправка кода для смены пароля
  resendBtn?.addEventListener("click", async () => {
    if (resendBtn.disabled) return;

    setMsg("");
    setError("");

    if (!passwordsMatch()) {
      setError(isEn ? "Passwords do not match" : "Пароли не совпадают");
      return;
    }

    const slot = parseInt(slotEl.value, 10) || 1;

    const originalText = resendBtn.textContent;
    resendBtn.disabled = true;
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
        if (typeof window.startResendCooldown === "function") {
          window.startResendCooldown(resendBtn, 60);
        } else {
          let left = 60;
          const id = setInterval(() => {
            left -= 1;
            if (left <= 0) {
              clearInterval(id);
              resendBtn.disabled = false;
              resendBtn.textContent = originalText;
            } else {
              resendBtn.textContent = isEn ? `Retry in ${left}s` : `Повторно через ${left}с`;
            }
          }, 1000);
        }
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
    } catch (e) {
      console.error(e);
      setMsg(isEn ? "Network error" : "Ошибка сети");
    } finally {
      // если таймер не был запущен — вернём кнопку в исходное состояние
      if (!resendBtn.disabled && resendBtn.textContent !== originalText) {
        // таймер уже работает, не трогаем
      } else if (!window.startResendCooldown) {
        resendBtn.disabled = false;
        resendBtn.textContent = originalText;
      }
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
    if (!isSixDigits(code)) {
      setMsg(isEn ? "Enter a 6-digit code" : "Введите 6-значный код");
      return;
    }

    confirmBtn.disabled = true;

    try {
      const resp = await fetch("/settings/security/change-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          new_password: newPassword.value,
          code: code,
        }),
      });

      if (!resp.ok) {
        const errText = await resp.text();
        setMsg(errText || `HTTP ${resp.status}`);
        confirmBtn.disabled = false;
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

      // обновим состояние, чтобы корректно пересчитать доступность кнопок
      refreshState();
    } catch (e) {
      console.error(e);
      setMsg(isEn ? "Network error" : "Ошибка сети");
      confirmBtn.disabled = false;
    }
  });

  // init
  if (mismatch) mismatch.classList.add("hidden");
  refreshState();
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

    const setErr = (t) => {
      if (!errEl) return;
      errEl.textContent = t || "";
    };

    const setMsg = (t) => {
      if (!msgEl) return;
      msgEl.textContent = t || "";
    };

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

      setErr("");
      setMsg("");

      const email = getEmail();

      // 1) если код-блок скрыт => отправляем код
      const codeVisible = codeBlock && !codeBlock.classList.contains("hidden");

      if (!codeVisible) {
        if (!email) {
          setErr(isEn ? "Enter email" : "Введите e-mail");
          return;
        }

        btnConfirm.disabled = true;

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
            btnConfirm.disabled = false;
            return;
          }

          // ok
          if (codeBlock) codeBlock.classList.remove("hidden");
          setMsg(isEn ? "Code sent. Enter the code from email." : "Код отправлен. Введите код из письма.");
          if (codeInput) codeInput.focus();
          btnConfirm.disabled = false;
        } catch (e) {
          console.error(e);
          setErr(isEn ? "Network error" : "Ошибка сети");
          btnConfirm.disabled = false;
        }

        return;
      }

      // 2) если код-блок видим => подтверждаем код
      const code = (codeInput && codeInput.value ? codeInput.value : "").trim();
      if (!isSixDigits(code)) {
        setErr(isEn ? "Enter a 6-digit code" : "Введите 6-значный код");
        return;
      }

      btnConfirm.disabled = true;

      try {
        const resp = await fetch("/settings/security/emails/confirm", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({ slot: slot, code: code }),
        });

        if (!resp.ok) {
          const errText = await resp.text();
          setErr(errText || `HTTP ${resp.status}`);
          btnConfirm.disabled = false;
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
        btnConfirm.disabled = false;
      }
    });

    btnDelete?.addEventListener("click", async () => {
      if (btnDelete.disabled) return;

      // если это единственная почта — вместо запроса показываем предупреждение
      if (btnDelete.dataset.onlyEmail === "1") {
        openLastEmailWarning();
        return;
      }

      setErr("");
      setMsg("");

      btnDelete.disabled = true;

      try {
        const resp = await fetch("/settings/security/emails/delete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({ slot: slot }),
        });

        if (!resp.ok) {
          const errText = await resp.text();
          setErr(errText || `HTTP ${resp.status}`);
          btnDelete.disabled = false;
          return;
        }

        // после успешного удаления покажем модалку "резервная почта была успешно удалена"
        try {
          localStorage.setItem("wb_email_backup_deleted", "1");
        } catch (e) {
          console.warn("localStorage not available", e);
        }

        // при удалении слота 1 backend может "поднять" слот 2 => делаем reload
        window.location.reload();
      } catch (e) {
        console.error(e);
        setErr(isEn ? "Network error" : "Ошибка сети");
        btnDelete.disabled = false;
      }
    });

    // Отдельная кнопка повторной отправки кода
    resendBtn?.addEventListener("click", async () => {
      if (resendBtn.disabled) return;

      setErr("");
      setMsg("");

      const email = getEmail();
      if (!email) {
        setErr(isEn ? "Enter email" : "Введите e-mail");
        return;
      }

      // визуально показать, что что-то происходит
      const originalText = resendBtn.textContent;
      resendBtn.disabled = true;
      resendBtn.textContent = isEn ? "Sending..." : "Отправляем...";

      try {
        const resp = await fetch("/settings/security/emails/send-code", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({ slot: slot, email: email }),
        });

        if (resp.ok) {
          // запустим таймер, если глобальный helper доступен,
          // иначе — простой локальный кулдаун
          if (typeof window.startResendCooldown === "function") {
            window.startResendCooldown(resendBtn, 60);
          } else {
            let left = 60;
            const id = setInterval(() => {
              left -= 1;
              if (left <= 0) {
                clearInterval(id);
                resendBtn.disabled = false;
                resendBtn.textContent = originalText;
              } else {
                resendBtn.textContent = isEn ? `Retry in ${left}s` : `Повторно через ${left}с`;
              }
            }, 1000);
          }
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
      } catch (e) {
        console.error(e);
        setErr(isEn ? "Network error" : "Ошибка сети");
      } finally {
        // если не запустили глобальный/локальный таймер (ошибка) — вернём кнопку в нормальное состояние
        if (!resendBtn.disabled || resendBtn.textContent !== originalText) {
          // если таймер уже сменил текст/состояние — не трогаем
        } else {
          resendBtn.disabled = false;
          resendBtn.textContent = originalText;
        }
      }
    });
  });
}

function isSixDigits(code) {
  return typeof code === "string" && /^\d{6}$/.test(code);
}

  