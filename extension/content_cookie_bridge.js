/**
 * content_cookie_bridge.js – MyFeed Extension
 * ============================================================
 * Content Script: Brücke zwischen dem Web-Frontend (window.postMessage)
 * und der chrome.cookies API.
 *
 * Sicherheitsmodell:
 *  - Reagiert AUSSCHLIESSLICH auf Nachrichten mit type: 'MYFEED_GET_COOKIES'
 *  - Prüft den Absender-Origin gegen den konfigurierten Gateway-Host
 *    (+ localhost/127.0.0.1 als immer erlaubte Adressen)
 *  - Gibt NUR Session-relevante Google-Cookies zurück (SID, HSID, SSID …)
 *  - Sendet NIEMALS Cookies an unbekannte Origins
 * ============================================================
 */

"use strict";

// Relevante Google-Cookie-Namen für die Android-Scraper-Session
const RELEVANT_COOKIE_NAMES = new Set([
  "SID", "HSID", "SSID", "APISID", "SAPISID", "NID", "1P_JAR",
  "__Secure-1PSID", "__Secure-3PSID",
  "__Secure-1PAPISID", "__Secure-3PAPISID",
]);

/**
 * Prüft ob der sendende Origin erlaubt ist.
 * Erlaubt:
 *  - localhost / 127.x.x.x  (Loopback)
 *  - RFC-1918 private IPs   (10.x, 172.16-31.x, 192.168.x)
 *  - Derselbe Host wie die konfigurierte Gateway-URL
 */
async function isOriginAllowed(origin) {
  if (!origin) return false;

  try {
    const senderHost = new URL(origin).hostname;

    // Loopback immer erlaubt
    if (senderHost === "localhost" || /^127\./.test(senderHost)) return true;

    // RFC-1918 private Adressen erlaubt (das Admin-Frontend läuft im LAN)
    if (
      /^10\./.test(senderHost) ||
      /^192\.168\./.test(senderHost) ||
      /^172\.(1[6-9]|2\d|3[01])\./.test(senderHost)
    ) return true;

    // Gateway-Host aus der gespeicherten Konfiguration lesen
    const settings = await chrome.storage.local.get(["gatewayUrl"]);
    if (settings.gatewayUrl) {
      const gatewayHost = new URL(settings.gatewayUrl).hostname;
      if (senderHost === gatewayHost) return true;
    }
  } catch {
    // Ungültiger Origin oder URL
  }
  return false;
}

console.info("[MyFeed Bridge] Content Script geladen – bereit für Cookie-Anfragen.");

// Nachrichten-Listener (von der Seite → Content Script)
window.addEventListener("message", (event) => {
  // Nur Nachrichten von derselben Seite akzeptieren
  if (event.source !== window) return;
  if (!event.data || event.data.type !== "MYFEED_GET_COOKIES") return;

  const origin = event.origin;

  isOriginAllowed(origin).then((allowed) => {
    if (!allowed) {
      console.warn(
        "[MyFeed Bridge] Cookie-Anfrage von nicht-erlaubtem Origin abgelehnt:",
        origin
      );
      return;
    }

    // chrome.cookies ist in Content Scripts nicht verfügbar (MV3-Einschränkung).
    // Anfrage an den Background Service Worker weiterleiten.
    chrome.runtime.sendMessage({ type: "MYFEED_GET_GOOGLE_COOKIES" }, (response) => {
      if (chrome.runtime.lastError) {
        console.error("[MyFeed Bridge] Fehler beim Nachrichtenaustausch:", chrome.runtime.lastError.message);
        window.postMessage(
          { type: "MYFEED_COOKIES_RESPONSE", cookies: [], error: "Kommunikation mit Service Worker fehlgeschlagen" },
          origin
        );
        return;
      }

      const relevant = response?.cookies || [];
      console.info(`[MyFeed Bridge] ${relevant.length} relevante Cookie(s) empfangen.`);

      window.postMessage(
        { type: "MYFEED_COOKIES_RESPONSE", cookies: relevant },
        origin
      );
    });
  });
});
