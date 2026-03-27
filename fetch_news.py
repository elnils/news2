#!/usr/bin/env python3
"""
Presseschau – Zweite Edition
- Rollendes 7-Tage-Archiv
- 3 Datensets: articles.json + eu_articles.json + bundestag.json + Dokumente
- URLs geprüft und gefixt (März 2026)
"""

import json, time, hashlib, re, sys, os
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from urllib.error import URLError
from xml.etree import ElementTree as ET
from email.utils import parsedate_to_datetime
from collections import Counter

# ═══════════════════════════════════════════════════════════════
# OFFIZIELLE EU-QUELLEN (gehen in eu_articles.json)
# ═══════════════════════════════════════════════════════════════
EU_OFFICIAL_FEEDS = [
    # ── Europäisches Parlament ─────────────────────────────────
    ("https://www.europarl.europa.eu/rss/doc/press-releases/de.xml",            "EP Pressemitt.",       "ep"),
    # EP News/Agenda: 404 → via Google News Proxy
    ("https://news.google.com/rss/search?q=site:europarl.europa.eu&hl=de&gl=DE&ceid=DE:de", "EP News", "ep"),
    # EP Ausschüsse: alle 404 → via Google News
    ("https://news.google.com/rss/search?q=Europaparlament+Ausschuss&hl=de&gl=DE&ceid=DE:de", "EP Ausschüsse", "ep-committee"),

    # ── Europäische Kommission ─────────────────────────────────
    ("https://ec.europa.eu/commission/presscorner/api/rss?language=de",          "EU-Kommission",        "ec"),
    # EU Digital Market + Wettbewerb: 404 → via Google News
    ("https://news.google.com/rss/search?q=EU+Kommission+Digital&hl=de&gl=DE&ceid=DE:de", "EU Digital",  "ec"),

    # ── Rat der EU → 403 → via Google News ────────────────────
    ("https://news.google.com/rss/search?q=site:consilium.europa.eu&hl=de&gl=DE&ceid=DE:de", "EU Rat",   "council"),

    # ── Weitere EU-Institutionen ───────────────────────────────
    ("https://www.ecb.europa.eu/rss/press.html",                                 "EZB",                  "eu-inst"),
    ("https://www.easa.europa.eu/newsroom-and-events/news/rss.xml",              "EASA",                 "eu-inst"),
    ("https://www.eurocontrol.int/rss.xml",                                      "Eurocontrol",          "eu-inst"),
    ("https://www.ombudsman.europa.eu/en/news/rss",                              "EU Ombudsmann",        "eu-inst"),
    # EBA: 404 → via Google News
    ("https://news.google.com/rss/search?q=site:eba.europa.eu&hl=de&gl=DE&ceid=DE:de", "EBA",           "eu-inst"),
    # ENISA: 404 → via Google News
    ("https://news.google.com/rss/search?q=site:enisa.europa.eu&hl=de&gl=DE&ceid=DE:de", "ENISA",       "eu-inst"),
    # ECHA: 403 → via Google News
    ("https://news.google.com/rss/search?q=site:echa.europa.eu&hl=de&gl=DE&ceid=DE:de", "ECHA",         "eu-inst"),
    # EFSA: 404 → via Google News
    ("https://news.google.com/rss/search?q=site:efsa.europa.eu&hl=de&gl=DE&ceid=DE:de", "EFSA",         "eu-inst"),
    # Eurojust: 404 → via Google News
    ("https://news.google.com/rss/search?q=site:eurojust.europa.eu&hl=de&gl=DE&ceid=DE:de", "Eurojust", "eu-inst"),
    # Europol: 404 → via Google News
    ("https://news.google.com/rss/search?q=site:europol.europa.eu&hl=de&gl=DE&ceid=DE:de", "Europol",   "eu-inst"),
    # Frontex: 403 → via Google News
    ("https://news.google.com/rss/search?q=site:frontex.europa.eu&hl=de&gl=DE&ceid=DE:de", "Frontex",   "eu-inst"),
    # EEAS: 404 → via Google News
    ("https://news.google.com/rss/search?q=site:eeas.europa.eu&hl=de&gl=DE&ceid=DE:de", "EU Außendienst", "eu-inst"),

    # ── EU-Amtsblatt & Gesetzgebung ────────────────────────────
    ("https://news.google.com/rss/search?q=site:eur-lex.europa.eu&hl=de&gl=DE&ceid=DE:de", "EUR-Lex OJ-L",  "eurlex"),
    ("https://news.google.com/rss/search?q=EU+Amtsblatt+Verordnung&hl=de&gl=DE&ceid=DE:de", "EUR-Lex OJ-C", "eurlex"),

    # ── Think Tanks & Analyse (EU-fokussiert) ──────────────────
    ("https://www.euractiv.com/feed/",                                           "Euractiv",             "eu-media"),
    ("https://www.politico.eu/feed/",                                            "Politico EU",          "eu-media"),
    ("https://ecfr.eu/feed/",                                                    "ECFR",                 "eu-think"),
    ("https://www.cer.eu/rss.xml",                                               "CER London",           "eu-think"),
    # SWP Berlin: 404 → via Google News
    ("https://news.google.com/rss/search?q=site:swp-berlin.org&hl=de&gl=DE&ceid=DE:de", "SWP Berlin",   "eu-think"),
    # Bruegel: 403 → via Google News
    ("https://news.google.com/rss/search?q=site:bruegel.org&hl=de&gl=DE&ceid=DE:de", "Bruegel",         "eu-think"),
    # Bertelsmann: 404 → via Google News
    ("https://news.google.com/rss/search?q=site:bertelsmann-stiftung.de&hl=de&gl=DE&ceid=DE:de", "Bertelsmann Stiftung", "eu-think"),
]

