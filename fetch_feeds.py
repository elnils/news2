#!/usr/bin/env python3
"""
Presseschau – RSS Fetch Script
Läuft via GitHub Actions alle 30 Minuten.
Holt alle Feeds, klassifiziert Artikel, speichert articles.json.
"""

import json
import time
import hashlib
import re
import sys
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from xml.etree import ElementTree as ET
from email.utils import parsedate_to_datetime

# ═══════════════════════════════════════════════════════════════
# FEED-LISTE (55+ Quellen)
# ═══════════════════════════════════════════════════════════════
FEEDS = [
    # ── Deutsch: Allgemein ─────────────────────────────────────
    ("https://www.tagesschau.de/infoservices/alle-meldungen-100~rss2.xml", "Tagesschau",    "de-general"),
    ("https://www.spiegel.de/schlagzeilen/rss/0,5291,,00.xml",             "Spiegel",       "de-general"),
    ("https://newsfeed.zeit.de/index",                                     "Zeit",          "de-general"),
    ("https://rss.sueddeutsche.de/rss/Alles",                              "SZ",            "de-general"),
    ("https://www.deutschlandfunk.de/politikportal-100.rss",               "DLF",           "de-general"),
    ("https://www.faz.net/rss/aktuell/",                                   "FAZ",           "de-general"),
    ("https://www.welt.de/feeds/latest.rss",                               "Welt",          "de-general"),
    ("https://www.stern.de/feed/standard/all/",                            "Stern",         "de-general"),
    ("https://www.focus.de/rssfeeds/neueste-artikel_id_2438.xml",          "Focus",         "de-general"),
    ("https://www.ndr.de/nachrichten/info/podcast4906.xml",                "NDR",           "de-general"),
    ("https://www.mdr.de/nachrichten/index-rss.xml",                      "MDR",           "de-general"),
    # ── Deutsch: Technik / IT ──────────────────────────────────
    ("https://www.heise.de/newsticker/heise.rdf",                          "Heise",         "de-tech"),
    ("https://www.golem.de/rss",                                           "Golem",         "de-tech"),
    ("https://t3n.de/rss.xml",                                             "t3n",           "de-tech"),
    ("https://www.ip-insider.de/rss/news.xml",                             "IP-Insider",    "de-tech"),
    ("https://www.computerwoche.de/feed/news.rss",                         "CompWoche",     "de-tech"),
    ("https://feeds.feedburner.com/netzwelt",                              "Netzwelt",      "de-tech"),
    # ── Deutsch: Wirtschaft ────────────────────────────────────
    ("https://www.handelsblatt.com/contentexport/feed/finanzen",           "HB Finanzen",   "de-economy"),
    ("https://www.handelsblatt.com/contentexport/feed/technologie",        "HB Technik",    "de-economy"),
    ("https://www.wiwo.de/contentexport/feed/rss/schlagzeilen",            "WiWo",          "de-economy"),
    ("https://www.finanznachrichten.de/rss-aktien-nachrichten",            "FinanzN.",      "de-economy"),
    ("https://www.finanzen.net/rss/news",                                  "Finanzen.net",  "de-economy"),
    ("https://www.manager-magazin.de/static/rss/alle.xml",                "Manager Mag.",  "de-economy"),
    # ── Deutsch: Politik / EU ─────────────────────────────────
    ("https://www.europarl.europa.eu/rss/doc/press-releases/de.xml",      "EU-Parl.",      "de-politics"),
    ("https://www.bundesregierung.de/breg-de/aktuelles/rss-nachrichten-bundesregierung-462382.xml", "Bundesreg.", "de-politics"),
    # ── Englisch: General ─────────────────────────────────────
    ("https://feeds.bbci.co.uk/news/rss.xml",                             "BBC News",      "en-general"),
    ("https://feeds.bbci.co.uk/news/world/rss.xml",                       "BBC World",     "en-general"),
    ("https://feeds.bbci.co.uk/news/technology/rss.xml",                  "BBC Tech",      "en-general"),
    ("https://rss.nytimes.com/services/xml/rss/nyt/World.xml",            "NYT World",     "en-general"),
    ("https://feeds.reuters.com/reuters/topNews",                          "Reuters",       "en-general"),
    ("https://feeds.reuters.com/Reuters/worldNews",                       "Reuters World", "en-general"),
    ("https://feeds.reuters.com/reuters/technologyNews",                   "Reuters Tech",  "en-general"),
    ("https://apnews.com/index.rss",                                      "AP News",       "en-general"),
    ("https://www.theguardian.com/world/rss",                             "Guardian",      "en-general"),
    ("https://www.dw.com/rss/rss.xml",                                    "DW English",    "en-general"),
    # ── Englisch: Tech / KI ───────────────────────────────────
    ("https://techcrunch.com/feed/",                                       "TechCrunch",    "en-tech"),
    ("https://www.theverge.com/rss/index.xml",                            "The Verge",     "en-tech"),
    ("https://www.wired.com/feed/rss",                                    "Wired",         "en-tech"),
    ("https://feeds.arstechnica.com/arstechnica/index",                   "Ars Technica",  "en-tech"),
    ("https://www.technologyreview.com/feed/",                            "MIT Tech Rev.", "en-tech"),
    ("https://venturebeat.com/feed/",                                     "VentureBeat",   "en-tech"),
    ("https://www.zdnet.com/news/rss.xml",                                "ZDNet",         "en-tech"),
    ("https://www.lightreading.com/rss/",                                 "Light Reading", "en-tech"),
    ("https://spectrum.ieee.org/feeds/feed.rss",                          "IEEE Spectrum", "en-tech"),
    ("https://9to5mac.com/feed/",                                         "9to5Mac",       "en-tech"),
    ("https://www.engadget.com/rss.xml",                                  "Engadget",      "en-tech"),
    # ── Englisch: KI speziell ─────────────────────────────────
    ("https://openai.com/news/rss.xml",                                   "OpenAI Blog",   "en-ai"),
    ("https://deepmind.google/blog/rss/",                                 "DeepMind",      "en-ai"),
    # ── Englisch: Verteidigung ────────────────────────────────
    ("https://www.defensenews.com/arc/outboundfeeds/rss/",                "Defense News",  "en-defense"),
    ("https://breakingdefense.com/feed/",                                 "Breaking Def.", "en-defense"),
    ("https://taskandpurpose.com/feed/",                                  "Task&Purpose",  "en-defense"),
    ("https://www.c4isrnet.com/arc/outboundfeeds/rss/",                   "C4ISRNET",      "en-defense"),
    # ── Englisch: Wirtschaft / Finanz ─────────────────────────
    ("https://www.ft.com/?format=rss",                                    "FT",            "en-economy"),
    ("https://feeds.bloomberg.com/markets/news.rss",                      "Bloomberg",     "en-economy"),
    ("https://www.economist.com/finance-and-economics/rss.xml",           "Economist",     "en-economy"),
    # ── Englisch: Wissenschaft ────────────────────────────────
    ("https://www.nature.com/nature.rss",                                 "Nature",        "en-science"),
    ("https://www.sciencedaily.com/rss/all.xml",                          "ScienceDaily",  "en-science"),
    ("https://phys.org/rss-feed/",                                        "Phys.org",      "en-science"),
    ("https://www.newscientist.com/feed/home/",                           "New Scientist", "en-science"),
]

