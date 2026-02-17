(() => {
    function initHistoryTabs() {
      const root = document.getElementById("historyTabs");
      if (!root) return;
  
      const mainBtns = root.querySelectorAll("[data-main]");
      const subGroups = root.querySelectorAll("[data-subgroup]");
  
      function setMain(name) {
        mainBtns.forEach((b) => b.classList.toggle("is-active", b.dataset.main === name));
        subGroups.forEach((g) => g.classList.toggle("hidden", g.dataset.subgroup !== name));
  
        // default subtab = all
        const activeGroup = root.querySelector(`[data-subgroup="${name}"]`);
        if (activeGroup) {
          const subBtns = activeGroup.querySelectorAll("[data-sub]");
          subBtns.forEach((b, i) => b.classList.toggle("is-active", i === 0));
        }
      }
  
      mainBtns.forEach((b) => {
        b.addEventListener("click", () => setMain(b.dataset.main));
      });
  
      subGroups.forEach((g) => {
        g.querySelectorAll("[data-sub]").forEach((b) => {
          b.addEventListener("click", () => {
            g.querySelectorAll("[data-sub]").forEach((x) => x.classList.remove("is-active"));
            b.classList.add("is-active");
          });
        });
      });
  
      // default
      setMain("trading");
    }
  
    document.addEventListener("DOMContentLoaded", initHistoryTabs);
  })();
  