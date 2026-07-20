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