# ═══════════════════════════════════════════════════════════════
# TOPIC SCORING (Mindest-Score 2, keine False Positives)
# ═══════════════════════════════════════════════════════════════
TOPIC_RULES = {
    "ki": {
        "score": [
            (3, ["künstliche intelligenz","artificial intelligence","machine learning","deep learning",
                 "large language model","llm","neural network","neuronales netz","generative ai","generativer"]),
            (2, ["chatgpt","gpt-4","gpt-5","gpt-","openai","anthropic","gemini","claude ai","mistral",
                 "llama ","stable diffusion","midjourney","dall-e","sora","copilot ai","ki-modell",
                 "ki-system","ki-gestützt","ai model","foundation model","sprachmodell","bildgenerierung"]),
            (1, ["algorithmus","transformer","roboter","automat","textgenerierung","deepfake",
                 "nvidia ai","gpu cluster","inference","training data"]),
        ], "min": 2
    },
    "tech": {
        "score": [
            (3, ["software","hardware","betriebssystem","prozessor","chip","halbleiter",
                 "quantencomputer","open source","linux","cloud computing","rechenzentrum","data center"]),
            (2, ["smartphone","5g","glasfaser","breitband","router","netzwerk","server","api ",
                 "entwickler","developer","github","programmier","microsoft","apple ","google ",
                 "samsung","intel","amd","arm chip","app "]),
            (1, ["digital","internet","it-","tech-","update","patch","release","browser","usb","wifi","streaming"]),
        ], "min": 2
    },
    "verteidigung": {
        "score": [
            (3, ["bundeswehr","nato","militär","streitkräfte","verteidigungsministerium","pentagon",
                 "defense department","armed forces","kriegsführung","truppenstationierung"]),
            (2, ["rüstung","waffe","drohne ","kampfjet","panzer ","fregatte","u-boot","rakete ",
                 "munition","sicherheitspolitik","geheimdienst","bnd ","cia ","nsa ","mossad"]),
            (1, ["soldat","gefecht","front","offensive","krieg ","war ","conflict","nato-","waffenlieferung"]),
        ], "min": 2
    },
    "politik": {
        "score": [
            (3, ["bundestag","bundesregierung","koalition","kanzler","wahlkampf","parlamentswahl",
                 "kongress ","senat ","nationalversammlung"]),
            (2, ["cdu","spd","fdp","grüne","afd","bsw","minister ","partei","wahl ","election",
                 "gesetzentwurf","gesetz ","reform ","abstimmung","eu-kommission","europaparlament"]),
            (1, ["merz","scholz","baerbock","habeck","trump","biden","harris","macron","putin","xi jinping"]),
        ], "min": 2
    },
    "wirtschaft": {
        "score": [
            (3, ["dax","bip","bruttoinlandsprodukt","konjunktur","rezession","inflation rate",
                 "federal reserve","europäische zentralbank","ezb","handelsdefizit"]),
            (2, ["unternehmen","konzern","aktie","börse","export","import","tarif","haushalt",
                 "schulden","insolvenz","fusion","übernahme","quartalsergebnis","gewinn","verlust"]),
            (1, ["wirtschaft","economy","market","markt","industrie","handel","finanz","bank","kredit","zins"]),
        ], "min": 2
    },
    "sicherheit": {
        "score": [
            (3, ["cyberangriff","ransomware","malware","zero-day","exploit","datenleck",
                 "data breach","phishing-kampagne","ddos-angriff","sicherheitslücke"]),
            (2, ["cybersecurity","cyber security","bsi ","hacker","hack ","vulnerability",
                 "vpn","firewall","verschlüsselung","datenschutz","dsgvo"]),
            (1, ["it-sicherheit","schutz","passwort","authentifizierung","backdoor"]),
        ], "min": 2
    },
    "energie": {
        "score": [
            (3, ["energiewende","erneuerbare energien","solar","windkraft","offshore-wind",
                 "atomkraft","kernkraft","lng terminal","stromerzeugung"]),
            (2, ["strompreis","gaspreise","ölpreis","wasserstoff","photovoltaik",
                 "kraftwerk","co2-emissionen","klimaschutz","dekarbonisierung"]),
            (1, ["energie","strom","gas ","öl ","pipeline","batterie","speicher"]),
        ], "min": 2
    },
    "medizin": {
        "score": [
            (3, ["klinische studie","impfstoff","mrna","onkologie","pharmaunternehmen",
                 "zulassung","fda ","ema ","krebstherapie"]),
            (2, ["krebs","tumor","therapie","behandlung","antibiotikum","virus ","impfung",
                 "krankenhaus","klinik","arznei","medikament","diagnose"]),
            (1, ["gesundheit","medizin","patient","chirurgie","pflege"]),
        ], "min": 2
    },
    "raumfahrt": {
        "score": [
            (3, ["nasa","esa ","spacex","raumfahrt","weltraummission","internationale raumstation",
                 "iss ","mondlandung","marsmission"]),
            (2, ["rakete ","satellit","orbit","ariane","starship","mond ","asteroid","raumsonde"]),
            (1, ["weltraum","space ","launch","galaxie","komet"]),
        ], "min": 2
    },
    "finanzen": {
        "score": [
            (3, ["dax","nasdaq","dow jones","s&p 500","etf ","börsengang","ipo ","bitcoin","ethereum","krypto"]),
            (2, ["aktie","fonds","dividende","anleihe","zinserhöhung","leitzins","fed ","ezb ",
                 "hedge fund","trading","währung"]),
            (1, ["rendite","depot","investition","portfolio","kredit"]),
        ], "min": 2
    },
    "startup": {
        "score": [
            (3, ["venture capital","series a","series b","series c","ipo ","unicorn","startup-finanzierung"]),
            (2, ["start-up","startup","gründer","finanzierungsrunde","inkubator","accelerator","pitch"]),
            (1, ["innovation","disruption","gründung","skalierung"]),
        ], "min": 2
    },
    "eu": {
        "score": [
            (3, ["europäische union","eu-kommission","europaparlament","europäischer rat",
                 "eu-richtlinie","eu-verordnung","eu-gipfel"]),
            (2, ["eu ","brüssel","von der leyen","eurozone","schengen","eu-wahl","eu-mitglied"]),
            (1, ["europä","eu-","brüsseler"]),
        ], "min": 2
    },
    "mobilitaet": {
        "score": [
            (3, ["elektroauto","e-mobilität","autonomes fahren","deutsche bahn","öpnv","verkehrswende"]),
            (2, ["tesla ","volkswagen","bmw ","mercedes ","verkehr","bahn ","zug ","flugzeug","lkw ","wasserstoffauto"]),
            (1, ["mobilität","transport","fahrzeug","antrieb","ladestation"]),
        ], "min": 2
    },
    "wissenschaft": {
        "score": [
            (3, ["peer-reviewed","studie zeigt","forschungsergebnis","quantencomputer",
                 "crispr","genomeditierung","durchbruch"]),
            (2, ["universität","forschung","experiment","physik","biologie","chemie",
                 "astronomie","archäologie","genetik","dna "]),
            (1, ["wissenschaft","wissenschaftler","labor","theorie","entdeckung"]),
        ], "min": 2
    },
    "ukraine": {
        "score": [
            (3, ["ukraine","selensky","kiew","donbas","cherson","saporizhzhia","frontlinie"]),
            (2, ["ukraine-krieg","russisch","russland","kreml","nato-ostflanke","waffenlieferung"]),
            (1, ["osteuropa","gegenoffensive","waffenstillstand"]),
        ], "min": 2
    },
    "nahost": {
        "score": [
            (3, ["israel","gaza","hamas","palästina","westjordanland","libanon","hisbollah"]),
            (2, ["nahost","iran","netanjahu","jemen","houthi","syrien","irak "]),
            (1, ["naher osten","middle east","arabisch"]),
        ], "min": 2
    },
    "asien": {
        "score": [
            (3, ["china","taiwan","hongkong","beijing","xi jinping","south china sea","taiwan-straße"]),
            (2, ["chinesisch","japan","südkorea","indien","nordkorea","asean","halbleiter china"]),
            (1, ["asien","pazifik","indo-pazifik"]),
        ], "min": 2
    },
    "usa": {
        "score": [
            (3, ["vereinigte staaten","washington dc","weißes haus","supreme court","us-kongress"]),
            (2, ["trump","harris","biden","demokraten","republikaner","us-","usa ","federal reserve"]),
            (1, ["american","washington","federal"]),
        ], "min": 2
    },
    "netzpolitik": {
        "score": [
            (3, ["netzausbau","glasfaserausbau","5g-ausbau","netzneutralität","internetfreiheit"]),
            (2, ["netzpolitik","digitale infrastruktur","breitband","starlink","kabel deutschland"]),
            (1, ["internet","netz","digital"]),
        ], "min": 2
    },
}


