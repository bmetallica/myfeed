/**
 * background.js – MyFeed Context Collector v2 (Service Worker)
 * =============================================================
 * Verbesserungen gegenüber v1:
 *
 *  1. BLOCKLIST statt Keyword-Allowlist
 *     → Alles wird aufgezeichnet, außer Lärm-Domains (Banking, Shopping …)
 *
 *  2. KUMULIERTE Aktivzeit statt konsekutiver Timer
 *     → Tab-Wechsel pausiert den Timer, kehrt man zurück läuft er weiter.
 *     → Schwellwert: konfigurierbarer Wert (Default 15 s kumuliert).
 *
 *  3. SUCHBEGRIFF-EXTRAKTION
 *     → Google, Bing, DuckDuckGo, YouTube, GitHub, npm, PyPI, MDN …
 *     → Suchen werden sofort (ohne Dwell-Zeit) gesendet.
 *
 *  4. URL-DEDUPLIZIERUNG
 *     → Gleiche URL wird nicht öfter als 1× pro Cooldown-Zeitraum gesendet.
 *     → Tracking-Parameter (utm_*, fbclid …) werden vor dem Vergleich entfernt.
 *
 *  5. SEITEN-KONTEXT-EXTRAKTION
 *     → Wird via chrome.scripting direkt in die Seite injiziert.
 *     → Extrahiert: Meta-Description, OG-Description, H1, erste Absätze.
 *     → Bis zu 2000 Zeichen werden zusammen mit Titel+URL gespeichert.
 *
 * Architektur: Manifest V3 Service Worker (Chrome & Firefox 109+).
 * Verwendet ausschließlich chrome.* API – Firefox mappt diese nativ.
 */

// ── Standard-Blocklist ──────────────────────────────────────────────────────
// Domains, die als "Lärm" gelten und nie aufgezeichnet werden.
// Kann in den Einstellungen angepasst werden.
const DEFAULT_BLOCKLIST_STR = [
  // Zahlungsdienste & Banking
  "paypal.com", "stripe.com", "dkb.de", "ing.de", "comdirect.de",
  "sparkasse.de", "volksbank.de", "postbank.de",
  // Shopping
  "amazon.com", "amazon.de", "ebay.com", "ebay.de", "etsy.com",
  "zalando.de", "otto.de", "mediamarkt.de", "saturn.de", "idealo.de",
  "aliexpress.com", "wish.com", "kleinanzeigen.de",
  // Social-Media-Feeds (nicht inhaltliche Profile)
  "facebook.com", "instagram.com", "tiktok.com", "snapchat.com",
  "x.com", "threads.net",
  // Kommunikation
  "whatsapp.com", "telegram.org", "signal.org", "discord.com",
  // Mail & Kalender
  "mail.google.com", "outlook.live.com", "outlook.office.com",
  "calendar.google.com",
  // Entertainment
  "netflix.com", "spotify.com", "disneyplus.com", "primevideo.com",
  // Werbung & Tracking
  "doubleclick.net", "googleadservices.com",
].join(", ");

