# Gewichtungs-Konzept: MyFeed

Dieses Dokument beschreibt das Gewichtungssystem von MyFeed — wie Gewichte entstehen, wo sie gespeichert werden, wofür sie genutzt werden, und welche Schwachstellen es gibt. Es dient als Grundlage für zukünftige Verbesserungen.

---

## 1. Überblick: Die 5 Gewichtungsgrößen

| Größe | Tabelle/Ort | Quelle | Skala |
|---|---|---|---|
| `weight` | `tags` | Ollama Schritt 2 / manuell | 1–10 |
| `category_weight` | `tags` | Ollama Schritt 1 / manuell | 1–10 |
| `effective_weight` | berechnet (nicht gespeichert) | `round(weight × category_weight / 10)` | 1–10 |
| `weight` | `long_term_tags` | laufender kumulativer Durchschnitt | 1–10 |
| `tag_weight` | `news_results` | Snapshot von `effective_weight` zur Suchzeit | 1–10 |

---

## 2. Wie die Gewichte entstehen

### Tag-Generierung (Ollama, 2-stufig)

**Schritt 1 — Hauptkategorien erkennen**

Ollama analysiert alle Browser-Titel des Tages und gibt Kategorien mit Intensitätswerten zurück:

```json
[
  {"category": "Gaming",      "weight": 9},
  {"category": "IT/Security", "weight": 6},
  {"category": "Nachrichten", "weight": 4}
]
```

Bedeutung von `category_weight`: *Wie intensiv hat der Nutzer heute mit dieser Kategorie beschäftigt* (Häufigkeit + Tiefe der Beschäftigung).

→ Wird als `categories_map` gespeichert: `{"Gaming": 9, "IT/Security": 6, ...}`

**Schritt 2 — Spezifische Tags extrahieren**

Ollama extrahiert konkrete Themen-Tags und ordnet sie den Kategorien zu:

```json
[
  {"tag": "Minecraft",       "category": "Gaming",      "weight": 8},
  {"tag": "Pokémon",         "category": "Gaming",      "weight": 6},
  {"tag": "Penetration Test","category": "IT/Security", "weight": 9}
]
```

Bedeutung von `tag_weight`: *Wie spezifisch/dominant ist dieses Thema innerhalb seiner Kategorie* (nicht absolut, sondern relativ zur Kategorie).

### Effective Weight (Kombination)

```python
def _effective_weight(tag_weight: int, category_weight: int) -> int:
    return max(1, min(10, round(tag_weight * category_weight / 10)))
```

Beispiele:

| Tag-Weight | Cat-Weight | Effective | Anmerkung |
|---|---|---|---|
| 8 | 9 | 7 | Dominanter Tag in starker Kategorie |
| 8 | 3 | 2 | Dominanter Tag in schwacher Kategorie |
| 5 | 5 | 3 | Durchschnittlich in allem → nur 3! |
| 10 | 10 | 10 | Maximum |
| 3 | 3 | 1 | Minimum (Clamp greift) |

### Manuelle Tags

Der Nutzer setzt `weight` (1–10, default 8) und `category_weight` (1–10, **default 10**).
Da `category_weight=10` der Default ist, gilt für manuelle Tags: `effective_weight ≈ weight`.

### Cluster-Tags (K-Means)

Cluster-Tags werden ohne Kategorie-Kontext erzeugt. `category_weight` bleibt auf dem DB-Default (5). Damit ist `effective_weight = round(tag_weight × 5 / 10)` — also etwa halb so hoch wie der vergebene Tag-Weight.

---

## 3. Wo `effective_weight` verwendet wird

### 3.1 News-Suche: Suchreihenfolge

Tags werden vor der DDG/SearXNG-Suche nach `effective_weight DESC` sortiert. Hohe Tags werden **zuerst** gesucht — relevant wenn Rate-Limits greifen, da spätere Tags dann ausfallen.

```python
results = sorted(tag_map.values(), key=lambda x: -x["effective_weight"])
```

### 3.2 News-Suche: Artikelanzahl pro Tag

**Aktuell:** Jeder Tag bekommt exakt `max_per_tag` Artikel (Standard: 5), unabhängig vom Gewicht.

```
Gaming    (eff=9): 5 Artikel
Finanzen  (eff=2): 5 Artikel  ← identisch!
```

Das Gewicht beeinflusst die Menge **nicht**.

### 3.3 Speicherung und Anzeige von News

`effective_weight` wird als `tag_weight` in `news_results` gespeichert (Snapshot zur Suchzeit).

Sortierung bei der Ausgabe:
```sql
ORDER BY tag_weight DESC, COALESCE(published_at, found_date::timestamptz) DESC
```

### 3.4 Ollama Re-Ranking Prompt

Das Gewichtungsprofil wird Ollama als Kontext übergeben:

```
Interessen-Profil des Nutzers:
- Gaming (Effektiv-Gewicht 7, Kategorie: Gaming)
- Penetration Test (Effektiv-Gewicht 5, Kategorie: IT/Security)
```

Ollama bewertet jeden Artikel mit einem Score 0–10, der dann als neues `weight` gesetzt wird.

### 3.5 Admin-UI Sortierung

`_get_tags()` sortiert nach **raw `weight` DESC** — nicht nach `effective_weight`.
`_get_active_tags_for_search()` sortiert nach **`effective_weight` DESC**.

---

## 4. Langzeit-Tags: Kumulativer Durchschnitt

Beim Speichern in `long_term_tags` wird `weight` als laufender Durchschnitt akkumuliert:

```sql
weight = ROUND(
    (long_term_tags.weight * long_term_tags.mention_count + new_weight)
    / (long_term_tags.mention_count + 1)
)
```

`category_weight` wird dagegen **direkt überschrieben** (nicht gemittelt).

