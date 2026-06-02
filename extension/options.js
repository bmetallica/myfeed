/**
 * options.js – Einstellungsseite für MyFeed Context Collector v2
 * ==============================================================
 * Liest/speichert: gatewayUrl, bearerToken, blocklist,
 *                  dwellSecs, cooldownMins, captureSearches
 */

// ── DOM-Referenzen ───────────────────────────────────────────
const gatewayUrlInput    = document.getElementById("gateway-url");
const bearerTokenInput   = document.getElementById("bearer-token");
const blocklistInput     = document.getElementById("blocklist");
const dwellSecsInput     = document.getElementById("dwell-secs");
const cooldownMinsInput  = document.getElementById("cooldown-mins");
const captureSearchesCb  = document.getElementById("capture-searches");
const saveBtn            = document.getElementById("btn-save");
const testBtn            = document.getElementById("btn-test");
const statusMsg          = document.getElementById("status-msg");

/** Standard-Blocklist – identisch zu background.js */
const DEFAULT_BLOCKLIST = [
  "paypal.com", "stripe.com", "dkb.de", "ing.de", "comdirect.de",
  "sparkasse.de", "volksbank.de", "postbank.de",
  "amazon.com", "amazon.de", "ebay.com", "ebay.de", "etsy.com",
  "zalando.de", "otto.de", "mediamarkt.de", "saturn.de", "idealo.de",
  "aliexpress.com", "wish.com", "kleinanzeigen.de",
  "facebook.com", "instagram.com", "tiktok.com", "snapchat.com",
  "x.com", "threads.net",
  "whatsapp.com", "telegram.org", "signal.org", "discord.com",
  "mail.google.com", "outlook.live.com", "outlook.office.com",
  "calendar.google.com",
  "netflix.com", "spotify.com", "disneyplus.com", "primevideo.com",
  "doubleclick.net", "googleadservices.com",
].join(", ");

// ── Hilfsfunktionen ──────────────────────────────────────────

/**
 * Zeigt eine farbige Status-Meldung unterhalb der Buttons.
 * @param {string} message
 * @param {"success"|"error"|"info"} type
 * @param {number} [durationMs=3000] – 0 = dauerhaft anzeigen
 */
function showStatus(message, type = "info", durationMs = 3000) {
  statusMsg.textContent = message;
  statusMsg.className   = type;
  if (durationMs > 0) {
    setTimeout(() => { statusMsg.textContent = ""; statusMsg.className = ""; }, durationMs);
  }
}

// ── Einstellungen laden ──────────────────────────────────────

async function loadSettings() {
  const s = await chrome.storage.local.get([
    "gatewayUrl", "bearerToken", "blocklist",
    "dwellSecs", "cooldownMins", "captureSearches",
  ]);

  gatewayUrlInput.value   = s.gatewayUrl   || "http://localhost:8000";
  bearerTokenInput.value  = s.bearerToken  || "";
  blocklistInput.value    = s.blocklist    || DEFAULT_BLOCKLIST;
  dwellSecsInput.value    = s.dwellSecs    ?? 15;
  cooldownMinsInput.value = s.cooldownMins ?? 30;
  captureSearchesCb.checked = (s.captureSearches !== false);
}

// ── Einstellungen speichern ──────────────────────────────────

saveBtn.addEventListener("click", async () => {
  const gatewayUrl    = gatewayUrlInput.value.trim();
  const bearerToken   = bearerTokenInput.value.trim();
  const blocklist     = blocklistInput.value.trim();
  const dwellSecs     = parseInt(dwellSecsInput.value, 10);
  const cooldownMins  = parseInt(cooldownMinsInput.value, 10);
  const captureSearches = captureSearchesCb.checked;

  if (!gatewayUrl)    return showStatus("Bitte eine Gateway-URL eingeben.", "error");
  if (!bearerToken)   return showStatus("Bitte einen Bearer-Token eingeben.", "error");
  if (isNaN(dwellSecs)   || dwellSecs < 1)   return showStatus("Aktivzeit-Schwelle: Zahl ≥ 1 eingeben.", "error");
  if (isNaN(cooldownMins) || cooldownMins < 1) return showStatus("Cooldown: Zahl ≥ 1 eingeben.", "error");

  await chrome.storage.local.set({
    gatewayUrl, bearerToken, blocklist,
    dwellSecs, cooldownMins, captureSearches,
  });
  showStatus("Einstellungen gespeichert.", "success");
});

// ── Verbindungstest ──────────────────────────────────────────

testBtn.addEventListener("click", async () => {
  const gatewayUrl  = gatewayUrlInput.value.trim();
  const bearerToken = bearerTokenInput.value.trim();

  if (!gatewayUrl || !bearerToken) {
    return showStatus("Bitte zuerst URL und Token ausfüllen.", "error");
  }

  showStatus("Teste Verbindung …", "info", 0);
  testBtn.disabled = true;

  try {
    const res = await fetch(gatewayUrl.replace(/\/$/, "") + "/health");
    if (res.ok) showStatus(`Gateway erreichbar (HTTP ${res.status}).`, "success");
    else        showStatus(`Gateway antwortete mit HTTP ${res.status}.`, "error");
  } catch (err) {
    showStatus(`Verbindungsfehler: ${err.message}`, "error", 6000);
  } finally {
    testBtn.disabled = false;
  }
});

// ── Initialisierung ──────────────────────────────────────────

loadSettings();