# ═══════════════════════════════════════════════════════════════
# BUNDESTAG & BUNDESREGIERUNG (gehen in bundestag_articles.json)
# ═══════════════════════════════════════════════════════════════
BUNDESTAG_FEEDS = [
    # ── Bundestag: Allgemein ───────────────────────────────────
    ("https://www.bundestag.de/static/appdata/includes/rss/aktuellethemen.rss",  "BT Aktuelle Themen",   "bt-allg"),
    ("https://www.bundestag.de/static/appdata/includes/rss/pressemitteilungen.rss", "BT Pressemitt.",    "bt-allg"),
    ("https://www.bundestag.de/static/appdata/includes/rss/hib.rss",             "BT hib-Meldungen",    "bt-allg"),
    ("https://www.bundestag.de/static/appdata/includes/rss/wissenschaftlichedienste.rss", "BT Wiss. Dienste", "bt-allg"),
    ("https://www.bundestag.de/static/appdata/includes/rss/drucksachen.rss",     "BT Drucksachen",       "bt-allg"),
    ("https://www.bundestag.de/static/appdata/includes/rss/plenarprotokolle.rss","BT Plenarprotokolle",  "bt-allg"),
    ("https://www.bundestag.de/static/appdata/includes/rss/tagesordnungen.rss",  "BT Tagesordnungen",    "bt-allg"),
    # ── Bundestag: Themen-Feeds ────────────────────────────────
    ("https://www.bundestag.de/static/appdata/includes/rss/arbeitsoziales.rss",  "BT Arbeit & Soziales", "bt-thema"),
    ("https://www.bundestag.de/static/appdata/includes/rss/auswaertiges.rss",    "BT Auswärtiges",       "bt-thema"),
    ("https://www.bundestag.de/static/appdata/includes/rss/familie.rss",         "BT Familie & Bildung", "bt-thema"),
    ("https://www.bundestag.de/static/appdata/includes/rss/digitales.rss",       "BT Digitales",         "bt-thema"),
    ("https://www.bundestag.de/static/appdata/includes/rss/eu.rss",              "BT Europäische Union", "bt-thema"),
    ("https://www.bundestag.de/static/appdata/includes/rss/finanzen.rss",        "BT Finanzen",          "bt-thema"),
    ("https://www.bundestag.de/static/appdata/includes/rss/bildung.rss",         "BT Forschung & Tech",  "bt-thema"),
    ("https://www.bundestag.de/static/appdata/includes/rss/gesundheit.rss",      "BT Gesundheit",        "bt-thema"),
    ("https://www.bundestag.de/static/appdata/includes/rss/haushalt.rss",        "BT Haushalt",          "bt-thema"),
    ("https://www.bundestag.de/static/appdata/includes/rss/inneres.rss",         "BT Inneres",           "bt-thema"),
    ("https://www.bundestag.de/static/appdata/includes/rss/kultur.rss",          "BT Kultur & Medien",   "bt-thema"),
    ("https://www.bundestag.de/static/appdata/includes/rss/landwirtschafternaehrung.rss", "BT Landwirtschaft", "bt-thema"),
    ("https://www.bundestag.de/static/appdata/includes/rss/menschenrechte.rss",  "BT Menschenrechte",    "bt-thema"),
    ("https://www.bundestag.de/static/appdata/includes/rss/recht.rss",           "BT Recht",             "bt-thema"),
    ("https://www.bundestag.de/static/appdata/includes/rss/umwelt.rss",          "BT Umwelt & Klima",    "bt-thema"),
    ("https://www.bundestag.de/static/appdata/includes/rss/verkehr.rss",         "BT Verkehr",           "bt-thema"),
    ("https://www.bundestag.de/static/appdata/includes/rss/verteidigung.rss",    "BT Verteidigung",      "bt-thema"),
    ("https://www.bundestag.de/static/appdata/includes/rss/wirtschaft.rss",      "BT Wirtschaft & Energie", "bt-thema"),
    ("https://www.bundestag.de/static/appdata/includes/rss/entwicklung.rss",     "BT Entwicklung",       "bt-thema"),
    ("https://www.bundestag.de/static/appdata/includes/rss/bauwohnenstadtentwicklungkommunen.rss", "BT Wohnen & Bau", "bt-thema"),
    # ── Bundesregierung ───────────────────────────────────────
    ("https://www.bundesregierung.de/service/rss/breg-de/1151242/feed.xml",      "BReg Kompakt",         "breg"),
    ("https://www.bundesregierung.de/service/rss/breg-de/1151244/feed.xml",      "BReg Pressemitt.",     "breg"),
    ("https://www.bundesregierung.de/service/rss/breg-de/1151246/feed.xml",      "BReg Artikel",         "breg"),
    ("https://www.bundesregierung.de/service/rss/breg-de/2318648/feed.xml",      "BReg Bulletin",        "breg"),
]

# Topic-Scoring für Bundestag-Tab (parlamentarische Themen)
BT_TOPIC_RULES = {
    "plenum":{"score":[(3,["plenum","plenarsitzung","plenarprotokoll","tagesordnung","abstimmung","debatte","bundestagssitzung"]),(2,["bundestag","abgeordnete","fraktion","antrag","anfrage"]),(1,["parlament","sitzung"])],"min":2},
    "gesetzgebung":{"score":[(3,["gesetzentwurf","gesetz","drucksache","lesung","verabschiedet","beschlossen","regelung"]),(2,["reform","richtlinie","verordnung","novelle","änderung","bundesrat"]),(1,["recht","gesetzlich","rechtlich"])],"min":2},
    "haushalt":{"score":[(3,["bundeshaushalt","haushaltsdebatte","schuldenbremse","haushaltsplan","etatberatung"]),(2,["haushalt","finanzen","ausgaben","einnahmen","schulden","investitionen"]),(1,["budget","milliarden","milliarden euro"])],"min":2},
    "verteidigung":{"score":[(3,["bundeswehr","verteidigungshaushalt","rüstung","wehrbeauftragter","sondervermögen"]),(2,["verteidigung","militär","nato","sicherheit","streitkräfte"]),(1,["soldat","mission","auslandseinsatz"])],"min":2},
    "aussenpolitik":{"score":[(3,["auswärtiger ausschuss","außenpolitik","auswärtiges amt","botschaft","staatsbesuch"]),(2,["außenminister","international","diplomatie","ukraine","sanktionen"]),(1,["ausland","bilateral","multilateral"])],"min":2},
    "wirtschaft":{"score":[(3,["wirtschaftsausschuss","konjunktur","industriepolitik","mittelstand","fachkräftemangel"]),(2,["wirtschaft","unternehmen","export","handel","wettbewerb","regulierung"]),(1,["markt","wirtschaftlich","ökonomisch"])],"min":2},
    "soziales":{"score":[(3,["sozialleistungen","bürgergeld","rente","krankenversicherung","pflegereform"]),(2,["sozial","arbeit","beschäftigung","mindestlohn","tarifvertrag"]),(1,["arbeitnehmer","sozialstaat","sozialpolitik"])],"min":2},
    "digitales":{"score":[(3,["digitalisierung","ki-strategie","dateninstitut","digitalministerium","breitbandausbau"]),(2,["digital","ki ","algorithmus","datenschutz","plattform","internet"]),(1,["technologie","innovation","online"])],"min":2},
    "umwelt":{"score":[(3,["klimaschutzgesetz","energiewende","emissionshandel","naturschutzgesetz","co2-preis"]),(2,["klimaschutz","umwelt","erneuerbare","solar","windkraft","co2"]),(1,["klima","energie","nachhaltig"])],"min":2},
    "innenpolitik":{"score":[(3,["innenministerium","verfassungsschutz","innere sicherheit","polizeigesetz","asylpolitik"]),(2,["innenpolitik","migration","sicherheit","bsi","kriminalität"]),(1,["innen","sicherheitsbehörde","polizei"])],"min":2},
    "bundesregierung":{"score":[(3,["kabinett","koalitionsvertrag","regierungserklärung","kanzler","bundesminister"]),(2,["bundesregierung","koalition","regierung","ministerium","beschlossen"]),(1,["regierungshandeln","regierungspolitik"])],"min":2},
}