Beispiel-Entwicklung eines Tags über 5 Tage (alle Tage weight=8, ein "schwacher" Tag weight=2):

| Tag | Neues weight | mention_count | Gespeichertes weight |
|---|---|---|---|
| 1 | 8 | 1 | 8 |
| 2 | 8 | 2 | 8 |
| 3 | 8 | 3 | 8 |
| 4 | 8 | 4 | 8 |
| 5 | 2 | 5 | **7** (ein schlechter Tag drückt kaum) |
| 20 | 2 | 20 | **7** (nach 15 weiteren Tagen: ~7.4) |

Nach vielen Erwähnungen ist der Wert eingefroren — neue Beobachtungen haben kaum Einfluss mehr.

---

## 5. Bekannte Schwachstellen

### Schwachstelle 1: Formel komprimiert den Mittelbereich

Die Multiplikationsformel `tag × cat / 10` drückt alle mittleren Werte stark nach unten. Tags die in der Mitte der Skala liegen kollabieren auf effektiv 2–4, obwohl der Nutzer sie als "mittel-wichtig" einschätzt.

**Folge:** Im Alltag haben fast alle Auto-Tags `effective_weight` zwischen 2 und 5. Die Differenzierung geht verloren.

**Vorschlag:** Arithmetischer Durchschnitt `round((tag + cat) / 2)` — intuitiver, erhält die Skala.

### Schwachstelle 2: `tag_weight` bedeutet je nach Quelle etwas anderes

| Quelle | Was `tag_weight` bedeutet |
|---|---|
| Auto-Tag (Ollama) | Dominanz *relativ zur Kategorie* |
| Manueller Tag | Absolute Wichtigkeit (Nutzer-Einschätzung) |
| Cluster-Tag | Undefiniert (Ollama ohne Kategorie-Kontext) |

Alle landen in derselben Tabelle. Die unterschiedliche Semantik wird nicht kommuniziert.

**Folge:** Manuelle Tags (category_weight=10 default) sind im Vergleich zu Auto-Tags systematisch bevorzugt.

### Schwachstelle 3: Artikelanzahl ist gewichtsunabhängig

Alle Tags bekommen dieselbe Anzahl Suchergebnisse. Das Gewicht beeinflusst nur die Suchreihenfolge (und damit welche Tags bei Rate-Limits ausfallen), nicht aber den Output.

**Vorschlag:** `max_per_tag` proportional zum Gewicht:
```python
per_tag = max(1, round(base_max * effective_weight / 10))
```

### Schwachstelle 4: Langzeit-Gewicht hat keinen Zeitverfall

Der kumulative Durchschnitt konvergiert mit der Zeit gegen einen festen Wert. Ein Thema das vor 3 Monaten dominant war, ist im Langzeit-Speicher genauso präsent wie heute.

**Vorschlag:** EWMA (exponentiell gewichteter gleitender Durchschnitt):
```
new_weight = round(0.3 × today_weight + 0.7 × stored_weight)
```
Neuere Messungen zählen mehr, ältere Interessen klingen langsam ab.

### Schwachstelle 5: `category_weight` in `long_term_tags` wird überschrieben

Das `weight` in `long_term_tags` wird korrekt gemittelt, `category_weight` dagegen direkt ersetzt. Wenn eine Kategorie an einem Tag ungewöhnlich niedrig bewertet wird, verliert der gesamte Langzeit-Eintrag sofort seinen Kategoriewert.

**Vorschlag:** `category_weight` analog zu `weight` mitteln.

### Schwachstelle 6: UI-Sortierung und Suchsortierung sind inkonsistent

- Admin-UI (`_get_tags()`): sortiert nach **raw `weight` DESC**
- News-Suche (`_get_active_tags_for_search()`): sortiert nach **`effective_weight` DESC**

Ein Tag mit `weight=10, category_weight=1` (effective=1) steht in der UI ganz oben, wird aber in der Suche zuletzt behandelt.

**Vorschlag:** `_get_tags()` nach `effective_weight` sortieren.

---

## 6. Offene Designfragen

**Sollte `tag_weight` in `news_results` ein Snapshot bleiben oder live berechnet werden?**

Aktuell: Snapshot zur Suchzeit. Wenn der Nutzer Tags ändert, bleiben alte Artikel mit alten Gewichten. Das kann zu widersprüchlichen Sortierungen führen wenn nach und nach neue Suchen laufen.

Alternative: Gewicht immer live aus der `tags`-Tabelle berechnen — aber die Tags werden täglich neu generiert und gelöscht, also wäre nach einem Tag das Gewicht für alte News nicht mehr abrufbar.

Mögliche Lösung: `news_results.tag_weight` bei jeder neuen Tag-Generierung für aktuell noch sichtbare News aktualisieren.

---

## 7. Umsetzungs-Backlog

| Priorität | Änderung | Aufwand | Auswirkung |
|---|---|---|---|
| Hoch | Formel → arithmetischer Durchschnitt | Klein (1 Funktion) | Alle Gewichte normalisieren sich |
| Hoch | UI-Sortierung nach effective_weight | Klein (1 ORDER BY) | Konsistenz UI ↔ Suche |
| Mittel | max_per_tag proportional zum Gewicht | Mittel (Suchlogik) | Echter Personalisierungseffekt bei News |
| Mittel | EWMA für long_term_tags | Mittel (SQL-Logik) | Zeitverfall für alte Interessen |
| Niedrig | category_weight in long_term_tags mitteln | Klein (SQL-Logik) | Stabilere Langzeit-Kategorien |
| Niedrig | Cluster-Tags mit Kategorie-Kontext | Groß (Prompt-Änderung) | Konsistentes tag_weight für alle Quellen |

---

*Zuletzt analysiert: 2026-06-05*
