#!/usr/bin/env python3
"""
Presseschau – Dritte Edition (v3, abwärtskompatibel)
- Rollendes 7-Tage-Archiv
- 5 Datensets: articles.json, eu_articles.json, bundestag_articles.json,
                laender_articles.json (NEU), us_articles.json (NEU)
- NEU je Artikel (additiv, alte Felder unverändert):
    priority   "eil" wenn das Portal selbst kennzeichnet (Titel-Marker, RSS-<category>)
    cats       eigene Kategorien/Tags des Portals aus <category>/<dc:subject>
    image      Bild-URL aus media:content / media:thumbnail / enclosure
    author     dc:creator / author
    kind       press | speech | transcript | consultation | agenda | news
    inst       Institution (bundestag, bundesrat, bverfg, ep, ec, eu-council, ecb, eca, cjeu,
               us-house, us-senate, us-whitehouse, us-scotus, landtag-<xx>, ...) – leer bei Medien
    cluster    Anzahl unterschiedlicher Quellen mit derselben Meldung (Basis für Top-Meldungen)
- Themen erweitert (33 statt 18), Sport im Noise-Filter
"""

import json, time, hashlib, re, sys, os
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from urllib.error import URLError
from xml.etree import ElementTree as ET
from email.utils import parsedate_to_datetime
from collections import Counter