NEWS_FEEDS = [
    # ── Deutsch: Leitmedien ────────────────────────────────────
    ("https://www.tagesschau.de/infoservices/alle-meldungen-100~rss2.xml",       "Tagesschau",           "de-leit"),
    ("https://www.spiegel.de/schlagzeilen/rss/0,5291,,00.xml",                   "Spiegel",              "de-leit"),
    ("https://newsfeed.zeit.de/index",                                           "Zeit",                 "de-leit"),
    ("https://rss.sueddeutsche.de/rss/Alles",                                    "SZ",                   "de-leit"),
    ("https://www.faz.net/rss/aktuell/",                                         "FAZ",                  "de-leit"),
    ("https://www.welt.de/feeds/latest.rss",                                     "Welt",                 "de-leit"),
    ("https://www.deutschlandfunk.de/politikportal-100.rss",                     "DLF",                  "de-leit"),
    # DLF Wirtschaft: 404 → neue URL
    ("https://www.deutschlandfunk.de/wirtschaft-106.rss",                        "DLF Wirtschaft",       "de-leit"),
    ("https://www.tagesspiegel.de/contentexport/feed/home",                      "Tagesspiegel",         "de-leit"),
    ("https://www.stern.de/feed/standard/all/",                                  "Stern",                "de-leit"),
    ("https://www.ndr.de/nachrichten/info/podcast4906.xml",                      "NDR Info",             "de-leit"),
    ("https://www.mdr.de/nachrichten/index-rss.xml",                             "MDR",                  "de-leit"),
    # BR: 404 → neue URL
    ("https://www.br.de/nachrichten/meldungen/nachrichten-bayerischer-rundfunk100~newsRss.xml", "BR",      "de-leit"),
    # ZDF: 404 → neue URL (ZDF heute)
    ("https://news.google.com/rss/search?q=site:zdf.de+nachrichten&hl=de&gl=DE&ceid=DE:de", "ZDF",       "de-leit"),

    # ── Deutsch: Politik ──────────────────────────────────────
    # Bundesregierung: 404 → neue URL
    ("https://news.google.com/rss/search?q=site:bundesregierung.de&hl=de&gl=DE&ceid=DE:de", "Bundesregierung", "de-pol"),
    # Bundestag: 404 → neue URL
    ("https://news.google.com/rss/search?q=site:bundestag.de&hl=de&gl=DE&ceid=DE:de", "Bundestag",         "de-pol"),
    # BMWK: 404 → via Google News
    ("https://news.google.com/rss/search?q=BMWK+Wirtschaftsministerium&hl=de&gl=DE&ceid=DE:de", "BMWK",  "de-pol"),
    # BMI: 400 → via Google News
    ("https://news.google.com/rss/search?q=Bundesinnenministerium+BMI&hl=de&gl=DE&ceid=DE:de", "BMI",    "de-pol"),

    # ── Deutsch: Wirtschaft ───────────────────────────────────
    ("https://www.handelsblatt.com/contentexport/feed/finanzen",                 "HB Finanzen",          "de-eco"),
    ("https://www.handelsblatt.com/contentexport/feed/technologie",              "HB Technik",           "de-eco"),
    ("https://www.handelsblatt.com/contentexport/feed/politik",                  "HB Politik",           "de-eco"),
    ("https://www.wiwo.de/contentexport/feed/rss/schlagzeilen",                  "WiWo",                 "de-eco"),
    ("https://www.wiwo.de/contentexport/feed/rss/politik",                       "WiWo Politik",         "de-eco"),
    # Manager Magazin: 404 → neue URL
    ("https://news.google.com/rss/search?q=site:manager-magazin.de&hl=de&gl=DE&ceid=DE:de", "Manager Mag.", "de-eco"),
    ("https://www.finanznachrichten.de/rss-aktien-nachrichten",                  "FinanzN.",             "de-eco"),
    # Börse.de: 404 → via Google News
    ("https://news.google.com/rss/search?q=Boerse+Aktien+DAX&hl=de&gl=DE&ceid=DE:de", "Börse.de",          "de-eco"),

    # ── Deutsch: Tech & Digital ───────────────────────────────
    ("https://www.heise.de/newsticker/heise.rdf",                                "Heise",                "de-tech"),
    ("https://www.heise.de/security/news/news-atom.xml",                         "Heise Security",       "de-tech"),
    ("https://www.golem.de/rss",                                                 "Golem",                "de-tech"),
    ("https://t3n.de/rss.xml",                                                   "t3n",                  "de-tech"),
    ("https://www.ip-insider.de/rss/news.xml",                                   "IP-Insider",           "de-tech"),
    # Computerwoche: 404 → neue URL
    ("https://www.computerwoche.de/feed/",                                        "CompWoche",            "de-tech"),
    # CIO: 404 → neue URL
    ("https://www.cio.de/feed/",                                                  "CIO",                  "de-tech"),
    ("https://www.silicon.de/feed",                                              "Silicon.de",           "de-tech"),

    # ── Deutsch: Energie & Industrie ─────────────────────────
    # VDI Nachrichten: 404 → neue URL
    ("https://www.vdi-nachrichten.com/feed/",                                     "VDI Nachrichten",      "de-ind"),

    # ── Englisch: Top-Tier ────────────────────────────────────
    ("https://feeds.bbci.co.uk/news/rss.xml",                                    "BBC News",             "en-top"),
    ("https://feeds.bbci.co.uk/news/world/rss.xml",                              "BBC World",            "en-top"),
    ("https://feeds.bbci.co.uk/news/technology/rss.xml",                         "BBC Tech",             "en-top"),
    ("https://feeds.bbci.co.uk/news/business/rss.xml",                           "BBC Business",         "en-top"),
    ("https://rss.nytimes.com/services/xml/rss/nyt/World.xml",                   "NYT World",            "en-top"),
    ("https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml",              "NYT Tech",             "en-top"),
    # Reuters: offizielle Feeds tot seit 2020, geblockt von GitHub Actions
    # → Google News Proxy als Ersatz
    ("https://news.google.com/rss/search?q=when:24h+allinurl:reuters.com&ceid=US:en&hl=en-US&gl=US", "Reuters Top",      "en-top"),
    ("https://news.google.com/rss/search?q=when:24h+allinurl:reuters.com+world&ceid=US:en&hl=en-US&gl=US", "Reuters World",   "en-top"),
    ("https://news.google.com/rss/search?q=when:24h+allinurl:reuters.com+technology&ceid=US:en&hl=en-US&gl=US", "Reuters Tech",    "en-top"),
    ("https://news.google.com/rss/search?q=when:24h+allinurl:reuters.com+business&ceid=US:en&hl=en-US&gl=US", "Reuters Business", "en-top"),
    # AP News: 401 → via Google News
    ("https://news.google.com/rss/search?q=when:24h+allinurl:apnews.com&ceid=US:en&hl=en-US&gl=US", "AP News", "en-top"),
    ("https://www.theguardian.com/world/rss",                                    "Guardian World",       "en-top"),
    ("https://www.theguardian.com/technology/rss",                               "Guardian Tech",        "en-top"),
    # DW English: 404 → neue URL
    ("https://rss.dw.com/rdf/rss-en-all",                                        "DW English",           "en-top"),
    ("https://www.euractiv.com/feed/",                                           "Euractiv",             "en-top"),
    ("https://www.politico.eu/feed/",                                            "Politico EU",          "en-top"),

    # ── Englisch: Tech & KI ──────────────────────────────────
    ("https://techcrunch.com/feed/",                                             "TechCrunch",           "en-tech"),
    ("https://www.wired.com/feed/rss",                                           "Wired",                "en-tech"),
    ("https://feeds.arstechnica.com/arstechnica/index",                          "Ars Technica",         "en-tech"),
    ("https://www.technologyreview.com/feed/",                                   "MIT Tech Rev.",        "en-tech"),
    ("https://venturebeat.com/feed/",                                            "VentureBeat",          "en-tech"),
    ("https://www.zdnet.com/news/rss.xml",                                       "ZDNet",                "en-tech"),
    # Light Reading: 404 → neue URL
    ("https://news.google.com/rss/search?q=site:lightreading.com&ceid=US:en&hl=en-US&gl=US", "Light Reading", "en-tech"),
    ("https://spectrum.ieee.org/feeds/feed.rss",                                 "IEEE Spectrum",        "en-tech"),
    ("https://openai.com/news/rss.xml",                                          "OpenAI Blog",          "en-ai"),
    # DeepMind: 404 → via Google News
    ("https://news.google.com/rss/search?q=site:deepmind.google&ceid=US:en&hl=en-US&gl=US", "DeepMind Blog", "en-ai"),
    ("https://blogs.microsoft.com/feed/",                                        "Microsoft Blog",       "en-ai"),

    # ── Englisch: Verteidigung ───────────────────────────────
    ("https://www.defensenews.com/arc/outboundfeeds/rss/",                       "Defense News",         "en-def"),
    ("https://breakingdefense.com/feed/",                                        "Breaking Defense",     "en-def"),
    ("https://taskandpurpose.com/feed/",                                         "Task & Purpose",       "en-def"),
    ("https://www.c4isrnet.com/arc/outboundfeeds/rss/",                          "C4ISRNET",             "en-def"),
    ("https://www.defensescoop.com/feed/",                                       "DefenseScoop",         "en-def"),
    ("https://www.militarytimes.com/arc/outboundfeeds/rss/",                     "Military Times",       "en-def"),
    # NATO: 404 → neue URL
    ("https://natowatch.org/news.xml",                                            "NATO News",            "en-def"),

    # ── Englisch: Cyber ──────────────────────────────────────
    ("https://www.bleepingcomputer.com/feed/",                                   "BleepingComp.",        "en-cyber"),
    ("https://krebsonsecurity.com/feed/",                                        "Krebs Security",       "en-cyber"),
    ("https://www.darkreading.com/rss.xml",                                      "Dark Reading",         "en-cyber"),
    ("https://feeds.feedburner.com/TheHackersNews",                              "Hacker News Sec",      "en-cyber"),
    ("https://www.securityweek.com/feed/",                                       "SecurityWeek",         "en-cyber"),
    ("https://www.cisa.gov/uscert/ncas/alerts.xml",                              "CISA Alerts",          "en-cyber"),

    # ── Englisch: Wirtschaft ─────────────────────────────────
    ("https://www.ft.com/?format=rss",                                           "FT",                   "en-eco"),
    ("https://feeds.bloomberg.com/markets/news.rss",                             "Bloomberg Markets",    "en-eco"),
    ("https://feeds.bloomberg.com/technology/news.rss",                          "Bloomberg Tech",       "en-eco"),
    ("https://www.economist.com/finance-and-economics/rss.xml",                  "Economist",            "en-eco"),
    ("https://fortune.com/feed/",                                                "Fortune",              "en-eco"),

    # ── Englisch: Internationale Politik ─────────────────────
    ("https://foreignpolicy.com/feed/",                                          "Foreign Policy",       "en-intl"),
    # Chatham House: 403 → via Google News
    ("https://news.google.com/rss/search?q=site:chathamhouse.org&ceid=US:en&hl=en-US&gl=US", "Chatham House", "en-intl"),
    # RAND Corp: 404 → neue URL
    ("https://news.google.com/rss/search?q=site:rand.org&ceid=US:en&hl=en-US&gl=US", "RAND Corp.",        "en-intl"),
    # IISS: 404 → via Google News
    ("https://news.google.com/rss/search?q=site:iiss.org&ceid=US:en&hl=en-US&gl=US", "IISS",            "en-intl"),

    # ── Englisch: Wissenschaft ───────────────────────────────
    ("https://www.nature.com/nature.rss",                                        "Nature",               "en-sci"),
    ("https://www.technologyreview.com/feed/",                                   "MIT Tech Rev.",        "en-sci"),
    ("https://phys.org/rss-feed/",                                               "Phys.org",             "en-sci"),
]

