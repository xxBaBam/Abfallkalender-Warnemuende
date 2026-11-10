# Abfallkalender Rostock-Warnemünde

Ein Kalender-Abo (ICS) für einen Haushalt in **Rostock-Warnemünde**, das sich selbst aktualisiert:
schwarze Tonne, Biotonne, gelbe Tonne und blaue Tonne. Einmal abonnieren, fertig.

**Seite zum Abonnieren (iPhone, Android, PC):** https://xxbabam.github.io/Abfallkalender-Warnemuende/

| | Abo-Adresse |
|---|---|
| Ohne Erinnerung (für Google Kalender) | `https://xxbabam.github.io/Abfallkalender-Warnemuende/abfall.ics` |
| Mit Erinnerung am Vorabend, 18 Uhr | `https://xxbabam.github.io/Abfallkalender-Warnemuende/abfall-erinnerung.ics` |

## Adresse (privat)

Die Stadtentsorgung vergibt die Termine pro Straße und Hausnummer. Die Adresse steht **nicht** im Repo, sondern im
GitHub-Secret **`ABFALL_ADRESSE`** (Settings → Secrets and variables → Actions) im Format `Straße|Hausnummer|PLZ`.
Secrets sind nicht öffentlich sichtbar und erscheinen auch nicht in den Protokollen. Auch der daraus ermittelte
Adress-Schlüssel der Stadtentsorgung wird weder gespeichert noch ausgegeben.

## Wie es funktioniert

- Der Workflow **„Kalender aktualisieren“** läuft täglich (und per Hand über Actions → „Run workflow“).
- Er ermittelt über das Formular auf stadtentsorgung-rostock.de den Adress-Schlüssel und lädt die Termine des
  **aktuellen Jahres** plus die **nächsten ca. 4 Wochen**. Die Stadtentsorgung veröffentlicht das Folgejahr nicht im
  Voraus; über den 4-Wochen-Ausblick reicht der Kalender aber immer über den Jahreswechsel, und ab Januar kommt das
  neue Jahr automatisch.
- Feste Termin-IDs (keine Doppelten, Änderungen werden ersetzt). Vergangene Termine werden nie gelöscht.
- Committet wird nur bei Änderungen, plus einmal im Monat ein kleiner Eintrag in `state.json`, damit GitHub den
  Zeitplan nicht nach 60 Tagen ohne Aktivität abschaltet.
- Scheitert der Abruf, bleibt der alte Kalender erhalten und der Workflow schlägt fehl (GitHub schickt eine E-Mail).

## Einstellungen

In `config.json`: `tonnen` (Liste aus `"rm"` Restmüll, `"lvp"` gelb, `"pap"` blau, `"bio"`), `erinnerung_uhrzeit`,
optional `namen` (z. B. `{"bio": "Braune Tonne"}`). Die `id` nie ändern, sonst gibt es doppelte Termine.
Adresse ändern: Secret `ABFALL_ADRESSE` neu setzen.

## Kosten

Keine: Für öffentliche Repos sind GitHub Actions (Standard-Runner) und GitHub Pages kostenlos und zählen nicht gegen
das Freikontingent.