def score_article(text: str) -> dict:
    """Gibt Dict {topic: score} zurück, nur wenn Score >= min."""
    text = text.lower()
    result = {}
    for topic, rule in TOPIC_RULES.items():
        s = 0
        for pts, kws in rule["score"]:
            for kw in kws:
                if kw in text:
                    s += pts
                    break  # pro Gruppe nur einmal zählen
        if s >= rule["min"]:
            result[topic] = s
    return result


def clean_html(text: str) -> str:
    """HTML-Tags entfernen, Whitespace normalisieren."""
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&#\d+;', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def parse_date(raw: str) -> str:
    """Parst RSS-Datum zu ISO-String, fallback leer."""
    if not raw:
        return ""
    try:
        dt = parsedate_to_datetime(raw)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        pass
    try:
        # ISO 8601 (Atom)
        dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return ""


# XML-Namespaces
NS = {
    'content': 'http://purl.org/rss/1.0/modules/content/',
    'dc':      'http://purl.org/dc/elements/1.1/',
    'atom':    'http://www.w3.org/2005/Atom',
}

def get_text(el, *tags) -> str:
    """Sucht erstes vorhandenes Kind-Element und gibt textContent zurück."""
    for tag in tags:
        child = el.find(tag) or el.find(f'{{http://www.w3.org/2005/Atom}}{tag}')
        if child is not None and child.text:
            return child.text.strip()
    return ""