# ═══════════════════════════════════════════════════════════════
# NOISE-FILTER
# ═══════════════════════════════════════════════════════════════
NOISE_KEYWORDS = [
    "iphone review","ipad review","macbook review","product review","hands-on review",
    "unboxing","specs leaked","pre-order","price drop","best deals","limited edition",
    "prime day","black friday","cyber monday","sale ends","coupon","promo code",
    "game review","game trailer","gameplay","esports","twitch",
    "netflix series","movie review","box office","grammy","oscar","award season",
    "recipe","cooking tips","weight loss","fitness routine","travel guide",
    "hotel review","restaurant review","fashion week","celebrity","kardashian",
    "bundesliga ergebnis","spielbericht","torschütze","aufstellung","transfer gerücht",
]

HIGH_VALUE_KEYWORDS = [
    "gesetzentwurf","gesetzgebung","verordnung","richtlinie","regulierung",
    "Zweite","kabinett","koalitionsvertrag","bundesrat",
    "eu-verordnung","eu-richtlinie","europaparlament","eu-kommission",
    "wirtschaftspolitik","industriepolitik","mittelstand","fachkräfte",
    "standort deutschland","lieferkette","subvention","förderung","handelsabkommen",
    "digitalisierung","digitalstrategie","e-government","digitale souveränität",
    "ki-strategie","ki-verordnung","ai act","digital services act","dsa","dma",
    "verteidigungshaushalt","rüstungsexport","dual use","kritis","kritische infrastruktur",
    "energiesicherheit","versorgungssicherheit","klimaziel","co2-preis","emissionshandel",
]

