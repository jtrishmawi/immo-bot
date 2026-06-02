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
    Browsers,
} from "@whiskeysockets/baileys";
import { Boom } from "@hapi/boom";
import express from "express";
import { mkdir, rm } from "fs/promises";

const PHONE       = process.env.WHATSAPP_PHONE;
const SESSION_DIR = "/app/data/whatsapp-session";
const PORT        = parseInt(process.env.PORT ?? "3000", 10);

let sock              = null;
let status            = "disconnected"; // connected | pairing_pending | disconnected
let abortPairing      = false;          // set true when connection closes before pairing completes
let pairingCodeSent   = false;          // prevent requesting a second code during handshake reconnects

function toJid(number) {
    return number.replace(/^\+/, "") + "@s.whatsapp.net";
}

async function clearSession() {
    pairingCodeSent = false;
    try {
        await rm(SESSION_DIR, { recursive: true, force: true });
        await mkdir(SESSION_DIR, { recursive: true });
        console.log("[whatsapp] Session cleared");
    } catch (err) {
        console.error("[whatsapp] Failed to clear session:", err.message);
    }
}

async function connect() {
    abortPairing = false;
    await mkdir(SESSION_DIR, { recursive: true });

    const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);
    const { version }          = await fetchLatestBaileysVersion();

    sock = makeWASocket({
        version,
        auth:              state,
        printQRInTerminal: false,
        browser:           Browsers.ubuntu("Chrome"),
    });

    sock.ev.on("creds.update", saveCreds);

    sock.ev.on("connection.update", async ({ connection, lastDisconnect }) => {
        if (connection === "open") {
            status = "connected";
            console.log("[whatsapp] Connected");
            return;
        }
        if (connection === "close") {
            status       = "disconnected";
            abortPairing = true;  // cancel any pending pairing request
            const code   = new Boom(lastDisconnect?.error)?.output?.statusCode;

            if (code === DisconnectReason.loggedOut) {
                // Session rejected — clear it and reconnect to get a fresh pairing code
                console.log("[whatsapp] Logged out — clearing session and reconnecting…");
                await clearSession();
                setTimeout(connect, 2_000);
            } else {
                console.log("[whatsapp] Connection closed, reconnecting in 5s…");
                setTimeout(connect, 5_000);
            }
        }
    });

    // Request pairing code outside the event handler, once socket has finished its
    // handshake with WA servers. Calling it inside connection.update causes
    // "unable to connect" on the phone.
    if (!state.creds.registered && PHONE && !pairingCodeSent) {
        pairingCodeSent = true;
        status = "pairing_pending";
        await new Promise(r => setTimeout(r, 5_000));
        if (abortPairing) {
            console.log("[whatsapp] Pairing aborted — connection closed before code could be requested");
            return;
        }
        try {
            const code = await sock.requestPairingCode(PHONE);
            const fmt  = code.length === 8 ? `${code.slice(0, 4)}-${code.slice(4)}` : code;
            console.log(`\n========================================`);
            console.log(`  WhatsApp pairing code: ${fmt}`);
            console.log(`  Enter in: Settings → Linked Devices`);
            console.log(`            → Link a Device → Link with phone number`);
            console.log(`  Code expires in ~30 seconds`);
            console.log(`========================================\n`);
        } catch (err) {
            console.error("[whatsapp] Failed to get pairing code:", err.message);
        }
    }
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
            await sock.sendMessage(jid, { image: { url: mediaUrl }, caption: text });
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
