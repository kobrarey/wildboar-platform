(() => {
  const CONTAINER_ID = "tv_chart_container";
  const CFG_ID = "terminalChartConfig";
  /** Must match .term-chart-shell --term-chart-px in terminal.css */
  const CHART_PX = 520;
  let widget = null;

  function waitTwoFrames() {
    return new Promise((resolve) => {
      requestAnimationFrame(() => {
        requestAnimationFrame(resolve);
      });
    });
  }

  function measureTerminalChart(containerEl) {
    const shell = containerEl?.closest?.(".term-chart-shell");
    const target = shell || containerEl;
    const r = target?.getBoundingClientRect?.() || { width: 0, height: 0 };
    const w = Math.max(200, Math.floor(r.width || containerEl?.clientWidth || 0));
    const h = Math.max(CHART_PX, Math.floor(r.height || containerEl?.clientHeight || CHART_PX));
    return { w, h };
  }

  function bumpWidgetSize() {
    const c = getContainerElement();
    if (!widget || !c || typeof widget.resize !== "function") return;
    try {
      widget.resize();
    } catch (_) {
      /* ignore */
    }
  }

  function scheduleChartResizePasses() {
    [0, 50, 150, 400, 1200].forEach((ms) => {
      setTimeout(() => bumpWidgetSize(), ms);
    });
  }

  function getConfigElement() {
    return document.getElementById(CFG_ID);
  }

  function getContainerElement() {
    return document.getElementById(CONTAINER_ID);
  }

  function parseConfig() {
    const el = getConfigElement();
    if (!el) {
      console.error("[terminal_chart] terminalChartConfig element not found");
      return null;
    }

    try {
      return JSON.parse(el.textContent || "{}");
    } catch (err) {
      console.error("[terminal_chart] Failed to parse config JSON:", err);
      return null;
    }
  }

  function normalizeUdfBars(payload) {
    if (
      !payload ||
      !payload.s ||
      !Array.isArray(payload.t) ||
      !Array.isArray(payload.o) ||
      !Array.isArray(payload.h) ||
      !Array.isArray(payload.l) ||
      !Array.isArray(payload.c)
    ) {
      return [];
    }

    const bars = [];
    for (let i = 0; i < payload.t.length; i += 1) {
      const ts = Number(payload.t[i]);
      if (!Number.isFinite(ts)) continue;

      bars.push({
        time: ts * 1000,
        open: Number(payload.o[i]),
        high: Number(payload.h[i]),
        low: Number(payload.l[i]),
        close: Number(payload.c[i]),
        volume:
          Array.isArray(payload.v) && payload.v[i] != null
            ? Number(payload.v[i])
            : 0,
      });
    }

    return bars.sort((a, b) => a.time - b.time);
  }

  function buildOverrides(theme) {
    if (theme === "dark") {
      return {
        "paneProperties.background": "#0b1220",
        "paneProperties.backgroundType": "solid",
        "paneProperties.backgroundGradientStartColor": "#0b1220",
        "paneProperties.backgroundGradientEndColor": "#0b1220",
        "paneProperties.vertGridProperties.color": "#1f2937",
        "paneProperties.horzGridProperties.color": "#1f2937",
        "scalesProperties.textColor": "#cbd5e1",
        "scalesProperties.lineColor": "#334155",
        "symbolWatermarkProperties.transparency": 90,
      };
    }

    return {
      "paneProperties.background": "#ffffff",
      "paneProperties.backgroundType": "solid",
      "paneProperties.backgroundGradientStartColor": "#ffffff",
      "paneProperties.backgroundGradientEndColor": "#ffffff",
      "paneProperties.vertGridProperties.color": "#e5e7eb",
      "paneProperties.horzGridProperties.color": "#e5e7eb",
      "scalesProperties.textColor": "#111827",
      "scalesProperties.lineColor": "#d1d5db",
      "symbolWatermarkProperties.transparency": 90,
    };
  }

  function buildChartConfig(raw) {
    const bodyTheme = (document.body?.dataset?.theme || "dark").toLowerCase();
    const theme = bodyTheme === "light" ? "light" : "dark";
    const lang = (document.documentElement.lang || raw.lang || "ru").toLowerCase();
    const locale = lang === "en" ? "en" : "ru";

    return {
      fund_code: raw.fund_code || raw.symbol_code || "unknown",
      symbol: raw.symbol_code || raw.fund_code || "unknown",
      name: raw.symbol_name || raw.name || raw.fund_code || "Fund",
      description: raw.description || raw.full_name || raw.symbol_name || "Fund",
      bars_url: raw.bars_url || raw.bars_endpoint || "/api/chart/bars/unknown",
      resolutions:
        Array.isArray(raw.resolutions) && raw.resolutions.length
          ? raw.resolutions
          : Array.isArray(raw.supported_resolutions) && raw.supported_resolutions.length
          ? raw.supported_resolutions
          : ["1D"],
      interval:
        raw.default_resolution ||
        raw.default_interval ||
        "1D",
      timezone: raw.timezone || "Etc/UTC",
      pricescale: Number(raw.pricescale || 100),
      theme,
      locale,
      library_path: raw.library_path || "/static/charting_library/",
      has_intraday: raw.has_intraday === true,
      has_daily: raw.has_daily !== false,
      has_weekly_and_monthly: raw.has_weekly_and_monthly !== false,
    };
  }

  function buildDatafeed(chartConfig) {
    return {
      onReady: (cb) => {
        setTimeout(() => {
          cb({
            supports_search: false,
            supports_group_request: false,
            supports_marks: false,
            supports_timescale_marks: false,
            supports_time: false,
            supported_resolutions: chartConfig.resolutions,
          });
        }, 0);
      },

      resolveSymbol: (_symbolName, onResolve, _onError) => {
        const symbolInfo = {
          name: chartConfig.symbol,
          ticker: chartConfig.symbol,
          description: chartConfig.description,
          type: "crypto",
          exchange: "WildBoar",
          listed_exchange: "WildBoar",
          session: "24x7",
          timezone: chartConfig.timezone,
          minmov: 1,
          pricescale: chartConfig.pricescale,
          has_intraday: chartConfig.has_intraday,
          has_daily: chartConfig.has_daily,
          has_weekly_and_monthly: chartConfig.has_weekly_and_monthly,
          supported_resolutions: chartConfig.resolutions,
          data_status: "streaming",
          visible_plots_set: "ohlc",
        };

        setTimeout(() => onResolve(symbolInfo), 0);
      },

      getBars: async (_symbolInfo, resolution, periodParams, onHistoryCallback, onErrorCallback) => {
        try {
          const from = periodParams?.from;
          const to = periodParams?.to;

          const url = new URL(chartConfig.bars_url, window.location.origin);
          url.searchParams.set("resolution", String(resolution));
          if (from != null) url.searchParams.set("from", String(from));
          if (to != null) url.searchParams.set("to", String(to));

          const resp = await fetch(url.toString(), {
            method: "GET",
            credentials: "same-origin",
            headers: { Accept: "application/json" },
          });

          if (!resp.ok) {
            console.error("[terminal_chart] bars HTTP error:", resp.status, url.toString());
            onErrorCallback(`HTTP ${resp.status}`);
            return;
          }

          const payload = await resp.json();
          const bars = normalizeUdfBars(payload);

          console.log("[terminal_chart] getBars", {
            url: url.toString(),
            resolution,
            bars: bars.length,
            status: payload?.s,
          });

          if (!bars.length) {
            onHistoryCallback([], { noData: true });
            return;
          }

          onHistoryCallback(bars, { noData: false });
        } catch (err) {
          console.error("[terminal_chart] getBars failed:", err);
          onErrorCallback(err?.message || "getBars error");
        }
      },

      subscribeBars: () => {},
      unsubscribeBars: () => {},
    };
  }

  function removeWidget() {
    if (widget && typeof widget.remove === "function") {
      widget.remove();
    }
    widget = null;
  }

  function waitForTradingView(maxAttempts = 60, delayMs = 150) {
    return new Promise((resolve, reject) => {
      let attempt = 0;

      function check() {
        const ok =
          typeof window.TradingView !== "undefined" &&
          typeof window.TradingView.widget === "function";

        if (ok) {
          resolve(window.TradingView);
          return;
        }

        attempt += 1;
        if (attempt >= maxAttempts) {
          reject(new Error("TradingView.widget is not available"));
          return;
        }

        setTimeout(check, delayMs);
      }

      check();
    });
  }

  async function initChart() {
    const rawConfig = parseConfig();
    const container = getContainerElement();

    if (!rawConfig || !container) {
      return;
    }

    try {
      const TradingView = await waitForTradingView();
      const chartConfig = buildChartConfig(rawConfig);

      removeWidget();
      container.innerHTML = "";

      await waitTwoFrames();

      const { w, h } = measureTerminalChart(container);

      console.log("[terminal_chart] initChart", chartConfig, { w, h });

      widget = new TradingView.widget({
        symbol: chartConfig.symbol,
        interval: chartConfig.interval,
        container,
        datafeed: buildDatafeed(chartConfig),
        library_path: chartConfig.library_path,
        locale: chartConfig.locale,
        timezone: chartConfig.timezone,
        theme: chartConfig.theme,
        autosize: false,
        width: w,
        height: h,
        fullscreen: false,
        overrides: buildOverrides(chartConfig.theme),
        loading_screen: {
          backgroundColor: chartConfig.theme === "dark" ? "#0b1220" : "#ffffff",
        },
        toolbar_bg: chartConfig.theme === "dark" ? "#111827" : "#ffffff",
        custom_css_url: `${window.location.origin}/static/css/tradingview-terminal-theme.css`,
        disabled_features: [
          "use_localstorage_for_settings",
          "header_symbol_search",
          "header_compare",
          "header_saveload",
          "header_screenshot",
          "display_market_status",
          "go_to_date",
          "timeframes_toolbar",
          "show_hide_button_in_legend",
          "edit_buttons_in_legend",
          "context_menus",
          "control_bar",
        ],
        load_last_chart: false,
      });

      if (widget && typeof widget.onChartReady === "function") {
        widget.onChartReady(() => {
          console.log("[terminal_chart] chart ready");
          requestAnimationFrame(() => {
            bumpWidgetSize();
            requestAnimationFrame(() => bumpWidgetSize());
          });
          scheduleChartResizePasses();
        });
      }
    } catch (err) {
      console.error("[terminal_chart] initChart failed:", err);
    }
  }

  let themeTimeout = null;

  function bindThemeRebuild() {
    const body = document.body;
    if (!body) return;

    const mo = new MutationObserver((mutations) => {
      for (const m of mutations) {
        if (m.type === "attributes" && m.attributeName === "data-theme") {
          clearTimeout(themeTimeout);
          themeTimeout = setTimeout(() => {
            initChart();
          }, 150);
          break;
        }
      }
    });

    mo.observe(body, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
  }

  function start() {
    initChart();
    bindThemeRebuild();
    window.addEventListener("resize", () => {
      bumpWidgetSize();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();