# ═══════════════════════════════════════════════════════════════
# TOPIC SCORING
# ═══════════════════════════════════════════════════════════════
TOPIC_RULES = {
    "ki":{"score":[(3,["künstliche intelligenz","artificial intelligence","machine learning","deep learning","large language model","llm","generative ai"]),(2,["chatgpt","gpt-","openai","anthropic","gemini","claude ai","mistral","ki-modell","ai act","ki-verordnung","foundation model","sprachmodell"]),(1,["algorithmus","transformer","roboter","deepfake","nvidia ai"])],"min":2},
    "tech":{"score":[(3,["software","hardware","prozessor","chip","halbleiter","quantencomputer","cloud computing","rechenzentrum"]),(2,["5g","glasfaser","breitband","netzwerk","server","digitalisierung","microsoft","apple ","google ","intel","amd","it-infrastruktur"]),(1,["digital","internet","it-","update","release"])],"min":2},
    "verteidigung":{"score":[(3,["bundeswehr","nato","militär","streitkräfte","verteidigungsministerium","pentagon","rüstungsexport"]),(2,["rüstung","waffe","drohne ","kampfjet","panzer ","rakete ","munition","sicherheitspolitik","dual use","kritis"]),(1,["soldat","gefecht","front","krieg ","waffenlieferung"])],"min":2},
    "politik":{"score":[(3,["bundestag","bundesregierung","koalition","kanzler","wahlkampf","gesetzentwurf","kabinett","bundesrat"]),(2,["cdu","spd","fdp","grüne","afd","minister ","partei","wahl ","gesetz ","reform ","abstimmung","regulierung","richtlinie"]),(1,["merz","scholz","trump","putin","macron"])],"min":2},
    "wirtschaft":{"score":[(3,["bip","konjunktur","rezession","inflation","ezb","wirtschaftspolitik","industriepolitik"]),(2,["unternehmen","konzern","export","import","tarif","haushalt","insolvenz","fusion","übernahme","lieferkette"]),(1,["wirtschaft","economy","markt","industrie","handel","bank"])],"min":2},
    "sicherheit":{"score":[(3,["cyberangriff","ransomware","malware","zero-day","exploit","datenleck","kritis"]),(2,["cybersecurity","bsi ","hacker","hack ","sicherheitslücke","verschlüsselung","dsgvo"]),(1,["it-sicherheit","passwort","backdoor"])],"min":2},
    "energie":{"score":[(3,["energiewende","erneuerbare energien","energiesicherheit","atomkraft","kernkraft","lng"]),(2,["solar","windkraft","wasserstoff","strompreis","gaspreise","co2","klimaschutz","kraftwerk"]),(1,["energie","strom","gas ","öl ","pipeline"])],"min":2},
    "eu":{"score":[(3,["europäische union","eu-kommission","europaparlament","eu-richtlinie","eu-verordnung","ai act","dsa","dma"]),(2,["eu ","brüssel","von der leyen","eurozone","eu-gipfel","binnenmarkt","euractiv","politico eu"]),(1,["europä","eu-"])],"min":2},
    "finanzen":{"score":[(3,["dax","nasdaq","börsengang","ipo ","bitcoin","krypto","staatsanleihe","schuldenbremse"]),(2,["aktie","anleihe","zinserhöhung","fed ","ezb ","haushaltskrise","staatsverschuldung"]),(1,["rendite","investition","kredit","währung"])],"min":2},
    "ukraine":{"score":[(3,["ukraine","selensky","kiew","donbas","frontlinie","ukraine-krieg"]),(2,["russland","kreml","nato-ostflanke","waffenlieferung","sanktionen gegen russland"]),(1,["osteuropa","gegenoffensive"])],"min":2},
    "nahost":{"score":[(3,["israel","gaza","hamas","palästina","libanon","hisbollah","iran"]),(2,["nahost","netanjahu","jemen","houthi","syrien"]),(1,["naher osten","middle east"])],"min":2},
    "asien":{"score":[(3,["china","taiwan","hongkong","beijing","xi jinping","south china sea"]),(2,["chinesisch","japan","südkorea","indien","nordkorea","asean"]),(1,["asien","pazifik","indo-pazifik"])],"min":2},
    "usa":{"score":[(3,["vereinigte staaten","washington dc","weißes haus","us-kongress","supreme court"]),(2,["trump","harris","biden","demokraten","republikaner","us-","usa "]),(1,["american","federal"])],"min":2},
    "startup":{"score":[(3,["venture capital","series a","series b","ipo ","unicorn","deeptech"]),(2,["start-up","startup","gründer","finanzierungsrunde","innovation"]),(1,["disruption","skalierung"])],"min":2},
    "wissenschaft":{"score":[(3,["peer-reviewed","forschungsergebnis","quantencomputer","crispr","durchbruch"]),(2,["universität","forschung","physik","biologie","genetik","fraunhofer","max planck"]),(1,["wissenschaft","labor","theorie","entdeckung"])],"min":2},
    "medizin":{"score":[(3,["klinische studie","impfstoff","mrna","onkologie","fda ","ema ","zulassung"]),(2,["krebs","therapie","antibiotikum","virus ","impfung","pharma","medikament"]),(1,["gesundheit","medizin","patient","diagnose"])],"min":2},
    "mobilitaet":{"score":[(3,["elektroauto","e-mobilität","autonomes fahren","verkehrswende","öpnv"]),(2,["tesla ","volkswagen","bmw ","mercedes ","deutsche bahn","wasserstoffauto"]),(1,["mobilität","transport","antrieb","ladestation"])],"min":2},
    "netzpolitik":{"score":[(3,["netzausbau","glasfaserausbau","5g-ausbau","netzneutralität","internetfreiheit"]),(2,["netzpolitik","digitale infrastruktur","breitband","starlink"]),(1,["internet","netz"])],"min":2},
}

