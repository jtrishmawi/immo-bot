/**
 * immo-bot — WhatsApp sidecar (Baileys + Express)
 *
 * Env vars:
 *   WHATSAPP_PHONE  phone number to pair, international format without +  (e.g. 33612345678)
 *   PORT            HTTP port (default 3000)
 */
import makeWASocket, {
    useMultiFileAuthState,
    DisconnectReason,
    fetchLatestBaileysVersion,
} from "@whiskeysockets/baileys";
import { Boom } from "@hapi/boom";
import express from "express";
import { mkdir } from "fs/promises";

const PHONE       = process.env.WHATSAPP_PHONE;
const SESSION_DIR = "/app/data/whatsapp-session";
const PORT        = parseInt(process.env.PORT ?? "3000", 10);

let sock             = null;
let status           = "disconnected"; // connected | pairing_pending | disconnected
let pairingRequested = false;

function toJid(number) {
    // "+33612345678" → "33612345678@s.whatsapp.net"
    return number.replace(/^\+/, "") + "@s.whatsapp.net";
}

async function connect() {
    await mkdir(SESSION_DIR, { recursive: true });

    const { state, saveCreds }  = await useMultiFileAuthState(SESSION_DIR);
    const { version }           = await fetchLatestBaileysVersion();

    sock = makeWASocket({
        version,
        auth:               state,
        printQRInTerminal:  false,
        browser:            ["immo-bot", "Chrome", "1.0"],
    });

    sock.ev.on("creds.update", saveCreds);

    sock.ev.on("connection.update", async ({ connection, lastDisconnect }) => {
        if (connection === "open") {
            status = "connected";
            pairingRequested = false;
            console.log("[whatsapp] Connected");
            return;
        }

        if (connection === "close") {
            status = "disconnected";
            const code = new Boom(lastDisconnect?.error)?.output?.statusCode;
            if (code === DisconnectReason.loggedOut) {
                console.log("[whatsapp] Logged out — delete session to re-pair");
            } else {
                console.log("[whatsapp] Connection closed, reconnecting in 5s…");
                setTimeout(connect, 5_000);
            }
            return;
        }

        // Request pairing code once when session is not registered
        if (!sock.authState.creds.registered && !pairingRequested && PHONE) {
            pairingRequested = true;
            status = "pairing_pending";
            try {
                // Small delay to let the socket stabilise
                await new Promise(r => setTimeout(r, 2_000));
                const code = await sock.requestPairingCode(PHONE);
                console.log(`\n[whatsapp] Pairing code: ${code}\n`);
            } catch (err) {
                console.error("[whatsapp] Failed to get pairing code:", err.message);
            }
        }
    });
}

// ---------------------------------------------------------------------------
// HTTP server
// ---------------------------------------------------------------------------
const app = express();
app.use(express.json());

app.get("/health", (_req, res) => {
    res.json({ status });
});

app.post("/send", async (req, res) => {
    const { to, text, mediaUrl } = req.body;

    if (!to || !text) {
        return res.status(400).json({ error: "to and text are required" });
    }
    if (status !== "connected") {
        return res.status(503).json({ error: `Not connected (status: ${status})` });
    }

    try {
        const jid = toJid(to);
        if (mediaUrl) {
            await sock.sendMessage(jid, {
                image: { url: mediaUrl },
                caption: text,
            });
        } else {
            await sock.sendMessage(jid, { text });
        }
        res.json({ ok: true });
    } catch (err) {
        console.error("[whatsapp] Send error:", err.message);
        res.status(500).json({ error: err.message });
    }
});

app.listen(PORT, () => console.log(`[whatsapp] HTTP server on :${PORT}`));
connect();
