const DIV = "─────────────────";

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("NEPSE Bot is alive.");

    let body;
    try {
      body = await request.json();
    } catch {
      return new Response("Bad Request", { status: 400 });
    }

    const message = body?.message;
    if (!message?.text) return new Response("OK");

    const chatId = String(message.chat.id);
    const text = message.text.trim();

    if (chatId !== env.ALLOWED_CHAT_ID) {
      await send(env.TELEGRAM_TOKEN, chatId, "⛔ Unauthorized.");
      return new Response("OK");
    }

    await typing(env.TELEGRAM_TOKEN, chatId);

    // ── /alert NABIL above 550  or  /alert NABIL below 520 ──────────
    if (text.startsWith("/alert") && !text.startsWith("/alerts") && !text.startsWith("/delalert")) {
      const parts = text.replace(/^\/alert\s*/i, "").toUpperCase().trim().split(/\s+/);
      const symbol = parts[0];
      const direction = parts.length === 3 ? parts[1].toLowerCase() : "above";
      const price = parseFloat(parts[parts.length - 1]);

      if (!symbol || isNaN(price) || !["above", "below"].includes(direction)) {
        await send(env.TELEGRAM_TOKEN, chatId, [
          "⚠️ <b>Usage</b>",
          "<code>/alert NABIL above 550</code>  — alert when price goes above 550",
          "<code>/alert NABIL below 520</code>  — alert when price drops below 520",
        ].join("\n"));
        return new Response("OK");
      }

      const raw = await env.NEPSE_KV.get("alerts");
      const alerts = raw ? JSON.parse(raw) : {};
      alerts[symbol] = { price, direction };
      await env.NEPSE_KV.put("alerts", JSON.stringify(alerts));

      const arrow = direction === "above" ? "▲" : "▼";
      await send(env.TELEGRAM_TOKEN, chatId, [
        `🔔 <b>Alert Set — ${symbol}</b>`,
        DIV,
        `Notify when price goes ${arrow} <b>Rs ${price.toLocaleString()}</b>`,
        "",
        "You'll get a message the next time the market update runs and the condition is met.",
        "Use /alerts to see all active alerts.",
      ].join("\n"));

    // ── /alerts ───────────────────────────────────────────────────
    } else if (text === "/alerts") {
      const raw = await env.NEPSE_KV.get("alerts");
      const alerts = raw ? JSON.parse(raw) : {};
      const keys = Object.keys(alerts);
      if (!keys.length) {
        await send(env.TELEGRAM_TOKEN, chatId, [
          "📭 <b>No active alerts</b>",
          "",
          "Set one with <code>/alert NABIL above 550</code>",
        ].join("\n"));
      } else {
        const lines = ["🔔 <b>Active Alerts</b>", DIV];
        for (const [sym, cfg] of Object.entries(alerts)) {
          const arrow = cfg.direction === "above" ? "▲" : "▼";
          lines.push(`  <b>${sym}</b>  ${arrow} Rs ${cfg.price.toLocaleString()}`);
        }
        lines.push("", "Use <code>/delalert NABIL</code> to remove one.");
        await send(env.TELEGRAM_TOKEN, chatId, lines.join("\n"));
      }

    // ── /delalert NABIL ───────────────────────────────────────────
    } else if (text.startsWith("/delalert")) {
      const symbol = text.replace(/^\/delalert\s*/i, "").toUpperCase().trim();
      if (!symbol) {
        await send(env.TELEGRAM_TOKEN, chatId, "Usage: <code>/delalert NABIL</code>");
        return new Response("OK");
      }
      const raw = await env.NEPSE_KV.get("alerts");
      const alerts = raw ? JSON.parse(raw) : {};
      if (alerts[symbol]) {
        delete alerts[symbol];
        await env.NEPSE_KV.put("alerts", JSON.stringify(alerts));
        await send(env.TELEGRAM_TOKEN, chatId, `✅ Alert for <b>${symbol}</b> removed.`);
      } else {
        await send(env.TELEGRAM_TOKEN, chatId, `⚠️ No alert found for <b>${symbol}</b>.`);
      }

    // ── /portfolio NABIL:100:520,NICA:200:800 ────────────────────
    } else if (text.startsWith("/portfolio") && !text.startsWith("/myportfolio")) {
      const raw = text.replace(/^\/portfolio\s*/i, "").toUpperCase().trim();
      if (!raw) {
        await send(env.TELEGRAM_TOKEN, chatId, [
          "⚠️ <b>Usage</b>",
          "<code>/portfolio NABIL:100:520,NICA:200:800</code>",
          "",
          "Format: <code>SYMBOL:QUANTITY:BUY_PRICE</code>",
          "Example: NABIL bought 100 shares at Rs 520 each.",
        ].join("\n"));
        return new Response("OK");
      }

      const portfolio = {};
      const errors = [];
      for (const entry of raw.split(",")) {
        const [sym, qty, buy] = entry.trim().split(":");
        if (!sym || !qty || !buy || isNaN(qty) || isNaN(buy)) {
          errors.push(entry);
          continue;
        }
        portfolio[sym] = { qty: parseInt(qty), buy: parseFloat(buy) };
      }

      if (errors.length) {
        await send(env.TELEGRAM_TOKEN, chatId, `⚠️ Skipped invalid entries: ${errors.join(", ")}\nFormat: SYMBOL:QTY:BUY_PRICE`);
      }

      await env.NEPSE_KV.put("portfolio", JSON.stringify(portfolio));

      const lines = ["💰 <b>Portfolio Saved</b>", DIV];
      for (const [sym, cfg] of Object.entries(portfolio)) {
        const total = (cfg.qty * cfg.buy).toLocaleString();
        lines.push(`  <b>${sym}</b>  ${cfg.qty} shares @ Rs ${cfg.buy}  =  Rs ${total}`);
      }
      lines.push("", "P&L will appear in every market update.");
      await send(env.TELEGRAM_TOKEN, chatId, lines.join("\n"));

    // ── /myportfolio ──────────────────────────────────────────────
    } else if (text === "/myportfolio") {
      const raw = await env.NEPSE_KV.get("portfolio");
      const portfolio = raw ? JSON.parse(raw) : {};
      const keys = Object.keys(portfolio);
      if (!keys.length) {
        await send(env.TELEGRAM_TOKEN, chatId, [
          "📭 <b>No portfolio set</b>",
          "",
          "Add holdings with:",
          "<code>/portfolio NABIL:100:520,NICA:200:800</code>",
        ].join("\n"));
      } else {
        const lines = ["💰 <b>Your Portfolio</b>", DIV];
        let total = 0;
        for (const [sym, cfg] of Object.entries(portfolio)) {
          const invested = cfg.qty * cfg.buy;
          total += invested;
          lines.push(`  <b>${sym}</b>  ${cfg.qty} @ Rs ${cfg.buy}  (Rs ${invested.toLocaleString()})`);
        }
        lines.push(DIV, `  Total invested: <b>Rs ${total.toLocaleString()}</b>`);
        lines.push("", "<i>Live P&L is shown in every market update.</i>");
        await send(env.TELEGRAM_TOKEN, chatId, lines.join("\n"));
      }

    } else if (text.startsWith("/watch")) {
      const raw = text.replace(/^\/watch\s*/i, "").toUpperCase();
      const stocks = raw.split(",").map((s) => s.trim()).filter(Boolean);

      if (!stocks.length) {
        await send(env.TELEGRAM_TOKEN, chatId, [
          "⚠️ <b>Missing stock symbols</b>",
          "",
          "Usage: <code>/watch NABIL,NICA,SANIMA</code>",
        ].join("\n"));
        return new Response("OK");
      }

      await env.NEPSE_KV.put("watch_stocks", stocks.join(","));
      await send(env.TELEGRAM_TOKEN, chatId, [
        "✅ <b>Watchlist Updated</b>",
        DIV,
        ...stocks.map((s) => `  • ${s}`),
        "",
        "📬 You'll receive price + broker data on every market update.",
        "💡 Type /check anytime to get live data right now.",
      ].join("\n"));

    } else if (text === "/check" || text === "/now") {
      if (!isMarketOpen()) {
        await send(env.TELEGRAM_TOKEN, chatId, [
          "🔴 <b>Market is Closed</b>",
          DIV,
          "NEPSE trades Monday–Friday, 11:00 AM – 3:00 PM NST.",
          "",
          "Come back during market hours for live data.",
        ].join("\n"));
        return new Response("OK");
      }

      // Trigger GitHub Actions to run the Python bot on-demand
      const ghRes = await fetch(
        `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/nepse_bot.yml/dispatches`,
        {
          method: "POST",
          headers: {
            "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "NEPSE-Bot",
          },
          body: JSON.stringify({ ref: "main" }),
        }
      );

      if (ghRes.ok || ghRes.status === 204) {
        await send(env.TELEGRAM_TOKEN, chatId, [
          "⏳ <b>Fetching live data...</b>",
          "",
          "Update will arrive in about 30–60 seconds.",
        ].join("\n"));
      } else {
        await send(env.TELEGRAM_TOKEN, chatId, "⚠️ Could not trigger update. Try again shortly.");
      }

    } else if (text.startsWith("/broker")) {
      const raw = text.replace(/^\/broker\s*/i, "").toUpperCase().trim();
      const symbols = raw ? raw.split(",").map(s => s.trim()).filter(Boolean) : [];

      if (!symbols.length) {
        await send(env.TELEGRAM_TOKEN, chatId, [
          "⚠️ <b>Missing symbol</b>",
          "",
          "Usage: <code>/broker NABIL</code> or <code>/broker NABIL,NICA</code>",
          "",
          "Analyzes broker buy/sell activity to detect accumulation or distribution.",
          "Best used after market closes at 3 PM NST.",
        ].join("\n"));
        return new Response("OK");
      }

      const ghRes = await fetch(
        `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/broker_report.yml/dispatches`,
        {
          method: "POST",
          headers: {
            "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "NEPSE-Bot",
          },
          body: JSON.stringify({ ref: "main", inputs: { symbols: symbols.join(",") } }),
        }
      );

      if (ghRes.ok || ghRes.status === 204) {
        await send(env.TELEGRAM_TOKEN, chatId, [
          `⏳ <b>Analyzing ${symbols.join(", ")}...</b>`,
          "",
          "Broker report will arrive in ~30–60 seconds.",
          "<i>Note: Full data is only available after 3 PM NST market close.</i>",
        ].join("\n"));
      } else {
        await send(env.TELEGRAM_TOKEN, chatId, "⚠️ Could not trigger analysis. Try again.");
      }

    } else if (text === "/open") {
      const ghRes = await fetch(
        `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/market_open.yml/dispatches`,
        {
          method: "POST",
          headers: {
            "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "NEPSE-Bot",
          },
          body: JSON.stringify({ ref: "main" }),
        }
      );

      if (ghRes.ok || ghRes.status === 204) {
        await send(env.TELEGRAM_TOKEN, chatId, [
          "⏳ <b>Fetching market open summary...</b>",
          "",
          "Summary will arrive in ~30–60 seconds.",
        ].join("\n"));
      } else {
        await send(env.TELEGRAM_TOKEN, chatId, "⚠️ Could not trigger market open summary. Try again.");
      }

    } else if (text === "/close") {
      const ghRes = await fetch(
        `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/broker_report.yml/dispatches`,
        {
          method: "POST",
          headers: {
            "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "NEPSE-Bot",
          },
          body: JSON.stringify({ ref: "main", inputs: { symbols: "" } }),
        }
      );

      if (ghRes.ok || ghRes.status === 204) {
        await send(env.TELEGRAM_TOKEN, chatId, [
          "⏳ <b>Fetching market close summary...</b>",
          "",
          "Close summary + broker analysis for your watchlist will arrive in ~30–60 seconds.",
        ].join("\n"));
      } else {
        await send(env.TELEGRAM_TOKEN, chatId, "⚠️ Could not trigger market close summary. Try again.");
      }

    } else if (text === "/status") {
      const stored = await env.NEPSE_KV.get("watch_stocks");
      if (stored) {
        const list = stored.split(",").map((s) => `  • ${s}`).join("\n");
        await send(env.TELEGRAM_TOKEN, chatId, [
          "👁 <b>Your Watchlist</b>",
          DIV,
          list,
          "",
          "💡 Type /check to fetch live prices now.",
        ].join("\n"));
      } else {
        await send(env.TELEGRAM_TOKEN, chatId, [
          "📭 <b>No watchlist set</b>",
          "",
          "Use <code>/watch NABIL,NICA</code> to start tracking stocks.",
        ].join("\n"));
      }

    } else if (text === "/stop") {
      await env.NEPSE_KV.delete("watch_stocks");
      await send(env.TELEGRAM_TOKEN, chatId, [
        "🛑 <b>Watchlist Cleared</b>",
        "",
        "Stock-specific updates are paused.",
        "Use <code>/watch</code> anytime to start again.",
      ].join("\n"));

    } else if (text === "/start") {
      await send(env.TELEGRAM_TOKEN, chatId, [
        "👋 <b>Welcome to NEPSE Market Bot</b>",
        DIV,
        "Your personal Nepal Stock Exchange assistant.",
        "",
        "📈 <b>Live Market Updates</b>",
        "    Every 30 min · 11 AM–3 PM NST · Mon–Fri",
        "    Index · gainers · losers · volume spikes",
        "    /open for open summary · /close for close summary",
        "",
        "🔔 <b>Price Alerts</b>",
        "    Get notified the moment a stock crosses",
        "    your target price",
        "",
        "💰 <b>Portfolio Tracker</b>",
        "    Live P&L on your holdings in every update",
        "",
        "⚡ <b>Circuit Breaker Alerts</b>",
        "    Instant notification when any stock hits",
        "    upper or lower circuit (±10%)",
        "",
        "🏦 <b>Broker Analysis</b>",
        "    End-of-day breakdown of who bought/sold",
        "    Detects accumulation, distribution,",
        "    wash trading, and manipulation signals",
        "",
        "Type /help to see all commands.",
        "",
        "<i>Start with:</i>  <code>/watch NABIL,NICA,SANIMA</code>",
      ].join("\n"));

    } else if (text === "/help") {
      await send(env.TELEGRAM_TOKEN, chatId, [
        "📖 <b>NEPSE Market Bot — Commands</b>",
        DIV,
        "",
        "📊 <b>Market Data</b>",
        "<code>/check</code>  — live index, gainers, losers, watchlist",
        "<code>/open</code>   — market open summary (index + sector outlook)",
        "<code>/close</code>  — market close summary + broker analysis for watchlist",
        "",
        "🔔 <b>Price Alerts</b>",
        "<code>/alert NABIL above 550</code>",
        "<code>/alert NABIL below 520</code>",
        "<code>/alerts</code>  — view all active alerts",
        "<code>/delalert NABIL</code>  — remove an alert",
        "",
        "💰 <b>Portfolio</b>",
        "<code>/portfolio NABIL:100:520,NICA:200:800</code>",
        "    (symbol : quantity : buy price)",
        "<code>/myportfolio</code>  — view your holdings",
        "",
        "🏦 <b>Broker Analysis</b>",
        "<code>/broker NABIL</code>  — who bought/sold today",
        "    Best after 3 PM NST market close",
        "",
        "👁 <b>Watchlist</b>",
        "<code>/watch NABIL,NICA,SANIMA</code>",
        "<code>/status</code>  — view watchlist",
        "<code>/stop</code>  — clear watchlist",
        "",
        DIV,
        "🕐 <b>Auto Schedules (Mon–Fri)</b>",
        "  11:00 AM  Market open summary  (or /open anytime)",
        "  Every 30 min  Market update + alerts + P&L",
        "   3:15 PM  Close summary + broker report  (or /close anytime)",
      ].join("\n"));

    } else {
      await send(env.TELEGRAM_TOKEN, chatId, [
        "❓ <b>Unknown command</b>",
        "",
        "Type /help to see all available commands.",
      ].join("\n"));
    }

    return new Response("OK");
  },
};

// ── Market hours check (NST = UTC+5:45) ──────────────────────────
function isMarketOpen() {
  const now = new Date();
  const nstOffset = 5 * 60 + 45; // minutes
  const utcMinutes = now.getUTCHours() * 60 + now.getUTCMinutes();
  const nstMinutes = utcMinutes + nstOffset;

  const nstDay = new Date(now.getTime() + nstOffset * 60 * 1000).getUTCDay();
  // 0=Sun, 6=Sat — market closed on weekends
  if (nstDay === 0 || nstDay === 6) return false;

  const marketOpen = 11 * 60;   // 11:00 AM
  const marketClose = 15 * 60;  // 3:00 PM
  const nstTimeOfDay = nstMinutes % (24 * 60);
  return nstTimeOfDay >= marketOpen && nstTimeOfDay <= marketClose;
}

// ── Telegram helpers ──────────────────────────────────────────────
async function typing(token, chatId) {
  await fetch(`https://api.telegram.org/bot${token}/sendChatAction`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, action: "typing" }),
  });
}

async function send(token, chatId, text) {
  await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text, parse_mode: "HTML" }),
  });
}