EU_TOPIC_RULES = {
    "gesetzgebung":{"score":[(3,["verordnung","richtlinie","gesetzgebungsverfahren","co-decision","trilogue","trilog"]),(2,["gesetzentwurf","legislativpaket","regelung","reform","regulierung"]),(1,["entwurf","vorschlag","konsultation"])],"min":2},
    "digitales":{"score":[(3,["ai act","dsa","dma","data act","digital services act","digital markets act","chips act","ki-verordnung"]),(2,["digitalisierung","digitaler binnenmarkt","daten","plattform","algorithmus","ki ","tech-regulierung"]),(1,["digital","internet","online"])],"min":2},
    "wirtschaft":{"score":[(3,["binnenmarkt","handelsabkommen","wettbewerbsrecht","staatshilfe","subvention","industriepolitik"]),(2,["wirtschaft","wirtschaftspolitik","haushalt","finanzen","euro","ezb"]),(1,["markt","handel","investition"])],"min":2},
    "energie_klima":{"score":[(3,["green deal","fit for 55","emissionshandel","ets","klimapaket","energieunion"]),(2,["klimaschutz","erneuerbare","co2","energiepolitik","versorgungssicherheit","taxonomie"]),(1,["energie","klima","nachhaltigkeit"])],"min":2},
    "aussenpolitik":{"score":[(3,["außenpolitik","sicherheitspolitik","cfsp","gasp","pesco","nato","ukraine-hilfe"]),(2,["außenbeziehungen","drittstaaten","sanktionen","erweiterung","kandidatenland"]),(1,["international","außen","beziehungen"])],"min":2},
    "migration":{"score":[(3,["migrationspakt","asylreform","schengen","frontex","migration","asyl","flüchtling"]),(2,["grenzschutz","einwanderung","dublin","aufnahme","integration"]),(1,["migration","asyl"])],"min":2},
    "haushalt":{"score":[(3,["mehrjähriger finanzrahmen","mff","wiederaufbaufonds","nextgeneration eu","eigenmittel"]),(2,["eu-haushalt","budget","finanzbeitrag","kohäsionsfonds","strukturfonds"]),(1,["haushalt","finanzen","budget"])],"min":2},
    "rechtsstaat":{"score":[(3,["rechtsstaatlichkeit","artikel 7","grundrechte","eu-charta","coreper"]),(2,["demokratie","justiz","recht","transparenz","korruption"]),(1,["recht","regel","standard"])],"min":2},
}

def is_noise(text):
    t=text.lower()
    return any(kw in t for kw in NOISE_KEYWORDS)

def relevance_boost(text):
    t=text.lower()
    return sum(1 for kw in HIGH_VALUE_KEYWORDS if kw in t)

def score_article(text, rules):
    t=text.lower()
    result={}
    for topic,rule in rules.items():
        s=0
        for pts,kws in rule["score"]:
            for kw in kws:
                if kw in t:
                    s+=pts
                    break
        if s>=rule["min"]:
            result[topic]=s
    return result

def clean_html(text):
    text=re.sub(r'<[^>]+>',' ',text)
    for e,c in [('&amp;','&'),('&lt;','<'),('&gt;','>'),('&nbsp;',' '),('&quot;','"')]:
        text=text.replace(e,c)
    text=re.sub(r'&#\d+;','',text)
    return re.sub(r'\s+',' ',text).strip()

def parse_date(raw):
    if not raw: return ""
    try: return parsedate_to_datetime(raw).astimezone(timezone.utc).isoformat()
    except: pass
    try: return datetime.fromisoformat(raw.replace('Z','+00:00')).astimezone(timezone.utc).isoformat()
    except: return ""

