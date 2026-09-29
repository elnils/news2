#!/usr/bin/env python3
"""
dokumente_lesen.py  –  schreibt dokumente_text.json

WORUM GEHT ES IN DEM DOKUMENT?
Gerichtsfeeds melden oft nur "IV ZR 259/25, Entscheidung vom 09.09.2026",
Bundestags-Feeds nur Art und Nummer einer Drucksache. Der Inhalt steckt im
verlinkten PDF oder auf der Entscheidungsseite. Dieses Skript

  1. lädt die Seite bzw. das PDF (Größe begrenzt) und liest den Text aus –
     HTML direkt, PDF mit pypdf; verweist eine Seite auf ein PDF, wird es
     nachgeladen,
  2. sucht Leitsatz und Tenor (bei Urteilen) bzw. den Anfang (bei Drucksachen),
  3. lässt die KI daraus einen sachlichen Titel, ein bis zwei Sätze "worum
     es geht" und das Ergebnis schreiben – nur aus dem Text, nichts dazu.

Gespeichert wird je Dokument nur ein Auszug (höchstens 1.500 Zeichen) plus
Leitsatz, Tenor und Kurzfassung – keine PDFs, keine Volltexte. Das hält das
Repo klein. Urteile und Drucksachen sind amtliche Werke (§ 5 UrhG).

Je Lauf DOK_PRO_LAUF Dokumente (Standard 20), einmal gelesen bleibt im
Zwischenspeicher; Einträge älter als 90 Tage fallen heraus.
"""

import io
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from html import unescape
from urllib.parse import urljoin
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

import ki_zusammenfassungen as ki

OUT = "dokumente_text.json"
PRO_LAUF = int(os.environ.get("DOK_PRO_LAUF", "20"))
BUDGET_SEC = int(os.environ.get("DOK_BUDGET_SEC", "300"))
MAX_BYTES = int(os.environ.get("DOK_MAX_MB", "12")) * 1024 * 1024
UA = {"User-Agent": "Mozilla/5.0 (compatible; Presseschau/1.0; +https://github.com)"}

GERICHT_RE = re.compile(r"bundesgerichtshof|bundesverfassungsgericht|bundesfinanzhof|bundesarbeitsgericht|"
                        r"bundessozialgericht|bverwg|bundesverwaltungsgericht|curia\.europa|juris\.|"
                        r"\bBGH\b|\bBVerfG\b|\bBFH\b|\bBAG\b|\bBSG\b|\bEuGH\b", re.I)
# Titel ohne Inhalt: nur Aktenzeichen, "Entscheidung vom …", "Entscheidung Detail"
LEERER_TITEL = re.compile(r"^(?:[\w\s./-]{3,25},\s*)?(entscheidung|urteil|beschluss)\s*(vom|detail|\d)|"
                          r"^[IVXLC\d]{1,5}\s?[A-Z]{1,4}\s?\d{1,4}/\d{2}\b|^entscheidung detail$|^drucksache\s", re.I)


def holen(url):
    """Seite oder PDF holen. Gibt (Bytes, Inhaltstyp) zurück."""
    with urlopen(Request(url, headers=UA), timeout=40) as r:
        typ = r.headers.get("Content-Type", "")
        daten = r.read(MAX_BYTES + 1)
    if len(daten) > MAX_BYTES:
        raise ValueError("zu groß")
    return daten, typ


def pdf_text(daten):
    try:
        from pypdf import PdfReader
    except ImportError:
        print("  pypdf fehlt – PDFs werden übersprungen (pip install pypdf)")
        return "", 0
    try:
        r = PdfReader(io.BytesIO(daten))
        seiten = len(r.pages)
        teile = []
        for s in r.pages[:25]:                      # die ersten 25 Seiten reichen für Leitsatz und Kern
            try:
                teile.append(s.extract_text() or "")
            except Exception:
                continue
        return "\n".join(teile), seiten
    except Exception as e:
        print(f"  PDF nicht lesbar ({type(e).__name__})")
        return "", 0