// ── Suchmaschinen-Konfiguration ─────────────────────────────────────────────
const SEARCH_ENGINES = [
  { pattern: /google\.[a-z.]+\/search/,         param: "q",            source: "search_google" },
  { pattern: /bing\.com\/search/,               param: "q",            source: "search_bing" },
  { pattern: /duckduckgo\.com\//,               param: "q",            source: "search_ddg" },
  { pattern: /youtube\.com\/results/,           param: "search_query", source: "search_youtube" },
  { pattern: /github\.com\/search/,             param: "q",            source: "search_github" },
  { pattern: /stackoverflow\.com\/search/,      param: "q",            source: "search_stackoverflow" },
  { pattern: /npmjs\.com\/search/,              param: "q",            source: "search_npm" },
  { pattern: /pypi\.org\/search/,               param: "q",            source: "search_pypi" },
  { pattern: /developer\.mozilla\.org.*search/, param: "q",            source: "search_mdn" },
  { pattern: /ecosia\.org\/search/,             param: "q",            source: "search_ecosia" },
];

// ── URL-Tracking-Parameter (werden vor Deduplizierung entfernt) ─────────────
const TRACKING_PARAMS = [
  "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
  "fbclid", "gclid", "_ga", "ref", "source", "mc_cid", "mc_eid",
];

// ── In-Memory-Zustand ───────────────────────────────────────────────────────

/**
 * Aktiver Tab je Fenster: windowId → tabId
 * Wird benötigt, um beim Tab-Wechsel den vorherigen Tab zu pausieren.
 * @type {Map<number, number>}
 */
const activeTabPerWindow = new Map();

/**
 * Tab-Zustand: tabId → { url, title, accumulatedMs, activatedAt }
 * - accumulatedMs: kumulierte Aktivzeit seit letztem Reset (URL-Wechsel)
 * - activatedAt: Zeitstempel der letzten Aktivierung (null = im Hintergrund)
 * @type {Map<number, {url:string, title:string, accumulatedMs:number, activatedAt:number|null}>}
 */
const tabState = new Map();

/**
 * Dedup-Cache: cacheKey → Zeitstempel des letzten Sendens
 * @type {Map<string, number>}
 */
const sentCache = new Map();

// ── Einstellungen ───────────────────────────────────────────────────────────

async function getSettings() {
  const s = await chrome.storage.local.get([
    "gatewayUrl", "bearerToken", "blocklist",
    "dwellSecs", "cooldownMins", "captureSearches",
  ]);
  return {
    gatewayUrl:      s.gatewayUrl    || "http://localhost:8000",
    bearerToken:     s.bearerToken   || "",
    blocklist: (typeof s.blocklist === "string" && s.blocklist.trim())
      ? s.blocklist.split(",").map(d => d.trim().toLowerCase()).filter(Boolean)
      : DEFAULT_BLOCKLIST_STR.split(",").map(d => d.trim().toLowerCase()).filter(Boolean),
    dwellMs:         ((s.dwellSecs    ?? 15) * 1000),
    cooldownMs:      ((s.cooldownMins ?? 30) * 60 * 1000),
    captureSearches: (s.captureSearches !== false), // default: true
  };
}

// ── Hilfsfunktionen ─────────────────────────────────────────────────────────

/** Gibt true zurück wenn die URL auf der Blocklist steht. */
function isBlocked(url, blocklist) {
  let host;
  try { host = new URL(url).hostname.replace(/^www\./, "").toLowerCase(); }
  catch { return true; }
  return blocklist.some(d => host === d || host.endsWith("." + d));
}

/** Entfernt Tracking-Parameter und gibt eine kanonische URL zurück. */
function canonicalUrl(url) {
  try {
    const u = new URL(url);
    TRACKING_PARAMS.forEach(p => u.searchParams.delete(p));
    return u.toString();
  } catch { return url; }
}

/**
 * Gibt Suchbegriff + Quell-ID zurück wenn die URL eine Suchanfrage ist,
 * sonst null.
 * @returns {{ query: string, source: string } | null}
 */
function extractSearch(url) {
  for (const engine of SEARCH_ENGINES) {
    if (engine.pattern.test(url)) {
      try {
        const q = new URL(url).searchParams.get(engine.param);
        if (q && q.trim()) return { query: q.trim(), source: engine.source };
      } catch { /* ignorieren */ }
    }
  }
  return null;
}

// ── Seiten-Kontext-Extraktion ───────────────────────────────────────────────

/**
 * Diese Funktion wird via chrome.scripting.executeScript in die Seite injiziert
 * und läuft im Kontext der Webseite (kein Zugriff auf Chrome-APIs).
 * Sie extrahiert Metadaten und einen Text-Snippet aus dem Seiteninhalt.
 *
 * Rückgabe: { description, heading, snippet }
 */
function extractPageContext() {
  /** Liest das erste nicht-leere Attribut aus einer Liste von Selektoren. */
  function getMeta(selectors) {
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (!el) continue;
      const val = (el.getAttribute("content") || el.textContent || "").trim();
      if (val) return val;
    }
    return null;
  }

  const description = getMeta([
    'meta[name="description"]',
    'meta[property="og:description"]',
    'meta[name="twitter:description"]',
  ]);

  const heading = getMeta(["h1"]);

  // Hauptinhalt: bevorzuge semantische Elemente
  const contentRoot =
    document.querySelector("article") ||
    document.querySelector("main") ||
    document.querySelector('[role="main"]') ||
    document.body;

  const snippet = Array.from(contentRoot.querySelectorAll("p"))
    .map(p => p.textContent.trim())
    .filter(t => t.length > 60)   // zu kurze Fragmente (Buttons, Labels) ignorieren
    .slice(0, 6)
    .join(" ")
    .slice(0, 800) || null;

  return { description, heading, snippet };
}

