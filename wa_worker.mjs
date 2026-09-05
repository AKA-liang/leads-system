#!/usr/bin/env node
/* wa_worker.mjs — WhatsApp 非官方通道(Baileys), 扫码登录 + 收发消息。
 *
 * 本地 HTTP 接口 (127.0.0.1:18791):
 *   GET  /status        -> {connected, phone, waVersion}
 *   GET  /qr.png        -> 扫码 PNG(未登录时生成到 data/wa_qr.png)
 *   POST /send {jid,text} -> 发送消息
 *
 * 收到客户消息 -> 追加 data/wa_inbox.jsonl(reply_listener.py 轮询处理)
 * 会话凭据 -> data/wa_session/(多号码时 data/wa_session_<phone>/)
 *
 * 启动: node wa_worker.mjs [--port 18791] [--number 8613800000000]
 * 环境: HTTP(S)_PROXY 走代理(Clash)
 */
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import QRCode from "qrcode";
import { HttpsProxyAgent } from "https-proxy-agent";

const __dir = path.dirname(fileURLToPath(import.meta.url));
const DATA = path.join(__dir, "data");
const PORT = Number(process.env.WA_WORKER_PORT || 18791);
const PROXY_URL = process.env.WA_PROXY_URL || "http://127.0.0.1:7890";
const PROXY_AGENT = new HttpsProxyAgent(PROXY_URL);

// 多号码支持: 传 --number 时会话目录/状态分开
const NUMBER = process.argv.includes("--number")
  ? process.argv[process.argv.indexOf("--number") + 1]
  : null;
// 配对码模式: --pairing 或环境变量 WA_PAIRING=true, 用手机号+8位码关联(无需扫码)
const PAIRING = process.env.WA_PAIRING === "true" || process.argv.includes("--pairing");
const SESSION_DIR = NUMBER
  ? path.join(DATA, `wa_session_${NUMBER}`)
  : path.join(DATA, "wa_session");
const INBOX = path.join(DATA, "wa_inbox.jsonl");
const QR_FILE = path.join(DATA, "wa_qr.png");
const PAIR_FILE = path.join(DATA, "wa_pairing_code.txt");

fs.mkdirSync(SESSION_DIR, { recursive: true });
fs.mkdirSync(DATA, { recursive: true });

let sock = null;
let qrString = null;
let lastQrAt = 0;
let pairingCode = null;
let lastPairPhone = null;   // 最近一次 /pair 用的号码, 用于自动续码
let pairTimer = null;       // 自动续码定时器

// 自动续码: 连接成功前每 PAIR_REFRESH_MS 自动刷新配对码(配对码 10 分钟有效)
const PAIR_REFRESH_MS = 8 * 60 * 1000;

async function requestPairing(phone) {
  if (!sock) return null;
  try {
    const code = await sock.requestPairingCode(phone);
    pairingCode = code;
    lastPairPhone = phone;
    try { fs.writeFileSync(PAIR_FILE, String(code)); } catch {}
    console.log(`[wa] 配对码已生成(${phone}): ${code} (${new Date().toLocaleTimeString()})`);
    return code;
  } catch (e) {
    console.error(`[wa] 配对码生成失败: ${e.message}`);
    return null;
  }
}

function schedulePairRefresh() {
  if (pairTimer) clearInterval(pairTimer);
  pairTimer = setInterval(async () => {
    if (sock?.user?.id) {           // 已连接, 不再需要
      clearInterval(pairTimer);
      return;
    }
    if (lastPairPhone) {
      console.log("[wa] 配对码接近过期, 自动刷新...");
      await requestPairing(lastPairPhone);
    }
  }, PAIR_REFRESH_MS);
}