def html_text(html):
    html = re.sub(r"(?is)<(script|style|nav|header|footer|noscript)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</h\d>|</li>|</tr>", "\n", html)
    t = unescape(re.sub(r"<[^>]+>", " ", html))
    t = re.sub(r"[ \t\xa0]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip()


def text_fuer(url):
    """Text eines Dokuments – bei HTML-Seiten mit PDF-Verweis das PDF."""
    daten, typ = holen(url)
    if "pdf" in typ.lower() or daten[:5] == b"%PDF-":
        return pdf_text(daten)
    html = daten.decode("utf-8", "replace")
    # Verweis auf das eigentliche PDF (BGH: "…&Blank=1.pdf", sonst ".pdf")
    m = re.search(r'href="([^"]+(?:Blank=1\.pdf|\.pdf)(?:[?#][^"]*)?)"', html, re.I)
    text = html_text(html)
    if m and len(text) < 3000:
        pdf_url = urljoin(url, unescape(m.group(1)))
        try:
            d2, t2 = holen(pdf_url)
            if "pdf" in t2.lower() or d2[:5] == b"%PDF-":
                return pdf_text(d2)
        except (HTTPError, URLError, ValueError, TimeoutError, OSError):
            pass
    return text, 0


def teile_finden(text, gericht):
    """Leitsatz, Tenor und Auszug aus dem Text."""
    t = re.sub(r"[ \t]+", " ", text)
    leitsatz = tenor = ""
    if gericht:
        m = (re.search(r"Nachschlagewerk\s*:\s*(?:ja|nein)(?:\s*BGH\w*\s*:\s*(?:ja|nein))*\s*(.{40,2000}?)\s*BGH,\s*(?:Urteil|Beschluss)\s+vom", t, re.S)
             or re.search(r"Leits(?:atz|ätze)\s*:?\s*(.{40,2000}?)(?=\n\s*(?:Tenor|Gründe|Tatbestand|Entscheidungsgründe|Orientierungssatz|Im Namen des Volkes)|\Z)", t, re.S | re.I))
        if m:
            leitsatz = re.sub(r"\s+", " ", m.group(1)).strip()[:1200]
        m = (re.search(r"(?:für Recht erkannt|beschlossen|entschieden)\s*:\s*(.{20,1200}?)(?=Von Rechts wegen|Gründe\s*:|Tatbestand\s*:|Entscheidungsgründe|Gründe\n)", t, re.S)
             or re.search(r"\nTenor\s*:?\s*(.{20,1200}?)(?=\n\s*(?:Gründe|Tatbestand|Entscheidungsgründe))", t, re.S))
        if m:
            tenor = re.sub(r"\s+", " ", m.group(1)).strip()[:900]
        start = max(t.find("Gründe"), t.find("Tatbestand"), 0)
        auszug = t[start:start + 4000]
    else:
        auszug = t[:4000]
    return leitsatz, tenor, re.sub(r"\s+", " ", auszug).strip()


def frage(anbieter, auftrag):
    system = ("Du fasst amtliche Dokumente (Gerichtsentscheidungen, Bundestagsdrucksachen) für eine "
              "Presseschau zusammen: sachlich, knapp, auf Deutsch, nur aus dem Text, ohne Wertung.")
    for _ in range(6):
        if not anbieter:
            return None
        name, url, key, modell = anbieter[0]
        koerper = json.dumps({"model": modell, "temperature": 0.1, "max_tokens": 350,
                              "messages": [{"role": "system", "content": system},
                                           {"role": "user", "content": auftrag}]}).encode("utf-8")
        kopf = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        if name == "openrouter":
            kopf["HTTP-Referer"] = "https://presseschau.example"
            kopf["X-Title"] = "Presseschau"
        try:
            with ki.ki_urlopen(name, url, koerper, kopf, 60) as r:
                j = json.loads(r.read().decode("utf-8", "replace"))
            return (j.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
        except HTTPError as e:
            print(f"  ---  {name} HTTP {e.code} – nächster Anbieter")
            anbieter.pop(0)
        except (URLError, ValueError, KeyError, TimeoutError, OSError) as e:
            print(f"  ---  {name} {type(e).__name__} – nächster Anbieter")
            anbieter.pop(0)
    return None


def einordnen(anbieter, d, leitsatz, tenor, auszug, gericht):
    art = "Gerichtsentscheidung" if gericht else "Bundestags-/Behördendokument"
    auftrag = (f"Art: {art}\nBisheriger Titel: {d.get('title','')}\n"
               + (f"Leitsatz: {leitsatz}\n" if leitsatz else "")
               + (f"Tenor: {tenor}\n" if tenor else "")
               + f"Textauszug:\n{auszug[:3000]}\n\n"
               "Antworte NUR mit JSON: {\"titel\": \"worum es geht, höchstens 90 Zeichen, ohne Aktenzeichen\", "
               "\"kurz\": \"ein bis zwei Sätze: was wurde entschieden bzw. gefragt/geantwortet/vorgeschlagen\", "
               "\"ergebnis\": \"bei Urteilen knapp (etwa 'Revision zurückgewiesen'), sonst leer\"}")
    antwort = frage(anbieter, auftrag) or ""
    m = re.search(r"\{.*\}", antwort, re.S)
    if not m:
        return {}
    try:
        j = json.loads(m.group(0))
    except ValueError:
        return {}
    return {k: str(j.get(k, "")).strip()[:400] for k in ("titel", "kurz", "ergebnis") if j.get(k)}


def kandidaten():
    """Gerichtsmeldungen und Dokumente mit Link, zuerst die ohne sprechenden Titel."""
    out = []
    for datei, feld in (("articles.json", "articles"), ("documents.json", "documents")):
        try:
            daten = json.load(open(datei, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for d in daten.get(feld) or []:
            link = d.get("link") or ""
            if not link.startswith("http"):
                continue
            gericht = bool(GERICHT_RE.search((d.get("source") or "") + " " + link))
            if datei == "articles.json" and not gericht:
                continue
            leer = bool(LEERER_TITEL.search((d.get("title") or "").strip())) or len(d.get("title") or "") < 30
            pdf = bool(re.search(r"\.pdf($|[?#])", link, re.I))
            if not (gericht or pdf):
                continue
            out.append((0 if leer else 1, d.get("date", ""), d, gericht))
    out.sort(key=lambda x: (x[0], "" if not x[1] else "~" + x[1]), reverse=False)
    # zuerst die leeren Titel, darin die neuesten
    leer = sorted([x for x in out if x[0] == 0], key=lambda x: x[1], reverse=True)
    rest = sorted([x for x in out if x[0] == 1], key=lambda x: x[1], reverse=True)
    return [(d, g) for _, _, d, g in leer + rest]


def main():
    start = time.time()
    try:
        bestand = json.load(open(OUT, encoding="utf-8"))
    except (OSError, ValueError):
        bestand = {"dokumente": {}}
    doks = bestand.setdefault("dokumente", {})
    anbieter = [list(a) for a in ki.ANBIETER]
    neu = 0
    for d, gericht in kandidaten():
        if neu >= PRO_LAUF or time.time() - start > BUDGET_SEC:
            break
        schluessel = d.get("id") or d.get("link")
        if schluessel in doks:
            continue
        try:
            text, seiten = text_fuer(d["link"])
        except (HTTPError, URLError, ValueError, TimeoutError, OSError) as e:
            doks[schluessel] = {"link": d["link"], "fehler": type(e).__name__, "stand": datetime.now(timezone.utc).isoformat()}
            continue
        if len(text) < 200:
            doks[schluessel] = {"link": d["link"], "fehler": "kein Text", "stand": datetime.now(timezone.utc).isoformat()}
            continue
        leitsatz, tenor, auszug = teile_finden(text, gericht)
        eintrag = {"link": d["link"], "titel_alt": d.get("title", ""), "gericht": gericht, "seiten": seiten,
                   "leitsatz": leitsatz, "tenor": tenor, "auszug": auszug[:1500],
                   "stand": datetime.now(timezone.utc).isoformat()}
        if anbieter:
            eintrag.update(einordnen(anbieter, d, leitsatz, tenor, auszug, gericht))
        doks[schluessel] = eintrag
        neu += 1
        print(f"  + {(eintrag.get('titel') or d.get('title',''))[:70]}  ({seiten or '–'} S., "
              f"{'Leitsatz' if leitsatz else 'ohne Leitsatz'})")
    grenze = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    bestand["dokumente"] = {k: v for k, v in doks.items() if v.get("stand", "") >= grenze}
    bestand["stand"] = datetime.now(timezone.utc).isoformat()
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(bestand, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUT)
    print(f"→ {OUT}: {neu} neu gelesen, {len(bestand['dokumente'])} im Bestand")
    return 0


if __name__ == "__main__":
    sys.exit(main())
