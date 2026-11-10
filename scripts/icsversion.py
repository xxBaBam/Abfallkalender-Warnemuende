"""Versionsstempel fuer ICS-Termine.

Kalender-Apps (vor allem Google Kalender) uebernehmen eine Aenderung an einem abonnierten Termin nur, wenn sich
dessen Versionsangaben aendern (SEQUENCE / DTSTAMP / LAST-MODIFIED). Dieses Modul merkt sich je Termin einen
Fingerabdruck des Inhalts. Aendert sich der Inhalt, wird SEQUENCE hochgezaehlt und der Aenderungszeitpunkt gesetzt.
Unveraenderte Termine behalten ihre Werte, damit die Datei nur bei echten Aenderungen anders wird.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path


def stamp(ics_file: Path, versions_file: Path, key_prefix: str = "") -> int:
    """Setzt SEQUENCE, DTSTAMP und LAST-MODIFIED in ics_file. Gibt die Zahl geaenderter Termine zurueck."""
    versions = json.loads(versions_file.read_text(encoding="utf-8")) if versions_file.exists() else {}
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    text = ics_file.read_bytes().decode("utf-8")  # Zeilenenden (\r\n) unveraendert lassen
    changed = 0
    seen = set()

    def fix(m: re.Match) -> str:
        nonlocal changed
        block = m.group(0)
        # alte Versionsangaben entfernen, Fingerabdruck ueber den reinen Inhalt bilden
        body = re.sub(r"\r\n(?:DTSTAMP|LAST-MODIFIED|SEQUENCE)[:;][^\r\n]*", "", block)
        uid = re.search(r"\r\nUID:([^\r\n]*)", body).group(1)
        key = key_prefix + uid
        seen.add(key)
        digest = hashlib.sha1(body.encode("utf-8")).hexdigest()[:16]
        v = versions.get(key)
        if v is None or v["hash"] != digest:
            v = {"hash": digest, "seq": (v["seq"] + 1) if v else 1, "geaendert": now}
            versions[key] = v
            changed += 1
        extra = f"\r\nDTSTAMP:{v['geaendert']}\r\nLAST-MODIFIED:{v['geaendert']}\r\nSEQUENCE:{v['seq']}"
        return body.replace(f"\r\nUID:{uid}", f"\r\nUID:{uid}{extra}", 1)

    text = re.sub(r"BEGIN:VEVENT.*?END:VEVENT", fix, text, flags=re.S)
    ics_file.write_text(text, encoding="utf-8", newline="")
    # Eintraege fuer Termine dieser Datei, die es nicht mehr gibt, aufraeumen
    for k in [k for k in versions if k.startswith(key_prefix) and k not in seen]:
        del versions[k]
    versions_file.parent.mkdir(parents=True, exist_ok=True)
    versions_file.write_text(json.dumps(versions, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return changed
