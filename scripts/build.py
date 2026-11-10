"""Baut automatisch aktualisierte Abfallkalender (ICS-Abos) fuer mehrere Adressen.

Quellen:
- "landkreis_rostock":      Eigenbetrieb Abfallwirtschaft Landkreis Rostock (abfall-lro.de)
- "stadtentsorgung_rostock": Stadtentsorgung Rostock (Hansestadt Rostock, z. B. Warnemuende)

Fuer jeden Kalender in config.json:
- holt das aktuelle und (sofern schon veroeffentlicht) das naechste Jahr
- fuehrt alles zu einer ICS zusammen, stabile UIDs, keine Duplikate
- schreibt docs/<ordner>/abfall.ics (ohne Erinnerung),
  docs/<ordner>/abfall-erinnerung.ics (mit Erinnerung am Vorabend) und docs/<ordner>/index.html
- bei Fehlern bleiben die alten Dateien dieses Kalenders unangetastet; das Skript endet mit Fehler

Nur Python-Standardbibliothek.
"""
from __future__ import annotations

import datetime as dt
import html
import http.cookiejar
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
DOCS = ROOT / "docs"
STATE = ROOT / "state.json"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# Erkennung der Abfallart anhand des Titels der Quelle (Reihenfolge wichtig: "braun" vor "bio")
TYPES = [
    ("schwarz", "Schwarze Tonne", ("schwarz", "restabfall", "restmüll", "hausmüll")),
    ("bio", "Biotonne", ("braun",)),
    ("gruen", "Grüne Tonne", ("grün", "gruen", "bio")),
    ("gelb", "Gelbe Tonne", ("gelb", "verpackung", "wertstoff")),
    ("blau", "Blaue Tonne", ("blau", "papier", "ppk")),
]

Events = dict  # {(datum, art): titel}


class FetchError(Exception):
    pass


_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def http_get(url: str, data: dict | None = None, headers: dict | None = None) -> str:
    h = {"User-Agent": UA, **(headers or {})}
    body = urllib.parse.urlencode(data, doseq=True).encode() if data is not None else None
    last = None
    for _ in range(3):
        try:
            with _opener.open(urllib.request.Request(url, data=body, headers=h), timeout=60) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise FetchError(f"404 {url}") from e
            last = e
        except Exception as e:  # noqa: BLE001
            last = e
    raise FetchError(f"{url}: {last}")


# ---------------------------------------------------------------- ICS lesen

def parse_ics(text: str) -> list[tuple[dt.date, str]]:
    if "BEGIN:VCALENDAR" not in text:
        raise FetchError("Antwort ist keine ICS-Datei")
    events, cur = [], None
    for line in re.sub(r"\r?\n[ \t]", "", text).splitlines():
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT" and cur is not None:
            if "DTSTART" in cur:
                events.append((cur["DTSTART"], cur.get("SUMMARY", "")))
            cur = None
        elif cur is not None and ":" in line:
            key, val = line.split(":", 1)
            name = key.split(";", 1)[0].upper()
            if name == "DTSTART":
                cur["DTSTART"] = dt.date(int(val[0:4]), int(val[4:6]), int(val[6:8]))
            elif name == "SUMMARY":
                cur["SUMMARY"] = val.replace("\\,", ",").replace("\\;", ";")
    return events


def classify(summary: str, cal: dict) -> tuple[str, str] | None:
    # Eigene Titel (aus frueher geschriebenen Dateien) zuerst exakt zuordnen
    for key, title, _ in TYPES:
        own = cal.get("namen", {}).get(key, title)
        if summary.strip() == own:
            return key, own
    s = summary.lower()
    for key, title, words in TYPES:
        if any(w in s for w in words):
            return key, cal.get("namen", {}).get(key, title)
    return None


def to_events(raw: str, cal: dict) -> Events:
    result = {}
    for date, summary in parse_ics(raw):
        c = classify(summary, cal)
        if c is None:
            print(f"::warning::[{cal['id']}] Unbekannter Termin '{summary}' am {date} – uebersprungen")
            continue
        result[(date, c[0])] = c[1]
    return result


# ---------------------------------------------------------------- Quelle: Landkreis Rostock