/**
 * Extrahiert Seiten-Kontext für einen Tab via chrome.scripting.
 * Gibt bei Fehler (z.B. chrome://-Seiten, CSP-Blockierung) ein leeres Objekt zurück.
 * @param {number} tabId
 * @returns {Promise<{description?:string, heading?:string, snippet?:string}>}
 */
async function getPageContext(tabId) {
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId },
      func:   extractPageContext,
    });
    return results?.[0]?.result || {};
  } catch {
    return {};
  }
}

/** Sendet einen Eintrag ans Gateway. */
async function sendToGateway(entry) {
  const { gatewayUrl, bearerToken } = await getSettings();
  if (!bearerToken) {
    console.warn("[MyFeed] Kein Bearer-Token konfiguriert.");
    return;
  }
  try {
    const res = await fetch(gatewayUrl.replace(/\/$/, "") + "/api/v1/context", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${bearerToken}`,
      },
      body: JSON.stringify({
        source:    entry.source,
        title:     entry.title,
        url:       entry.url,
        content:   entry.content || null,
        timestamp: entry.timestamp || new Date().toISOString(),
      }),
    });
    if (!res.ok) console.warn(`[MyFeed] Gateway ${res.status}:`, await res.text());
    else console.info(`[MyFeed] ✓ [${entry.source}] "${entry.title}"`);
  } catch (err) {
    console.error("[MyFeed] Netzwerkfehler:", err);
  }
}

// ── Kumulativer Dwell-Timer ─────────────────────────────────────────────────

/** Initialisiert den Zustand für einen Tab (neue URL oder neuer Tab). */
function resetTabState(tabId, url, title, isActive) {
  tabState.set(tabId, {
    url:           url   || "",
    title:         title || "",
    accumulatedMs: 0,
    activatedAt:   isActive ? Date.now() : null,
  });
}

/**
 * Pausiert den Timer für einen Tab (er geht in den Hintergrund).
 * Addiert die seit letzter Aktivierung vergangene Zeit.
 */
function pauseTab(tabId) {
  const state = tabState.get(tabId);
  if (!state || state.activatedAt === null) return;
  state.accumulatedMs += Date.now() - state.activatedAt;
  state.activatedAt = null;
}

/** Markiert einen Tab als wieder aktiv (Timer läuft weiter). */
function resumeTab(tabId) {
  const state = tabState.get(tabId);
  if (state && state.activatedAt === null) {
    state.activatedAt = Date.now();
  }
}

/**
 * Prüft ob der Dwell-Schwellwert erreicht ist und sendet ggf. den Eintrag.
 * Kümmert sich auch um Blocklist- und Dedup-Prüfung.
 */
async function checkAndSend(tabId) {
  const state = tabState.get(tabId);
  if (!state || !state.url || !state.title) return;

  // Laufende Zeit einberechnen (falls Tab noch aktiv ist)
  let totalMs = state.accumulatedMs;
  if (state.activatedAt !== null) totalMs += Date.now() - state.activatedAt;

  const settings = await getSettings();
  if (totalMs < settings.dwellMs) return;

  // Blocklist
  if (isBlocked(state.url, settings.blocklist)) {
    console.debug("[MyFeed] Blocklist skip:", state.url);
    return;
  }

  // Dedup
  const cacheKey = canonicalUrl(state.url);
  const lastSent = sentCache.get(cacheKey);
  if (lastSent && (Date.now() - lastSent) < settings.cooldownMs) {
    console.debug("[MyFeed] Dedup skip:", state.url);
    return;
  }

  // Senden und Timer zurücksetzen (damit nicht sofort wieder gesendet wird)
  sentCache.set(cacheKey, Date.now());
  state.accumulatedMs = 0;
  if (state.activatedAt !== null) state.activatedAt = Date.now();

  // Seiten-Kontext extrahieren (läuft direkt in der Seite)
  const ctx = await getPageContext(tabId);
  const contentParts = [ctx.description, ctx.heading, ctx.snippet].filter(Boolean);
  const content = contentParts.join("\n\n").slice(0, 2000) || null;

  await sendToGateway({ source: "browser_chrome", title: state.title, url: state.url, content });
}

/**
 * Verarbeitet eine Suchmaschinen-URL: sendet den Suchbegriff sofort
 * (kein Dwell-Timer nötig – Suche ist ein klares Interessens-Signal).
 */
async function handleSearch(url) {
  const settings = await getSettings();
  if (!settings.captureSearches) return;

  const match = extractSearch(url);
  if (!match) return;

  // Suchen: 5-Minuten-Cooldown pro Query+Engine
  const cacheKey = `search:${match.source}:${match.query}`;
  const lastSent = sentCache.get(cacheKey);
  if (lastSent && (Date.now() - lastSent) < 5 * 60 * 1000) return;

  sentCache.set(cacheKey, Date.now());
  await sendToGateway({
    source: match.source,
    title:  `Suche: ${match.query}`,
    url:    url,
  });
}

// ── Event-Listener ───────────────────────────────────────────────────────────

// ── Cookie-Abruf für Content Script (chrome.cookies nicht in Content Scripts verfügbar) ──

const _RELEVANT_COOKIE_NAMES = new Set([
  "SID", "HSID", "SSID", "APISID", "SAPISID", "NID", "1P_JAR",
  "__Secure-1PSID", "__Secure-3PSID",
  "__Secure-1PAPISID", "__Secure-3PAPISID",
]);

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type !== "MYFEED_GET_GOOGLE_COOKIES") return false;
  console.info("[MyFeed] Cookie-Anfrage vom Content Script empfangen.");
  chrome.cookies.getAll({ domain: ".google.com" }, (allCookies) => {
    const relevant = (allCookies || []).filter(c => _RELEVANT_COOKIE_NAMES.has(c.name));
    console.info(`[MyFeed] ${relevant.length} Google-Cookie(s) gefunden und gesendet.`);
    sendResponse({ cookies: relevant });
  });
  return true; // Async-Antwort: Kanal offenhalten
});

/**
 * Beim ersten Installieren: vorkonfigurierte Defaults laden (falls vorhanden).
 * Die Datei _myfeed_defaults.json wird nur im vorkonfigurierten Paket mitgeliefert,
 * das über das Admin-Dashboard heruntergeladen wurde.
 */
chrome.runtime.onInstalled.addListener(async ({ reason }) => {
  if (reason !== "install") return;
  try {
    const r = await fetch(chrome.runtime.getURL("myfeed_defaults.json"));
    if (!r.ok) return;                      // Datei nicht vorhanden → kein Problem
    const defaults = await r.json();
    const current  = await chrome.storage.local.get(["gatewayUrl", "bearerToken"]);
    const toSet    = {};
    if (!current.gatewayUrl  && defaults.gatewayUrl)  toSet.gatewayUrl  = defaults.gatewayUrl;
    if (!current.bearerToken && defaults.bearerToken) toSet.bearerToken = defaults.bearerToken;
    if (Object.keys(toSet).length > 0) {
      await chrome.storage.local.set(toSet);
      console.info("[MyFeed] Vorkonfigurierte Einstellungen geladen:", Object.keys(toSet));
    }
  } catch { /* Stille Fehlerbehandlung – kein Absturz wenn Datei fehlt */ }
});

/** Tab-Aktivierung: vorherigen Tab pausieren, neuen Tab fortsetzen/initialisieren. */
chrome.tabs.onActivated.addListener(async ({ tabId, windowId }) => {
  const prevTabId = activeTabPerWindow.get(windowId);
  activeTabPerWindow.set(windowId, tabId);

  // Vorherigen Tab pausieren + prüfen ob er gesendet werden soll
  if (prevTabId && prevTabId !== tabId) {
    pauseTab(prevTabId);
    await checkAndSend(prevTabId);
  }

  // Neuen Tab initialisieren oder fortsetzen
  if (!tabState.has(tabId)) {
    try {
      const tab = await chrome.tabs.get(tabId);
      if (tab.url?.startsWith("http")) resetTabState(tabId, tab.url, tab.title, true);
    } catch { /* Tab bereits geschlossen */ }
  } else {
    resumeTab(tabId);
  }
});

/** Seite geladen (Navigation / Reload): Zustand zurücksetzen + Suche prüfen. */
chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  // Titel-Update (SPA-Navigation ohne vollständigen Reload)
  if (changeInfo.title) {
    const state = tabState.get(tabId);
    if (state) state.title = changeInfo.title;
  }

  // Vollständig geladene Seite
  if (changeInfo.status === "complete" && tab.url?.startsWith("http")) {
    // Suchbegriffe sofort abhandeln
    await handleSearch(tab.url);

    // Tab-Zustand für neue URL zurücksetzen
    resetTabState(tabId, tab.url, tab.title, tab.active === true);
  }
});

/** Tab geschlossen: letzte akkumulierte Zeit prüfen, Zustand aufräumen. */
chrome.tabs.onRemoved.addListener(async (tabId) => {
  pauseTab(tabId);
  await checkAndSend(tabId);
  tabState.delete(tabId);
  for (const [winId, tid] of activeTabPerWindow) {
    if (tid === tabId) activeTabPerWindow.delete(winId);
  }
});

/** Fenster-Fokus: alle aktiven Tabs pausieren wenn Fokus verloren, sonst fortsetzen. */
chrome.windows.onFocusChanged.addListener(async (windowId) => {
  if (windowId === chrome.windows.WINDOW_ID_NONE) {
    // Browser verliert Fokus → alle aktiven Tabs pausieren
    for (const [, tabId] of activeTabPerWindow) {
      pauseTab(tabId);
      await checkAndSend(tabId);
    }
  } else {
    // Browser erhält Fokus → aktiven Tab im Fenster fortsetzen
    const tabId = activeTabPerWindow.get(windowId);
    if (tabId) {
      resumeTab(tabId);
    } else {
      // Fenster war vorher nicht bekannt
      const [tab] = await chrome.tabs.query({ active: true, windowId });
      if (tab?.id && tab.url?.startsWith("http")) {
        activeTabPerWindow.set(windowId, tab.id);
        resetTabState(tab.id, tab.url, tab.title, true);
      }
    }
  }
});

// ── Chrome History Sync (inkl. Android-Chrome via Google-Sync) ────────────────

const _HISTORY_ALARM  = "myfeed_history_sync";
const _SYNC_INTERVAL  = 15;   // Minuten
const _LOOKBACK_MS    = 7 * 24 * 60 * 60 * 1000; // 7 Tage
const _MAX_RESULTS    = 500;

// Domains, die nie als Verlaufseintrag gesendet werden sollen
const _SKIP_HOSTS = ["google.com", "gstatic.com", "googleapis.com", "youtube.com", "ggpht.com"];

async function syncChromeHistory() {
  try {
    const { historyLastSync } = await chrome.storage.local.get("historyLastSync");
    const startTime = historyLastSync
      ? new Date(historyLastSync).getTime()
      : Date.now() - _LOOKBACK_MS;

    const results = await chrome.history.search({ text: "", startTime, maxResults: _MAX_RESULTS });

    let sent = 0;
    for (const item of results) {
      if (!item.url?.startsWith("http") || !item.title) continue;
      if ((item.visitCount ?? 0) < 2) continue;
      try {
        const host = new URL(item.url).hostname;
        if (_SKIP_HOSTS.some(d => host === d || host.endsWith("." + d))) continue;
      } catch { continue; }

      await sendToGateway({
        source:    "browser_history",
        title:     item.title,
        url:       item.url,
        timestamp: item.lastVisitTime ? new Date(item.lastVisitTime).toISOString() : undefined,
      });
      sent++;
    }

    if (sent > 0) console.info(`[MyFeed History] ${sent} Verlaufseinträge synchronisiert.`);
    await chrome.storage.local.set({ historyLastSync: new Date().toISOString() });
  } catch (err) {
    console.error("[MyFeed History] Sync-Fehler:", err);
  }
}

// Alarm einrichten (1 Min nach Start, dann alle 15 Min)
chrome.alarms.create(_HISTORY_ALARM, { delayInMinutes: 1, periodInMinutes: _SYNC_INTERVAL });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === _HISTORY_ALARM) syncChromeHistory();
});