def parse_feed(xml_bytes, source, topic_rules):
    try:
        text=xml_bytes.decode('utf-8',errors='replace')
        text=re.sub(r'[^\x09\x0A\x0D\x20-\uD7FF\uE000-\uFFFD]+','',text)
        root=ET.fromstring(text)
    except:
        return []
    items=root.findall('.//item') or root.findall('.//{http://www.w3.org/2005/Atom}entry')
    arts=[]
    for item in items:
        def g(tag):
            el=item.find(tag)
            return el.text.strip() if el is not None and el.text else ''
        title=g('title') or g('{http://www.w3.org/2005/Atom}title')
        if not title or len(title)<10: continue
        link=g('link')
        if not link:
            le=item.find('{http://www.w3.org/2005/Atom}link')
            link=le.get('href','') if le is not None else ''
        desc_raw=(g('{http://purl.org/rss/1.0/modules/content/}encoded') or
                  g('description') or g('{http://www.w3.org/2005/Atom}summary') or
                  g('{http://www.w3.org/2005/Atom}content') or '')
        desc=clean_html(desc_raw)[:600]
        pub_raw=(g('pubDate') or g('{http://www.w3.org/2005/Atom}published') or
                 g('{http://www.w3.org/2005/Atom}updated') or
                 g('{http://purl.org/dc/elements/1.1/}date') or '')
        pub_iso=parse_date(pub_raw)
        full_text=(title+' '+desc).lower()
        if is_noise(full_text): continue
        scored=score_article(full_text, topic_rules)
        topics=sorted(scored,key=lambda t:-scored[t])
        boost=relevance_boost(full_text)
        uid=hashlib.md5((source+title+link).encode()).hexdigest()[:12]
        arts.append({"id":uid,"source":source,"title":title,"link":link.strip(),
                     "desc":desc,"date":pub_iso,"topics":topics,"boost":boost})
    return arts

def fetch_url(url, timeout=15):
    from urllib.parse import quote
    # ASCII-encode the URL properly (handles Umlaute etc.)
    url_encoded = ''.join(c if ord(c) < 128 else quote(c) for c in url)
    headers={'User-Agent':'Mozilla/5.0 (compatible; Presseschau-Bot/1.0)',
             'Accept':'application/rss+xml,application/xml,text/xml,*/*'}
    try:
        with urlopen(Request(url_encoded,headers=headers),timeout=timeout) as r:
            return r.read()
    except Exception as e:
        print(f"  ✗ {url[:80]}: {e}",file=sys.stderr)
        return None

def load_existing(filename):
    if not os.path.exists(filename):
        return []
    try:
        with open(filename,'r',encoding='utf-8') as f:
            d=json.load(f)
        return d.get('articles',[])
    except:
        return []

def merge_rolling(existing, new_articles, days=7, max_count=5000):
    cutoff=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat()
    existing_filtered=[a for a in existing
                       if a.get('date','') >= cutoff or not a.get('date','')]
    existing_ids={a['id'] for a in new_articles}
    existing_keep=[a for a in existing_filtered if a['id'] not in existing_ids]
    merged=new_articles + existing_keep
    merged.sort(key=lambda a:a.get('date','') or '0000', reverse=True)
    return merged[:max_count]

def compute_trends(articles):
    now=datetime.now(timezone.utc)
    recent=[]
    for a in articles:
        if not a.get('date'): continue
        try:
            dt=datetime.fromisoformat(a['date'])
            if (now-dt).total_seconds()/3600<=48:
                recent.append(a)
        except: pass
    topic_counts=Counter(t for a in recent for t in a.get('topics',[]))
    stop={'der','die','das','ein','eine','und','oder','aber','nicht','mit','von','für',
          'auf','in','an','zu','im','am','ist','sind','hat','haben','wird','werden',
          'nach','vor','über','unter','bei','aus','durch','the','a','an','and','or',
          'of','in','to','for','is','are','was','has','have','with','be','it','its',
          'this','that','as','by','at','from','they','we','new','said','says','nach',
          'beim','also','mehr','noch','alle','sein','ihre','ihrer','seinen'}
    word_counts=Counter()
    for a in recent:
        words=re.findall(r'\b[a-zäöüßA-ZÄÖÜ][a-zäöüß]{4,}\b',a.get('title',''))
        for w in words:
            if w.lower() not in stop:
                word_counts[w.lower()]+=1
    return {
        "topic_counts":dict(topic_counts.most_common(20)),
        "top_keywords":dict(word_counts.most_common(40)),
        "recent_count":len(recent),
    }

def fetch_all(feed_list, topic_rules, label):
    all_arts=[]
    ok=fail=0
    for url,name,cat in feed_list:
        print(f"  [{label}] {name}...",end=' ',flush=True)
        data=fetch_url(url)
        if not data:
            fail+=1
            print(f"FAIL")
            continue
        arts=parse_feed(data,name,topic_rules)
        print(f"{len(arts)}")
        all_arts.extend(arts)
        ok+=1
        time.sleep(0.2)
    return all_arts, ok, fail

def save_json(filename, articles, ok, fail, extra=None):
    seen=set()
    deduped=[]
    for a in articles:
        if a['id'] not in seen:
            seen.add(a['id'])
            deduped.append(a)
    trends=compute_trends(deduped)
    out={
        "updated":datetime.now(timezone.utc).isoformat(),
        "feeds_ok":ok,"feeds_fail":fail,
        "count":len(deduped),
        "trends":trends,
        "articles":deduped,
    }
    if extra:
        out.update(extra)
    with open(filename,'w',encoding='utf-8') as f:
        json.dump(out,f,ensure_ascii=False,separators=(',',':'))
    size_kb=os.path.getsize(filename)//1024
    print(f"  → {filename}: {len(deduped)} Artikel, {size_kb}KB")
    return deduped