LRO = "https://www.abfall-lro.de"


def lro_scan_codes(cal: dict, st: dict) -> None:
    """Ortscode (PDF-Dateiname) je Jahr von der Uebersichtsseite lesen.

    Links: /default-wAssets/docs/abfuhrtermine/2026/pdf/CODE.pdf">Tessin</a>
    Dahinter kann ein Zusatz wie " (Teterow)" stehen – der muss exakt passen.
    """
    url = LRO + "/de/abfuhrtermine/"
    page = http_get(url)
    ort = cal["ort"].strip().lower()
    found = False
    for year, code, text, suffix in re.findall(
        r'<a[^>]+href="[^"]*/abfuhrtermine/(\d{4})/pdf/([^"/]+)\.pdf"[^>]*>(.*?)</a>([^<]*)', page, re.S | re.I
    ):
        label = " ".join(re.sub(r"<[^>]+>", " ", text + " " + suffix).split()).lower()
        if label == ort:
            found = True
            if st["codes"].get(year) != code:
                print(f"[{cal['id']}] Ortscode {year}: {code}")
            st["codes"][year] = code
    if not found:
        raise FetchError(f"Ort '{cal['ort']}' nicht auf {url} gefunden – hat sich die Seite geaendert?")


def lro_fetch(cal: dict, st: dict, today: dt.date) -> Events:
    st.setdefault("codes", {})
    lro_scan_codes(cal, st)
    events: Events = {}
    for year in (today.year, today.year + 1):
        codes = st["codes"]
        code = codes.get(str(year)) or codes[max(codes)]
        params = {
            "letters": code,
            "year": year,
            "black": cal["schwarz"],
            "green": cal["gruen"],
            "yellow": "y" if cal["gelb"] else "",
            "blue": "y" if cal["blau"] else "",
        }
        if cal.get("schwarz_saison"):
            params["bsaison"] = "y"
        if cal.get("gruen_saison"):
            params["gsaison"] = "y"
        try:
            raw = http_get(LRO + "/default-wGlobal/wGlobal/abfuhrtermine/ical.php?" + urllib.parse.urlencode(params))
        except FetchError as e:
            if year == today.year:
                raise
            print(f"[{cal['id']}] {year}: noch nicht verfuegbar ({e})")
            continue
        if "BEGIN:VCALENDAR" not in raw:
            # z. B. "Directory not found!" solange das Jahr noch nicht veroeffentlicht ist
            msg = f"keine ICS fuer {year}: {raw.strip()[:80]!r}"
            if year == today.year:
                raise FetchError(msg)
            print(f"[{cal['id']}] {year}: noch nicht verfuegbar ({msg})")
            continue
        events.update({k: v for k, v in to_events(raw, cal).items() if k[0].year == year})
    return events


# ---------------------------------------------------------------- Quelle: Stadtentsorgung Rostock

SR = "https://www.stadtentsorgung-rostock.de"
SR_FORM = SR + "/service/ekalend_form_data/1216"


def sr_address(cal: dict) -> tuple[str, str, str]:
    """Adresse aus dem GitHub-Secret ABFALL_ADRESSE ("Strasse|Hausnummer|PLZ") – sie steht bewusst nicht im Repo."""
    raw = os.environ.get("ABFALL_ADRESSE", "").strip()
    if raw:
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) == 3 and all(parts):
            return parts[0], parts[1], parts[2]
        raise FetchError("Secret ABFALL_ADRESSE hat das falsche Format (erwartet: Strasse|Hausnummer|PLZ)")
    if cal.get("strasse"):
        return cal["strasse"], cal["hausnummer"], cal["plz"]
    raise FetchError("Secret ABFALL_ADRESSE fehlt (Settings -> Secrets and variables -> Actions)")


