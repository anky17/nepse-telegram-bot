const DIV = "─────────────────";
const URL_RE = /https?:\/\/[^\s]+|t\.me\/[^\s]+/i;

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
    if (!message) return new Response("OK");

    const chatId = String(message.chat.id);
    if (chatId !== env.ALLOWED_CHAT_ID) return new Response("OK");

    // ── Welcome new members ───────────────────────────────────────
    if (message.new_chat_members?.length) {
      for (const member of message.new_chat_members) {
        if (member.is_bot) continue;
        const name = member.first_name + (member.last_name ? ` ${member.last_name}` : "");
        await send(env.TELEGRAM_TOKEN, chatId, [
          `👋 Welcome, <b>${name}</b>!`,
          DIV,
          "This group gets <b>live NEPSE market updates</b> every 30 minutes during trading hours.",
          "",
          "📊 <b>What you'll see here:</b>",
          "  • Index movement & top gainers/losers",
          "  • Circuit breaker alerts",
          "  • Broker buy/sell analysis",
          "  • IPO/rights notices",
          "  • On-demand stock data",
          "",
          "📜 Please read /rules before posting.",
          "Type /help to see all available commands.",
          "",
          "<i>Market hours: Mon–Fri · 11:00 AM – 3:00 PM NST</i>",
        ].join("\n"));
      }
      return new Response("OK");
    }

    if (!message.text) return new Response("OK");

    const text = message.text.trim().replace(/@\w+/, "").trim();
    const sender = message.from;

    // ── Anti-spam: auto-delete URLs from non-admins ───────────────
    if (!text.startsWith("/")) {
      if (URL_RE.test(text) && !(await isAdmin(env.TELEGRAM_TOKEN, chatId, sender.id))) {
        await deleteMessage(env.TELEGRAM_TOKEN, chatId, message.message_id);
        await addWarn(env, chatId, sender);
      }
      return new Response("OK");
    }

    await typing(env.TELEGRAM_TOKEN, chatId);

    // ── Market data commands ──────────────────────────────────────
    if (text === "/check" || text === "/now") {
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
      const ok = await dispatchWorkflow(env, "nepse_bot.yml", {});
      await send(env.TELEGRAM_TOKEN, chatId, ok
        ? "⏳ <b>Fetching live data...</b>\n\nUpdate will arrive in about 30–60 seconds."
        : "⚠️ Could not trigger update. Try again shortly.");

    } else if (text.startsWith("/stock")) {
      const symbols = parseSymbols(text, "/stock");
      if (!symbols.length) {
        await send(env.TELEGRAM_TOKEN, chatId, [
          "⚠️ <b>Missing symbol</b>",
          "",
          "Usage: <code>/stock NABIL</code> or <code>/stock NABIL,NICA</code>",
          "Shows LTP, OHLC, volume, 52-week range, market cap, and fundamentals.",
        ].join("\n"));
        return new Response("OK");
      }
      const ok = await dispatchWorkflow(env, "stock_info.yml", { symbols: symbols.join(",") });
      await send(env.TELEGRAM_TOKEN, chatId, ok
        ? `⏳ <b>Fetching ${symbols.join(", ")}...</b>\n\nStock details will arrive in ~30–60 seconds.`
        : "⚠️ Could not fetch stock info. Try again.");

    } else if (text.startsWith("/broker")) {
      const symbols = parseSymbols(text, "/broker");
      if (!symbols.length) {
        await send(env.TELEGRAM_TOKEN, chatId, [
          "⚠️ <b>Missing symbol</b>",
          "",
          "Usage: <code>/broker NABIL</code> or <code>/broker NABIL,NICA</code>",
          "Shows top buyers, sellers, accumulators and distributors.",
          "<i>Uses daily floorsheet data — available after market close.</i>",
        ].join("\n"));
        return new Response("OK");
      }
      const ok = await dispatchWorkflow(env, "broker_floorsheet.yml", { symbols: symbols.join(",") });
      await send(env.TELEGRAM_TOKEN, chatId, ok
        ? `⏳ <b>Analyzing ${symbols.join(", ")}...</b>\n\nBroker report will arrive in ~30–60 seconds.`
        : "⚠️ Could not trigger analysis. Try again.");

    } else if (text === "/top") {
      const ok = await dispatchWorkflow(env, "top_stocks.yml", { mode: "top" });
      await send(env.TELEGRAM_TOKEN, chatId, ok
        ? "⏳ <b>Fetching top stocks...</b>\n\nTop gainers, losers & turnover will arrive in ~30–60 seconds."
        : "⚠️ Could not fetch top stocks. Try again.");

    } else if (text === "/sector") {
      if (!isMarketOpen()) {
        await send(env.TELEGRAM_TOKEN, chatId, "🔴 <b>Market is Closed</b>\n\nSector performance resets after close — data will be live again tomorrow at 11:00 AM NST.");
        return new Response("OK");
      }
      const ok = await dispatchWorkflow(env, "top_stocks.yml", { mode: "sector" });
      await send(env.TELEGRAM_TOKEN, chatId, ok
        ? "⏳ <b>Fetching sector performance...</b>\n\nSector breakdown will arrive in ~30–60 seconds."
        : "⚠️ Could not fetch sector data. Try again.");

    // ── Index threshold ───────────────────────────────────────────
    } else if (text.startsWith("/threshold")) {
      const parts = text.replace(/^\/threshold\s*/i, "").trim().toLowerCase().split(/\s+/);
      if (parts[0] === "clear") {
        await env.NEPSE_KV.delete("index_threshold");
        await env.NEPSE_KV.delete("threshold_alerted");
        await send(env.TELEGRAM_TOKEN, chatId, "✅ Index threshold alerts cleared.");
      } else if ((parts[0] === "above" || parts[0] === "below") && parts[1] && !isNaN(parts[1])) {
        const direction = parts[0];
        const value = parseFloat(parts[1]);
        const raw = await env.NEPSE_KV.get("index_threshold");
        const current = raw ? JSON.parse(raw) : {};
        current[direction] = value;
        await env.NEPSE_KV.put("index_threshold", JSON.stringify(current));
        const arrow = direction === "above" ? "▲" : "▼";
        await send(env.TELEGRAM_TOKEN, chatId, [
          `📊 <b>Index Alert Set</b>`,
          DIV,
          `Alert when NEPSE index goes ${arrow} <b>${value.toLocaleString()}</b>`,
          "",
          "Use <code>/threshold clear</code> to remove.",
        ].join("\n"));
      } else {
        await send(env.TELEGRAM_TOKEN, chatId, [
          "⚠️ <b>Usage</b>",
          "<code>/threshold above 2800</code>  — alert when index crosses above 2800",
          "<code>/threshold below 2600</code>  — alert when index drops below 2600",
          "<code>/threshold clear</code>  — remove all threshold alerts",
        ].join("\n"));
      }

    // ── Setup check ───────────────────────────────────────────────
    } else if (text === "/setup") {
      if (!(await isAdmin(env.TELEGRAM_TOKEN, chatId, sender.id))) {
        await send(env.TELEGRAM_TOKEN, chatId, "⛔ Only admins can use this command.");
        return new Response("OK");
      }
      const meRes = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_TOKEN}/getMe`);
      const me = await meRes.json();
      const botId = me.result?.id;

      const memberRes = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_TOKEN}/getChatMember`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: chatId, user_id: botId }),
      });
      const member = await memberRes.json();
      const perms = member.result || {};

      const checks = [
        ["can_delete_messages",  "Delete messages  (anti-spam)"],
        ["can_restrict_members", "Restrict members  (mute / ban)"],
        ["can_invite_users",     "Invite users  (optional)"],
      ];

      const lines = ["🔧 <b>Bot Permission Check</b>", DIV];
      for (const [perm, label] of checks) {
        lines.push(`${perms[perm] ? "✅" : "❌"}  ${label}`);
      }

      const missing = checks.filter(([p]) => !perms[p]);
      if (missing.length) {
        lines.push(
          "",
          "⚠️ <b>Missing permissions. To fix:</b>",
          "Group name → Administrators → select this bot → enable the missing permissions above → Save."
        );
      } else {
        lines.push("", "✅ All permissions are configured correctly!");
      }

      await send(env.TELEGRAM_TOKEN, chatId, lines.join("\n"));

    // ── Rules ─────────────────────────────────────────────────────
    } else if (text === "/rules") {
      await send(env.TELEGRAM_TOKEN, chatId, [
        "📜 <b>Group Rules</b>",
        DIV,
        "",
        "1️⃣  <b>No spam</b> — no repeated messages, ads, or self-promotion",
        "2️⃣  <b>No scam/pump links</b> — no referral links, fake tips, or suspicious URLs",
        "3️⃣  <b>Stay on topic</b> — NEPSE stocks and market discussion only",
        "4️⃣  <b>Be respectful</b> — no personal attacks or abusive language",
        "5️⃣  <b>No unverified tips</b> — do not spread unconfirmed buy/sell signals",
        "6️⃣  <b>No forwarded spam</b> — avoid chain messages or off-topic content",
        "",
        DIV,
        "⚠️ Violations result in a warning. 3 warnings = permanent ban.",
        "<i>Links are automatically deleted and count as a warning.</i>",
      ].join("\n"));

    // ── Admin: warn ───────────────────────────────────────────────
    } else if (text === "/warn") {
      const target = message.reply_to_message?.from;
      if (!target) {
        await send(env.TELEGRAM_TOKEN, chatId, "⚠️ Reply to a message to warn that user.");
        return new Response("OK");
      }
      if (!(await isAdmin(env.TELEGRAM_TOKEN, chatId, sender.id))) {
        await send(env.TELEGRAM_TOKEN, chatId, "⛔ Only admins can use this command.");
        return new Response("OK");
      }
      await addWarn(env, chatId, target);

    // ── Admin: ban ────────────────────────────────────────────────
    } else if (text === "/ban") {
      const target = message.reply_to_message?.from;
      if (!target) {
        await send(env.TELEGRAM_TOKEN, chatId, "⚠️ Reply to a message to ban that user.");
        return new Response("OK");
      }
      if (!(await isAdmin(env.TELEGRAM_TOKEN, chatId, sender.id))) {
        await send(env.TELEGRAM_TOKEN, chatId, "⛔ Only admins can use this command.");
        return new Response("OK");
      }
      await banMember(env.TELEGRAM_TOKEN, chatId, target.id);
      await env.NEPSE_KV.delete(`warns_${target.id}`);
      await send(env.TELEGRAM_TOKEN, chatId, `🚫 <b>${target.first_name}</b> has been banned.`);

    // ── Admin: kick ───────────────────────────────────────────────
    } else if (text === "/kick") {
      const target = message.reply_to_message?.from;
      if (!target) {
        await send(env.TELEGRAM_TOKEN, chatId, "⚠️ Reply to a message to kick that user.");
        return new Response("OK");
      }
      if (!(await isAdmin(env.TELEGRAM_TOKEN, chatId, sender.id))) {
        await send(env.TELEGRAM_TOKEN, chatId, "⛔ Only admins can use this command.");
        return new Response("OK");
      }
      await kickMember(env.TELEGRAM_TOKEN, chatId, target.id);
      await send(env.TELEGRAM_TOKEN, chatId, `👢 <b>${target.first_name}</b> has been kicked. They can rejoin via invite link.`);

    // ── Admin: mute ───────────────────────────────────────────────
    } else if (text.startsWith("/mute")) {
      const target = message.reply_to_message?.from;
      if (!target) {
        await send(env.TELEGRAM_TOKEN, chatId, "⚠️ Reply to a message to mute that user.\nUsage: <code>/mute 1h</code> · <code>/mute 30m</code> · <code>/mute 1d</code>");
        return new Response("OK");
      }
      if (!(await isAdmin(env.TELEGRAM_TOKEN, chatId, sender.id))) {
        await send(env.TELEGRAM_TOKEN, chatId, "⛔ Only admins can use this command.");
        return new Response("OK");
      }
      const durationStr = text.replace(/^\/mute\s*/i, "").trim() || "1h";
      const seconds = parseDuration(durationStr);
      const untilDate = Math.floor(Date.now() / 1000) + seconds;
      await muteMember(env.TELEGRAM_TOKEN, chatId, target.id, untilDate);
      await send(env.TELEGRAM_TOKEN, chatId, `🔇 <b>${target.first_name}</b> muted for <b>${durationStr}</b>.`);

    // ── Admin: unmute ─────────────────────────────────────────────
    } else if (text === "/unmute") {
      const target = message.reply_to_message?.from;
      if (!target) {
        await send(env.TELEGRAM_TOKEN, chatId, "⚠️ Reply to a message to unmute that user.");
        return new Response("OK");
      }
      if (!(await isAdmin(env.TELEGRAM_TOKEN, chatId, sender.id))) {
        await send(env.TELEGRAM_TOKEN, chatId, "⛔ Only admins can use this command.");
        return new Response("OK");
      }
      await unmuteMember(env.TELEGRAM_TOKEN, chatId, target.id);
      await send(env.TELEGRAM_TOKEN, chatId, `🔊 <b>${target.first_name}</b> has been unmuted.`);

    // ── Admin: unban ──────────────────────────────────────────────
    } else if (text === "/unban") {
      const target = message.reply_to_message?.from;
      if (!target) {
        await send(env.TELEGRAM_TOKEN, chatId, "⚠️ Reply to a forwarded message from the banned user to unban them.");
        return new Response("OK");
      }
      if (!(await isAdmin(env.TELEGRAM_TOKEN, chatId, sender.id))) {
        await send(env.TELEGRAM_TOKEN, chatId, "⛔ Only admins can use this command.");
        return new Response("OK");
      }
      await unbanMember(env.TELEGRAM_TOKEN, chatId, target.id);
      await env.NEPSE_KV.delete(`warns_${target.id}`);
      await send(env.TELEGRAM_TOKEN, chatId, `✅ <b>${target.first_name}</b> has been unbanned.`);

    // ── Start ─────────────────────────────────────────────────────
    } else if (text === "/start") {
      await send(env.TELEGRAM_TOKEN, chatId, [
        "👋 <b>Welcome to NEPSE Market Bot</b>",
        DIV,
        "Your Nepal Stock Exchange group assistant.",
        "",
        "📈 <b>Live Market Updates</b>",
        "    Every 30 min · 11 AM–3 PM NST · Mon–Fri",
        "    Index · gainers · losers · volume spikes",
        "",
        "⚡ <b>Circuit Breaker Alerts</b>",
        "    Instant notification when any stock hits ±10%",
        "",
        "📢 <b>IPO / Rights Alerts</b>",
        "    Daily check for new IPO and rights notices",
        "",
        "Type /help to see all commands.",
      ].join("\n"));

    // ── Help ──────────────────────────────────────────────────────
    } else if (text === "/help") {
      await send(env.TELEGRAM_TOKEN, chatId, [
        "📖 <b>NEPSE Market Bot — Commands</b>",
        DIV,
        "",
        "📊 <b>Market Data</b>",
        "<code>/check</code>     — live index, gainers, losers",
        "<code>/top</code>       — top gainers, losers & turnover",
        "<code>/sector</code>    — sector-wise performance",
        "<code>/stock NABIL</code>  — LTP, OHLC, volume, fundamentals",
        "",
        "🏦 <b>Broker Analysis</b>",
        "<code>/broker NABIL</code>  — top buyers/sellers from daily floorsheet",
        "    Available after market close",
        "",
        "📊 <b>Index Alerts</b>",
        "<code>/threshold above 2800</code>",
        "<code>/threshold below 2600</code>",
        "<code>/threshold clear</code>",
        "",
        "📜 <b>Group</b>",
        "<code>/rules</code>  — group rules",
        "",
        "🛡 <b>Admin Only</b>",
        "<code>/setup</code>  — check bot permissions",
        "<code>/warn</code>   — reply to warn a user (3 = auto-ban)",
        "<code>/ban</code>    — reply to permanently ban",
        "<code>/kick</code>   — reply to remove (can rejoin)",
        "<code>/mute 1h</code>  — reply to mute (30m · 1h · 1d · 7d)",
        "<code>/unmute</code> — reply to remove mute",
        "<code>/unban</code>  — reply to unban",
        "",
        DIV,
        "🕐 <b>Auto Schedules (Mon–Fri)</b>",
        "  10:55 AM  Morning briefing",
        "  11:00 AM  Market open summary",
        "  Every 30 min  Market update",
        "   5:15 PM  EOD recap",
        "   8:00 PM  Broker floorsheet analysis",
        "  Daily  IPO / rights notices check",
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

  async scheduled(event, env, ctx) {
    if (event.cron === "10 5 * * 1-5") {
      // 10:55 AM NST — morning briefing
      await send(env.TELEGRAM_TOKEN, env.ALLOWED_CHAT_ID, [
        "🔔 <b>Market opens in 5 minutes!</b>",
        DIV,
        "Trading begins at <b>11:00 AM NST</b>.",
        "",
        "📊 Full open summary with index & sector outlook arriving shortly.",
        "💡 Use /check anytime for live data during market hours.",
      ].join("\n"));
    } else if (event.cron === "15 5 * * 1-5") {
      // 11:00 AM NST — market open summary
      await dispatchWorkflow(env, "market_open.yml", {});
    } else if (event.cron === "30 11 * * 1-5") {
      // 5:15 PM NST — EOD recap
      await dispatchWorkflow(env, "eod_recap.yml", {});
    }
  },
};

// ── GitHub Actions dispatcher ─────────────────────────────────────
async function dispatchWorkflow(env, workflow, inputs) {
  const res = await fetch(
    `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${workflow}/dispatches`,
    {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "NEPSE-Bot",
      },
      body: JSON.stringify({ ref: "main", inputs }),
    }
  );
  return res.ok || res.status === 204;
}

// ── Symbol parser ─────────────────────────────────────────────────
function parseSymbols(text, command) {
  const raw = text.replace(new RegExp(`^\\/${command.replace("/", "")}\\s*`, "i"), "").toUpperCase().trim();
  return raw ? raw.split(",").map(s => s.trim()).filter(Boolean) : [];
}

// ── Warn system ───────────────────────────────────────────────────
async function addWarn(env, chatId, user) {
  const key = `warns_${user.id}`;
  const count = parseInt(await env.NEPSE_KV.get(key) || "0") + 1;
  if (count >= 3) {
    await banMember(env.TELEGRAM_TOKEN, chatId, user.id);
    await env.NEPSE_KV.delete(key);
    await send(env.TELEGRAM_TOKEN, chatId, [
      `🚫 <b>${user.first_name}</b> has been permanently banned.`,
      "",
      "Reason: 3 warnings reached.",
    ].join("\n"));
  } else {
    await env.NEPSE_KV.put(key, String(count));
    await send(env.TELEGRAM_TOKEN, chatId, [
      `⚠️ <b>${user.first_name}</b> — Warning <b>${count}/3</b>`,
      "",
      "Please follow /rules. Reaching 3 warnings results in a permanent ban.",
    ].join("\n"));
  }
}

// ── Market hours check (NST = UTC+5:45) ──────────────────────────
function isMarketOpen() {
  const now = new Date();
  const nstOffset = 5 * 60 + 45;
  const nstDay = new Date(now.getTime() + nstOffset * 60 * 1000).getUTCDay();
  if (nstDay === 0 || nstDay === 6) return false;
  const nstMinutes = (now.getUTCHours() * 60 + now.getUTCMinutes() + nstOffset) % (24 * 60);
  return nstMinutes >= 11 * 60 && nstMinutes <= 15 * 60;
}

// ── Duration parser ───────────────────────────────────────────────
function parseDuration(str) {
  const match = str.match(/^(\d+)(m|h|d)$/i);
  if (!match) return 3600;
  const n = parseInt(match[1]);
  const unit = match[2].toLowerCase();
  return unit === "m" ? n * 60 : unit === "h" ? n * 3600 : n * 86400;
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

async function deleteMessage(token, chatId, messageId) {
  await fetch(`https://api.telegram.org/bot${token}/deleteMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, message_id: messageId }),
  });
}

async function isAdmin(token, chatId, userId) {
  const res = await fetch(`https://api.telegram.org/bot${token}/getChatMember`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, user_id: userId }),
  });
  const data = await res.json();
  return ["administrator", "creator"].includes(data.result?.status);
}

async function banMember(token, chatId, userId) {
  await fetch(`https://api.telegram.org/bot${token}/banChatMember`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, user_id: userId, revoke_messages: true }),
  });
}

