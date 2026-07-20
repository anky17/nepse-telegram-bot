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

    // Show typing indicator before any processing
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
        "─────────────────",
        ...stocks.map((s) => `  • ${s}`),
        "",
        "📬 You'll receive price + broker data on every market update.",
      ].join("\n"));

    } else if (text === "/status") {
      await typing(env.TELEGRAM_TOKEN, chatId);
      const stored = await env.NEPSE_KV.get("watch_stocks");
      if (stored) {
        const list = stored.split(",").map((s) => `  • ${s}`).join("\n");
        await send(env.TELEGRAM_TOKEN, chatId, [
          "👁 <b>Your Watchlist</b>",
          "─────────────────",
          list,
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
        "─────────────────",
        "Sends live NEPSE updates every 30 min",
        "during market hours (11 AM – 3 PM NST).",
        "",
        "⚙️ <b>Commands</b>",
        "<code>/watch NABIL,NICA</code>  — track specific stocks",
        "<code>/status</code>            — show current watchlist",
        "<code>/stop</code>              — clear watchlist",
        "<code>/help</code>              — show this message",
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