def sr_lookup_key(cal: dict) -> str:
    """Adress-Schluessel ueber das Formular der Stadtentsorgung ermitteln (wird nie gespeichert oder ausgegeben)."""
    strasse, nr, plz = sr_address(cal)
    ajax = {"X-Requested-With": "XMLHttpRequest", "Referer": SR + "/service/ekalend/1216"}
    try:
        http_get(SR_FORM + "/", {}, ajax)  # Sitzung starten
        page = http_get(SR_FORM + "/SearchByAddress", {
            "Input[address][street]": strasse,
            "Input[address][no]": nr,
            "Input[address][zip]": plz,
            "Input[address][legit]": "on",
            "Input[address][chipno]": "",
            "Input[address][key]": "",
        }, ajax)
    except FetchError:
        raise FetchError("Formular der Stadtentsorgung nicht erreichbar") from None
    m = re.search(r'id="address-key"[^>]*value="([^"]+)"', page) or re.search(r"ekalend_ical/\(key\)/([^/'\"]+)", page)
    if not m:
        raise FetchError("Adresse bei der Stadtentsorgung nicht gefunden – hat sich die Seite geaendert?")
    return html.unescape(m.group(1))


def sr_fetch(cal: dict, st: dict, today: dt.date) -> Events:
    st.pop("key", None)  # frueher gespeichert – nicht mehr im Repo ablegen
    key = sr_lookup_key(cal)
    base = f"{SR}/service/ekalend_ical/(key)/{key}"
    types = "|".join(cal.get("tonnen", ["rm", "lvp", "pap", "bio"]))
    # "(period)/year" = aktuelles Kalenderjahr. Ohne period liefert die Stadtentsorgung die
    # naechsten ca. 4 Wochen – das reicht ueber den Jahreswechsel und schliesst so die Luecke,
    # bis im neuen Jahr "year" das neue Jahr liefert. Ein Folgejahr im Voraus gibt es dort nicht.
    try:
        events = to_events(http_get(f"{base}/(period)/year/(type)/{types}"), cal)
        events.update(to_events(http_get(f"{base}/(type)/{types}"), cal))
    except FetchError:
        # Fehlermeldung ohne URL, damit der Adress-Schluessel nicht im oeffentlichen Protokoll landet
        raise FetchError("Termine der Stadtentsorgung nicht abrufbar") from None
    return events


SOURCES = {"landkreis_rostock": lro_fetch, "stadtentsorgung_rostock": sr_fetch}


# ---------------------------------------------------------------- Ausgabe

def esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;")


def render(cal: dict, events: Events, reminder: str | None) -> str:
    stamp = "20260101T000000Z"  # fest, damit sich die Datei nur bei echten Aenderungen aendert
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//abfallkalender-{cal['id']}//DE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{esc(cal['kalendername'])}",
        "X-WR-TIMEZONE:Europe/Berlin",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
    ]
    for (date, key), title in sorted(events.items()):
        lines += [
            "BEGIN:VEVENT",
            f"UID:{date:%Y%m%d}-{key}@abfallkalender-{cal['id']}",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{date:%Y%m%d}",
            f"DTEND;VALUE=DATE:{date + dt.timedelta(days=1):%Y%m%d}",
            f"SUMMARY:{esc(title)}",
            "TRANSP:TRANSPARENT",
        ]
        if reminder:
            h, m = (int(x) for x in reminder.split(":"))
            # Ganztagstermin beginnt 00:00 -> Vorabend hh:mm = -(24h - hh:mm)
            mins = 24 * 60 - (h * 60 + m)
            lines += [
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                f"DESCRIPTION:Morgen: {esc(title)} rausstellen",
                f"TRIGGER:-PT{mins // 60}H{mins % 60}M",
                "END:VALARM",
            ]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


