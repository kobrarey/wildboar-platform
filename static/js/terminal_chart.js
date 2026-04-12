(() => {
  const cfgEl = document.getElementById("terminalChartConfig");
  const containerId = "tv_chart_container";

  if (!cfgEl) return;

  let rawConfig = {};
  try {
    rawConfig = JSON.parse(cfgEl.textContent || "{}");
  } catch (_) {
    rawConfig = {};
  }

  const body = document.body;
  const lang = (document.documentElement.lang || rawConfig.lang || "ru").toLowerCase();
  const locale = lang === "en" ? "en" : "ru";

  const chartConfig = {
    library_path: rawConfig.library_path || "/static/charting_library/",
    bars_url: rawConfig.bars_url || rawConfig.api_bars_url || rawConfig.chart_api_url || "/api/chart/bars",
    symbol_code: rawConfig.symbol_code || rawConfig.fund_code || rawConfig.current_fund_code || rawConfig.code || "unknown",
    symbol_name: rawConfig.symbol_name || rawConfig.short_name || rawConfig.name || rawConfig.fund_name || "Fund",
    full_name: rawConfig.full_name || rawConfig.symbol_name || rawConfig.short_name || rawConfig.name || "Fund",
    description: rawConfig.description || rawConfig.full_name || rawConfig.symbol_name || rawConfig.short_name || "Fund",
    resolutions: Array.isArray(rawConfig.resolutions) && rawConfig.resolutions.length ? rawConfig.resolutions : ["1D"],
    default_resolution: rawConfig.default_resolution || rawConfig.interval || "1D",
    timezone: rawConfig.timezone || "Etc/UTC",
    session: rawConfig.session || "24x7",
    minmov: Number(rawConfig.minmov || 1),
    pricescale: Number(rawConfig.pricescale || 100),
    volume_precision: Number(rawConfig.volume_precision || 2),
    has_intraday: rawConfig.has_intraday !== undefined ? !!rawConfig.has_intraday : true,
    has_daily: rawConfig.has_daily !== undefined ? !!rawConfig.has_daily : true,
    has_weekly_and_monthly: rawConfig.has_weekly_and_monthly !== undefined ? !!rawConfig.has_weekly_and_monthly : true,
  };

  let widget = null;

  function getThemeName() {
    return (body.dataset.theme || "light") === "dark" ? "Dark" : "Light";
  }

  function normalizeBars(payload) {
    const source = Array.isArray(payload)
      ? payload
      : Array.isArray(payload?.bars)
      ? payload.bars
      : Array.isArray(payload?.data)
      ? payload.data
      : Array.isArray(payload?.items)
      ? payload.items
      : [];

    return source
      .map((row) => {
        const ts = row.time ?? row.ts ?? row.t ?? row.timestamp ?? row.datetime;
        const open = row.open ?? row.o;
        const high = row.high ?? row.h;
        const low = row.low ?? row.l;
        const close = row.close ?? row.c;
        const volume = row.volume ?? row.v ?? 0;

        if (ts == null || open == null || high == null || low == null || close == null) {
          return null;
        }

        const tsNum = Number(ts);
        const timeMs = tsNum < 10000000000 ? tsNum * 1000 : tsNum;

        return {
          time: timeMs,
          open: Number(open),
          high: Number(high),
          low: Number(low),
          close: Number(close),
          volume: Number(volume || 0),
        };
      })
      .filter(Boolean)
      .sort((a, b) => a.time - b.time);
  }

  function buildDatafeed() {
    return {
      onReady: (cb) => {
        setTimeout(() => {
          cb({
            supported_resolutions: chartConfig.resolutions,
            supports_search: false,
            supports_group_request: false,
            supports_marks: false,
            supports_timescale_marks: false,
            supports_time: true,
          });
        }, 0);
      },

      resolveSymbol: (symbolName, onResolve, onError) => {
        try {
          onResolve({
            ticker: chartConfig.symbol_code,
            name: chartConfig.symbol_name,
            description: chartConfig.description,
            type: "crypto",
            session: chartConfig.session,
            timezone: chartConfig.timezone,
            exchange: "WildBoar",
            listed_exchange: "WildBoar",
            minmov: chartConfig.minmov,
            pricescale: chartConfig.pricescale,
            has_intraday: chartConfig.has_intraday,
            has_daily: chartConfig.has_daily,
            has_weekly_and_monthly: chartConfig.has_weekly_and_monthly,
            supported_resolutions: chartConfig.resolutions,
            volume_precision: chartConfig.volume_precision,
            data_status: "streaming",
          });
        } catch (err) {
          onError(err?.message || "resolveSymbol error");
        }
      },

      getBars: async (symbolInfo, resolution, periodParams, onHistoryCallback, onErrorCallback) => {
        try {
          const from = periodParams?.from;
          const to = periodParams?.to;

          const url = new URL(chartConfig.bars_url, window.location.origin);
          url.searchParams.set("fund_code", chartConfig.symbol_code);
          url.searchParams.set("symbol", chartConfig.symbol_code);
          url.searchParams.set("resolution", resolution);
          if (from != null) url.searchParams.set("from", String(from));
          if (to != null) url.searchParams.set("to", String(to));

          const resp = await fetch(url.toString(), {
            method: "GET",
            credentials: "same-origin",
            headers: { "Accept": "application/json" },
          });

          if (!resp.ok) {
            onErrorCallback(`HTTP ${resp.status}`);
            return;
          }

          const payload = await resp.json().catch(() => null);
          const bars = normalizeBars(payload);

          if (!bars.length) {
            onHistoryCallback([], { noData: true });
            return;
          }

          onHistoryCallback(bars, { noData: false });
        } catch (err) {
          onErrorCallback(err?.message || "getBars error");
        }
      },

      subscribeBars: (_symbolInfo, _resolution, _onRealtimeCallback, _subscriberUID, _onResetCacheNeededCallback) => {
        // Stage 13: stub only
      },

      unsubscribeBars: (_subscriberUID) => {
        // Stage 13: stub only
      },
    };
  }

  function removeWidget() {
    if (widget && typeof widget.remove === "function") {
      widget.remove();
    }
    widget = null;
  }

  function createWidget() {
    const container = document.getElementById(containerId);
    if (!container) return;
    if (typeof TradingView === "undefined" || typeof TradingView.widget !== "function") return;

    container.innerHTML = "";
    removeWidget();

    widget = new TradingView.widget({
      container: containerId,
      library_path: chartConfig.library_path,
      datafeed: buildDatafeed(),
      symbol: chartConfig.symbol_code,
      interval: chartConfig.default_resolution,
      locale,
      timezone: chartConfig.timezone,
      autosize: true,
      fullscreen: false,
      theme: getThemeName(),

      disabled_features: [
        "header_symbol_search",
        "symbol_search_hot_key",
        "header_compare",
        "compare_symbol",
        "header_saveload",
        "header_screenshot",
        "display_market_status",
        "go_to_date",
        "timeframes_toolbar",
        "show_hide_button_in_legend",
        "edit_buttons_in_legend",
        "context_menus",
        "control_bar",
        "use_localstorage_for_settings",
      ],
      enabled_features: [
        "hide_left_toolbar_by_default",
      ],
    });
  }

  function debounce(fn, wait) {
    let t = null;
    return (...args) => {
      clearTimeout(t);
      t = setTimeout(() => fn(...args), wait);
    };
  }

  const recreateChart = debounce(createWidget, 120);

  document.addEventListener("DOMContentLoaded", () => {
    createWidget();

    const observer = new MutationObserver((mutations) => {
      for (const m of mutations) {
        if (m.type === "attributes" && m.attributeName === "data-theme") {
          recreateChart();
          break;
        }
      }
    });
    observer.observe(body, { attributes: true, attributeFilter: ["data-theme"] });

    window.addEventListener("resize", recreateChart);
  });
})();
