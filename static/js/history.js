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

  function initHistoryTabs() {
    const root = document.getElementById("historyTabs");
    if (!root) return;

    const mainBtns = Array.from(root.querySelectorAll("[data-main]"));
    const subGroups = Array.from(root.querySelectorAll("[data-subgroup]"));
    const panels = Array.from(root.querySelectorAll("[data-panel]"));

    let currentMain = "trading";
    let currentSub = "all";

    function showPanel(mainName, subName) {
      const key = `${mainName}/${subName}`;
      panels.forEach((p) => p.classList.toggle("hidden", p.dataset.panel !== key));
    }

    function setSub(groupEl, subName) {
      const subBtns = Array.from(groupEl.querySelectorAll("[data-sub]"));
      subBtns.forEach((b) => b.classList.toggle("is-active", b.dataset.sub === subName));
      currentSub = subName;
      showPanel(currentMain, currentSub);
    }

    function setMain(name) {
      currentMain = name;
      mainBtns.forEach((b) => b.classList.toggle("is-active", b.dataset.main === name));
      subGroups.forEach((g) => g.classList.toggle("hidden", g.dataset.subgroup !== name));

      const activeGroup = root.querySelector(`[data-subgroup="${name}"]`);
      const first = activeGroup ? activeGroup.querySelector("[data-sub]") : null;
      const sub = (first && first.dataset.sub) || "all";
      setSub(activeGroup, sub);
    }

    mainBtns.forEach((b) => b.addEventListener("click", () => setMain(b.dataset.main)));
    subGroups.forEach((g) => {
      g.querySelectorAll("[data-sub]").forEach((b) => b.addEventListener("click", () => setSub(g, b.dataset.sub)));
    });

    // copy (delegation)
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

    // default
    setMain("trading");
  }

  document.addEventListener("DOMContentLoaded", initHistoryTabs);
})();
