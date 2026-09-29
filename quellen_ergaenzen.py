#!/usr/bin/env python3
"""
quellen_ergaenzen.py  –  läuft nach fetch_news.py, vor entdoppeln.py

PFLICHTQUELLEN
Für jede Region gibt es Häuser, ohne die eine Presseschau lückenhaft ist:
AP, Reuters, New York Times, Washington Post für die USA; Tagesschau, FAZ,
SZ, Spiegel für Deutschland; Politico Europe, FT, Guardian für Europa.

Liefert eines davon in den letzten 24 Stunden weniger als MIN_JE_HAUS
Meldungen – weil der Feed verstummt ist, die Adresse sich geändert hat
oder er nie eingetragen war –, holt dieses Skript die neuesten Meldungen
über die Google-News-Suche je Website ("site:apnews.com") nach und hängt sie
an die passende Datei an. Die Meldungen tragen "haus" (die Domain) und
"ersatz": "google-news", damit sie im Frontend als Ersatz erkennbar sind.

So bleibt die Quellenlage stabil, ohne dass jemand Feeds nachpflegen muss.
entdoppeln.py führt danach Doppelungen mit den regulären Feeds zusammen.

Einstellbar: QE_MIN_JE_HAUS (Standard 3), QE_MAX_JE_HAUS (15),
QE_BUDGET_SEC (180).
"""

import hashlib
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

try:
    from entdoppeln import domain as _domain
except Exception:                                   # pragma: no cover
    _domain = lambda url: ""

MIN_JE_HAUS = int(os.environ.get("QE_MIN_JE_HAUS", "3"))
MAX_JE_HAUS = int(os.environ.get("QE_MAX_JE_HAUS", "15"))
BUDGET_SEC = int(os.environ.get("QE_BUDGET_SEC", "180"))
UA = {"User-Agent": "Mozilla/5.0 (Presseschau)"}

# Datei → [(Name, Domain, Sprache)]
PFLICHT = {
    "us_articles.json": [
        ("AP News", "apnews.com", "en"), ("Reuters", "reuters.com", "en"),
        ("The New York Times", "nytimes.com", "en"), ("The Washington Post", "washingtonpost.com", "en"),
        ("The Wall Street Journal", "wsj.com", "en"), ("Politico", "politico.com", "en"),
        ("The Hill", "thehill.com", "en"), ("Axios", "axios.com", "en"), ("NPR", "npr.org", "en"),
        ("CNN", "cnn.com", "en"), ("Bloomberg", "bloomberg.com", "en"), ("PBS News", "pbs.org", "en"),
    ],
    "articles.json": [
        ("Tagesschau", "tagesschau.de", "de"), ("Spiegel", "spiegel.de", "de"), ("Zeit", "zeit.de", "de"),
        ("FAZ", "faz.net", "de"), ("Süddeutsche Zeitung", "sueddeutsche.de", "de"),
        ("Handelsblatt", "handelsblatt.com", "de"), ("WELT", "welt.de", "de"),
        ("Deutschlandfunk", "deutschlandfunk.de", "de"), ("ZDF heute", "zdfheute.de", "de"),
        ("Tagesspiegel", "tagesspiegel.de", "de"), ("Reuters", "reuters.com", "en"),
    ],
    "eu_articles.json": [
        ("Politico Europe", "politico.eu", "en"), ("Euractiv", "euractiv.com", "en"),
        ("Financial Times", "ft.com", "en"), ("The Guardian", "theguardian.com", "en"),
        ("BBC News", "bbc.co.uk", "en"), ("Le Monde", "lemonde.fr", "fr"),
    ],
}
SPRACHE = {"de": ("de", "DE", "DE:de"), "en": ("en-US", "US", "US:en"), "fr": ("fr", "FR", "FR:fr")}


def zeit(a):
    try:
        return datetime.fromisoformat(str(a.get("date", "")).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def haus(a):
    return a.get("haus") or _domain(a.get("link", "")) or ""


def nachholen(name, dom, sprache, bekannt):
    hl, gl, ceid = SPRACHE.get(sprache, SPRACHE["en"])
    url = "https://news.google.com/rss/search?" + urlencode({"q": f"site:{dom} when:1d", "hl": hl, "gl": gl, "ceid": ceid})
    try:
        with urlopen(Request(url, headers=UA), timeout=20) as r:
            wurzel = ET.fromstring(r.read())
    except (HTTPError, URLError, ET.ParseError, TimeoutError, OSError) as e:
        print(f"  {name}: Suche nicht erreichbar ({type(e).__name__})")
        return []
    out = []
    for it in wurzel.iter("item"):
        titel = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if not titel or not link:
            continue
        titel = re.sub(r"\s+[-–|]\s+[^-–|]{2,60}$", "", titel)           # " - The New York Times"
        schluessel = re.sub(r"[^a-z0-9]+", " ", titel.lower()).strip()
        if schluessel in bekannt:
            continue
        try:
            datum = parsedate_to_datetime(it.findtext("pubDate") or "").astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError):
            continue                                                     # ohne Datum lieber nicht
        bekannt.add(schluessel)
        out.append({"id": "gn-" + hashlib.sha1(link.encode()).hexdigest()[:12], "title": titel, "source": name,
                    "link": link, "date": datum, "desc": "", "haus": dom, "ersatz": "google-news"})
        if len(out) >= MAX_JE_HAUS:
            break
    return out


def main():
    start = time.time()
    jetzt = time.time()
    gesamt = 0
    for datei, liste in PFLICHT.items():
        try:
            with open(datei, encoding="utf-8") as fh:
                daten = json.load(fh)
        except (OSError, ValueError):
            print(f"  {datei}: nicht lesbar – übersprungen")
            continue
        arts = daten.get("articles") or []
        zaehler, bekannt = {}, set()
        for a in arts:
            bekannt.add(re.sub(r"[^a-z0-9]+", " ", (a.get("title") or "").lower()).strip())
            if jetzt - zeit(a) <= 24 * 3600:
                h = haus(a)
                zaehler[h] = zaehler.get(h, 0) + 1
        neu = []
        for name, dom, sprache in liste:
            if time.time() - start > BUDGET_SEC:
                print("  Zeitbudget erreicht – Rest im nächsten Lauf")
                break
            n = zaehler.get(dom, 0)
            if n >= MIN_JE_HAUS:
                continue
            geholt = nachholen(name, dom, sprache, bekannt)
            print(f"  {datei}: {name} hatte {n} Meldungen in 24 Std. – {len(geholt)} über die Suche ergänzt")
            neu += geholt
            time.sleep(0.8)
        if neu:
            daten["articles"] = arts + neu
            daten.setdefault("ergaenzt", {})["pflichtquellen"] = len(neu)
            tmp = datei + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(daten, fh, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, datei)
            gesamt += len(neu)
    print(f"→ Pflichtquellen: {gesamt} Meldungen ergänzt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