def _atomic_dump(obj, path):
    """Erst in eine Nebendatei schreiben, dann umbenennen. Bricht der Lauf mittendrin
    ab (Timeout, Netzfehler), bleibt die alte Datei vollstaendig erhalten - statt einer
    halb geschriebenen, die im Browser als "kein gueltiges JSON" ankommt."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)



# ═══════════════════════════════════════════════════════════════
# OFFIZIELLE EU-QUELLEN (gehen in eu_articles.json)
# ═══════════════════════════════════════════════════════════════
# Google-News-Proxy: Rueckfallebene fuer Quellen ohne eigenen oder mit defektem RSS
def _gn(q):   return f"https://news.google.com/rss/search?q={q}&hl=de&gl=DE&ceid=DE:de"
def _gnus(q): return f"https://news.google.com/rss/search?q={q}&ceid=US:en&hl=en-US&gl=US"

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

    # Rueckfallebenen fuer das EP – die offiziellen Feeds fallen zeitweise aus
    (_gn("site:europarl.europa.eu"),                                              "EP Pressemitt.",    "ep"),
    ("https://www.europarl.europa.eu/rss/doc/top-stories/de.xml",                  "EP Top Stories",    "ep"),
    # ── Europäischer Rat / Gerichtshof / Rechnungshof (NEU) ───
    ("https://news.google.com/rss/search?q=site:consilium.europa.eu+%22Europäischer+Rat%22&hl=de&gl=DE&ceid=DE:de", "Europäischer Rat", "eu-council"),
    ("https://curia.europa.eu/jcms/jcms/Jo2_16799/de/?rss=1",                              "EuGH",              "cjeu"),
    ("https://news.google.com/rss/search?q=site:eca.europa.eu&hl=de&gl=DE&ceid=DE:de",     "EU-Rechnungshof",   "eca"),
    # ── Weitere EU-Institutionen ───────────────────────────────
    ("https://www.ecb.europa.eu/rss/press.html",                                 "EZB Pressemitt.",      "ecb"),
    ("https://www.ecb.europa.eu/rss/pub.html",                                   "EZB Publikationen",    "ecb"),
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
    # ── Bundesrat (offizielle Feeds, Übersicht: bundesrat.de/DE/service-navi/rss) ──
    ("https://www.bundesrat.de/SiteGlobals/Functions/RSSFeed/RSSGenerator_Announcement.xml", "Bundesrat Aktuelles",   "bundesrat"),
    ("https://www.bundesrat.de/SiteGlobals/Functions/RSSFeed/RSSGenerator_top_plenumkompakt.xml", "BR Plenum kompakt", "bundesrat"),
    ("https://www.bundesrat.de/SiteGlobals/Functions/RSSFeed/RSSGenerator_Publication.xml",  "Bundesrat Publikationen","bundesrat"),
    # ── Bundesgerichte (offizielle Feeds) ─────────────────────
    ("https://www.bundesgerichtshof.de/DE/Service/RSSFeed/Function/RSS_EN.xml?nn=373238",    "BGH Entscheidungen",   "bgh"),
    # BVerfG bietet keinen RSS-Feed, nur Suchformulare (Entscheidungs- und Pressemitteilungssuche)
    # → Google-News-Proxy auf die Domain; liefert Pressemitteilungen zuverlässig.
    ("https://news.google.com/rss/search?q=site:bundesverfassungsgericht.de&hl=de&gl=DE&ceid=DE:de", "BVerfG", "bverfg"),
    # ── Bundesbank ────────────────────────────────────────────
    ("https://www.bundesbank.de/service/rss/de/633282/feed.rss",                             "Bundesbank",           "bundesbank"),
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


# ═══════════════════════════════════════════════════════════════
# LÄNDER: Landtage + Landesregierungen (NEU → laender_articles.json)
# cat = Länderkürzel; direkte RSS-URLs der Landtage variieren stark → Google-News-Proxy
# als robuster Start; wer direkte Feeds kennt, trägt sie hier ein.
# ═══════════════════════════════════════════════════════════════
LAENDER = [("bw","Baden-Württemberg","landtag-bw.de"),("by","Bayern","bayern.landtag.de"),("be","Berlin","parlament-berlin.de"),
           ("bb","Brandenburg","landtag.brandenburg.de"),("hb","Bremen","bremische-buergerschaft.de"),("hh","Hamburg","hamburgische-buergerschaft.de"),
           ("he","Hessen","hessischer-landtag.de"),("mv","Mecklenburg-Vorpommern","landtag-mv.de"),("ni","Niedersachsen","landtag-niedersachsen.de"),
           ("nw","Nordrhein-Westfalen","landtag.nrw.de"),("rp","Rheinland-Pfalz","landtag.rlp.de"),("sl","Saarland","landtag-saar.de"),
           ("sn","Sachsen","landtag.sachsen.de"),("st","Sachsen-Anhalt","landtag.sachsen-anhalt.de"),("sh","Schleswig-Holstein","landtag.ltsh.de"),("th","Thüringen","thueringer-landtag.de")]
# Direkte Landtags-Feeds, wo vorhanden – haben Vorrang vor dem Google-Proxy.
LAENDER_DIRECT = [
    ("https://www.landtag.nrw.de/home/aktuelles/meldungen-und-berichte/rss-newsfeed/rss-feed/contentArea/meldungen-rss-feed.xml",
     "Landtag Nordrhein-Westfalen", "nw"),
    ("https://www.landtag-niedersachsen.de/rss-feeds/rss.xml", "Landtag Niedersachsen", "ni"),
    ("https://www.bayern.landtag.de/webangebot3/views/rss/main.xhtml", "Landtag Bayern", "by"),
]
_DIRECT_CODES = {c for _, _, c in LAENDER_DIRECT}
LAENDER_FEEDS = list(LAENDER_DIRECT)
LAENDER_FEEDS += [(_gn(f"site:{dom}"), f"Landtag {name}", code) for code,name,dom in LAENDER if code not in _DIRECT_CODES]
LAENDER_FEEDS += [(_gn(f"Landesregierung+{name.replace(' ','+')}+Ministerpräsident"), f"LReg {name}", code) for code,name,_ in LAENDER]
LAENDER_TOPIC_RULES = None   # wird unten auf BT_TOPIC_RULES gesetzt (gleiche Ressorts)
_CONSOLIDATED = False        # Flag: siehe _apply_consolidation() am Ende der Datei

# ═══════════════════════════════════════════════════════════════
# USA: Institutionen + US-Presse (NEU → us_articles.json)
# ═══════════════════════════════════════════════════════════════
US_FEEDS = [
    # Kongress (congress.gov RSS – Feeds im Log prüfen)
    ("https://www.congress.gov/rss/house-floor-today.xml",        "House Floor",        "us-house"),
    ("https://www.congress.gov/rss/senate-floor-today.xml",       "Senate Floor",       "us-senate"),
    ("https://www.congress.gov/rss/presented-to-president.xml",   "Bills to President", "us-congress"),
    ("https://www.congress.gov/rss/most-viewed-bills.xml",        "Most-viewed Bills",  "us-congress"),
    (_gnus("site:house.gov+press"),                               "House Press",        "us-house"),
    (_gnus("site:senate.gov+press"),                              "Senate Press",       "us-senate"),
    # Weißes Haus
    ("https://www.whitehouse.gov/feed/",                          "White House",        "us-whitehouse"),
    # Gerichte
    ("https://www.uscourts.gov/rss.xml",                          "US Courts",          "us-courts"),
    ("https://www.law.cornell.edu/supct/cert/cert.rss",           "SCOTUS Cert (LII)",  "us-scotus"),
    ("https://www.law.cornell.edu/supct/recent/recent.rss",       "SCOTUS Entsch. (LII)","us-scotus"),
    (_gnus("site:supremecourt.gov"),                              "Supreme Court",      "us-scotus"),
    # Bundesbehörden / amtliche Veröffentlichungen (govinfo.gov/feeds)
    ("https://www.federalregister.gov/documents/search.rss?conditions%5Btype%5D%5B%5D=RULE", "Federal Register Rules", "us-agency"),
    ("https://www.govinfo.gov/rss/bills.xml",                     "govinfo Bills",      "us-congress"),
    ("https://www.govinfo.gov/rss/plaw.xml",                      "govinfo Public Laws","us-congress"),
    ("https://www.govinfo.gov/rss/cpd.xml",                       "govinfo Präsident.", "us-whitehouse"),
    # US-Presse Politik
    ("https://rss.politico.com/politics-news.xml",                "Politico US",        "us-media"),
    ("https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml", "NYT Politics",       "us-media"),
    ("https://feeds.washingtonpost.com/rss/politics",             "WaPo Politics",      "us-media"),
    ("https://thehill.com/feed/",                                 "The Hill",           "us-media"),
    (_gnus("when:24h+allinurl:apnews.com+politics"),              "AP Politics",        "us-media"),
    (_gnus("site:axios.com+politics"),                            "Axios",              "us-media"),
]
US_TOPIC_RULES = {
    "congress":{"score":[(3,["congress","house of representatives","senate","speaker","filibuster","bill passed","floor vote"]),(2,["lawmakers","committee hearing","markup","appropriations"]),(1,["bill","vote"])],"min":2},
    "white_house":{"score":[(3,["white house","executive order","president signed","oval office","press secretary"]),(2,["president","administration","cabinet"]),(1,["federal"])],"min":2},
    "scotus":{"score":[(3,["supreme court","scotus","justices","ruling","opinion of the court"]),(2,["appeals court","federal judge","injunction"]),(1,["court"])],"min":2},
    "trade":{"score":[(3,["tariff","trade war","import duties","section 232","section 301","ustr"]),(2,["trade deal","exports","imports","wto"]),(1,["trade"])],"min":2},
    "tech_policy":{"score":[(3,["ai regulation","section 230","antitrust","ftc","fcc","chips act","export controls"]),(2,["big tech","data privacy","tiktok","semiconductor"]),(1,["tech"])],"min":2},
    "defense":{"score":[(3,["pentagon","ndaa","defense department","department of war","military aid","nato"]),(2,["troops","weapons","missile","army","navy"]),(1,["defense"])],"min":2},
    "economy":{"score":[(3,["federal reserve","fed rate","inflation","gdp","jobs report","treasury"]),(2,["economy","recession","debt ceiling","budget deficit"]),(1,["economic"])],"min":2},
    "elections":{"score":[(3,["midterms","primary","election","campaign","polling","ballot"]),(2,["democrats","republicans","gop","dnc","rnc"]),(1,["voters"])],"min":2},
    "immigration":{"score":[(3,["immigration","border","deportation","asylum","ice "]),(2,["migrants","visa","dhs"]),(1,["citizenship"])],"min":2},
    "foreign_policy":{"score":[(3,["state department","secretary of state","sanctions","ukraine","china","israel","iran"]),(2,["diplomacy","ambassador","allies","summit"]),(1,["foreign"])],"min":2},
    "energy":{"score":[(3,["energy department","oil","gas prices","epa","climate"]),(2,["renewable","drilling","pipeline","emissions"]),(1,["energy"])],"min":2},
    "health":{"score":[(3,["medicare","medicaid","fda","cdc","hhs","obamacare","affordable care act"]),(2,["health care","drug prices","vaccine"]),(1,["health"])],"min":2},
}

NEWS_FEEDS = [
    # ── Eilmeldungen / Breaking ───────────────────────────────
    # Beim ersten Lauf im Log pruefen; scheitert eine URL, die Google-News-Zeile
    # darunter aktivieren (liefert dieselben Meldungen, nur etwas verzoegert).
    # Diese beiden Feeds enthalten ausschliesslich Eilmeldungen:
    ("https://www.tagesschau.de/eilmeldung/index~rss2.xml",   "Tagesschau Eil", "eil"),
    ("https://www.n-tv.de/eilmeldungen/rss",                  "n-tv Eil",       "eil"),
    # WELT "latest" und ZDF "nachrichten" sind normale Nachrichtenfeeds und
    # daher bewusst KEINE Eil-Quellen - sie laufen unten als regulaere Feeds mit.
    ("https://www.welt.de/feeds/latest.rss",                  "WELT",           "de-leit"),
    ("https://www.zdfheute.de/rss/zdf/nachrichten",           "ZDF heute",      "de-leit"),
    # Fallbacks:
    # (_gn("site:tagesschau.de+eilmeldung"),                  "Tagesschau Eil", "eil"),
    # (_gn("site:n-tv.de+eilmeldung"),                        "n-tv Eil",       "eil"),

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

    # ── Deutsch: Mobilfunk & Glasfaser / Telko ───────────────
    # Telekom Newsroom (Netze/Technologie)
    ("https://www.telekom.com/de/medien/medieninformationen/rss",                  "Telekom",              "de-telko"),
    # Telefónica / O2 Newsroom
    ("https://www.telefonica.de/rss/news.rss",                                    "Telefónica DE",        "de-telko"),
    # Vodafone Newsroom DE
    ("https://newsroom.vodafone.de/rss/",                                         "Vodafone DE",          "de-telko"),
    # 1&1 Unternehmen Presse → via Google News (direkter Feed oft geschützt)
    ("https://news.google.com/rss/search?q=site:unternehmen.1und1.de+presse&hl=de&gl=DE&ceid=DE:de", "1&1",  "de-telko"),
    # Bundesnetzagentur Pressemitteilungen
    ("https://www.bundesnetzagentur.de/SharedDocs/RSS/DE/Pressemitteilungen.xml", "Bundesnetzagentur",    "de-telko"),
    # VATM (Verband TK-Anbieter)
    ("https://news.google.com/rss/search?q=VATM+Telekommunikation&hl=de&gl=DE&ceid=DE:de", "VATM",        "de-telko"),
    # Glasfaser: Deutsche Glasfaser / GlasfaserPlus / Ausbau News
    ("https://news.google.com/rss/search?q=Glasfaserausbau+FTTH+Deutschland&hl=de&gl=DE&ceid=DE:de", "Glasfaser News", "de-telko"),
    ("https://news.google.com/rss/search?q=%22Deutsche+Glasfaser%22+OR+%22GlasfaserPlus%22&hl=de&gl=DE&ceid=DE:de", "Deutsche Glasfaser", "de-telko"),
    # Heise Netze & Telekommunikation
    ("https://www.heise.de/thema/telekommunikation-rss",                          "Heise Telko",          "de-telko"),

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
    # Sport (NEU) – nur eindeutige Sport-Begriffe, damit Sportpolitik nicht mitgefiltert wird
    "bundesliga","2. liga","champions league","europa league","conference league","dfb-pokal",
    "spieltag","tabellenführer","fc bayern","bayern münchen","borussia dortmund","bvb","schalke 04",
    "eintracht frankfurt","werder bremen","bayer leverkusen","rb leipzig","hertha bsc","hamburger sv",
    "nationalelf","länderspiel","torwart","torhüter","trainerwechsel","relegation","transfermarkt",
    "formel 1","grand prix","wimbledon","french open","us open","nfl","nba","nhl","mlb","super bowl",
    "world series","premier league","la liga","serie a","biathlon","skispringen","tour de france",
    # Nachwuchs- und Trainerthemen (z. B. "X wird neuer U19-Coach")
    "u15","u16","u17","u18","u19","u20","u21","u23","coach","cheftrainer","co-trainer",
    "nachwuchstrainer","interimstrainer","trainerposten","trainerstab","sportdirektor",
    "cheftrainerin","kader","kaderplanung","vertragsverlängerung spieler","leihe",
    # Ergebnismeldungen
    "unentschieden","auswärtssieg","heimsieg","punktspiel","viertelfinale","halbfinale",
    "achtelfinale","gruppenphase","torjäger","elfmeter","abstiegskampf","aufstiegsrennen",
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
    # ── NEU: erweiterte Themen (v3) ──────────────────────────
    "gesundheit":{"score":[(3,["gesundheitsministerium","krankenhausreform","krankenkasse","pflegeversicherung","lauterbach","gesundheitswesen"]),(2,["gesundheit","krankenhaus","pflege","ärzte","apotheke","patienten"]),(1,["medizinisch","klinik"])],"min":2},
    "soziales":{"score":[(3,["bürgergeld","rente","rentenreform","mindestlohn","sozialstaat","grundsicherung"]),(2,["sozial","arbeitslos","armut","kindergeld","elterngeld","tarif"]),(1,["gewerkschaft","sozialverband"])],"min":2},
    "bildung":{"score":[(3,["bildungsministerium","kultusminister","schulreform","bafög","digitalpakt","hochschule"]),(2,["schule","universität","lehrer","studierende","ausbildung","kita"]),(1,["bildung"])],"min":2},
    "migration":{"score":[(3,["asylpolitik","migrationspolitik","abschiebung","grenzkontrolle","frontex","asylreform"]),(2,["migration","asyl","flüchtlinge","einwanderung","integration"]),(1,["zuwanderung"])],"min":2},
    "justiz":{"score":[(3,["bundesverfassungsgericht","bundesgerichtshof","eugh","urteil","verfassungsgericht","staatsanwaltschaft"]),(2,["gericht","richter","klage","verfahren","justiz","anklage"]),(1,["recht"])],"min":2},
    "innenpolitik":{"score":[(3,["innenministerium","verfassungsschutz","innere sicherheit","polizeigesetz","extremismus","terror"]),(2,["polizei","innenminister","bka","bnd","spionage","sabotage"]),(1,["sicherheitsbehörden"])],"min":2},
    "umwelt":{"score":[(3,["umweltministerium","naturschutz","artenschutz","klimaanpassung","hochwasser","dürre"]),(2,["umwelt","biodiversität","wald","gewässer","plastik","müll"]),(1,["ökologisch"])],"min":2},
    "agrar":{"score":[(3,["landwirtschaftsministerium","agrarpolitik","bauernverband","tierwohl","glyphosat","gap-reform"]),(2,["landwirtschaft","bauern","ernte","düngemittel","fischerei"]),(1,["agrar"])],"min":2},
    "wohnen":{"score":[(3,["mietpreisbremse","wohnungsbau","bauministerium","mietendeckel","wohnungsnot","baugenehmigung"]),(2,["miete","wohnung","bauen","immobilien","stadtentwicklung"]),(1,["wohnen"])],"min":2},
    "arbeit":{"score":[(3,["arbeitsministerium","fachkräftemangel","tarifverhandlung","arbeitszeit","kurzarbeit","streik"]),(2,["arbeitsmarkt","beschäftigung","gewerkschaft","arbeitgeber","homeoffice"]),(1,["arbeit"])],"min":2},
    "kultur":{"score":[(3,["kulturstaatsminister","rundfunkbeitrag","öffentlich-rechtlich","medienstaatsvertrag","pressefreiheit"]),(2,["kultur","medien","rundfunk","verlag","museum","theater"]),(1,["journalismus"])],"min":2},
    "handel":{"score":[(3,["zölle","handelsabkommen","handelskrieg","wto","mercosur","lieferkettengesetz"]),(2,["export","import","handel","zoll","freihandel"]),(1,["außenhandel"])],"min":2},
    "raumfahrt":{"score":[(3,["esa","nasa","spacex","raumfahrt","satellit","ariane"]),(2,["rakete","weltraum","orbit","starlink","iris²"]),(1,["space"])],"min":2},
    "afrika":{"score":[(3,["afrika","nigeria","südafrika","äthiopien","sahel","kongo","kenia"]),(2,["afrikanische union","sudan","mali","niger","ghana"]),(1,["subsahara"])],"min":2},
    "lateinamerika":{"score":[(3,["brasilien","mexiko","argentinien","venezuela","lateinamerika","kolumbien"]),(2,["chile","peru","kuba","milei","lula"]),(1,["südamerika"])],"min":2},
    "verbraucher":{"score":[(3,["verbraucherschutz","verbraucherzentrale","produktsicherheit","rückruf","irreführende werbung"]),(2,["verbraucher","gewährleistung","abmahnung","preisbindung"]),(1,["kunden"])],"min":2},
    "kommunales":{"score":[(3,["kommunalfinanzen","städtetag","landkreistag","gemeindebund","kommunale selbstverwaltung"]),(2,["kommune","stadtrat","landrat","bürgermeister","gemeinde"]),(1,["kommunal"])],"min":2},
    "sicherheitspolitik":{"score":[(3,["hybride bedrohung","spionageabwehr","sabotage","desinformation","zivilschutz","bevölkerungsschutz"]),(2,["nachrichtendienst","bnd","verfassungsschutz","drohnensichtung"]),(1,["bedrohung"])],"min":2},
    "religion":{"score":[(3,["kirchensteuer","religionsgemeinschaft","antisemitismus","islamismus","kirchentag"]),(2,["kirche","moschee","synagoge","religion","bischof"]),(1,["glaube"])],"min":2},
    "sport_politik":{"score":[(3,["sportförderung","dopingbekämpfung","olympiabewerbung","sportausschuss","stadionsicherheit"]),(2,["dsb","dosb","sportpolitik","sportverband"]),(1,["sportstätte"])],"min":3},
    "demografie":{"score":[(3,["demografischer wandel","geburtenrate","alterung","fachkräfteeinwanderung","bevölkerungsentwicklung"]),(2,["demografie","überalterung","zuwanderungsbedarf"]),(1,["bevölkerung"])],"min":2},
    "korruption":{"score":[(3,["lobbyregister","korruptionsverdacht","untersuchungsausschuss","transparenzregister","interessenkonflikt"]),(2,["lobbyismus","bestechung","compliance","whistleblower"]),(1,["transparenz"])],"min":2},
    "nordeuropa":{"score":[(3,["schweden","norwegen","finnland","dänemark","ostsee","baltikum"]),(2,["nordische","estland","lettland","litauen","island"]),(1,["skandinavien"])],"min":2},
    "osteuropa":{"score":[(3,["polen","tschechien","ungarn","slowakei","rumänien","westbalkan"]),(2,["visegrad","bulgarien","kroatien","serbien","moldau"]),(1,["osteuropa"])],"min":2},
    "netzpolitik":{"score":[(3,["netzausbau","glasfaserausbau","glasfaser","ftth","5g-ausbau","netzneutralität","internetfreiheit","bundesnetzagentur","frequenzvergabe"]),(2,["netzpolitik","digitale infrastruktur","breitband","starlink","telekom","vodafone","telefónica","telefonica","1&1 netz","o2 netz","mobilfunk","lte","5g ","mobilfunknetz"]),(1,["internet","netz","telko","isp"])],"min":2},
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
    return any(kw_hit(kw, t) for kw in NOISE_KEYWORDS)

def relevance_boost(text):
    t=text.lower()
    return sum(1 for kw in HIGH_VALUE_KEYWORDS if kw_hit(kw, t))

# ═══════════════════════════════════════════════════════════════
# THEMEN-KONSOLIDIERUNG (v3.1)
# 42 Einzelregeln waren zu fein für die Oberfläche. Die Regeln bleiben erhalten,
# werden aber auf ~20 Oberthemen zusammengeführt: die Stichwortlisten der
# zusammengelegten Themen werden vereinigt, es geht also keine Trefferqualität verloren.
# Ein Thema wieder auftrennen = Zeile aus TOPIC_MERGE entfernen.
# ═══════════════════════════════════════════════════════════════
# An den Bundesressorts orientiert: Landwirtschaft, Arbeit, Bauen/Wohnen und
# Kultur/Medien sind eigene Ressorts und nicht mehr unter Umwelt bzw. Soziales
# versteckt - genau das hatte in der Praxis gefehlt.
TOPIC_MERGE = {
    "medizin":"gesundheit",
    "demografie":"soziales",
    "agrar":"landwirtschaft",
    "rechtsstaat":"justiz", "korruption":"justiz",
    "innenpolitik":"sicherheit", "sicherheitspolitik":"verteidigung",
    "verbraucher":"wirtschaft", "handel":"wirtschaft", "startup":"wirtschaft",
    "raumfahrt":"wissenschaft",
    "religion":"politik", "kommunales":"politik",
    "ukraine":"international", "nahost":"international", "asien":"international",
    "afrika":"international", "lateinamerika":"international",
    "nordeuropa":"international", "osteuropa":"international",
    "aussenpolitik":"international",
    "sport_politik":None,          # None = ersatzlos entfernen
}
# ── Nachjustierung nach dem Zusammenfuehren ──────────────────
# 1) ADD: Stichwoerter, die im Alltag fehlten (z. B. Landwirtschaft war zu duenn,
#    dadurch landeten Agrarthemen bei Wirtschaft oder gar nicht).
# 2) DEMOTE: mehrdeutige Woerter, die allein kein Thema begruenden. "Drohne" etwa
#    steckt genauso in Rehkitzrettung wie in Luftraumverletzungen - erst zusammen
#    mit einem zweiten Militaerbegriff ist es Verteidigung.
TOPIC_ADD = {
    "landwirtschaft": [(3, ["landwirt", "landwirtin", "bauernhof", "nutztier", "weidetier", "rehkitz",
                            "kitzrettung", "maehwerk", "jaeger", "jäger", "jagd", "pestizid",
                            "duenger", "dünger", "ackerbau", "viehhaltung", "milchbauern", "obstbau",
                            "weinbau", "agrardiesel", "hoefesterben", "höfesterben", "direktzahlungen"]),
                       (2, ["hecke", "wiese", "acker", "weide", "ernte", "stall", "melken", "traktor"])],
    "umwelt": [(3, ["wildtier", "wildtiere", "artensterben", "tierschutz", "forst", "moor",
                    "renaturierung", "flaechenversiegelung", "flächenversiegelung"]),
               (2, ["boden", "insekten", "bienen", "vogelschutz"])],
    "finanzen": [(3, ["zins", "zinsen", "leitzins", "zinssenkung", "zinserhoehung", "zinserhöhung",
                      "kapitalmarkt", "geldpolitik", "anleihe", "anleihen", "rendite", "boerse", "börse",
                      "aktienmarkt", "waehrung", "währung", "wechselkurs", "notenbank", "zentralbank",
                      "kreditvergabe", "bankenaufsicht", "finanzaufsicht", "bafin"]),
                 (2, ["inflation", "konjunkturprognose", "wertpapier", "fonds", "dividende", "kurssturz"])],
    "arbeit": [(3, ["arbeitsmarkt", "fachkraeftemangel", "fachkräftemangel", "tarifrunde", "tarifabschluss",
                    "streik", "kurzarbeit", "mindestlohn", "arbeitszeitgesetz", "arbeitsagentur",
                    "betriebsrat", "mitbestimmung", "arbeitslosenquote"]),
               (2, ["gewerkschaft", "arbeitgeber", "beschaeftigung", "beschäftigung", "homeoffice", "azubi"])],
    "wohnen": [(3, ["wohnungsbau", "mietpreisbremse", "mietendeckel", "wohnungsnot", "baugenehmigung",
                    "sozialwohnung", "bauministerium", "grundsteuer", "wohnungsmarkt", "mietrecht",
                    "bauordnung", "wohngeld", "bauland"]),
               # "bauen" allein ist wertlos: "Nvidia will mehr Chips bauen" landete damit
               # unter Bauen & Wohnen. Nur Wohnungsbezug zaehlt.
               (2, ["miete", "mieten", "vermieter", "immobilienmarkt", "stadtentwicklung",
                    "wohnraum", "neubau", "bauherren"])],
    "kultur": [(3, ["rundfunkbeitrag", "oeffentlich-rechtlich", "öffentlich-rechtlich", "medienstaatsvertrag",
                    "pressefreiheit", "kulturstaatsminister", "denkmalschutz"]),
               (2, ["kultur", "museum", "theater", "verlag", "journalismus", "film", "buchmesse"])],
    "gesundheit": [(3, ["hausarzt", "hausaerzte", "notaufnahme", "rettungsdienst", "impfstoff",
                        "epidemie", "seuche", "krankenkassenbeitrag"])],
    "bildung": [(3, ["lehrermangel", "schulbau", "ganztagsbetreuung", "abitur", "berufsschule",
                     "studienplatz", "kitaplatz"])],
    "mobilitaet": [(3, ["deutschlandticket", "nahverkehr", "oepnv", "öpnv", "radweg", "tempolimit",
                        "schienennetz", "brueckensanierung", "brückensanierung"])],
}
# Wort -> Thema(en), in denen es auf Gewicht 1 herabgestuft wird
TOPIC_DEMOTE = {
    # Woerter mit doppelter Bedeutung: allein begruenden sie kein Thema
    "bauen":        ["wohnen", "wirtschaft"],
    "bau":          ["wohnen"],
    "chip":         ["wohnen"],
    "netz":         ["energie"],
    "kette":        ["wirtschaft"],
    "welle":        ["gesundheit"],
    "spitze":       ["politik"],
    "kurs":         ["finanzen", "bildung"],
    "zug":          ["mobilitaet"],
    "linie":        ["mobilitaet"],
    "drohne":       ["verteidigung"],
    "drohnen":      ["verteidigung"],
    "schutz":       ["verteidigung", "sicherheit"],
    "angriff":      ["verteidigung"],
    "einsatz":      ["verteidigung"],
    "krise":        ["wirtschaft", "finanzen"],
    "markt":        ["wirtschaft"],
    "produktion":   ["wirtschaft"],
    "wachstum":     ["wirtschaft"],
    "kosten":       ["wirtschaft", "finanzen"],
    "preise":       ["wirtschaft"],
    "technologie":  ["tech"],
    "system":       ["tech"],
    "plattform":    ["tech"],
}

# Themen, die leicht falsch anspringen, brauchen mehr Belege
TOPIC_MIN = {"verteidigung": 3, "sicherheit": 3, "justiz": 3, "international": 3}

def tune(rules):
    for topic, m in TOPIC_MIN.items():
        if topic in rules: rules[topic]["min"] = max(rules[topic].get("min", 2), m)
    for topic, adds in TOPIC_ADD.items():
        if topic not in rules: continue
        buckets = {w: set(kws) for w, kws in rules[topic]["score"]}
        for w, kws in adds:
            buckets.setdefault(w, set()).update(kws)
        rules[topic]["score"] = [(w, sorted(buckets[w])) for w in sorted(buckets, reverse=True)]
    for word, topics in TOPIC_DEMOTE.items():
        for topic in topics:
            if topic not in rules: continue
            buckets = {w: set(kws) for w, kws in rules[topic]["score"]}
            moved = False
            for w in list(buckets):
                if w > 1 and word in buckets[w]:
                    buckets[w].discard(word); moved = True
            if moved:
                buckets.setdefault(1, set()).add(word)
                rules[topic]["score"] = [(w, sorted(buckets[w])) for w in sorted(buckets, reverse=True) if buckets[w]]
    return rules

def consolidate(rules):
    out = {}
    for key, rule in rules.items():
        target = TOPIC_MERGE.get(key, key)
        if target is None:
            continue
        if target not in out:
            out[target] = {"score": [(w, list(kws)) for w, kws in rule["score"]], "min": rule.get("min", 2)}
            continue
        # Stichwörter je Gewichtung vereinigen
        buckets = {w: set(kws) for w, kws in out[target]["score"]}
        for w, kws in rule["score"]:
            buckets.setdefault(w, set()).update(kws)
        out[target]["score"] = [(w, sorted(buckets[w])) for w in sorted(buckets, reverse=True)]
        out[target]["min"] = min(out[target]["min"], rule.get("min", 2))
    return out

# ═══════════════════════════════════════════════════════════════
# STICHWORT-TREFFER MIT WORTGRENZEN (v3.2)
#
# Vorher wurde mit `kw in text` gesucht. Das erzeugte falsche Treffer, weil
# Stichwörter mitten in anderen Wörtern steckten:
#     "telekom"  → Tele|kom|munikation      → Netz/Telko bei Pandemie-Meldungen
#     "netz"     → Gewässer|netz|, Strom|netz| → Netz/Telko bei Umweltmeldungen
#     "mobil"    → Im|mobil|ien             → Netz/Telko bei Bankmeldungen
#
# Jetzt gilt:
#   • kurze Stichwörter (≤ 8 Zeichen, ein Wort) müssen ein ganzes Wort sein,
#     deutsche Endungen (-e, -en, -s, -er …) sind erlaubt
#   • lange oder zusammengesetzte Stichwörter dürfen am Wortanfang stehen,
#     damit "glasfaser" auch "Glasfaserausbau" findet
#   • Stichwörter in AMBIGUOUS sind immer exakte Wörter
# ═══════════════════════════════════════════════════════════════
AMBIGUOUS = {"netz","funk","mobil","telekom","internet","isp","daten","gas","strom","bahn",
             "recht","kultur","medien","arbeit","wasser","wald","handel","zoll","tarif",
             "kunden","technik","digital","system","platform","plattform","energie"}
SUFFIX = r"(?:e|en|es|s|n|er|ern|em|in|innen)?"
_RX_CACHE = {}
def kw_regex(kw):
    rx = _RX_CACHE.get(kw)
    if rx is None:
        k = kw.strip()
        esc = re.escape(k).replace(r"\ ", r"\s+")
        if " " in k or "-" in k or (len(k) > 8 and k not in AMBIGUOUS):
            pat = r"(?<![\wäöüß])" + esc                      # Wortanfang, Kompositum erlaubt
        else:
            pat = r"(?<![\wäöüß])" + esc + SUFFIX + r"(?![\wäöüß])"   # ganzes Wort
        rx = re.compile(pat, re.I)
        _RX_CACHE[kw] = rx
    return rx

def kw_hit(kw, text):
    return bool(kw_regex(kw).search(text))

def score_article(text, rules):
    t=text.lower()
    result={}
    for topic,rule in rules.items():
        s=0
        for pts,kws in rule["score"]:
            for kw in kws:
                if kw_hit(kw, t):
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

def parse_feed(fetch_result, source, topic_rules):
    if fetch_result is None:
        return []
    xml_bytes, http_charset = fetch_result
    try:
        # 1. Try to detect encoding from XML declaration (<?xml ... encoding="..."?>)
        xml_head = xml_bytes[:200]
        enc_match = re.search(rb'encoding=["\']([^"\']+)["\']', xml_head)
        xml_declared = enc_match.group(1).decode('ascii','replace').lower() if enc_match else None

        # 2. Priority: XML declaration > HTTP header > UTF-8
        charset = xml_declared or http_charset or 'utf-8'
        # Normalize common aliases
        charset = {'iso-8859-1':'latin-1','iso8859-1':'latin-1','windows-1252':'cp1252'}.get(charset, charset)

        try:
            text = xml_bytes.decode(charset, errors='replace')
        except (LookupError, UnicodeDecodeError):
            text = xml_bytes.decode('utf-8', errors='replace')

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
        # ── NEU (v3): Portal-eigene Kennzeichnung, Bild, Autor ──
        cats=[]
        for c in item.findall('category')+item.findall('{http://purl.org/dc/elements/1.1/}subject')+item.findall('{http://www.w3.org/2005/Atom}category'):
            v=(c.text or c.get('term') or c.get('label') or '').strip()
            if v and v not in cats: cats.append(v)
        cats=cats[:6]
        priority=detect_priority(title, cats)
        img=''
        for tag in ('{http://search.yahoo.com/mrss/}content','{http://search.yahoo.com/mrss/}thumbnail','enclosure','{http://search.yahoo.com/mrss/}group/{http://search.yahoo.com/mrss/}content'):
            for el in item.findall(tag):
                u=el.get('url') or ''
                if u.startswith('http') and ('image' in (el.get('type') or 'image/') or el.get('medium')=='image'):
                    img=u; break
            if img: break
        if not img:
            m=re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc_raw or '')
            if m: img=m.group(1)
        author=(g('{http://purl.org/dc/elements/1.1/}creator') or g('author') or
                g('{http://www.w3.org/2005/Atom}author/{http://www.w3.org/2005/Atom}name') or '')[:120]
        access = detect_access_cat(source, title, cats, link, cat)
        if access == "paid":
            title = clean_paid_marker(title)
        title = improve_title(title, desc, source)
        a={"id":uid,"source":source,"title":title,"link":link.strip(),"access":access,
           "desc":desc,"date":pub_iso,"topics":topics,"boost":boost,
           "priority":priority,"cats":cats,"image":img,"author":author}
        arts.append(a)
    return arts

# ── Feeds, die ausschliesslich Eilmeldungen/Breaking fuehren ──
# Alles aus diesen Quellen bekommt priority="eil", ohne dass ein Titel-Marker noetig ist.
# Bisher kam fast nur Handelsblatt durch, weil nur wenige Redaktionen "+++" oder
# "Eilmeldung:" in den Titel schreiben.
# NUR Feeds, die ausschliesslich Breaking-Meldungen enthalten. Ein normaler
# "latest"- oder "nachrichten"-Feed gehoert hier NICHT hinein - sonst waere jede
# Routinemeldung eine Eilmeldung. Im Zweifel weglassen: der Brennpunkt (mehrere
# Quellen zur selben Meldung) traegt die Rubrik auch ohne Eil-Kennzeichnung.
EIL_SOURCES = {"Tagesschau Eil", "n-tv Eil"}

# ── NEU (v3): Eilmeldung nur, wenn das Portal es selbst so kennzeichnet ──
# "+++" und "Live:" sind reine Aufmachermarker und sagen nichts ueber die Dringlichkeit
# aus - sie erzeugten die meisten Fehlalarme und sind daher raus.
EIL_TITLE_RE = re.compile(r'^\s*(eilmeldung|eilt|breaking news)\s*[:\-–]', re.I)
EIL_CAT_RE   = re.compile(r'^(eil|eilmeldung|breaking|breaking news)$', re.I)
def detect_priority(title, cats):
    if EIL_TITLE_RE.search(title or ''):
        return "eil"
    if any(EIL_CAT_RE.match(c) for c in cats or []):
        return "eil"
    return ""

# ── NEU (v3): Inhaltstyp und Institution je Feed ──
# ── ZUGANG: frei oder kostenpflichtig ──────────────────────
# Viele Haeuser kennzeichnen Bezahlartikel im Titel oder in der Kategorie
# ("S+", "ZEIT+", "SZ Plus", "F+", "(Abo)"). Andere Angebote sind vollstaendig
# kostenpflichtig. Beides wird hier zusammengefuehrt, damit im Interface
# erkennbar ist, was sich ohne Abonnement lesen laesst.

# Angebote, die praktisch vollstaendig hinter einer Bezahlschranke stehen
PAID_SOURCES = {
    "Handelsblatt", "Table.Briefings", "Table.Media", "Tagesspiegel Background",
    "Politico Pro", "Börsen-Zeitung", "WirtschaftsWoche", "Capital",
    "Der Spiegel Plus", "Wall Street Journal", "Financial Times", "Bloomberg",
    "The Economist", "New York Times", "Washington Post",
}
# Angebote ohne Bezahlschranke (oeffentlich-rechtlich, Behoerden, Institutionen)
FREE_SOURCES = {
    "Tagesschau", "Tagesschau Eil", "ZDF heute", "Deutschlandfunk", "DLF",
    "n-tv Eil", "heise online", "Heise", "Golem", "netzpolitik.org",
    "Bundesregierung", "Bundestag", "Bundesrat", "EU-Kommission", "EP Pressemitt.",
    "BGH", "BVerfG", "Bundesbank", "EZB", "SWP Berlin", "Bruegel", "White House",
}
# Kennzeichnung im Titel oder in den Kategorien des Feeds
PAID_MARK_RE = re.compile(
    r'(^|[\s\[(|])('
    r's\+|z\+|f\+|w\+|zeit\+|spiegel\+|welt\+|faz\+|nzz\+|'          # Kuerzel mit Pluszeichen
    r'sz\s*plus|zeit\s*plus|spiegel\s*plus|welt\s*plus|faz\s*plus|'      # ausgeschrieben
    r'plus-artikel|abo|abonnenten|paywall|premium|exklusiv f[üu]r'
    r')([\s\])|:.\-–]|$)', re.I)

# Kategorien, deren Inhalte per Definition oeffentlich sind: Behoerden,
# Parlamente, Gerichte, Zentralbanken, Denkfabriken. Diese Stellen veroeffentlichen
# ihre Mitteilungen und Dokumente ohne Bezahlschranke - das ist keine Vermutung,
# sondern folgt aus ihrem Auftrag.
FREE_CATS = {
    "ec", "ep", "eu-council", "ecb", "eu-think",          # EU-Organe und Institute
    "bundesrat", "bt-allg", "breg", "bverfg", "bgh",       # Bund, Parlament, Gerichte
    "bundesbank", "us-whitehouse", "us-house", "us-senate",
}

def detect_access(source, title, cats, link=""):
    """'paid', 'free' oder 'unknown' – bewusst zurueckhaltend."""
    if source in PAID_SOURCES: return "paid"
    if any(re.fullmatch(r"(s\+|plus|abo|premium|paywall)", str(c).strip(), re.I) for c in (cats or [])):
        return "paid"
    if PAID_MARK_RE.search(title or ""): return "paid"
    l = (link or "").lower()
    if re.search(r"[/\-.](plus|premium|abo|paywall)[/\-.?]|[/\-]plus\b", l): return "paid"
    if source in FREE_SOURCES: return "free"
    return "unknown"

def detect_access_cat(source, title, cats, link="", cat=""):
    """Wie detect_access, beruecksichtigt zusaetzlich die Feed-Kategorie."""
    a = detect_access(source, title, cats, link)
    if a == "unknown" and cat in FREE_CATS: return "free"
    return a

def clean_paid_marker(title):
    """Entfernt die Kennzeichnung aus dem Titel, damit sie nicht doppelt erscheint."""
    return re.sub(r'^\s*[\[(]?\s*(s\+|z\+|f\+|w\+|zeit\+|spiegel\+|welt\+|faz\+|'
                  r'sz\s*plus|zeit\s*plus|spiegel\s*plus|plus)\s*[\])]?\s*[:\-–]?\s*',
                  '', title or '', flags=re.I).strip()

# ── Titel aufwerten ──────────────────────────────────────────
# Manche Quellen liefern Titel ohne Aussage ("Entscheidung vom 12.08.2026",
# "Pressemitteilung Nr. 141/2026") oder haengen die Quelle an
# ("... - Tagesschau"). Beides wird hier bereinigt bzw. um den ersten
# Satz der Beschreibung ergaenzt, damit in der Liste erkennbar ist, worum es geht.
VAGUE_TITLE = re.compile(
    r'^\s*(entscheidung(en)?|beschluss|urteil|pressemitteilung|mitteilung|meldung|dokument|'
    r'tagesordnung|drucksache|plenarprotokoll|nachricht|artikel|newsletter)'
    r'\s*(nr\.?|vom|des|der|zum)?\s*[\d./ -]*\s*$', re.I)

def first_sentence(text, limit=110):
    t = re.sub(r'\s+', ' ', (text or '')).strip()
    if not t: return ''
    m = re.search(r'(.{25,%d}?[.!?])\s' % limit, t)
    out = (m.group(1) if m else t[:limit]).strip(' .;,–-')
    return out

def improve_title(title, desc, source):
    t = (title or '').strip()
    # "Schlagzeile - Quelle" (Google News) → Quelle abschneiden
    t = re.sub(r'\s+[-–—]\s+[^-–—]{2,40}$', '', t) if re.search(r'\s[-–—]\s', t) and len(t) > 45 else t
    if VAGUE_TITLE.match(t) or len(t) < 22:
        add = first_sentence(desc)
        if add and add.lower() not in t.lower():
            t = f"{t.rstrip(':')}: {add}" if t else add
    return t.strip()

def kind_for(source, cat, title):
    t=(title or '').lower(); s=(source or '').lower()
    if re.search(r'plenarprotokoll|protokoll|verbatim|transcript', t): return "transcript"
    if re.search(r'tagesordnung|agenda|sitzungswoche|floor today', t+' '+s): return "agenda"
    if re.search(r'konsultation|consultation|anhörung|call for evidence|have your say', t): return "consultation"
    if re.search(r'^(rede|speech|statement|remarks|erklärung)\b', t): return "speech"
    if re.search(r'pressemitt|press release|presse|hib|presscorner|newsroom', s) or cat in ("ec","ep","council","eu-council","ecb","eca","cjeu","bt-allg","breg","bundesrat","bverfg","bgh","us-whitehouse","us-house","us-senate","us-scotus","us-agency","us-congress"): return "press"
    return "news"
INST_OF_CAT = {"ep":"ep","ep-committee":"ep","ec":"ec","council":"eu-council","eu-council":"eu-council","ecb":"ecb","eca":"eca","cjeu":"cjeu",
               "eu-inst":"eu-agency","eurlex":"eurlex","bt-allg":"bundestag","bt-thema":"bundestag","breg":"bundesregierung",
               "bundesrat":"bundesrat","bverfg":"bverfg","bgh":"bgh","us-house":"us-house","us-senate":"us-senate","us-congress":"us-congress",
               "us-whitehouse":"us-whitehouse","us-scotus":"us-scotus","us-agency":"us-agency"}
def inst_for(cat, source):
    if cat in INST_OF_CAT: return INST_OF_CAT[cat]
    if cat in {c for c,_,_ in LAENDER}: return "landtag-"+cat if (source or '').startswith("Landtag") else "lreg-"+cat
    return ""

def fetch_url(url, timeout=int(os.environ.get("FEED_TIMEOUT", "10"))):
    from urllib.parse import quote
    # ASCII-encode the URL properly (handles Umlaute etc.)
    url_encoded = ''.join(c if ord(c) < 128 else quote(c) for c in url)
    headers={'User-Agent':'Mozilla/5.0 (compatible; Presseschau-Bot/1.0)',
             'Accept':'application/rss+xml,application/xml,text/xml,*/*',
             'Accept-Charset':'utf-8,iso-8859-1;q=0.9,*;q=0.8'}
    try:
        with urlopen(Request(url_encoded,headers=headers),timeout=timeout) as r:
            raw = r.read()
            # Try to detect charset from Content-Type header
            ct = r.headers.get('Content-Type','')
            charset = None
            if 'charset=' in ct.lower():
                charset = ct.lower().split('charset=')[-1].split(';')[0].strip()
            return (raw, charset)
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

# ─────────────────────────────────────────────────────────────
# ARCHIV
# Die laufenden Dateien halten nur wenige Tage – sonst wird der erste
# Seitenaufruf zu langsam. Alles, was aus diesem Fenster faellt, wandert in
# Monatsdateien unter archive/. Die Seite laedt sie nur auf Wunsch nach.
# ─────────────────────────────────────────────────────────────
ARCHIVE_DIR = "archive"
ARCHIVE_ENABLED = os.environ.get("ARCHIVE", "1") not in ("0", "false", "no")

def _monat(iso):
    return (str(iso) or "")[:7] or "unbekannt"

def archiviere(artikel):
    """Legt herausgefallene Artikel in archive/YYYY-MM.json ab (ohne Dubletten)."""
    if not ARCHIVE_ENABLED or not artikel: return 0
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    nach_monat = {}
    for a in artikel:
        m = _monat(a.get("date"))
        if m == "unbekannt": continue
        nach_monat.setdefault(m, []).append(a)
    gesamt = 0
    for monat, neue in nach_monat.items():
        pfad = os.path.join(ARCHIVE_DIR, f"{monat}.json")
        vorhanden = []
        try:
            vorhanden = json.load(open(pfad, encoding="utf-8")).get("articles", [])
        except Exception:
            pass
        bekannt = {a.get("id") for a in vorhanden}
        # Im Archiv nur die Felder, die zum Suchen und Anzeigen noetig sind
        schlank = [{k: a.get(k) for k in
                    ("id", "source", "title", "link", "desc", "date", "topics",
                     "cat", "boost", "cluster", "access", "_set")}
                   for a in neue if a.get("id") not in bekannt]
        if not schlank: continue
        alle = vorhanden + schlank
        alle.sort(key=lambda x: x.get("date") or "", reverse=True)
        _atomic_json(pfad, {"month": monat, "updated": datetime.now(timezone.utc).isoformat(),
                            "count": len(alle), "articles": alle})
        gesamt += len(schlank)
        print(f"  Archiv {monat}: +{len(schlank)} → {len(alle)}")
    if gesamt:
        schreibe_archiv_index()
    return gesamt

def schreibe_archiv_index():
    """Verzeichnis aller Monatsdateien – die Seite weiss so, was es gibt."""
    monate = []
    for name in sorted(os.listdir(ARCHIVE_DIR)):
        if not name.endswith(".json") or name == "index.json": continue
        try:
            d = json.load(open(os.path.join(ARCHIVE_DIR, name), encoding="utf-8"))
            monate.append({"month": d.get("month", name[:-5]), "count": d.get("count", 0),
                           "file": f"{ARCHIVE_DIR}/{name}"})
        except Exception:
            pass
    monate.sort(key=lambda x: x["month"], reverse=True)
    _atomic_json(os.path.join(ARCHIVE_DIR, "index.json"),
                 {"updated": datetime.now(timezone.utc).isoformat(),
                  "months": monate, "total": sum(m["count"] for m in monate)})
    print(f"  Archiv-Index: {len(monate)} Monate, {sum(m['count'] for m in monate)} Artikel")

def _atomic_json(pfad, obj):
    tmp = pfad + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"))
        fh.flush(); os.fsync(fh.fileno())
    os.replace(tmp, pfad)

def merge_rolling(existing, new_articles, days=7, max_count=5000):
    cutoff=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat()
    _rausgefallen=[]
    # Articles without a date: keep only if very recently fetched (grace: 2 days max)
    # by dropping them after they've cycled out — don't keep "date=''" forever
    existing_filtered=[a for a in existing if a.get('date','') >= cutoff]
    # Was aus dem Zeitfenster faellt, wird nicht verworfen, sondern archiviert
    _rausgefallen=[a for a in existing if a.get('date','') and a.get('date','') < cutoff]
    existing_ids={a['id'] for a in new_articles}
    existing_keep=[a for a in existing_filtered if a['id'] not in existing_ids]
    merged=new_articles + existing_keep
    merged.sort(key=lambda a:a.get('date','') or '0000', reverse=True)
    # Auch was ueber die Hoechstzahl hinausgeht, kommt ins Archiv
    if len(merged) > max_count:
        _rausgefallen += merged[max_count:]
    if _rausgefallen:
        try: archiviere(_rausgefallen)
        except Exception as e: print(f"  Archiv-Fehler: {type(e).__name__}: {e}")
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

FAILED_FEEDS = []   # (label, name, url, grund) – wird am Ende zusammengefasst

# ── ZEITBUDGET UND PARALLELE ABRUFE ──────────────────────────────────
# Ueber hundert Feeds nacheinander mit je 15 Sekunden Zeitlimit sprengen das
# Job-Limit: ein einziger haengender Server kostete bisher eine Viertelminute.
# Deshalb mehrere Abrufe gleichzeitig und ein hartes Gesamtbudget. Was nicht
# hineinpasst, holt der naechste Lauf (alle 25 Minuten).
TIME_BUDGET_MIN = int(os.environ.get("NEWS_BUDGET_MIN", "9"))
_START = time.monotonic()
_DEADLINE = _START + TIME_BUDGET_MIN * 60
FEED_WORKERS = int(os.environ.get("FEED_WORKERS", "8"))

# Gestaffeltes Budget: Nachrichten sind zeitkritisch und duerfen NIE ausgesetzt
# werden – sie laufen alle 25 Minuten und muessen aktuell sein. Dokumente
# (Drucksachen, Tagesordnungen, PDF-Downloads) sind es nicht; sie bekommen nur
# die Restzeit und werden bei Bedarf auf den naechsten Lauf verschoben.
ARTIKEL_ANTEIL = float(os.environ.get("NEWS_ARTICLE_SHARE", "0.75"))
_ARTIKEL_DEADLINE = _START + TIME_BUDGET_MIN * 60 * ARTIKEL_ANTEIL

def out_of_time(phase="artikel"):
    """phase='artikel': erst am harten Gesamtlimit stoppen.
       phase='dokumente': schon am Ende des Artikel-Anteils stoppen."""
    if phase == "dokumente":
        return time.monotonic() > _ARTIKEL_DEADLINE
    return time.monotonic() > _DEADLINE

def restzeit():
    return max(0, int(_DEADLINE - time.monotonic()))

def fetch_all(feed_list, topic_rules, label):
    """Ein defekter Feed darf den Lauf nie abbrechen: jeder Schritt ist einzeln
    abgesichert. Die Abrufe laufen parallel, die Auswertung danach der Reihe nach."""
    from concurrent.futures import ThreadPoolExecutor
    all_arts=[]
    ok=fail=0
    if out_of_time():
        print(f"  [{label}] Zeitbudget aufgebraucht – übersprungen, folgt im nächsten Lauf")
        for url,name,cat in feed_list:
            FAILED_FEEDS.append((label,name,url,"Zeitbudget"))
        return all_arts, ok, len(feed_list)

    roh={}
    with ThreadPoolExecutor(max_workers=FEED_WORKERS) as pool:
        auftraege={pool.submit(fetch_url,url):name for url,name,cat in feed_list}
        for fut,name in auftraege.items():
            if out_of_time():
                fut.cancel(); roh.setdefault(name,None); continue
            try:
                roh[name]=fut.result(timeout=max(1,int(_DEADLINE-time.monotonic())))
            except Exception as e:
                roh[name]=None
                print(f"  [{label}] {name}: {type(e).__name__}")

    for url,name,cat in feed_list:
        print(f"  [{label}] {name}...",end=' ',flush=True)
        result=roh.get(name)
        if not result:
            fail+=1
            FAILED_FEEDS.append((label,name,url,"nicht erreichbar"))
            print("FAIL")
            continue
        try:
            arts=parse_feed(result,name,topic_rules)
        except Exception as e:
            fail+=1
            FAILED_FEEDS.append((label,name,url,f"Parser: {type(e).__name__}"))
            print("PARSE ERR")
            continue
        for a in arts:
            a['cat']=cat
            a['kind']=kind_for(name, cat, a.get('title',''))
            a['inst']=inst_for(cat, name)
            if name in EIL_SOURCES:
                a['priority'] = 'eil'
        if not arts:
            FAILED_FEEDS.append((label,name,url,"0 Artikel (Filter oder leerer Feed)"))
        print(f"{len(arts)}")
        all_arts.extend(arts)
        ok+=1
        time.sleep(0.2)
    return all_arts, ok, fail

def report_failed():
    """Ein Block zum Kopieren: welche Feeds nichts geliefert haben und warum."""
    if not FAILED_FEEDS:
        print("\n✅ FEEDS: alle Quellen erreichbar.\n"); return
    grupp = {}
    for f in FAILED_FEEDS:
        name, url, grund = (f + ("", "", ""))[:3] if isinstance(f, (list, tuple)) else (str(f), "", "")
        kurz = str(grund)[:60] or "ohne Ergebnis"
        grupp.setdefault(kurz, []).append((name, url))
    print("\n" + "=" * 68)
    print(f"PROBLEMBERICHT FEEDS · {datetime.now().strftime('%d.%m.%Y %H:%M')} · {len(FAILED_FEEDS)} Quellen")
    print("Zum Kopieren – diese Quellen haben nichts geliefert.")
    print("=" * 68)
    for grund, eintraege in sorted(grupp.items(), key=lambda x: -len(x[1])):
        print(f"\n[{grund}]  ({len(eintraege)}x)")
        for name, url in eintraege[:12]:
            print(f"    {name}")
            if url: print(f"      {url}")
        if len(eintraege) > 12: print(f"    … und {len(eintraege)-12} weitere")
    print("\n" + "=" * 68 + "\n")

def _norm_title(t):
    return re.sub(r'\s+',' ',re.sub(r'[^a-zäöüß0-9 ]',' ',(t or '').lower())).strip()[:60]
def add_clusters(articles):
    """cluster = Zahl verschiedener Quellen mit (fast) gleicher Schlagzeile → Signal für Top-Meldungen."""
    groups={}
    for a in articles:
        k=_norm_title(a.get('title'))
        if len(k)<20: continue
        groups.setdefault(k,set()).add(a.get('source',''))
    for a in articles:
        a['cluster']=len(groups.get(_norm_title(a.get('title')),()))
def save_json(filename, articles, ok, fail, extra=None):
    seen=set()
    deduped=[]
    for a in articles:
        if a['id'] not in seen:
            seen.add(a['id'])
            deduped.append(a)
    add_clusters(deduped)
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
    # ── Bundesrat: Termine (→ Kalender), Drucksachen, Publikationen ──
    ("https://www.bundesrat.de/SiteGlobals/Functions/RSSFeed/RSSGenerator_Event.xml",
     "Bundesrat", "Tagesordnung", "br", False),
    ("https://www.bundesrat.de/SiteGlobals/Functions/RSSFeed/RSSGenerator_Event_Ausschuss.xml",
     "Bundesrat", "Ausschusstermin", "br", False),
    ("https://www.bundesrat.de/SiteGlobals/Functions/RSSFeed/RSSGenerator_PBPrintout.xml",
     "Bundesrat", "Drucksache", "br", False),
    # download=True → PDF wird heruntergeladen und im Repo gespeichert
    # download=False → nur Link wird gespeichert
    ("https://www.bundestag.de/static/appdata/includes/rss/drucksachen.rss",
     "Bundestag", "Drucksache", "bt", False),
    ("https://www.bundestag.de/static/appdata/includes/rss/plenarprotokolle.rss",
     "Bundestag", "Plenarprotokoll", "bt", False),
    ("https://www.bundestag.de/static/appdata/includes/rss/tagesordnungen.rss",
     "Bundestag", "Tagesordnung", "bt", True),
    ("https://www.bundestag.de/static/appdata/includes/rss/wissenschaftlichedienste.rss",
     "Bundestag", "Wissenschaftlicher Dienst", "bt", True),
    ("https://eur-lex.europa.eu/rss/OJ_L_rss.xml",
     "EUR-Lex", "Amtsblatt L (Rechtsakte)", "eurlex", False),
    ("https://eur-lex.europa.eu/rss/OJ_C_rss.xml",
     "EUR-Lex", "Amtsblatt C (Mitteilungen)", "eurlex", False),
]

PDF_DIR = "docs_files"
PDF_KEEP_DAYS = 7
LINK_KEEP_DAYS = 30

def ensure_pdf_dir():
    os.makedirs(PDF_DIR, exist_ok=True)

def cleanup_old_pdfs():
    """Löscht PDFs die älter als PDF_KEEP_DAYS Tage sind."""
    if not os.path.exists(PDF_DIR):
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=PDF_KEEP_DAYS)
    deleted = 0
    for fname in os.listdir(PDF_DIR):
        fpath = os.path.join(PDF_DIR, fname)
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(fpath), tz=timezone.utc)
            if mtime < cutoff:
                os.remove(fpath)
                deleted += 1
        except:
            pass
    if deleted:
        print(f"  → {deleted} alte PDFs gelöscht")

def download_pdf(url, doc_id):
    """Lädt PDF herunter, speichert als docs_files/{id}.pdf. Gibt lokalen Pfad zurück oder None."""
    ensure_pdf_dir()
    fpath = os.path.join(PDF_DIR, f"{doc_id}.pdf")
    if os.path.exists(fpath):
        return fpath  # schon vorhanden
    result = fetch_url(url, timeout=30)
    if not result:
        return None
    data, _ = result
    if b'%PDF' not in data[:20]:
        return None
    try:
        with open(fpath, 'wb') as f:
            f.write(data)
        return fpath
    except:
        return None

def fetch_documents():
    """Dokumente sind nicht zeitkritisch: laeuft nur, wenn nach den Nachrichten
    noch Zeit bleibt. Sonst bleibt documents.json unveraendert und der naechste
    Lauf holt sie."""
    if out_of_time("dokumente"):
        print("── Dokumente: übersprungen, damit die Nachrichten sicher fertig werden ──")
        print(f"   (verbleibende Zeit: {restzeit()} s – Dokumente folgen im nächsten Lauf)")
        FAILED_FEEDS.append(("docs", "Dokumentenabruf", "", "zugunsten der Nachrichten verschoben"))
        return []
    return _fetch_documents_inner()

def _fetch_documents_inner():
    """Holt Dokument-Links, lädt PDFs wo download=True."""
    docs = []
    cleanup_old_pdfs()
    for url, source, doc_type, origin, do_download in DOCUMENT_FEEDS:
        if out_of_time():
            print("  Zeitbudget erschöpft – restliche Dokumentquellen folgen im nächsten Lauf")
            break
        print(f"  [docs] {source} – {doc_type}...", end=' ', flush=True)
        result = fetch_url(url)
        if not result:
            print("FAIL")
            continue
        xml_bytes, http_charset = result
        try:
            xml_head = xml_bytes[:200]
            enc_match = re.search(rb'encoding=["\']([^"\']+)["\']', xml_head)
            xml_declared = enc_match.group(1).decode('ascii','replace').lower() if enc_match else None
            charset = xml_declared or http_charset or 'utf-8'
            charset = {'iso-8859-1':'latin-1','iso8859-1':'latin-1','windows-1252':'cp1252'}.get(charset, charset)
            try:
                text = xml_bytes.decode(charset, errors='replace')
            except (LookupError, UnicodeDecodeError):
                text = xml_bytes.decode('utf-8', errors='replace')
            text = re.sub(r'[^\x09\x0A\x0D\x20-\uD7FF\uE000-\uFFFD]+', '', text)
            root = ET.fromstring(text)
        except:
            print("PARSE ERR")
            continue
        items = root.findall('.//item') or root.findall('.//{http://www.w3.org/2005/Atom}entry')
        count = 0
        downloaded = 0
        for item in items:
            def gi(tag):
                el = item.find(tag)
                return el.text.strip() if el is not None and el.text else ''
            title = gi('title') or gi('{http://www.w3.org/2005/Atom}title')
            if not title or len(title) < 5: continue
            link = gi('link')
            if not link:
                le = item.find('{http://www.w3.org/2005/Atom}link')
                link = le.get('href', '') if le is not None else ''
            if not link: continue
            pub_raw = (gi('pubDate') or gi('{http://www.w3.org/2005/Atom}published') or
                       gi('{http://www.w3.org/2005/Atom}updated') or '')
            pub_iso = parse_date(pub_raw)
            desc_raw = gi('description') or gi('{http://www.w3.org/2005/Atom}summary') or ''
            desc = clean_html(desc_raw)[:400]
            is_pdf = link.lower().endswith('.pdf') or 'pdf' in link.lower()
            uid = hashlib.md5((source + doc_type + title + link).encode()).hexdigest()[:12]
            local_path = None
            if do_download and is_pdf:
                local_path = download_pdf(link.strip(), uid)
                if local_path:
                    downloaded += 1
                time.sleep(0.3)
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
                "local": local_path,
                "has_file": local_path is not None,
            })
            count += 1
        print(f"{count} ({downloaded} PDFs)" if downloaded else str(count))
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

def merge_docs(existing, new_docs):
    """Links LINK_KEEP_DAYS behalten, PDFs durch cleanup_old_pdfs() verwaltet."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=LINK_KEEP_DAYS)).isoformat()
    existing_filtered = [d for d in existing
                         if d.get('date', '') >= cutoff or not d.get('date', '')]
    existing_ids = {d['id'] for d in new_docs}
    existing_keep = [d for d in existing_filtered if d['id'] not in existing_ids]
    # has_file aktualisieren — PDF könnte inzwischen gelöscht sein
    for d in existing_keep:
        if d.get('local'):
            d['has_file'] = os.path.exists(d['local'])
        else:
            d['has_file'] = False
    merged = new_docs + existing_keep
    merged.sort(key=lambda d: d.get('date', '') or '0000', reverse=True)
    return merged

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
        "files_count": sum(1 for d in deduped if d.get('has_file')),
        "documents": deduped,
    }
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    size_kb = os.path.getsize(filename) // 1024
    files = out['files_count']
    print(f"  → {filename}: {len(deduped)} Dokumente ({files} mit Datei), {size_kb}KB")