async function kickMember(token, chatId, userId) {
  await banMember(token, chatId, userId);
  await unbanMember(token, chatId, userId);
}

async function unbanMember(token, chatId, userId) {
  await fetch(`https://api.telegram.org/bot${token}/unbanChatMember`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, user_id: userId, only_if_banned: true }),
  });
}

async function muteMember(token, chatId, userId, untilDate) {
  await fetch(`https://api.telegram.org/bot${token}/restrictChatMember`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: chatId,
      user_id: userId,
      until_date: untilDate,
      permissions: {
        can_send_messages: false,
        can_send_audios: false,
        can_send_documents: false,
        can_send_photos: false,
        can_send_videos: false,
        can_send_video_notes: false,
        can_send_voice_notes: false,
        can_send_polls: false,
        can_send_other_messages: false,
      },
    }),
  });
}

async function unmuteMember(token, chatId, userId) {
  await fetch(`https://api.telegram.org/bot${token}/restrictChatMember`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: chatId,
      user_id: userId,
      permissions: {
        can_send_messages: true,
        can_send_audios: true,
        can_send_documents: true,
        can_send_photos: true,
        can_send_videos: true,
        can_send_video_notes: true,
        can_send_voice_notes: true,
        can_send_polls: true,
        can_send_other_messages: true,
      },
    }),
  });
}