async function makeSocket() {
  const { default: makeWASocket, useMultiFileAuthState, DisconnectReason } =
    await import("@whiskeysockets/baileys");

  const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);

  console.log("[wa] 创建 socket...");
  sock = makeWASocket({
    auth: state,
    printQRInTerminal: false,
    browser: NUMBER ? ["Chrome (Linux)", "Chrome", "22.0.0"] : ["Chrome (Linux)", "Chrome", "22.0.0"],
    markOnlineOnConnect: true,
    agent: PROXY_AGENT,
    connectTimeoutMs: 30000,
    keepAliveIntervalMs: 45000, // 45秒应用层心跳(判定窗口50秒, 容忍代理抖动); 保活靠原生WS ping(5s)
    qrTimeout: 15 * 60 * 1000,  // QR/配对码等待期 15 分钟(默认60s会导致QR模式3分钟主动断连)
    pairingCode: PAIRING,   // true = 手机号配对码模式, 不走扫码
  });

  sock.ev.on("creds.update", saveCreds);

  // 注入原生 WS ping frame(每5秒): 解决 WhatsApp 服务器对 baileys 连接 ~3.5 分钟限时断开
  // (baileys 的 w:p 应用层心跳无效, 但 WS ping frame 能保活)
  try {
    const wsNative = sock?.ws;
    if (wsNative && typeof wsNative.ping === "function" && !wsNative.__pingInjected) {
      wsNative.__pingInjected = true;
      const pingTimer = setInterval(() => {
        if (sock?.ws?.readyState === 1) {  // OPEN
          try { sock.ws.ping(); } catch {}
        } else {
          clearInterval(pingTimer);
        }
      }, 5000);
      console.log("[wa] 原生 WS ping 已注入(5s)");
    }
  } catch (e) {
    console.log("[wa] ws ping 注入失败(不影响主流程):", e.message);
  }

  sock.ev.on("connection.update", async (u) => {
    const { connection, lastDisconnect, qr } = u;
    if (PAIRING && u.pairingCode) {
      pairingCode = u.pairingCode;
      try {
        fs.writeFileSync(PAIR_FILE, String(u.pairingCode));
        console.log(`[wa] 配对码已生成: ${u.pairingCode} -> ${PAIR_FILE}`);
      } catch (e) {
        console.error("[wa] 配对码写文件失败:", e.message);
      }
    }
    if (qr) {
      qrString = qr;
      lastQrAt = Date.now();
      try {
        await QRCode.toFile(QR_FILE, qr, { width: 320, margin: 1 });
        console.log(`[wa] QR 已生成 -> ${QR_FILE}`);
      } catch (e) {
        console.error("[wa] QR 生成失败:", e.message);
      }
      // 自动请求配对码(握手完成后, 服务器才会接受配对注册)
      if (PAIRING && lastPairPhone && !sock.user?.id) {
        try {
          const code = await requestPairing(lastPairPhone);
          console.log(`[wa] 自动配对码: ${code}`);
        } catch (e) {
          console.log(`[wa] 自动配对失败: ${e.message}`);
        }
      }
    }
    if (connection === "open") {
      qrString = null;
      pairingCode = null;
      if (pairTimer) { clearInterval(pairTimer); pairTimer = null; }
      try { fs.rmSync(PAIR_FILE, { force: true }); } catch {}
      console.log("[wa] 已连接:", sock.user?.id || "?");
      // 重连后自动重新请求配对码, 保证码对应当前连接
      if (PAIRING && lastPairPhone && !sock.user?.id) {
        setTimeout(async () => {
          console.log("[wa] 连接就绪, 重新请求配对码...");
          await requestPairing(lastPairPhone);
        }, 3000);
      }
    }
    if (connection === "close") {
      const code = lastDisconnect?.error?.output?.statusCode;
      console.log(`[wa] 连接断开 code=${code} (401=凭据失效需重新扫码, 408=超时)`);
      if (code === DisconnectReason.loggedOut) {
        fs.rmSync(SESSION_DIR, { recursive: true, force: true });
        console.log("[wa] 已清除会话, 请重新扫码");
      }
      sock = null;
      setTimeout(makeSocket, 5000);
    }
  });

  sock.ev.on("messages.upsert", (m) => {
    const msgs = (m.messages || []).filter(
      (x) => x.message && !x.key.fromMe && x.key.remoteJid && x.key.remoteJid.endsWith("@s.whatsapp.net")
    );
    for (const x of msgs) {
      const text =
        x.message.conversation ||
        x.message.extendedTextMessage?.text ||
        x.message.imageMessage?.caption ||
        "";
      if (!text) continue;
      const rec = {
        id: x.key.id || `${Date.now()}`,
        jid: x.key.remoteJid,
        phone: (x.key.remoteJid || "").split("@")[0],
        text,
        ts: Date.now(),
      };
      try {
        fs.appendFileSync(INBOX, JSON.stringify(rec) + "\n");
        console.log(`[wa] 收到回复 ${rec.phone}: ${text.slice(0, 60)}`);
      } catch (e) {
        console.error("[wa] inbox 写入失败:", e.message);
      }
    }
  });
}

async function sendText(jid, text) {
  if (!sock) return { ok: false, error: "not_connected" };
  try {
    await sock.sendMessage(jid, { text });
    return { ok: true };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, "http://127.0.0.1");
  if (req.method === "GET" && url.pathname === "/status") {
    const connected = sock?.user?.id ? true : false;
    const j = { connected, phone: sock?.user?.id || null, qrReady: !!qrString, pairingCode, pairing: PAIRING };
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify(j));
  } else if (req.method === "GET" && url.pathname === "/qr.png") {
    if (fs.existsSync(QR_FILE)) {
      res.writeHead(200, { "Content-Type": "image/png" });
      res.end(fs.readFileSync(QR_FILE));
    } else {
      res.writeHead(404);
      res.end("no qr");
    }
  } else if (req.method === "POST" && url.pathname === "/pair") {
    let body = "";
    for await (const c of req) body += c;
    try {
      const { phone } = JSON.parse(body || "{}");
      if (!phone) {
        res.writeHead(400, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ ok: false, error: "phone required (含国家码, 如 8613800000000)" }));
        return;
      }
      if (!sock) {
        res.writeHead(500, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ ok: false, error: "not_connected, 等 worker 连上再试" }));
        return;
      }
      const code = await sock.requestPairingCode(phone);
      pairingCode = code;
      lastPairPhone = phone;
      try { fs.writeFileSync(PAIR_FILE, String(code)); } catch {}
      console.log(`[wa] 配对码已生成(${phone}): ${code}`);
      schedulePairRefresh();   // 启动自动续码
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ok: true, code, phone }));
    } catch (e) {
      res.writeHead(500, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ok: false, error: e.message }));
    }
  } else if (req.method === "POST" && url.pathname === "/send") {
    let body = "";
    for await (const c of req) body += c;
    try {
      const { jid, text } = JSON.parse(body || "{}");
      if (!jid || !text) {
        res.writeHead(400, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ ok: false, error: "jid+text required" }));
        return;
      }
      const r = await sendText(jid, text);
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(r));
    } catch (e) {
      res.writeHead(500, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ok: false, error: e.message }));
    }
  } else {
    res.writeHead(404);
    res.end("not found");
  }
});

makeSocket();
server.listen(PORT, "127.0.0.1", () => {
  console.log(`[wa] worker 已启动 http://127.0.0.1:${PORT}`);
});