def _apply_consolidation():
    """Führt die Themen zusammen, sobald alle Regelsätze definiert sind."""
    global TOPIC_RULES, EU_TOPIC_RULES, BT_TOPIC_RULES, US_TOPIC_RULES, _CONSOLIDATED
    if _CONSOLIDATED: return
    TOPIC_RULES    = tune(consolidate(TOPIC_RULES))
    EU_TOPIC_RULES = tune(consolidate(EU_TOPIC_RULES))
    BT_TOPIC_RULES = tune(consolidate(BT_TOPIC_RULES))
    US_TOPIC_RULES = tune(consolidate(US_TOPIC_RULES))
    _CONSOLIDATED = True
    print(f"Themen: {len(TOPIC_RULES)} News · {len(EU_TOPIC_RULES)} EU · "
          f"{len(BT_TOPIC_RULES)} BT/Länder · {len(US_TOPIC_RULES)} USA")

def main():
    _apply_consolidation()
    print(f"[{datetime.now().isoformat()}] Presseschau Fetch "
          f"(Zeitbudget {TIME_BUDGET_MIN} Min, {FEED_WORKERS} parallele Abrufe)")

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

    print("\n── Länder (Landtage & Landesregierungen) ──")
    new_l, ok4, fail4 = fetch_all(LAENDER_FEEDS, BT_TOPIC_RULES, "laender")
    existing_l = load_existing("laender_articles.json")
    merged_l = merge_rolling(existing_l, new_l, days=7, max_count=3000)
    save_json("laender_articles.json", merged_l, ok4, fail4,
              extra={"note":"Landtage + Landesregierungen der 16 Länder (cat = Länderkürzel)"})

    print("\n── USA (Kongress, Weißes Haus, Supreme Court, US-Presse) ──")
    new_us, ok5, fail5 = fetch_all(US_FEEDS, US_TOPIC_RULES, "usa")
    existing_us = load_existing("us_articles.json")
    merged_us = merge_rolling(existing_us, new_us, days=7, max_count=3000)
    save_json("us_articles.json", merged_us, ok5, fail5,
              extra={"note":"US-Institutionen (congress.gov, whitehouse.gov) + US-Politikpresse"})

    print("\n── Dokumente ──")
    new_docs = fetch_documents()
    existing_docs = load_existing_docs("documents.json")
    merged_docs = merge_docs(existing_docs, new_docs)
    save_docs("documents.json", merged_docs)

    print(f"\n✓ News: {ok1} Feeds ok, {fail1} Feeds fehlgeschlagen, {len(merged_news)} Artikel")
    print(f"✓ EU Direkt: {ok2} Feeds ok, {fail2} Feeds fehlgeschlagen, {len(merged_eu)} Artikel")
    print(f"✓ Bundestag: {ok3} Feeds ok, {fail3} Feeds fehlgeschlagen, {len(merged_bt)} Artikel")
    print(f"✓ Länder: {ok4} Feeds ok, {fail4} fehlgeschlagen, {len(merged_l)} Artikel")
    print(f"✓ USA: {ok5} Feeds ok, {fail5} fehlgeschlagen, {len(merged_us)} Artikel")
    print(f"✓ Dokumente: {len(merged_docs)} Dokumente")
    report_failed()

if __name__ == "__main__":
    main()