PAGE = """<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{titel}</title>
<style>
  :root {{ --bg:#fff; --fg:#1d1d1f; --muted:#666; --box:#f3f4f6; --btn:#0a7d3b; --btn2:#3a3a3c; --g:#1a73e8; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg:#111; --fg:#eee; --muted:#aaa; --box:#222; --btn:#1f9d55; --btn2:#555; --g:#4c8df6; }} }}
  body {{ background:var(--bg); color:var(--fg); font:17px/1.5 system-ui,-apple-system,sans-serif; max-width:36rem; margin:1.5rem auto; padding:0 16px; }}
  h1 {{ font-size:1.6rem; margin-bottom:.25rem; }}
  .btn {{ display:block; text-align:center; text-decoration:none; color:#fff; background:var(--btn); padding:1rem; border-radius:12px; font-weight:600; margin:.75rem 0; }}
  .btn.alt {{ background:var(--btn2); }}
  .btn.g {{ background:var(--g); }}
  .box {{ background:var(--box); padding:.75rem 1rem; border-radius:12px; margin:1rem 0; }}
  code {{ word-break:break-all; font-size:.9rem; }}
  small {{ color:var(--muted); }}
  ol {{ padding-left:1.25rem; }} li {{ margin:.3rem 0; }}
</style>
</head>
<body>
<h1>{titel}</h1>
<p>{beschreibung}
Neue Termine und Änderungen kommen von selbst dazu, auch für die nächsten Jahre.</p>

<h2>iPhone / iPad</h2>
<a class="btn" id="ios-alarm" href="abfall-erinnerung.ics">Abonnieren – mit Erinnerung am Vorabend ({uhr} Uhr)</a>
<a class="btn alt" id="ios-plain" href="abfall.ics">Abonnieren – ohne Erinnerung</a>
<p><small>Danach fragt das iPhone „Kalenderabo hinzufügen?“ → <strong>Abonnieren</strong> → <strong>Hinzufügen</strong>.</small></p>
<div class="box">
<strong>iPhone und iPad (mehrere Apple-Geräte)?</strong><br>
Über die Knöpfe landet das Abo oft nur „Auf meinem iPhone“ und erscheint nicht auf dem iPad.
Entweder auf jedem Gerät einmal den Knopf antippen – oder einmal über iCloud einrichten:
<ol>
  <li>Kalender-App → unten <strong>„Kalender“</strong> → <strong>„Kalender hinzufügen“</strong> → <strong>„Kalenderabo hinzufügen“</strong>.</li>
  <li>Diese Adresse eingeben: <code class="alarm-url"></code></li>
  <li><strong>„Abonnieren“</strong> → bei <strong>„Account“</strong> <strong>iCloud</strong> wählen (nicht „Auf meinem iPhone“) → <strong>„Hinzufügen“</strong>.</li>
</ol>
<small>Die Auswahl „Account“ gibt es ab ca. iOS 17. Bei iCloud-Abos kann die Erinnerung wegfallen; dann in den Einstellungen unter Kalender → „Standardhinweiszeiten“ für ganztägige Ereignisse eine Erinnerung einstellen.
Ein altes Abo entfernen: Kalender-App → „Kalender“ → ⓘ beim Abfallkalender → „Abo löschen“.</small>
</div>

<h2>Android / Google Kalender</h2>
<a class="btn g" id="google" href="https://calendar.google.com/">In Google Kalender abonnieren</a>
<ol>
  <li>Knopf antippen, mit dem Google-Konto anmelden und bei „Kalender hinzufügen?“ auf <strong>Hinzufügen</strong> tippen.</li>
  <li>Öffnet sich nur die Kalender-App ohne Nachfrage: Knopf lange drücken → „In neuem Tab öffnen“ (Chrome), dort im Menü ⋮ <strong>„Desktopwebsite“</strong> anhaken und nochmal versuchen. Am Computer klappt es immer.</li>
  <li>Nach ein paar Minuten (manchmal Stunden) erscheint der Kalender „{kalendername}“ in der Kalender-App. Falls nicht: Kalender-App → Menü ☰ → Einstellungen → „{kalendername}“ → <strong>Synchronisieren</strong> einschalten.</li>
  <li>Erinnerung (optional): Google übernimmt keine Erinnerungen aus Abos. Am Computer unter calendar.google.com → Einstellungen → „{kalendername}“ → Benachrichtigungen für ganztägige Termine, z. B. „1 Tag vorher um {uhr}“.</li>
</ol>

<h2>Andere Programme (Outlook, Thunderbird …)</h2>
<p>Adresse mit Erinnerung:</p>
<div class="box"><code id="alarm"></code></div>
<p>Adresse ohne Erinnerung:</p>
<div class="box"><code id="plain"></code></div>

<p><small>Inoffizieller Dienst, keine Gewähr. Maßgeblich sind die Angaben von {quelle}.</small></p>
<script>
  var base = location.href.replace(/[?#].*$/, "").replace(/[^/]*$/, "");
  var plain = base + "abfall.ics", alarm = base + "abfall-erinnerung.ics";
  var webcal = function (u) {{ return u.replace(/^https?:/, "webcal:"); }};
  document.getElementById("plain").textContent = plain;
  document.getElementById("alarm").textContent = alarm;
  document.querySelectorAll(".alarm-url").forEach(function (e) {{ e.textContent = alarm; }});
  document.getElementById("ios-alarm").href = webcal(alarm);
  document.getElementById("ios-plain").href = webcal(plain);
  document.getElementById("google").href =
    "https://calendar.google.com/calendar/render?cid=" + encodeURIComponent(webcal(plain));
</script>
</body>
</html>
"""