def parse_feed(xml_bytes: bytes, source: str) -> list:
    articles = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        # Manchmal ist der XML-Header kaputt – versuche mit utf-8-sig
        try:
            text = xml_bytes.decode('utf-8', errors='replace')
            text = re.sub(r'[^\x09\x0A\x0D\x20-\uD7FF\uE000-\uFFFD]+', '', text)
            root = ET.fromstring(text)
        except Exception:
            return []

    # RSS 2.0 / RDF
    items = root.findall('.//item')
    # Atom
    if not items:
        items = root.findall('.//{http://www.w3.org/2005/Atom}entry')

    for item in items:
        def g(tag, ns_tag=None):
            el = item.find(tag)
            if el is None and ns_tag:
                el = item.find(ns_tag)
            return el.text.strip() if el is not None and el.text else ''

        title = g('title') or g('{http://www.w3.org/2005/Atom}title')
        if not title:
            continue

        # Link – RSS hat <link>, Atom hat href-Attribut
        link = g('link')
        if not link:
            link_el = item.find('{http://www.w3.org/2005/Atom}link')
            if link_el is not None:
                link = link_el.get('href', '')

        # Beschreibung
        desc_raw = (
            g('{http://purl.org/rss/1.0/modules/content/}encoded') or
            g('description') or
            g('{http://www.w3.org/2005/Atom}summary') or
            g('{http://www.w3.org/2005/Atom}content') or ''
        )
        desc = clean_html(desc_raw)[:500]  # max 500 Zeichen

        # Datum
        pub_raw = (
            g('pubDate') or
            g('{http://www.w3.org/2005/Atom}published') or
            g('{http://www.w3.org/2005/Atom}updated') or
            g('{http://purl.org/dc/elements/1.1/}date') or ''
        )
        pub_iso = parse_date(pub_raw)

        # Klassifizierung
        text_for_scoring = (title + ' ' + desc).lower()
        scored = score_article(text_for_scoring)
        topics = sorted(scored, key=lambda t: -scored[t])  # beste zuerst

        # Unique ID
        uid = hashlib.md5((source + title + link).encode()).hexdigest()[:12]

        articles.append({
            "id":     uid,
            "source": source,
            "title":  title,
            "link":   link.strip(),
            "desc":   desc,
            "date":   pub_iso,
            "topics": topics,
        })

    return articles