# ═══════════════════════════════════════════════════════════════
# DOKUMENT-QUELLEN
# ═══════════════════════════════════════════════════════════════
DOCUMENT_FEEDS = [
    # ── Bundestag ──────────────────────────────────────────────
    ("https://www.bundestag.de/static/appdata/includes/rss/drucksachen.rss",
     "Bundestag", "Drucksache", "bt"),
    ("https://www.bundestag.de/static/appdata/includes/rss/plenarprotokolle.rss",
     "Bundestag", "Plenarprotokoll", "bt"),
    ("https://www.bundestag.de/static/appdata/includes/rss/tagesordnungen.rss",
     "Bundestag", "Tagesordnung", "bt"),
    ("https://www.bundestag.de/static/appdata/includes/rss/wissenschaftlichedienste.rss",
     "Bundestag", "Wissenschaftlicher Dienst", "bt"),
    # ── EUR-Lex (EU-Amtsblatt) ─────────────────────────────────
    ("https://eur-lex.europa.eu/rss/OJ_L_rss.xml",
     "EUR-Lex", "Amtsblatt L (Rechtsakte)", "eurlex"),
    ("https://eur-lex.europa.eu/rss/OJ_C_rss.xml",
     "EUR-Lex", "Amtsblatt C (Mitteilungen)", "eurlex"),
]

def fetch_documents():
    """Holt Dokument-Links aus RSS-Feeds, speichert nur Metadaten."""
    docs = []
    for url, source, doc_type, origin in DOCUMENT_FEEDS:
        print(f"  [docs] {source} – {doc_type}...", end=' ', flush=True)
        data = fetch_url(url)
        if not data:
            print("FAIL")
            continue
        try:
            text = data.decode('utf-8', errors='replace')
            text = re.sub(r'[^\x09\x0A\x0D\x20-\uD7FF\uE000-\uFFFD]+', '', text)
            root = ET.fromstring(text)
        except:
            print("PARSE ERR")
            continue
        items = root.findall('.//item') or root.findall('.//{http://www.w3.org/2005/Atom}entry')
        count = 0
        for item in items:
            def g(tag):
                el = item.find(tag)
                return el.text.strip() if el is not None and el.text else ''
            title = g('title') or g('{http://www.w3.org/2005/Atom}title')
            if not title or len(title) < 5: continue
            link = g('link')
            if not link:
                le = item.find('{http://www.w3.org/2005/Atom}link')
                link = le.get('href', '') if le is not None else ''
            if not link: continue
            pub_raw = (g('pubDate') or g('{http://www.w3.org/2005/Atom}published') or
                       g('{http://www.w3.org/2005/Atom}updated') or '')
            pub_iso = parse_date(pub_raw)
            desc_raw = g('description') or g('{http://www.w3.org/2005/Atom}summary') or ''
            desc = clean_html(desc_raw)[:300]
            # Erkennt ob Link direkt auf PDF zeigt
            is_pdf = link.lower().endswith('.pdf') or 'pdf' in link.lower()
            uid = hashlib.md5((source + doc_type + title + link).encode()).hexdigest()[:12]
            docs.append({
                "id": uid,
                "source": source,
                "type": doc_type,
                "origin": origin,
                "title": title,
                "link": link.strip(),
                "desc": desc,
                "date": pub_iso,
                "is_pdf": is_pdf,
            })
            count += 1
        print(count)
        time.sleep(0.2)
    return docs

def load_existing_docs(filename):
    if not os.path.exists(filename):
        return []
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            d = json.load(f)
        return d.get('documents', [])
    except:
        return []

def merge_docs(existing, new_docs, days=7):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    existing_filtered = [d for d in existing
                         if d.get('date', '') >= cutoff or not d.get('date', '')]
    existing_ids = {d['id'] for d in new_docs}
    existing_keep = [d for d in existing_filtered if d['id'] not in existing_ids]
    merged = new_docs + existing_keep
    merged.sort(key=lambda d: d.get('date', '') or '0000', reverse=True)
    return merged[:2000]

def save_docs(filename, docs):
    seen = set()
    deduped = []
    for d in docs:
        if d['id'] not in seen:
            seen.add(d['id'])
            deduped.append(d)
    out = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "count": len(deduped),
        "documents": deduped,
    }
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    size_kb = os.path.getsize(filename) // 1024
    print(f"  → {filename}: {len(deduped)} Dokumente, {size_kb}KB")

def main():
    print(f"[{datetime.now().isoformat()}] Presseschau Fetch")

    print("\n── Allgemeine News ──")
    new_news, ok1, fail1 = fetch_all(NEWS_FEEDS, TOPIC_RULES, "news")
    existing_news = load_existing("articles.json")
    merged_news = merge_rolling(existing_news, new_news, days=7, max_count=5000)
    save_json("articles.json", merged_news, ok1, fail1)

    print("\n── EU Direkt ──")
    new_eu, ok2, fail2 = fetch_all(EU_OFFICIAL_FEEDS, EU_TOPIC_RULES, "eu")
    existing_eu = load_existing("eu_articles.json")
    merged_eu = merge_rolling(existing_eu, new_eu, days=14, max_count=2000)
    save_json("eu_articles.json", merged_eu, ok2, fail2,
              extra={"note":"Offizielle EU-Quellen: Parlament, Kommission, Rat, Institutionen"})

    print("\n── Bundestag & Bundesregierung ──")
    new_bt, ok3, fail3 = fetch_all(BUNDESTAG_FEEDS, BT_TOPIC_RULES, "bt")
    existing_bt = load_existing("bundestag_articles.json")
    merged_bt = merge_rolling(existing_bt, new_bt, days=14, max_count=3000)
    save_json("bundestag_articles.json", merged_bt, ok3, fail3,
              extra={"note":"Offizielle Quellen: Bundestag RSS-Feeds + Bundesregierung"})

    print("\n── Dokumente ──")
    new_docs = fetch_documents()
    existing_docs = load_existing_docs("documents.json")
    merged_docs = merge_docs(existing_docs, new_docs, days=7)
    save_docs("documents.json", merged_docs)

    print(f"\n✓ News: {ok1} Feeds ok, {fail1} Feeds fehlgeschlagen, {len(merged_news)} Artikel")
    print(f"✓ EU Direkt: {ok2} Feeds ok, {fail2} Feeds fehlgeschlagen, {len(merged_eu)} Artikel")
    print(f"✓ Bundestag: {ok3} Feeds ok, {fail3} Feeds fehlgeschlagen, {len(merged_bt)} Artikel")
    print(f"✓ Dokumente: {len(merged_docs)} Dokumente")

if __name__ == "__main__":
    main()