QUELLEN_NAME = {
    "landkreis_rostock": "abfall-lro.de (Landkreis Rostock)",
    "stadtentsorgung_rostock": "stadtentsorgung-rostock.de",
}


def write_calendar(cal: dict, events: Events, today: dt.date) -> None:
    out = DOCS / cal["ordner"]
    out.mkdir(parents=True, exist_ok=True)
    # Vergangene Termine aus der bisherigen Datei immer behalten (werden nie geloescht)
    old = out / "abfall.ics"
    if old.exists():
        for date, summary in parse_ics(old.read_text(encoding="utf-8")):
            c = classify(summary, cal)
            if c and date < today:
                events.setdefault((date, c[0]), c[1])
    (out / "abfall.ics").write_text(render(cal, events, None), encoding="utf-8", newline="")
    (out / "abfall-erinnerung.ics").write_text(
        render(cal, events, CONFIG["erinnerung_uhrzeit"]), encoding="utf-8", newline="")
    (out / "index.html").write_text(PAGE.format(
        titel=html.escape(cal["titel"]),
        beschreibung=html.escape(cal["beschreibung"]),
        kalendername=html.escape(cal["kalendername"]),
        uhr=CONFIG["erinnerung_uhrzeit"],
        quelle=QUELLEN_NAME[cal["quelle"]],
    ), encoding="utf-8")


def load_state() -> dict:
    st = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    if "codes" in st:  # altes Format (nur Tessin) uebernehmen
        st = {"tessin": {"codes": st.pop("codes")}, **st}
    return st


def main() -> int:
    today = dt.date.fromisoformat(os.environ["TODAY"]) if os.environ.get("TODAY") else dt.date.today()
    state = load_state()
    rc = 0
    for cal in CONFIG["kalender"]:
        cid = cal["id"]
        st = state.setdefault(cid, {})
        try:
            events = SOURCES[cal["quelle"]](cal, st, today)
        except FetchError as e:
            print(f"::error::[{cid}] Abruf fehlgeschlagen: {e} – bisheriger Kalender bleibt erhalten")
            rc = max(rc, 1)
            continue
        per_year = {}
        for d, _ in events:
            per_year[d.year] = per_year.get(d.year, 0) + 1
        print(f"[{cid}] Termine je Jahr: {dict(sorted(per_year.items()))}")
        if not any(d >= today for d, _ in events):
            print(f"::error::[{cid}] Keine kommenden Termine erhalten – bisheriger Kalender bleibt erhalten")
            rc = max(rc, 1)
            continue
        write_calendar(cal, events, today)
        print(f"[{cid}] geschrieben: {len(events)} Termine")
        # Hinweis nur fuer Quellen, die das Folgejahr vorab veroeffentlichen (Landkreis)
        if cal["quelle"] == "landkreis_rostock" and not per_year.get(today.year + 1) \
                and today.month == 12 and today.day >= 15 and today.weekday() == 0:  # nur montags mailen
            print(f"::error::[{cid}] Fuer {today.year + 1} sind noch keine Termine veroeffentlicht. "
                  "Der Kalender gilt weiter bis Jahresende; der Workflow prueft jede Woche erneut.")
            rc = max(rc, 2)

    # Einmal pro Monat aendert sich diese Zeile -> monatlicher Commit. Sonst schaltet GitHub
    # den woechentlichen Zeitplan nach 60 Tagen ohne Aktivitaet im Repo ab.
    if rc != 1:
        state["letzte_erfolgreiche_pruefung"] = f"{today:%Y-%m}"
    STATE.write_text(json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return rc


if __name__ == "__main__":
    sys.exit(main())
