const NEPSE = "https://nepalstock.com.np/api/nots";
const DIV = "─────────────────";
const HEADERS = {
  "User-Agent": "Mozilla/5.0 (compatible; NEPSEBot/1.0)",
  "Referer": "https://nepalstock.com.np/",
  "Accept": "application/json",
};

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

    if (text.startsWith("/watch")) {
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
      const isOpen = isMarketOpen();
      if (!isOpen) {
        await send(env.TELEGRAM_TOKEN, chatId, [
          "🔴 <b>Market is Closed</b>",
          DIV,
          "NEPSE trades Monday–Friday, 11:00 AM – 3:00 PM NST.",
          "",
          "Come back during market hours for live data.",
        ].join("\n"));
        return new Response("OK");
      }

      await send(env.TELEGRAM_TOKEN, chatId, "⏳ Fetching live NEPSE data...");
      await typing(env.TELEGRAM_TOKEN, chatId);

      const watchlist = (await env.NEPSE_KV.get("watch_stocks") || "").split(",").map(s => s.trim()).filter(Boolean);
      const msg = await buildMarketMessage(watchlist);
      await send(env.TELEGRAM_TOKEN, chatId, msg);

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

    } else if (text === "/help" || text === "/start") {
      await send(env.TELEGRAM_TOKEN, chatId, [
        "📈 <b>NEPSE Market Bot</b>",
        DIV,
        "Live NEPSE updates during market hours",
        "(Mon–Fri, 11 AM – 3 PM NST).",
        "",
        "⚙️ <b>Commands</b>",
        "<code>/check</code>              — fetch live data right now",
        "<code>/watch NABIL,NICA</code>   — track specific stocks",
        "<code>/status</code>             — show your watchlist",
        "<code>/stop</code>               — clear watchlist",
        "<code>/help</code>               — show this message",
        "",
        "📬 Automatic updates every 30 min during market hours.",
      ].join("\n"));

    } else {
      await send(env.TELEGRAM_TOKEN, chatId, [
        "❓ Unknown command.",
        "",
        "Type <code>/help</code> to see available commands.",
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

// ── NEPSE data fetchers ───────────────────────────────────────────
async function apiFetch(path) {
  try {
    const r = await fetch(`${NEPSE}${path}`, { headers: HEADERS });
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  }
}

function tagChange(value) {
  const sign = value >= 0 ? "+" : "";
  const icon = value >= 0 ? "▲" : "▼";
  return `${icon} ${sign}${value.toFixed(2)}`;
}

async function buildMarketMessage(watchlist = []) {
  const now = new Date();
  const nst = new Date(now.getTime() + (5 * 60 + 45) * 60 * 1000);
  const timeStr = nst.toUTCString().replace("GMT", "NST").slice(0, -4);

  const [indexData, summaryData, gainersData, losersData] = await Promise.all([
    apiFetch("/nepse-data/index"),
    apiFetch("/market-open"),
    apiFetch("/top25GainerLoser/top25Gainer"),
    apiFetch("/top25GainerLoser/top25Loser"),
  ]);

  const lines = [
    "📈 <b>NEPSE Live Update</b>",
    `<i>${timeStr}</i>`,
  ];

  // Index
  lines.push(`\n📊 <b>NEPSE Index</b>\n${DIV}`);
  try {
    const d = indexData?.data ?? indexData;
    const val = parseFloat(d?.currentValue ?? d?.index ?? 0);
    const chg = parseFloat(d?.change ?? d?.absoluteChange ?? 0);
    const pct = parseFloat(d?.perChange ?? d?.percentageChange ?? 0);
    const icon = chg >= 0 ? "🟢" : "🔴";
    lines.push(`${icon}  <b>${val.toLocaleString("en", { minimumFractionDigits: 2 })}</b>   ${tagChange(chg)} pts  (${tagChange(pct)}%)`);
  } catch {
    lines.push("⚠️ Unavailable");
  }

  // Market summary
  try {
    const d = summaryData?.data ?? summaryData;
    const turnover = parseFloat(d?.totalTurnover ?? 0);
    const shares = parseInt(d?.totalTradedShares ?? 0);
    const txns = parseInt(d?.totalTransactions ?? 0);
    lines.push(`\n💼 <b>Market Summary</b>\n${DIV}`);
    lines.push(`  Turnover      Rs ${(turnover / 1_000_000).toFixed(2)}M`);
    lines.push(`  Shares Traded ${shares.toLocaleString()}`);
    lines.push(`  Transactions  ${txns.toLocaleString()}`);
  } catch { /* skip */ }

  // Gainers
  lines.push(`\n🟢 <b>Top Gainers</b>\n${DIV}`);
  try {
    const items = gainersData?.data ?? gainersData ?? [];
    const list = Array.isArray(items) ? items : Object.values(items)[0] ?? [];
    for (const [i, s] of list.slice(0, 5).entries()) {
      const sym = s.symbol ?? s.stockSymbol ?? "?";
      const ltp = parseFloat(s.lastTradedPrice ?? s.ltp ?? 0);
      const pct = parseFloat(s.pointChange ?? s.percentageChange ?? 0);
      lines.push(`  ${i + 1}.  <b>${sym.padEnd(10)}</b> ${ltp.toFixed(1).padStart(8)}   <code>+${pct.toFixed(2)}%</code>`);
    }
  } catch { lines.push("⚠️ Unavailable"); }

  // Losers
  lines.push(`\n🔴 <b>Top Losers</b>\n${DIV}`);
  try {
    const items = losersData?.data ?? losersData ?? [];
    const list = Array.isArray(items) ? items : Object.values(items)[0] ?? [];
    for (const [i, s] of list.slice(0, 5).entries()) {
      const sym = s.symbol ?? s.stockSymbol ?? "?";
      const ltp = parseFloat(s.lastTradedPrice ?? s.ltp ?? 0);
      const pct = parseFloat(s.pointChange ?? s.percentageChange ?? 0);
      lines.push(`  ${i + 1}.  <b>${sym.padEnd(10)}</b> ${ltp.toFixed(1).padStart(8)}   <code>${pct.toFixed(2)}%</code>`);
    }
  } catch { lines.push("⚠️ Unavailable"); }

  // Watchlist
  if (watchlist.length) {
    lines.push(`\n👁 <b>Your Watchlist</b>\n${DIV}`);
    const stockFetches = watchlist.map((sym) => apiFetch(`/security/symbol/${sym}`));
    const results = await Promise.all(stockFetches);
    for (const [i, data] of results.entries()) {
      const sym = watchlist[i];
      try {
        let d = data?.data ?? data;
        if (Array.isArray(d)) d = d[0];
        const ltp = parseFloat(d?.lastTradedPrice ?? d?.ltp ?? 0);
        const chg = parseFloat(d?.change ?? d?.pointChange ?? 0);
        const pct = parseFloat(d?.perChange ?? d?.percentageChange ?? 0);
        const vol = parseInt(d?.totalTradeQuantity ?? d?.volume ?? 0);
        const icon = chg >= 0 ? "🟢" : "🔴";
        const sign = chg >= 0 ? "+" : "";
        lines.push(`  ${icon} <b>${sym.padEnd(10)}</b> ${ltp.toFixed(2).padStart(8)}   <code>${sign}${pct.toFixed(2)}%</code>   Vol ${vol.toLocaleString()}`);
      } catch {
        lines.push(`  ⚠️ ${sym}: unavailable`);
      }
    }
  }

  return lines.join("\n");
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