def fetch_url(url: str, timeout: int = 15) -> bytes | None:
    headers = {
        'User-Agent': 'Mozilla/5.0 (compatible; Presseschau-Bot/1.0; +https://github.com)',
        'Accept': 'application/rss+xml, application/xml, text/xml, */*',
    }
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except (URLError, HTTPError, Exception) as e:
        print(f"  ✗ {url[:60]}: {e}", file=sys.stderr)
        return None


def main():
    print(f"[{datetime.now().isoformat()}] Starte Feed-Fetch ({len(FEEDS)} Quellen)")
    all_articles = []
    ok = 0
    fail = 0

    for url, name, cat in FEEDS:
        print(f"  Lade {name}...", end=' ', flush=True)
        data = fetch_url(url)
        if not data:
            print("FEHLER")
            fail += 1
            continue
        arts = parse_feed(data, name)
        print(f"{len(arts)} Artikel")
        all_articles.extend(arts)
        ok += 1
        time.sleep(0.3)  # kurze Pause, höfliches Crawlen

    # Deduplizieren
    seen = set()
    deduped = []
    for a in all_articles:
        if a["id"] not in seen:
            seen.add(a["id"])
            deduped.append(a)

    # Sortieren: neueste zuerst (Artikel ohne Datum ans Ende)
    deduped.sort(key=lambda a: a["date"] or "0000", reverse=True)

    # Auf 1500 Artikel begrenzen (reicht für ~3 Tage, ~800KB JSON)
    deduped = deduped[:1500]

    output = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "feeds_ok": ok,
        "feeds_fail": fail,
        "count": len(deduped),
        "articles": deduped,
    }

    with open("articles.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✓ {ok}/{len(FEEDS)} Feeds geladen · {len(deduped)} Artikel → articles.json")
    print(f"✗ {fail} Feeds fehlgeschlagen")


if __name__ == "__main__":
    main()
