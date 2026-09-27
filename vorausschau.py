#!/usr/bin/env python3
"""
vorausschau.py  –  schreibt vorausschau.json (einmal am Tag)

VORAUSSCHAU (FORESIGHT) MIT ZAHLEN STATT KAFFEESATZ
Grundlage ist eine Wissensbasis geprüfter Zusammenhänge
(vorausschau_basis.json – die Datei gehört dir). Die KI erfindet keine
Zusammenhänge; sie prüft nur, ob Meldungen einen Auslöser wirklich treffen,
und schreibt den Wirkungspfad auf den Fall angewandt aus.

WAHRSCHEINLICHKEIT
Für jede Einschätzung mit Messpunkt rechnet das Skript, wie wahrscheinlich
die erwartete Richtung im Prüfzeitraum ist – nachvollziehbar in vier
Schritten:
  1. Grundrate p0: Wie oft lief der Wert in der Jahresreihe über genau diesen
     Zeitraum in diese Richtung? (Ohne Ereignis wäre das die beste Schätzung.)
  2. Verschiebung d in Log-Odds: Stärke des Zusammenhangs laut Literatur
     (stark 0,9 · mittel 0,6 · schwach 0,3) × Belegstärke (Häuser, weltweite
     Berichterstattung) × Sicherheit der Prüfung × Abschlag, wenn der Kurs
     sich schon deutlich bewegt hat (eingepreist).
  3. p = logistisch(logit(p0) + d)
  4. Lernen: Hat derselbe Zusammenhang schon Prüfungen hinter sich, wird p
     mit der beobachteten Trefferquote verrechnet (Beta-Mittel, 8 Pseudo-
     beobachtungen) – das Modell wird mit jeder Prüfung ehrlicher.
Dazu ein Zeitprofil (5, 20, 60 Handelstage) und die übliche Spanne des
Werts im Zeitraum (10.–90. Perzentil der Jahresreihe). In der Bilanz steht
der Brier-Wert des Modells neben dem der bloßen Grundrate: Nur wenn das
Modell besser ist, trägt es.

HYBRIDE BEDROHUNGEN
Eigene Klasse S (Sabotage an Kabeln und Pipelines, Drohnen über Anlagen,
staatlich zugeschriebene Cyberangriffe, GPS-Störungen, Anschläge auf Bahn
und Netze, Desinformation, Migration als Druckmittel). Die KI klassifiziert
Art, Ziel und Stand der Zuschreibung und wählt aus einer Liste möglicher
Folgen – auch solcher ohne Kurs (politisch, sicherheitspolitisch).

QUELLEN
quellen_status.json aus quellen_pruefen.py: Ist die Kursquelle eines
Messpunkts gestört, wird keine Wahrscheinlichkeit gerechnet.

AUSGABE
  vorausschau.json          Einschätzungen, Beobachtung, Vorschläge, Bilanz,
                            Wissensbasis, Literatur, Quellenstand
  vorausschau_archiv.json   alle Einschätzungen mit Startwert, p und Ergebnis
  vorausschau_basis.json    beim ersten Lauf angelegt (übernimmt eine
                            vorhandene wirkungsketten_basis.json)
"""

import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

import ki_zusammenfassungen as ki

OUT = "vorausschau.json"
ARCHIV = "vorausschau_archiv.json"
BASIS = "vorausschau_basis.json"
ALT = {"vorausschau_basis.json": "wirkungsketten_basis.json",
       "vorausschau_archiv.json": "wirkungsketten_archiv.json"}   # Übernahme aus der Vorversion
QUELLEN_STATUS = "quellen_status.json"
BASIS_VERSION = 2
MAX_KETTEN = int(os.environ.get("VS_MAX", "10"))
STUNDE = int(os.environ.get("VS_STUNDE", "7"))
FORCE = os.environ.get("VS_FORCE", "") == "1"
GDELT = os.environ.get("VS_GDELT", "1") == "1"
FENSTER_H = int(os.environ.get("VS_FENSTER_H", "72"))
MIN_HAEUSER = int(os.environ.get("VS_MIN_HAEUSER", "2"))
TIMEOUT = int(os.environ.get("VS_TIMEOUT", "60"))

try:
    from zoneinfo import ZoneInfo
    BERLIN = ZoneInfo("Europe/Berlin")
except Exception:                                   # pragma: no cover
    BERLIN = timezone.utc


# ─────────────────────────────────────────────────────────────
# WISSENSBASIS (Vorgabe; wird nur angelegt, wenn die Datei fehlt)
#
# stichworte  Liste von Mustern; ALLE müssen in Schlagzeile + Vorspann
#             vorkommen, mindestens eines in der Schlagzeile. So trifft E1
#             nur, wenn Öl UND Angriff UND Golfregion zusammen auftauchen.
# ausschluss  Muster, das eine Meldung ausschließt (Rückblick, Übung …)
# messpunkt   {"sym", "richtung": 1 | -1 | "auswahl", "tage": Handelstage}
#             "auswahl" bei der Richtung: die KI wählt + oder − und begründet
#             es (etwa OPEC: Kürzung oder Erhöhung). "wahl" statt "sym": die
#             KI wählt einen der genannten Werte. null = nicht automatisch
#             prüfbar.
# fuehrt_zu   Zusammenhänge, die als Folge zweiter Ordnung genannt werden dürfen
# gdelt       englische Suchanfrage für die weltweite Berichterstattung
# ─────────────────────────────────────────────────────────────
BEREICHE = {"E": "Energie und Geopolitik", "K": "Konflikte und Sicherheit", "H": "Handel und Lieferketten",
            "L": "Landwirtschaft und Lebensmittel", "G": "Geldpolitik und Konjunktur",
            "T": "Technologie, Energiewende, Natur, Gesundheit", "S": "Hybride Bedrohungen"}

# Stärke des Zusammenhangs laut Literatur und Erfahrung – Verschiebung in
# Log-Odds bei voller Belegstärke. Pflegbar je Eintrag über das Feld "effekt".
EFFEKT = {"stark": 0.9, "mittel": 0.6, "schwach": 0.3}
EFFEKT_VORGABE = {"E1": "stark", "E3": "stark", "E4": "mittel", "E5": "mittel", "E6": "stark", "E7": "mittel",
                  "E2": "mittel", "E8": "schwach", "K1": "mittel", "K2": "mittel", "K3": "mittel", "K4": "schwach",
                  "K5": "mittel", "H1": "mittel", "H3": "mittel", "H4": "schwach", "L1": "mittel", "L3": "mittel",
                  "L4": "stark", "L5": "mittel", "L6": "schwach", "G1": "stark", "G2": "stark", "G3": "mittel",
                  "G5": "mittel", "G6": "mittel", "G7": "mittel", "T1": "schwach", "T3": "mittel", "T4": "mittel",
                  "T5": "mittel", "T6": "mittel", "T7": "schwach"}

GOLF = r"saudi|golf|gulf|irak|iraq|iran|kuwait|katar|qatar|emirate|uae|hormus|hormuz|houthi|huthi|jemen|yemen|abqaiq|ras tanura"

DEFAULT_BASIS = [
    # ── Energie und Geopolitik ──
    {"id": "E1", "titel": "Angriff auf Ölinfrastruktur am Golf",
     "ausloeser": "Angriff oder Störung an Ölinfrastruktur am Golf (Pipeline, Raffinerie, Tanker)",
     "stichworte": [r"öl|oil|pipeline|raffinerie|refiner|tanker|aramco|förderanlage|ölfeld|oilfield|ölterminal",
                    r"angriff|attack|anschlag|drohne|drone|rakete|missile|beschuss|explosion|sabotage|getroffen|struck|hit by",
                    GOLF],
     "ausschluss": r"jahrestag|anniversary|vor \d+ jahren|übung|exercise",
     "wirkung": "Ölpreis steigt", "mechanismus": "Angebotsrisiko und Risikoaufschlag am Ölmarkt",
     "zeitraum": "Tage", "messpunkt": {"sym": "BZ=F", "richtung": 1, "tage": 5},
     "gegenkraefte": "freie OPEC-Kapazität, strategische Reserven", "belege": ["kilian2009", "caldara2022"],
     "fuehrt_zu": ["G1"], "gdelt": "(oil OR pipeline OR refinery OR tanker) (attack OR drone OR missile) (Saudi OR Gulf OR Houthi)"},
    {"id": "E2", "titel": "Störung der Schifffahrt auf Hauptrouten",
     "ausloeser": "Störung der Schifffahrt im Roten Meer, am Suezkanal oder in der Straße von Hormus",
     "stichworte": [r"rotes meer|red sea|suez|hormus|hormuz|bab el-mandeb|bab al-mandab",
                    r"schiff|ship|reederei|shipping|container|frachter|tanker|maersk|hapag|route|umleitung|reroute|transit|angriff|attack"],
     "wirkung": "Frachtraten, Lieferzeiten und Ölpreis steigen; später höhere Erzeugerpreise",
     "mechanismus": "Umwege verlängern Transporte und binden Schiffe", "zeitraum": "Tage (Fracht), 1–3 Monate (Preise)",
     "messpunkt": {"sym": "BZ=F", "richtung": 1, "tage": 10},
     "gegenkraefte": "Umleitung ums Kap, Überkapazität der Reedereien", "belege": ["benigno2022"],
     "fuehrt_zu": ["H5"], "gdelt": "(\"Red Sea\" OR Suez OR Hormuz) (shipping OR vessel OR tanker)"},
    {"id": "E3", "titel": "OPEC+ ändert die Förderung",
     "ausloeser": "OPEC+ beschließt Förderkürzung oder -erhöhung",
     "stichworte": [r"opec", r"förder|produktion|production|output|kürz|cut|erhöh|increase|quote|quota"],
     "ausschluss": r"erwartet|expected to|könnte|could|analyst",
     "wirkung": "Ölpreis steigt (Kürzung) bzw. fällt (Erhöhung)", "mechanismus": "Angebot am Weltmarkt ändert sich",
     "zeitraum": "Tage bis Wochen", "messpunkt": {"sym": "BZ=F", "richtung": "auswahl", "tage": 10},
     "gegenkraefte": "Umsetzungstreue der Mitglieder, US-Schieferöl", "belege": ["kilian2009"],
     "fuehrt_zu": [], "gdelt": "OPEC (cut OR increase) (output OR production)"},
    {"id": "E4", "titel": "Sanktionen gegen großen Ölexporteur",
     "ausloeser": "Neue Sanktionen gegen einen großen Ölexporteur",
     "stichworte": [r"sanktion|sanction|embargo|preisobergrenze|price cap",
                    r"öl|oil|rohöl|crude|diesel|tanker|schattenflotte|shadow fleet",
                    r"russland|russia|iran|venezuela|rosneft|lukoil"],
     "wirkung": "Ölpreis und besonders Diesel steigen", "mechanismus": "Angebot wird knapper, Handelsströme werden umgelenkt",
     "zeitraum": "Wochen", "messpunkt": {"sym": "BZ=F", "richtung": 1, "tage": 20},
     "gegenkraefte": "Umgehung über Drittstaaten, Preisobergrenzen", "belege": ["kilian2009"], "fuehrt_zu": [],
     "gdelt": "sanctions (oil OR crude) (Russia OR Iran OR Venezuela)"},
    {"id": "E5", "titel": "Hurrikan trifft Förderung im Golf von Mexiko",
     "ausloeser": "Hurrikan im Golf von Mexiko trifft Förderung oder Raffinerien",
     "stichworte": [r"hurrikan|hurricane|tropensturm|tropical storm",
                    r"golf von mexiko|gulf of mexico|texas|louisiana|raffinerie|refiner|förderung|offshore|oil|öl|gas"],
     "wirkung": "US-Öl und US-Gas steigen kurzfristig", "mechanismus": "Förderung und Raffinerien stehen still",
     "zeitraum": "Tage", "messpunkt": {"sym": "NG=F", "richtung": 1, "tage": 5},
     "gegenkraefte": "schnelle Wiederaufnahme, Lagerbestände", "belege": [], "fuehrt_zu": [],
     "gdelt": "hurricane (\"Gulf of Mexico\" OR refinery OR offshore)"},
    {"id": "E6", "titel": "Ausfall bei der Gasversorgung Europas",
     "ausloeser": "Ausfall bei der Gasversorgung Europas (Pipeline, LNG-Terminal, Wartung in Norwegen)",
     "stichworte": [r"\bgas|lng|flüssiggas",
                    r"ausfall|störung|outage|lieferstopp|stopp|wartung|maintenance|leck|leak|sabotage|abgeschaltet|shut",
                    r"europa|europe|deutschland|germany|norwegen|norway|pipeline|terminal|speicher|storage"],
     "wirkung": "Gaspreis steigt, Strompreis folgt; energieintensive Industrie drosselt",
     "mechanismus": "Gas setzt in Europa oft den Strompreis", "zeitraum": "Tage (Preise), Monate (Produktion)",
     "messpunkt": {"sym": "TTF=F", "richtung": 1, "tage": 5},
     "gegenkraefte": "Speicherfüllstand, LNG-Zufuhr", "belege": ["bachmann2022"], "fuehrt_zu": ["G1"],
     "gdelt": "(gas OR LNG) (outage OR disruption OR maintenance) (Europe OR Norway)"},
    {"id": "E7", "titel": "Kälte- oder Hitzewelle in Europa",
     "ausloeser": "Kälte- oder Hitzewelle in Europa",
     "stichworte": [r"kältewelle|cold snap|cold wave|dauerfrost|hitzewelle|heatwave|heat wave|rekordhitze",
                    r"europa|europe|deutschland|germany|frankreich|france|italien|italy"],
     "wirkung": "Gas- und Strompreise steigen", "mechanismus": "mehr Heizen oder Kühlen, weniger Kühlwasser für Kraftwerke",
     "zeitraum": "Tage bis Wochen", "messpunkt": {"sym": "TTF=F", "richtung": 1, "tage": 7},
     "gegenkraefte": "Wind- und Solarertrag, Speicher", "belege": ["dell2014"], "fuehrt_zu": [],
     "gdelt": "(heatwave OR \"cold snap\") Europe (gas OR power OR electricity)"},
    {"id": "E8", "titel": "Niedrigwasser am Rhein",
     "ausloeser": "Niedrigwasser am Rhein", "stichworte": [r"niedrigwasser|low water|pegel|kaub", r"rhein|rhine"],
     "wirkung": "Transportkosten steigen, Chemie- und Stahlwerke drosseln",
     "mechanismus": "Schiffe können nur Teilladungen fahren", "zeitraum": "Wochen",
     "messpunkt": {"sym": "EXV7.DE", "richtung": -1, "tage": 15},
     "gegenkraefte": "Regen, Umstieg auf die Schiene", "belege": [], "fuehrt_zu": [],
     "gdelt": "Rhine (\"low water\" OR drought) shipping"},
    # ── Konflikte und Sicherheit ──
    {"id": "K1", "titel": "Militärische Eskalation",
     "ausloeser": "Militärische Eskalation in Europa oder Nahost",
     "stichworte": [r"eskalation|escalat|offensive|luftangriff|airstrike|invasion|angriffswelle|großangriff",
                    r"ukraine|russland|russia|israel|iran|libanon|lebanon|gaza|nato|baltikum|baltic|polen|poland",
                    r"militär|military|armee|army|streitkräfte|forces|raketen|missiles|truppen|troops|drohnen|drones"],
     "wirkung": "Rüstungswerte und Gold steigen, Renditen sicherer Staatsanleihen fallen",
     "mechanismus": "Flucht in sichere Häfen, Erwartung höherer Verteidigungsausgaben", "zeitraum": "Tage",
     "messpunkt": {"sym": "GC=F", "richtung": 1, "tage": 5},
     "gegenkraefte": "schnelle Deeskalation", "belege": ["caldara2022"], "fuehrt_zu": ["K2", "E1"],
     "gdelt": "(escalation OR offensive OR airstrike) (military OR troops)"},
    {"id": "K2", "titel": "Höhere Verteidigungsausgaben beschlossen",
     "ausloeser": "Beschluss höherer Verteidigungsausgaben (NATO-Ziel, Sondervermögen)",
     "stichworte": [r"verteidigungs|defen[cs]e|rüstung|bundeswehr|nato",
                    r"ausgaben|spending|budget|sondervermögen|milliarden|billion|prozent|percent|ziel|target",
                    r"beschl|approv|verabschied|einig|agree|zusag|pledge|erhöh|increase"],
     "wirkung": "Rüstungswerte steigen; Bundrenditen steigen bei mehr Schulden",
     "mechanismus": "mehr Aufträge, mehr Staatsanleihen", "zeitraum": "Wochen bis Monate",
     "messpunkt": {"sym": "DFEN.DE", "richtung": 1, "tage": 20},
     "gegenkraefte": "Umsetzungstempo, Kapazitätsgrenzen der Industrie", "belege": [], "fuehrt_zu": ["G3"],
     "gdelt": "(defence OR defense) spending (approve OR pledge OR increase)"},
    {"id": "K3", "titel": "Unruhen in einer Förder- oder Anbauregion",
     "ausloeser": "Unruhen oder Bürgerkrieg in einer Förder- oder Anbauregion",
     "stichworte": [r"unruhen|unrest|bürgerkrieg|civil war|kämpfe|fighting|putsch|coup|rebellen|rebels|miliz|militia",
                    r"sudan|libyen|libya|nigeria|elfenbeinküste|ivory coast|côte d.ivoire|ghana|kongo|congo|niger\b|mali\b|venezuela"],
     "wirkung": "Preis des dort gewonnenen Rohstoffs steigt",
     "mechanismus": "Förderung oder Ernte und Ausfuhr werden gestört", "zeitraum": "Wochen",
     "messpunkt": {"wahl": ["BZ=F", "GC=F", "CC=F"], "richtung": 1, "tage": 15},
     "gegenkraefte": "kleiner Weltmarktanteil der Region", "belege": [], "fuehrt_zu": ["L3"],
     "gdelt": "(unrest OR \"civil war\" OR fighting) (Sudan OR Libya OR Nigeria OR Ghana OR \"Ivory Coast\")"},
    {"id": "K4", "titel": "Großer Cyberangriff",
     "ausloeser": "Großer Cyberangriff auf ein Unternehmen oder kritische Infrastruktur",
     "stichworte": [r"cyberangriff|cyber-angriff|cyberattack|cyber attack|hackerangriff|ransomware",
                    r"konzern|unternehmen|company|infrastruktur|infrastructure|krankenhaus|hospital|flughafen|airport|bank|netz|grid|pipeline|behörde"],
     "wirkung": "Cybersicherheitswerte steigen", "mechanismus": "Nachfrage nach Schutz steigt",
     "zeitraum": "Tage", "messpunkt": {"sym": "HACK", "richtung": 1, "tage": 5},
     "gegenkraefte": "schnelle Wiederherstellung", "belege": [], "fuehrt_zu": [],
     "gdelt": "(cyberattack OR ransomware) (infrastructure OR company OR hospital)"},
    {"id": "K5", "titel": "Spannungen in der Taiwanstraße",
     "ausloeser": "Spannungen oder Blockade in der Taiwanstraße",
     "stichworte": [r"taiwan", r"blockade|manöver|drills|militär|military|spannung|tension|invasion|kriegsschiff|warship",
                    r"china|peking|beijing|pla\b|volksbefreiungsarmee"],
     "wirkung": "Halbleiterwerte fallen und schwanken stärker", "mechanismus": "Risiko für die wichtigste Chipfertigung der Welt",
     "zeitraum": "Tage bis Wochen", "messpunkt": {"sym": "SMH", "richtung": -1, "tage": 10},
     "gegenkraefte": "diplomatische Signale", "belege": [], "fuehrt_zu": ["H4"],
     "gdelt": "Taiwan (blockade OR drills OR warships) China"},
    # ── Handel und Lieferketten ──
    {"id": "H1", "titel": "Neue Zölle",
     "ausloeser": "Neue Zölle angekündigt (USA, EU, China)",
     "stichworte": [r"zoll|zölle|tariff|strafzoll|einfuhrabgabe|import dut",
                    r"ankündig|announce|verhäng|impose|erhöh|raise|droht|threat|beschl|verkünd"],
     "ausschluss": r"zollstock|zollamt|zollfahnd",
     "wirkung": "Betroffene Branchen und Indizes fallen, Einfuhrpreise steigen",
     "mechanismus": "höhere Kosten, Unsicherheit über Absatzmärkte", "zeitraum": "Tage (Kurse), Monate (Preise)",
     "messpunkt": {"wahl": ["^GDAXI", "^STOXX50E", "^GSPC", "^HSI", "EXV5.DE"], "richtung": -1, "tage": 5},
     "gegenkraefte": "Ausnahmen, Verhandlungen", "belege": ["amiti2019", "baker2016"], "fuehrt_zu": ["G1"],
     "gdelt": "tariffs (announce OR impose OR raise)"},
    {"id": "H2", "titel": "Exportkontrollen für Chips",
     "ausloeser": "Exportkontrollen für Chips oder Chipmaschinen",
     "stichworte": [r"exportkontroll|export control|ausfuhrbeschränkung|export ban|exportverbot|entity list",
                    r"chip|halbleiter|semiconductor|asml|nvidia|lithograph|ki-chip|ai chip"],
     "wirkung": "Halbleiterwerte gespalten: Ausrüster unter Druck, heimische Anbieter profitieren",
     "mechanismus": "Absatzmärkte fallen weg, Konkurrenz wird geschützt", "zeitraum": "Tage bis Monate",
     "messpunkt": None, "gegenkraefte": "Lizenzen, Umgehung", "belege": [], "fuehrt_zu": ["H4"],
     "gdelt": "\"export controls\" (chips OR semiconductors)"},
    {"id": "H3", "titel": "Exportstopp für kritische Rohstoffe",
     "ausloeser": "Exportstopp eines Förderlandes für kritische Rohstoffe",
     "stichworte": [r"exportstopp|exportverbot|export ban|export restriction|ausfuhrbeschränkung|exportkontroll|export control|lizenzpflicht",
                    r"gallium|germanium|seltene erden|rare earth|graphit|graphite|antimon|antimony|kobalt|cobalt|magnet"],
     "wirkung": "Preis des Rohstoffs steigt, Nutzerbranchen geraten unter Druck",
     "mechanismus": "wenige Förderländer, kaum Ersatz", "zeitraum": "Wochen bis Monate",
     "messpunkt": {"sym": "REMX", "richtung": 1, "tage": 20},
     "gegenkraefte": "Lagerbestände, andere Förderländer", "belege": [], "fuehrt_zu": ["H4"],
     "gdelt": "(\"export ban\" OR \"export controls\") (\"rare earths\" OR gallium OR germanium OR graphite)"},
    {"id": "H4", "titel": "Chipknappheit",
     "ausloeser": "Chipknappheit, lange Lieferzeiten",
     "stichworte": [r"chipknappheit|chipmangel|chip shortage|halbleitermangel|semiconductor shortage|engpass bei chips|lieferengpässe bei chips",
                    r"chip|halbleiter|semiconductor"],
     "wirkung": "Halbleiterumsätze steigen, Autoproduktion sinkt", "mechanismus": "Knappheit hebt Preise, Autobauer fehlen Bauteile",
     "zeitraum": "Monate", "messpunkt": {"sym": "EXV5.DE", "richtung": -1, "tage": 40},
     "gegenkraefte": "schwache Nachfrage", "belege": [], "fuehrt_zu": [],
     "gdelt": "\"chip shortage\" OR \"semiconductor shortage\""},
    {"id": "H5", "titel": "Fabrikschließungen in Asien",
     "ausloeser": "Fabrikschließungen oder Lockdowns in Asien",
     "stichworte": [r"lockdown|fabrik|factory|werk\b|werke|plant|produktion|production",
                    r"geschlossen|schließ|stillstand|shut|halt|stopp|closure|stillgelegt",
                    r"china|shanghai|shenzhen|vietnam|taiwan|korea|japan|asien|asia|malaysia|indien|india"],
     "wirkung": "Lieferkettendruck steigt, Erzeugerpreise in Europa folgen",
     "mechanismus": "Vorprodukte fehlen, Lieferzeiten verlängern sich", "zeitraum": "2–6 Monate",
     "messpunkt": None, "gegenkraefte": "Lagerpuffer", "belege": ["benigno2022"], "fuehrt_zu": ["G1"],
     "gdelt": "(factory OR plant) (shutdown OR closure OR lockdown) (China OR Vietnam OR Asia)"},
    {"id": "H6", "titel": "Hafenstreik",
     "ausloeser": "Hafenstreik (US-Ostküste, Rotterdam, Hamburg)",
     "stichworte": [r"streik|strike|arbeitskampf|walkout", r"hafen|häfen|\bport|dock|hafenarbeiter|longshore"],
     "wirkung": "Frachtraten und Lieferzeiten steigen", "mechanismus": "Umschlag stockt, Schiffe warten",
     "zeitraum": "Wochen", "messpunkt": None, "gegenkraefte": "kurze Streikdauer", "belege": [], "fuehrt_zu": ["H5"],
     "gdelt": "port strike (dockworkers OR longshore)"},
    # ── Landwirtschaft und Lebensmittel ──
    {"id": "L1", "titel": "Missernte bei Weizen",
     "ausloeser": "Dürre oder Missernte in einem großen Weizen-Exportland",
     "stichworte": [r"weizen|wheat|getreide|grain",
                    r"dürre|drought|missernte|ernteausfall|crop failure|ernte|harvest|frost|hitze|heat",
                    r"russland|russia|ukraine|usa\b|australien|australia|kanada|canada|argentinien|argentina|indien|india|kasachstan|kazakhstan"],
     "wirkung": "Weizenpreis steigt; Mehl und Brot werden später teurer",
     "mechanismus": "Angebot am Weltmarkt sinkt, Preise werden entlang der Kette weitergegeben",
     "zeitraum": "Wochen (Weltmarkt), 3–9 Monate (Verbraucherpreise)",
     "messpunkt": {"sym": "ZW=F", "richtung": 1, "tage": 20},
     "gegenkraefte": "Lager, gute Ernten anderswo", "belege": ["wright2011", "ferrucci2012"], "fuehrt_zu": ["G1"],
     "gdelt": "wheat (drought OR \"crop failure\" OR harvest)"},
    {"id": "L2", "titel": "Schlechte Kartoffelernte",
     "ausloeser": "Schlechte Kartoffelernte in Europa (Nässe, Dürre)",
     "stichworte": [r"kartoffel|potato|pommes|fries", r"ernte|harvest|missernte|nässe|dürre|drought|regen|rain|fäule|blight"],
     "wirkung": "Erzeugerpreise für Kartoffeln steigen; Pommes und Chips werden später teurer",
     "mechanismus": "knappe Ware, verarbeitende Industrie zahlt mehr", "zeitraum": "Monate bis ein Jahr",
     "messpunkt": None, "gegenkraefte": "Importe, Lagerware", "belege": ["ferrucci2012"], "fuehrt_zu": ["G1"],
     "gdelt": "potato (harvest OR drought OR blight) Europe"},
    {"id": "L3", "titel": "Schwache Kakaoernte",
     "ausloeser": "Schwache Kakaoernte in Westafrika",
     "stichworte": [r"kakao|cocoa", r"ernte|harvest|wetter|weather|krankheit|disease|swollen shoot|dürre|drought|regen|rain"],
     "wirkung": "Kakaopreis steigt; Schokolade wird 6–12 Monate später teurer",
     "mechanismus": "zwei Länder liefern den Großteil der Welternte", "zeitraum": "Wochen (Rohstoff), Monate (Verbraucher)",
     "messpunkt": {"sym": "CC=F", "richtung": 1, "tage": 20},
     "gegenkraefte": "Nachfragerückgang, geänderte Rezepturen", "belege": ["ferrucci2012"], "fuehrt_zu": [],
     "gdelt": "cocoa (harvest OR crop) (\"Ivory Coast\" OR Ghana)"},
    {"id": "L4", "titel": "Frost oder Dürre in Brasilien",
     "ausloeser": "Frost oder Dürre in den Kaffeeanbaugebieten Brasiliens",
     "stichworte": [r"kaffee|coffee|arabica|robusta", r"frost|dürre|drought|ernte|harvest", r"brasilien|brazil|minas gerais"],
     "wirkung": "Kaffeepreis steigt; Verbraucherpreise folgen", "mechanismus": "größtes Anbauland fällt teilweise aus",
     "zeitraum": "Tage (Rohstoff), Monate (Verbraucher)", "messpunkt": {"sym": "KC=F", "richtung": 1, "tage": 10},
     "gegenkraefte": "Ernte in Vietnam", "belege": [], "fuehrt_zu": [],
     "gdelt": "coffee (frost OR drought) Brazil"},
    {"id": "L5", "titel": "Exportverbot für Nahrungsmittel",
     "ausloeser": "Exportverbot für Nahrungsmittel (Reis, Weizen, Zucker)",
     "stichworte": [r"exportverbot|exportstopp|export ban|ausfuhrverbot|export restriction",
                    r"reis\b|rice|weizen|wheat|zucker|sugar|speiseöl|palmöl|palm oil|getreide|grain"],
     "wirkung": "Weltmarktpreis steigt", "mechanismus": "Angebot am Weltmarkt fällt weg, andere Länder horten",
     "zeitraum": "Wochen", "messpunkt": {"wahl": ["ZW=F", "SB=F", "ZC=F"], "richtung": 1, "tage": 10},
     "gegenkraefte": "Aufhebung, Umgehung", "belege": ["headey2008"], "fuehrt_zu": ["G1"],
     "gdelt": "\"export ban\" (rice OR wheat OR sugar)"},
    {"id": "L6", "titel": "El Niño oder La Niña ausgerufen",
     "ausloeser": "Ausrufung von El Niño oder La Niña",
     "stichworte": [r"el niño|el nino|la niña|la nina"],
     "wirkung": "Ernteerwartungen in Asien und Südamerika sinken (Zucker, Kaffee, Palmöl)",
     "mechanismus": "Dürre und Starkregen in wichtigen Anbaugebieten", "zeitraum": "Monate",
     "messpunkt": {"sym": "SB=F", "richtung": 1, "tage": 30},
     "gegenkraefte": "Stärke des Phänomens", "belege": ["dell2014"], "fuehrt_zu": ["L4"],
     "gdelt": "\"El Nino\" OR \"La Nina\""},
    {"id": "L7", "titel": "Tierseuche",
     "ausloeser": "Ausbruch von Vogelgrippe oder Schweinepest",
     "stichworte": [r"vogelgrippe|bird flu|avian flu|h5n1|schweinepest|swine fever|maul- und klauenseuche|foot-and-mouth"],
     "wirkung": "Eierpreise steigen; bei Schweinepest Exportstopp, Erzeugerpreise im Inland fallen",
     "mechanismus": "Bestände werden gekeult, Ausfuhren gesperrt", "zeitraum": "Wochen",
     "messpunkt": None, "gegenkraefte": "schnelle Eindämmung", "belege": [], "fuehrt_zu": [],
     "gdelt": "(\"bird flu\" OR \"swine fever\") outbreak"},
    # ── Geldpolitik und Konjunktur ──
    {"id": "G1", "titel": "Inflation über der Erwartung",
     "ausloeser": "Inflationsdaten über der Erwartung",
     "stichworte": [r"inflation|teuerung|verbraucherpreise|consumer prices|\bcpi\b|hicp",
                    r"höher als erwartet|stärker als erwartet|unerwartet|überraschend|beschleunigt|higher than expected|above expectations|unexpected|accelerat"],
     "wirkung": "Renditen steigen, Aktien fallen, Währung wird stärker",
     "mechanismus": "Markt erwartet strengere Geldpolitik", "zeitraum": "Stunden bis Tage",
     "messpunkt": {"sym": "DE10Y", "richtung": 1, "tage": 3},
     "gegenkraefte": "schwächere Kerninflation", "belege": ["kuttner2001"], "fuehrt_zu": ["G2"],
     "gdelt": "inflation \"higher than expected\""},
    {"id": "G2", "titel": "Überraschung bei Notenbank",
     "ausloeser": "Zinsentscheid oder -signal von EZB oder Fed weicht von der Erwartung ab",
     "stichworte": [r"ezb|ecb|\bfed\b|federal reserve|notenbank|zentralbank|central bank",
                    r"zins|leitzins|interest rate|rate hike|rate cut|zinsschritt|zinssenkung|zinserhöhung"],
     "wirkung": "Renditen, Wechselkurs und Gold bewegen sich in Richtung der Überraschung",
     "mechanismus": "nur die Überraschung bewegt die Kurse, das Erwartete ist eingepreist", "zeitraum": "Stunden bis Tage",
     "messpunkt": {"wahl": ["DE10Y", "^TNX", "EURUSD=X"], "richtung": "auswahl", "tage": 3},
     "gegenkraefte": "schon eingepreist", "belege": ["kuttner2001", "bernanke2005"], "fuehrt_zu": [],
     "gdelt": "(ECB OR \"Federal Reserve\") (\"rate hike\" OR \"rate cut\" OR \"interest rates\")"},
    {"id": "G3", "titel": "Großes staatliches Ausgabenpaket",
     "ausloeser": "Großes staatliches Ausgabenpaket, Lockerung der Schuldenregel",
     "stichworte": [r"schuldenbremse|debt brake|sondervermögen|konjunkturpaket|stimulus|investitionspaket|ausgabenpaket",
                    r"milliarden|billion|beschl|approv|verabschied|einig|agree|lockerung|reform"],
     "wirkung": "Renditen steigen; Bau- und Rüstungswerte steigen",
     "mechanismus": "mehr Staatsanleihen, mehr Aufträge", "zeitraum": "Tage bis Wochen",
     "messpunkt": {"sym": "DE10Y", "richtung": 1, "tage": 10},
     "gegenkraefte": "Zweifel an der Umsetzung", "belege": [], "fuehrt_zu": [],
     "gdelt": "(\"debt brake\" OR stimulus) Germany billion"},
    {"id": "G4", "titel": "Herabstufung eines Staates",
     "ausloeser": "Herabstufung der Bonität eines Staates",
     "stichworte": [r"herabgestuft|herabstufung|downgrade|abgestuft", r"moody|s&p|fitch|scope|dbrs|bonität|kreditwürdigkeit|rating"],
     "wirkung": "Rendite des Landes und Abstand zu Bundesanleihen steigen",
     "mechanismus": "Investoren verlangen mehr Risikoaufschlag", "zeitraum": "Tage",
     "messpunkt": None, "gegenkraefte": "Herabstufung war erwartet", "belege": [], "fuehrt_zu": ["G7"],
     "gdelt": "downgrade (Moody's OR Fitch OR \"S&P\") sovereign"},
    {"id": "G5", "titel": "Überraschung am US-Arbeitsmarkt",
     "ausloeser": "US-Arbeitsmarktdaten deutlich stärker oder schwächer als erwartet",
     "stichworte": [r"arbeitsmarkt|jobs report|payrolls|nonfarm|arbeitslosenquote|unemployment rate|stellenaufbau",
                    r"usa|\bus\b|amerika|united states|u\.s\."],
     "wirkung": "US-Renditen und Dollar steigen bzw. fallen", "mechanismus": "Erwartung an die Fed ändert sich",
     "zeitraum": "Stunden", "messpunkt": {"sym": "^TNX", "richtung": "auswahl", "tage": 3},
     "gegenkraefte": "spätere Revisionen", "belege": ["kuttner2001"], "fuehrt_zu": ["G2"],
     "gdelt": "\"jobs report\" OR payrolls"},
    {"id": "G6", "titel": "Konjunkturimpuls aus China",
     "ausloeser": "Konjunkturpaket oder starke Daten aus China",
     "stichworte": [r"china|chinas|peking|beijing",
                    r"konjunkturpaket|stimulus|konjunkturprogramm|wachstum übertrifft|stärker als erwartet|stronger than expected|beats expectations"],
     "wirkung": "Kupfer und Rohstoffwerte steigen; exportstarke DAX-Werte profitieren",
     "mechanismus": "China ist größter Rohstoffabnehmer", "zeitraum": "Tage bis Wochen",
     "messpunkt": {"sym": "HG=F", "richtung": 1, "tage": 10},
     "gegenkraefte": "Immobilienkrise in China", "belege": [], "fuehrt_zu": [],
     "gdelt": "China stimulus (economy OR growth)"},
    {"id": "G7", "titel": "Regierungskrise in großem Euroland",
     "ausloeser": "Regierungskrise oder Neuwahl in einem großen Euroland",
     "stichworte": [r"regierungskrise|koalitionskrise|misstrauensvotum|no-confidence|vertrauensfrage|neuwahl|snap election|regierung gestürzt|government collapse",
                    r"frankreich|france|italien|italy|spanien|spain|niederlande|netherlands|belgien|belgium|deutschland|germany"],
     "wirkung": "Renditeabstand steigt, Euro wird schwächer", "mechanismus": "politische Unsicherheit erhöht Risikoaufschläge",
     "zeitraum": "Tage", "messpunkt": {"sym": "EURUSD=X", "richtung": -1, "tage": 5},
     "gegenkraefte": "Stabilisierungsinstrumente der EZB", "belege": ["baker2016"], "fuehrt_zu": [],
     "gdelt": "(\"no-confidence\" OR \"snap election\" OR \"government collapse\") (France OR Italy OR Spain)"},
    # ── Technologie, Energiewende, Natur, Gesundheit ──
    {"id": "T1", "titel": "Milliarden für KI-Rechenzentren",
     "ausloeser": "Große Investitionen in KI-Rechenzentren angekündigt",
     "stichworte": [r"rechenzentr|data center|datacenter|data centre|ki-infrastruktur|ai infrastructure|ki-gigafabrik",
                    r"milliarden|billion|investi|invest"],
     "wirkung": "Halbleiterwerte steigen; höherer Strombedarf lässt Strompreise und Kupfer steigen",
     "mechanismus": "Nachfrage nach Chips, Strom und Leitungen", "zeitraum": "Monate",
     "messpunkt": {"sym": "SMH", "richtung": 1, "tage": 30},
     "gegenkraefte": "Effizienzgewinne der Chips", "belege": [], "fuehrt_zu": [],
     "gdelt": "(\"data center\" OR \"data centre\") AI billion investment"},
    {"id": "T2", "titel": "Strafen für große Plattformen",
     "ausloeser": "Strafen oder Auflagen für große Plattformen",
     "stichworte": [r"digital markets act|\bdma\b|digital services act|\bdsa\b|kartell|antitrust|wettbewerbsverfahren",
                    r"strafe|fine|buße|bußgeld|auflage|verfahren|probe|investigation|verstoß|breach",
                    r"google|alphabet|apple|meta\b|amazon|microsoft|tiktok|bytedance|plattform|platform"],
     "wirkung": "Aktie des betroffenen Unternehmens fällt", "mechanismus": "Strafe und Auflagen belasten das Geschäftsmodell",
     "zeitraum": "Tage", "messpunkt": None, "gegenkraefte": "geringe Strafhöhe im Verhältnis zum Umsatz",
     "belege": [], "fuehrt_zu": [], "gdelt": "(\"Digital Markets Act\" OR antitrust) fine (Google OR Apple OR Meta OR Amazon)"},
    {"id": "T3", "titel": "Förderung erneuerbarer Energien geändert",
     "ausloeser": "Änderung bei der Förderung erneuerbarer Energien",
     "stichworte": [r"erneuerbar|renewable|solar|photovoltaik|windkraft|wind power|offshore-wind|\beeg\b",
                    r"förderung|subvention|subsid|tax credit|einspeisevergütung|ausschreibung|auction",
                    r"kürz|streich|cut|stopp|erhöh|extend|reform|änder|abschaff"],
     "wirkung": "Clean-Energy-Werte steigen bzw. fallen", "mechanismus": "Ertragsaussichten neuer Anlagen ändern sich",
     "zeitraum": "Tage bis Wochen", "messpunkt": {"sym": "INRG.L", "richtung": "auswahl", "tage": 10},
     "gegenkraefte": "Zinsniveau", "belege": [], "fuehrt_zu": [],
     "gdelt": "renewable (subsidies OR \"tax credits\") (cut OR extend)"},
    {"id": "T4", "titel": "Reform des CO2-Preises",
     "ausloeser": "Reform des CO2-Preises (EU-Emissionshandel)",
     "stichworte": [r"emissionshandel|emissions trading|\bets\b|co2-preis|co2 price|carbon price|marktstabilitätsreserve|cbam",
                    r"reform|änder|beschl|einig|agree|vorschlag|proposal|verschärf|tighten|verschieb|delay"],
     "wirkung": "CO2-Preis steigt oder fällt; Strompreis folgt", "mechanismus": "Angebot an Zertifikaten ändert sich",
     "zeitraum": "Wochen", "messpunkt": {"sym": "EUA", "richtung": "auswahl", "tage": 15},
     "gegenkraefte": "Konjunkturlage", "belege": [], "fuehrt_zu": [],
     "gdelt": "(\"emissions trading\" OR \"carbon price\") EU reform"},
    {"id": "T5", "titel": "Krankheitsausbruch mit Reisebeschränkungen",
     "ausloeser": "Neuer Krankheitsausbruch mit Reisebeschränkungen",
     "stichworte": [r"ausbruch|outbreak|epidemie|epidemic|pandemie|pandemic|virus",
                    r"reisebeschränk|travel restriction|quarantäne|quarantine|grenzschließ|gesundheitsnotstand|health emergency|notlage"],
     "wirkung": "Reise- und Airlinewerte fallen, Pharma steigt, Ölpreis fällt",
     "mechanismus": "Reisen und Nachfrage brechen ein", "zeitraum": "Tage bis Wochen",
     "messpunkt": {"sym": "JETS", "richtung": -1, "tage": 10},
     "gegenkraefte": "Ausbruch bleibt begrenzt", "belege": [], "fuehrt_zu": [],
     "gdelt": "outbreak (\"travel restrictions\" OR quarantine OR \"health emergency\")"},
    {"id": "T6", "titel": "Naturkatastrophe in Industriezentrum",
     "ausloeser": "Erdbeben oder Flut in einem Industriezentrum (Taiwan, Japan)",
     "stichworte": [r"erdbeben|earthquake|tsunami|überschwemmung|flood|taifun|typhoon",
                    r"taiwan|japan|korea|südkorea|thailand|malaysia"],
     "wirkung": "Halbleiter- und Autoproduktion gestört", "mechanismus": "Fabriken stehen still, Lieferketten reißen",
     "zeitraum": "Tage bis Wochen", "messpunkt": {"sym": "SMH", "richtung": -1, "tage": 5},
     "gegenkraefte": "Ausweichkapazität", "belege": ["dell2014"], "fuehrt_zu": ["H4"],
     "gdelt": "(earthquake OR typhoon OR flood) (Taiwan OR Japan) (factory OR semiconductor)"},
    {"id": "T7", "titel": "Großschäden durch Unwetter und Brände",
     "ausloeser": "Waldbrände, Hitze oder Unwetter mit großen Schäden",
     "stichworte": [r"waldbrand|wildfire|unwetter|hagel|hail|flut|flood|sturm|storm|hurrikan|hurricane",
                    r"schäden|damage|versicher|insur|milliarden|billion|verwüst|devastat"],
     "wirkung": "Versicherer fallen", "mechanismus": "hohe Schadenszahlungen", "zeitraum": "Tage bis Wochen",
     "messpunkt": {"sym": "EXH5.DE", "richtung": -1, "tage": 10},
     "gegenkraefte": "Rückversicherung", "belege": ["dell2014"], "fuehrt_zu": [],
     "gdelt": "(wildfire OR storm OR flood) (damage OR insured losses) billion"},
    # ── Hybride Bedrohungen ──
    # folgen_offen: mögliche Folgen, auch ohne Kurs. Die KI wählt daraus aus und
    # begründet es mit den Meldungen; eigene Folgen darf sie nicht erfinden.
    {"id": "S1", "titel": "Sabotage an Unterseekabeln oder Pipelines",
     "ausloeser": "Beschädigung oder Sabotage an Unterseekabeln oder Pipelines in Nord- oder Ostsee",
     "stichworte": [r"kabel|cable|pipeline|leitung|unterseekabel|subsea|undersea|balticconnector",
                    r"beschädig|damage|durchtrennt|severed|sabotage|anker|anchor|leck|leak|ausfall|\bcut\b",
                    r"ostsee|baltic|nordsee|north sea|finnland|finland|estland|estonia|schweden|sweden|norwegen|norway|gotland|bornholm|lettland|latvia|litauen|lithuania"],
     "wirkung": "Gaspreis steigt bei Pipelineschäden; Sicherheitspolitik reagiert",
     "mechanismus": "Leitungen fallen aus, Wiederherstellung dauert Wochen", "zeitraum": "Tage bis Wochen",
     "messpunkt": {"wahl": ["TTF=F", "DFEN.DE"], "richtung": 1, "tage": 5}, "effekt": "mittel",
     "gegenkraefte": "Umleitung über andere Leitungen, schnelle Reparatur", "belege": ["caldara2022"], "fuehrt_zu": ["E6", "K2"],
     "folgen_offen": ["Mehr Marinepräsenz und Überwachung kritischer Unterwasserinfrastruktur",
                      "Kontrolle oder Festsetzung verdächtiger Schiffe (Schattenflotte)",
                      "Debatte über Schutz und Redundanz von Daten- und Energieleitungen",
                      "Höhere Versicherungsprämien für die Schifffahrt in der Region",
                      "Diplomatische Spannungen und mögliche Sanktionen"],
     "gdelt": "(\"undersea cable\" OR pipeline) (damage OR sabotage) (Baltic OR \"North Sea\")"},
    {"id": "S2", "titel": "Drohnen über Flughäfen, Kasernen oder Industrie",
     "ausloeser": "Drohnensichtungen über Flughäfen, militärischen Anlagen oder kritischer Infrastruktur",
     "stichworte": [r"drohne|drone",
                    r"flughafen|airport|kaserne|stützpunkt|base|militär|military|kraftwerk|power plant|industriepark|chemiepark|lng-terminal|hafen",
                    r"sichtung|gesichtet|sighting|überflug|überflogen|flew over|luftraum|airspace|gesperrt|closed|eingestellt|halted|unbekannte"],
     "ausschluss": r"lieferdrohne|delivery drone|drohnenshow|drone show",
     "wirkung": "Rüstungswerte steigen; Luftverkehr wird gestört",
     "mechanismus": "Nachfrage nach Drohnenabwehr, Flughäfen schließen zeitweise", "zeitraum": "Tage bis Wochen",
     "messpunkt": {"sym": "DFEN.DE", "richtung": 1, "tage": 10}, "effekt": "schwach",
     "gegenkraefte": "Vorfälle bleiben vereinzelt", "belege": [], "fuehrt_zu": ["K2"],
     "folgen_offen": ["Befugnisse zur Drohnenabwehr für Polizei oder Bundeswehr werden ausgeweitet",
                      "Aufträge für Drohnenabwehrsysteme",
                      "Flugausfälle und Kosten für Airlines",
                      "Zusätzlicher Schutz kritischer Infrastruktur",
                      "Zuschreibung an einen staatlichen Akteur und diplomatische Reaktion"],
     "gdelt": "drones (airport OR \"military base\") (sighting OR closed OR airspace)"},
    {"id": "S3", "titel": "Staatlich zugeschriebener Cyberangriff",
     "ausloeser": "Cyberangriff auf Behörden oder kritische Infrastruktur, einem Staat zugeschrieben",
     "stichworte": [r"cyber|hacker|ransomware|ddos",
                    r"behörde|ministerium|ministry|bundestag|parlament|parliament|stadtwerk|energieversorger|krankenhaus|wasserwerk|infrastruktur|infrastructure|verwaltung",
                    r"russ|china|chines|iran|nordkorea|north korea|staatlich|state-sponsored|state-backed|\bapt\d*|zugeschrieben|attributed"],
     "wirkung": "Cybersicherheitswerte steigen; Staat reagiert mit Abwehr und Sanktionen",
     "mechanismus": "mehr Ausgaben für Schutz, politischer Druck", "zeitraum": "Tage bis Wochen",
     "messpunkt": {"sym": "HACK", "richtung": 1, "tage": 5}, "effekt": "schwach",
     "gegenkraefte": "Angriff bleibt folgenlos", "belege": [], "fuehrt_zu": [],
     "folgen_offen": ["Sanktionen gegen verantwortliche Personen oder Einheiten",
                      "Mehr Mittel für Cyberabwehr (BSI, Cyber- und Informationsraum der Bundeswehr)",
                      "Verschärfte Melde- und Schutzpflichten für Betreiber",
                      "Ausfälle öffentlicher Dienste"],
     "gdelt": "cyberattack (government OR infrastructure) (Russia OR China OR Iran OR \"state-backed\")"},
    {"id": "S4", "titel": "GPS-Störungen",
     "ausloeser": "Störung oder Fälschung von Satellitennavigation (Jamming, Spoofing)",
     "stichworte": [r"gps|gnss|satellitennavigation|navigationssignal", r"störung|stör|jamming|spoofing|interference|gefälscht"],
     "wirkung": "Luft- und Schifffahrt werden behindert", "mechanismus": "Navigation fällt aus, Routen werden verlegt",
     "zeitraum": "Tage bis Wochen", "messpunkt": None,
     "gegenkraefte": "Ausweichverfahren der Flugsicherung", "belege": [], "fuehrt_zu": [],
     "folgen_offen": ["Umleitungen und Verspätungen im Luftverkehr",
                      "Warnungen für Schifffahrt und Luftfahrt",
                      "Zuschreibung und diplomatische Reaktion",
                      "Investitionen in störfeste Navigation"],
     "gdelt": "GPS (jamming OR spoofing OR interference)"},
    {"id": "S5", "titel": "Anschlag auf Bahn- oder Energieinfrastruktur",
     "ausloeser": "Brandanschlag oder Sabotage an Bahn- oder Energieinfrastruktur",
     "stichworte": [r"brandanschlag|arson|sabotage|anschlag",
                    r"bahn|rail|railway|gleis|stellwerk|kabelschacht|strommast|umspannwerk|substation|stromnetz|power grid|oberleitung"],
     "ausschluss": r"übung|exercise|jahrestag",
     "wirkung": "Verkehr und Versorgung werden gestört", "mechanismus": "zentrale Knoten fallen aus",
     "zeitraum": "Tage", "messpunkt": None,
     "gegenkraefte": "schnelle Reparatur, Umleitung", "belege": [], "fuehrt_zu": [],
     "folgen_offen": ["Zugausfälle und Störungen im Güterverkehr",
                      "Mehr Schutz kritischer Infrastruktur",
                      "Ermittlungen der Bundesanwaltschaft bei Verdacht auf staatlichen Auftrag",
                      "Debatte über die Sicherheit von Bahnanlagen"],
     "gdelt": "(arson OR sabotage) (railway OR \"power grid\" OR substation) Germany"},
    {"id": "S6", "titel": "Staatliche Desinformationskampagne",
     "ausloeser": "Aufgedeckte Desinformations- oder Einflusskampagne eines Staates",
     "stichworte": [r"desinformation|disinformation|manipulationskampagne|einflusskampagne|influence operation|einflussnahme|doppelgänger|bot-netz|botnet|troll",
                    r"russ|china|chines|iran|staatlich|state|kreml|kremlin"],
     "wirkung": "Politischer und regulatorischer Druck auf Plattformen", "mechanismus": "Aufdeckung erhöht Handlungsdruck",
     "zeitraum": "Wochen", "messpunkt": None,
     "gegenkraefte": "geringe Reichweite der Kampagne", "belege": [], "fuehrt_zu": ["T2"],
     "folgen_offen": ["Verfahren gegen Plattformen nach dem Digital Services Act",
                      "Aufklärungskampagnen von Behörden",
                      "Sanktionen gegen beteiligte Personen oder Medien",
                      "Debatte über den Schutz von Wahlen"],
     "gdelt": "disinformation campaign (Russia OR China OR Iran)"},
    {"id": "S7", "titel": "Migration als Druckmittel",
     "ausloeser": "Gezielt gesteuerte Migration an EU-Außengrenzen",
     "stichworte": [r"migration|migranten|migrants|flüchtlinge|refugees|grenze|border",
                    r"instrumentalis|weaponi|hybrid|belarus|lukaschenko|lukashenko|gezielt geschleust|geschleust"],
     "wirkung": "Grenzpolitik wird verschärft", "mechanismus": "Druck auf Grenzstaaten und EU",
     "zeitraum": "Wochen", "messpunkt": None,
     "gegenkraefte": "Einigung mit Transitländern", "belege": [], "fuehrt_zu": [],
     "folgen_offen": ["Grenzschließungen oder Aussetzung von Asylverfahren an der Grenze",
                      "Zusätzliche Grenzsicherung und EU-Mittel",
                      "Sanktionen gegen Herkunfts- oder Transitland",
                      "Humanitäre Notlage an der Grenze"],
     "gdelt": "(migrants OR migration) border (Belarus OR weaponized OR hybrid)"},
]

LITERATUR = [
    {"id": "tetlock2015", "bereich": "Foresight und Vorhersagen prüfen", "text": "Tetlock, P. & Gardner, D. (2015): Superforecasting. The Art and Science of Prediction.", "wofuer": "Prognosen messbar machen und prüfen – Grundlage für Messpunkt und Trefferquote"},
    {"id": "tetlock2005", "bereich": "Foresight und Vorhersagen prüfen", "text": "Tetlock, P. (2005): Expert Political Judgment. How Good Is It? How Can We Know?", "wofuer": "warum Einschätzungen ohne Rückmeldung kaum besser als der Zufall sind"},
    {"id": "brier1950", "bereich": "Foresight und Vorhersagen prüfen", "text": "Brier, G. W. (1950): Verification of Forecasts Expressed in Terms of Probability. Monthly Weather Review 78(1).", "wofuer": "Brier-Wert zur Bewertung"},
    {"id": "voros2003", "bereich": "Foresight und Vorhersagen prüfen", "text": "Voros, J. (2003): A Generic Foresight Process Framework. Foresight 5(3).", "wofuer": "Ablauf von Signal bis Handlung"},
    {"id": "inayatullah1998", "bereich": "Foresight und Vorhersagen prüfen", "text": "Inayatullah, S. (1998): Causal Layered Analysis. Futures 30(8).", "wofuer": "Ursachen auf mehreren Ebenen denken"},
    {"id": "goscience2017", "bereich": "Foresight und Vorhersagen prüfen", "text": "Government Office for Science (2017): The Futures Toolkit.", "wofuer": "praktische Methoden, frei verfügbar"},
    {"id": "pearl2018", "bereich": "Foresight und Vorhersagen prüfen", "text": "Pearl, J. & Mackenzie, D. (2018): The Book of Why.", "wofuer": "Korrelation ist nicht Ursache"},
    {"id": "kilian2009", "bereich": "Energie und Geopolitik", "text": "Kilian, L. (2009): Not All Oil Price Shocks Are Alike. American Economic Review 99(3).", "wofuer": "Angebots- und Nachfrageschocks beim Öl trennen"},
    {"id": "kilianpark2009", "bereich": "Energie und Geopolitik", "text": "Kilian, L. & Park, C. (2009): The Impact of Oil Price Shocks on the U.S. Stock Market. International Economic Review 50(4).", "wofuer": "Aktien und Ölschocks"},
    {"id": "hamilton1983", "bereich": "Energie und Geopolitik", "text": "Hamilton, J. D. (1983): Oil and the Macroeconomy since World War II. Journal of Political Economy 91(2).", "wofuer": "Ölpreis und Konjunktur"},
    {"id": "caldara2022", "bereich": "Energie und Geopolitik", "text": "Caldara, D. & Iacoviello, M. (2022): Measuring Geopolitical Risk. American Economic Review 112(4).", "wofuer": "geopolitische Risiken messen"},
    {"id": "bachmann2022", "bereich": "Energie und Geopolitik", "text": "Bachmann, R. u. a. (2022): What if? The Economic Effects for Germany of a Stop of Energy Imports from Russia. ECONtribute Policy Brief 28.", "wofuer": "Gasversorgung und Industrie"},
    {"id": "wright2011", "bereich": "Landwirtschaft und Lebensmittel", "text": "Wright, B. D. (2011): The Economics of Grain Price Volatility. Applied Economic Perspectives and Policy 33(1).", "wofuer": "warum Getreidepreise springen"},
    {"id": "headey2008", "bereich": "Landwirtschaft und Lebensmittel", "text": "Headey, D. & Fan, S. (2008): Anatomy of a Crisis. The Causes and Consequences of Surging Food Prices. Agricultural Economics 39.", "wofuer": "Exportverbote und Preisspitzen"},
    {"id": "ferrucci2012", "bereich": "Landwirtschaft und Lebensmittel", "text": "Ferrucci, G., Jiménez-Rodríguez, R. & Onorante, L. (2012): Food Price Pass-Through in the Euro Area. International Journal of Central Banking 8(1).", "wofuer": "wie lange Rohstoffpreise bis in den Laden brauchen"},
    {"id": "dell2014", "bereich": "Landwirtschaft und Lebensmittel", "text": "Dell, M., Jones, B. F. & Olken, B. A. (2014): What Do We Learn from the Weather? Journal of Economic Literature 52(3).", "wofuer": "Wetter und Wirtschaft"},
    {"id": "benigno2022", "bereich": "Handel und Lieferketten", "text": "Benigno, G. u. a. (2022): The GSCPI. A New Barometer of Global Supply Chain Pressures. Federal Reserve Bank of New York Staff Reports 1017.", "wofuer": "Lieferkettendruck messen"},
    {"id": "amiti2019", "bereich": "Handel und Lieferketten", "text": "Amiti, M., Redding, S. J. & Weinstein, D. E. (2019): The Impact of the 2018 Tariffs on Prices and Welfare. Journal of Economic Perspectives 33(4).", "wofuer": "wer Zölle trägt"},
    {"id": "baker2016", "bereich": "Handel und Lieferketten", "text": "Baker, S. R., Bloom, N. & Davis, S. J. (2016): Measuring Economic Policy Uncertainty. Quarterly Journal of Economics 131(4).", "wofuer": "Unsicherheit aus Nachrichten messen"},
    {"id": "kuttner2001", "bereich": "Geldpolitik und Finanzmärkte", "text": "Kuttner, K. N. (2001): Monetary Policy Surprises and Interest Rates. Journal of Monetary Economics 47(3).", "wofuer": "nur die Überraschung bewegt Zinsen"},
    {"id": "bernanke2005", "bereich": "Geldpolitik und Finanzmärkte", "text": "Bernanke, B. S. & Kuttner, K. N. (2005): What Explains the Stock Market's Reaction to Federal Reserve Policy? Journal of Finance 60(3).", "wofuer": "Aktien und Zinsentscheide"},
    {"id": "fama1970", "bereich": "Geldpolitik und Finanzmärkte", "text": "Fama, E. F. (1970): Efficient Capital Markets. Journal of Finance 25(2).", "wofuer": "warum Kurzfristiges schnell eingepreist ist"},
    {"id": "mackinlay1997", "bereich": "Geldpolitik und Finanzmärkte", "text": "MacKinlay, A. C. (1997): Event Studies in Economics and Finance. Journal of Economic Literature 35(1).", "wofuer": "Wirkung von Ereignissen auf Kurse messen"},
    {"id": "radinsky2012", "bereich": "Ereignisse aus Nachrichten vorhersagen", "text": "Radinsky, K., Davidovich, S. & Markovitch, S. (2012): Learning Causality for News Events Prediction. WWW 2012.", "wofuer": "Kausalketten aus Nachrichtenarchiven lernen"},
    {"id": "radinsky2013", "bereich": "Ereignisse aus Nachrichten vorhersagen", "text": "Radinsky, K. & Horvitz, E. (2013): Mining the Web to Predict Future Events. WSDM 2013.", "wofuer": "aus Nachrichtenverläufen auf künftige Ereignisse schließen"},
    {"id": "halawi2024", "bereich": "Ereignisse aus Nachrichten vorhersagen", "text": "Halawi, D. u. a. (2024): Approaching Human-Level Forecasting with Language Models. arXiv.", "wofuer": "wie gut Sprachmodelle prognostizieren – und wo nicht"},
    {"id": "heindorf2020", "bereich": "Ereignisse aus Nachrichten vorhersagen", "text": "Heindorf, S. u. a. (2020): CauseNet. Towards a Causality Graph Extracted from the Web. CIKM 2020.", "wofuer": "Kausalgraph aus dem Web, Quelle für neue Zusammenhänge"},
]
from urllib.parse import quote_plus as _qp
for _l in LITERATUR:
    # Such-Link statt geratener DOI: findet den Titel verlässlich
    _l["link"] = "https://scholar.google.com/scholar?q=" + _qp(_l["text"])

WERKZEUGE = [
    {"name": "GDELT DOC 2.0 API", "link": "https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/",
     "wofuer": "weltweite Berichterstattung je Suchbegriff – zweites Signal neben den eigenen Feeds; hier eingebaut"},
    {"name": "gdeltdoc (Python-Client)", "link": "https://github.com/alex9smith/gdelt-doc-api",
     "wofuer": "bequemer Zugriff auf dieselbe Schnittstelle, falls später mehr gebraucht wird"},
    {"name": "CauseNet", "link": "https://github.com/heindorf/CauseNet",
     "wofuer": "über 11 Millionen aus dem Web gelesene Ursache-Wirkungs-Aussagen – Ideengeber für neue Einträge der Wissensbasis, nicht ungeprüft übernehmen"},
    {"name": "Pegelonline", "link": "https://www.pegelonline.wsv.de/webservice/ueberblick",
     "wofuer": "Rheinpegel Kaub für E8 – in fetch_markets_extra.py eingebaut"},
]


# ─────────────────────────────────────────────────────────────
# HILFEN
# ─────────────────────────────────────────────────────────────
def _laden(pfad, vorgabe):
    try:
        with open(pfad, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return vorgabe


def _speichern(pfad, daten):
    tmp = pfad + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(daten, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, pfad)


def hauskern(src):
    return re.split(r"[\s\-–|:.]+", (src or "").lower().strip())[0] or (src or "").lower()


def _json_aus(text):
    roh = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    m = re.search(r"[\[{].*[\]}]", roh, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def frage(anbieter, system, auftrag, max_tokens=900):
    """Eine Anfrage mit Modellwechsel wie in ki_zusammenfassungen."""
    for _ in range(3):
        if not anbieter:
            return None
        name, url, key, modell = anbieter[0]
        koerper = json.dumps({"model": modell, "temperature": 0.2, "max_tokens": max_tokens,
                              "messages": [{"role": "system", "content": system},
                                           {"role": "user", "content": auftrag}]}).encode("utf-8")
        kopf = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        if name == "openrouter":
            kopf["HTTP-Referer"] = "https://presseschau.example"
            kopf["X-Title"] = "Presseschau"
        try:
            with urlopen(Request(url, data=koerper, headers=kopf), timeout=TIMEOUT) as r:
                j = json.loads(r.read().decode("utf-8", "replace"))
            return (j.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
        except HTTPError as e:
            leib = ""
            try:
                leib = json.loads(e.read().decode())["error"]["message"]
            except Exception:
                pass
            print(f"  ---  {name} HTTP {e.code}: {leib[:140]}")
            if name == "openrouter" and (e.code in (400, 404) or "unavailable for free" in leib):
                ersatz = ki.or_ersatzmodell(leib, modell)
                if ersatz:
                    anbieter[0][3] = ersatz
                    continue
            anbieter.pop(0)
        except (URLError, ValueError, KeyError, TimeoutError) as e:
            print(f"  ---  {type(e).__name__}: {e}")
            return None
    return None


# ─────────────────────────────────────────────────────────────
# KURSE
# ─────────────────────────────────────────────────────────────
def kurse_laden():
    mk = _laden("markets.json", {})
    werte = {}
    for gr in mk.get("groups") or []:
        for i in gr.get("items") or []:
            last = i.get("last")
            if last is None and i.get("s"):
                last = i["s"][-1]
            if last is None:
                continue
            werte[i.get("sym") or i.get("n")] = {"sym": i.get("sym"), "n": i.get("n", i.get("sym")),
                                                  "last": last, "unit": i.get("unit") or "",
                                                  "series": i.get("series") or {}, "grp": gr.get("grp", "")}
    return werte


def veraenderung(w, bereich):
    r = (w.get("series") or {}).get(bereich) or []
    if len(r) < 2 or not r[0]:
        return None
    return (r[-1] - r[0]) if w["unit"] == "%" else (r[-1] - r[0]) / r[0] * 100


def kurs_zeile(w):
    teile = []
    for k, name in (("1t", "Tag"), ("1w", "Woche"), ("1m", "Monat")):
        v = veraenderung(w, k)
        if v is not None:
            teile.append(f"{name} {v:+.2f} Pkt." if w["unit"] == "%" else f"{name} {v:+.1f} %")
    return f"{w['n']} ({w['sym']}): {w['last']} {w['unit']} | " + " | ".join(teile)


def grundrate(w, tage, richtung):
    """Wie oft lief der Wert in der Jahresreihe über `tage` Handelstage in
    diese Richtung? Das ist die Messlatte für die Trefferquote."""
    r = (w.get("series") or {}).get("1j") or []
    if len(r) < tage + 20:
        return None
    ok = n = 0
    for i in range(len(r) - tage):
        if r[i] is None or r[i + tage] is None:
            continue
        n += 1
        if (r[i + tage] - r[i]) * richtung > 0:
            ok += 1
    return round(ok / n, 3) if n else None


# ─────────────────────────────────────────────────────────────
# WAHRSCHEINLICHKEIT
# ─────────────────────────────────────────────────────────────
HORIZONTE = [5, 20, 60]


def _dez(x, stellen=1, plus=True):
    """Deutsche Schreibweise: +3,5"""
    t = f"{x:{'+' if plus else ''}.{stellen}f}"
    return t.replace(".", ",")


def _logit(p):
    p = min(0.97, max(0.03, p))
    return math.log(p / (1 - p))


def _logistisch(x):
    return 1 / (1 + math.exp(-x))


def renditen(w, h):
    """Veränderungen über h Handelstage in der Jahresreihe – in Prozent,
    bei Renditen in Prozentpunkten."""
    r = [x for x in ((w.get("series") or {}).get("1j") or []) if x is not None]
    out = []
    for i in range(len(r) - h):
        if not r[i]:
            continue
        out.append((r[i + h] - r[i]) if w["unit"] == "%" else (r[i + h] - r[i]) / r[i] * 100)
    return out


def grundrate_h(w, h, richtung):
    x = renditen(w, h)
    if len(x) < 30:
        return None
    return sum(1 for v in x if v * richtung > 0) / len(x)


def spanne(w, h):
    x = sorted(renditen(w, h))
    if len(x) < 30:
        return None
    q = lambda p: x[min(len(x) - 1, max(0, int(round(p * (len(x) - 1)))))]
    return {"p10": round(q(0.1), 2), "p50": round(q(0.5), 2), "p90": round(q(0.9), 2)}


def tages_sigma(w):
    r = [x for x in ((w.get("series") or {}).get("1m") or []) if x is not None]
    d = [((r[i] - r[i - 1]) if w["unit"] == "%" else (r[i] - r[i - 1]) / r[i - 1] * 100)
         for i in range(1, len(r)) if r[i - 1]]
    if len(d) < 5:
        return None
    m = sum(d) / len(d)
    return math.sqrt(sum((v - m) ** 2 for v in d) / (len(d) - 1))


def wahrscheinlichkeit(eintrag, w, h_kb, richtung, sig, sicherheit, archiv):
    """Gibt p für den Prüfzeitraum zurück, dazu Zeitprofil, Spanne und die
    Begründung Schritt für Schritt."""
    warum = []
    p0 = grundrate_h(w, h_kb, richtung)
    if p0 is None:
        p0 = 0.5
        warum.append("Grundrate unbekannt (Jahresreihe zu kurz) – 50 % angenommen")
    else:
        warum.append(f"Grundrate {round(p0*100)} %: so oft lief {w['n']} über {h_kb} Handelstage ohnehin in diese Richtung")
    effekt = EFFEKT.get(eintrag.get("effekt") or EFFEKT_VORGABE.get(eintrag["id"], "mittel"), 0.6)
    belege = min(1.0, max(0.0, (sig["haeuser_n"] - 1) / 4))
    gd = (sig.get("gdelt") or {}).get("faktor")
    if gd and gd >= 1.5:
        belege = min(1.0, belege + 0.2)
    sich = {"hoch": 1.0, "mittel": 0.75, "niedrig": 0.45}.get(sicherheit, 0.75)
    ein = 1.0
    sd = tages_sigma(w)
    bewegt = None
    for k in ("1t", "1w"):
        v = veraenderung(w, k)
        if v is not None:
            bewegt = v
            break
    if sd and bewegt is not None and bewegt * richtung > 2 * sd:
        ein = 0.6
        warum.append(f"Kurs hat sich schon um {_dez(bewegt)}{' Pkt.' if w['unit']=='%' else ' %'} bewegt (mehr als zwei übliche Tagesschwankungen) – großteils eingepreist")
    elif sd and bewegt is not None and bewegt * richtung > sd:
        ein = 0.8
        warum.append(f"Kurs hat sich schon um {_dez(bewegt)}{' Pkt.' if w['unit']=='%' else ' %'} bewegt – teils eingepreist")
    d = effekt * belege * sich * ein
    warum.append(f"Verschiebung {_dez(d, 2)} Log-Odds = Stärke {_dez(effekt, 1, False)} × Belege {_dez(belege, 2, False)} "
                 f"({sig['haeuser_n']} Häuser" + (f", weltweit ×{_dez(gd, 1, False)}" if gd else "")
                 + f") × Sicherheit {_dez(sich, 2, False)} × Eingepreist-Faktor {_dez(ein, 1, False)}")
    p = _logistisch(_logit(p0) + d)
    # Lernen aus der eigenen Bilanz
    frueher = [k for k in archiv if k.get("kb_id") == eintrag["id"] and k.get("ergebnis") in ("eingetreten", "gegenläufig", "unverändert")]
    if len(frueher) >= 3:
        treffer = sum(1 for k in frueher if k["ergebnis"] == "eingetreten")
        m = 8
        p_alt = p
        p = (treffer + p * m) / (len(frueher) + m)
        warum.append(f"Gelernt: bisher {treffer} von {len(frueher)} eingetreten – p von {round(p_alt*100)} auf {round(p*100)} % angepasst")
    profil = []
    for h in HORIZONTE:
        g = grundrate_h(w, h, richtung)
        if g is None:
            continue
        gew = math.exp(-abs(math.log(h / max(1, h_kb))))
        profil.append({"tage": h, "p": round(_logistisch(_logit(g) + d * gew), 3), "grundrate": round(g, 3)})
    return {"p": round(p, 3), "grundrate": round(p0, 3), "verschiebung": round(d, 3), "warum": warum,
            "profil": profil, "spanne": spanne(w, h_kb), "einheit": w["unit"]}


# ─────────────────────────────────────────────────────────────
# SIGNALE
# ─────────────────────────────────────────────────────────────
def artikel_laden():
    alle = []
    for p in ki.QUELLEN:
        alle += ki.lade(p)
    gesehen, out = set(), []
    for a in alle:
        if not a.get("title") or ki.alter_stunden(a.get("date", "")) > FENSTER_H:
            continue
        if ki.RAUSCH_RE.search(a.get("title") or ""):
            continue
        k = a.get("id") or a.get("link") or a.get("title")
        if k in gesehen:
            continue
        gesehen.add(k)
        out.append(a)
    return out


def signal_fuer(eintrag, artikel):
    muster = [re.compile(g, re.I) for g in eintrag.get("stichworte") or []]
    if not muster:
        return None
    aus = re.compile(eintrag["ausschluss"], re.I) if eintrag.get("ausschluss") else None
    treffer = []
    for a in artikel:
        titel = a.get("title") or ""
        text = titel + " " + (a.get("desc") or "")[:400]
        if not all(m.search(text) for m in muster):
            continue
        if not any(m.search(titel) for m in muster):
            continue                                    # mindestens ein Begriff in der Schlagzeile
        if aus and aus.search(text):
            continue
        treffer.append(a)
    if not treffer:
        return None
    haeuser = {hauskern(a.get("source", "")) for a in treffer}
    jung = [a for a in treffer if ki.alter_stunden(a.get("date", "")) <= 24]
    cl = max(int(a.get("cluster") or 1) for a in treffer)
    staerke = ("stark" if len(haeuser) >= 3 or (len(haeuser) >= MIN_HAEUSER and cl >= 3)
               else "mittel" if len(haeuser) >= MIN_HAEUSER else "schwach")
    treffer.sort(key=lambda a: (int(a.get("cluster") or 1), a.get("date", "")), reverse=True)
    return {"treffer": treffer, "haeuser": sorted(haeuser), "jung": len(jung),
            "aelter": len(treffer) - len(jung), "cluster": cl, "staerke": staerke}


def gdelt_volumen(anfrage):
    """Weltweite Berichterstattung: letzte zwei Tage gegen die fünf davor."""
    if not GDELT or not anfrage:
        return None
    url = "https://api.gdeltproject.org/api/v2/doc/doc?" + urlencode(
        {"query": anfrage, "mode": "timelinevolraw", "timespan": "7d", "format": "json"})
    try:
        with urlopen(Request(url, headers={"User-Agent": "Presseschau/1.0"}), timeout=20) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
        daten = ((j.get("timeline") or [{}])[0].get("data") or [])
        werte = [float(d.get("value") or 0) for d in daten]
        if len(werte) < 24:
            return None
        # Werte liegen meist stündlich oder in 15-Minuten-Schritten vor
        schritt = max(1, len(werte) // 7)
        tage = [sum(werte[i:i + schritt]) for i in range(0, len(werte), schritt)][:7]
        if len(tage) < 5:
            return None
        neu = sum(tage[-2:]) / 2
        vorher = sum(tage[:-2]) / max(1, len(tage) - 2)
        return {"faktor": round(neu / vorher, 2) if vorher else None, "artikel_2tage": int(sum(tage[-2:]))}
    except Exception as e:
        print(f"  GDELT nicht erreichbar ({type(e).__name__}) – ohne zweites Signal weiter.")
        return None


# ─────────────────────────────────────────────────────────────
# PRÜFEN UND AUSSCHREIBEN
# ─────────────────────────────────────────────────────────────
SYSTEM_WK = ("Du bist Analyst für strategische Vorausschau in einer deutschen Presseschau. Du prüfst streng, "
             "ob aktuelle Meldungen einen bekannten Wirkungszusammenhang auslösen, und schreibst die "
             "Wirkungskette nüchtern und konkret aus. Du erfindest nichts und gibst keine Anlageempfehlung.")


def messpunkt_text(eintrag, kurse):
    mp = eintrag.get("messpunkt")
    if not mp:
        return "keiner – Kette wird nicht automatisch geprüft", []
    syms = mp.get("wahl") or [mp.get("sym")]
    zeilen = [kurs_zeile(kurse[s]) for s in syms if s in kurse]
    richtung = mp.get("richtung")
    rt = "Auswahl: begründe + oder −" if richtung == "auswahl" else ("steigt" if richtung == 1 else "fällt")
    if mp.get("wahl"):
        return f"Auswahl aus {', '.join(syms)} (wähle den passendsten), Richtung: {rt}, in {mp['tage']} Handelstagen", zeilen
    return f"{syms[0]}, Richtung: {rt}, in {mp['tage']} Handelstagen", zeilen


def kette_schreiben(anbieter, eintrag, sig, kurse, basis_nach_id):
    offen = eintrag.get("folgen_offen") or []
    hybrid = eintrag["id"].startswith("S")
    mp_text, kurszeilen = messpunkt_text(eintrag, kurse)
    folgen = [basis_nach_id[f] for f in eintrag.get("fuehrt_zu") or [] if f in basis_nach_id]
    meldungen = sig["treffer"][:8]
    liste = "\n".join(f"[{i+1}] {a.get('source','')} · {str(a.get('date',''))[:16]} · {a.get('title','')}"
                      + (f" – {(a.get('desc') or '')[:280]}" if a.get("desc") else "")
                      for i, a in enumerate(meldungen))
    heute = datetime.now(BERLIN).strftime("%d.%m.%Y")
    auftrag = f"""Heute ist der {heute}. Deine Trainingsdaten sind veraltet – maßgeblich sind nur die Meldungen unten.

ZUSAMMENHANG AUS DER GEPRÜFTEN WISSENSBASIS ({eintrag['id']}, nicht verändern):
- Auslöser: {eintrag['ausloeser']}
- Erwartete Wirkung: {eintrag['wirkung']}
- Mechanismus: {eintrag['mechanismus']}
- Zeitraum: {eintrag['zeitraum']}
- Messpunkt: {mp_text}
- Bekannte Gegenkräfte: {eintrag['gegenkraefte']}

MÖGLICHE FOLGEN ZWEITER ORDNUNG (nur diese dürfen genannt werden, sonst keine):
{chr(10).join(f"- {f['id']}: {f['ausloeser']} → {f['wirkung']}" for f in folgen) or "- keine"}

MÖGLICHE WEITERE FOLGEN, AUCH OHNE KURS (nur aus dieser Liste wählen, keine eigenen):
{chr(10).join(f"- {x}" for x in offen) or "- keine"}

MELDUNGEN ({len(sig['haeuser'])} Häuser, {sig['jung']} davon aus den letzten 24 Stunden):
{liste}

KURSE:
{chr(10).join("- " + z for z in kurszeilen) or "- keine Kursdaten"}

AUFGABE
1. Prüfe streng: Beschreiben die Meldungen ein KONKRETES, AKTUELLES Ereignis, das den Auslöser erfüllt?
   Nein bei: Rückblick, Jahrestag, Übung, Meinung, Analystenprognose ohne Anlass, bloßer Erwähnung,
   Ereignis, das den Auslöser nur entfernt streift. Im Zweifel: nein.
2. Nur wenn ja: Schreibe die Kette aus – auf den Fall angewandt, nicht allgemein.

REGELN
- Namen, Orte, Zahlen, Daten nur aus den Meldungen oder Kursen.
- "ereignis": 1–2 Sätze: wer, was, wo, wann.
- "mechanismus": wie genau das Ereignis hier auf den Messpunkt wirkt, 1–2 Sätze.
- "eingepreist": Ist die Wirkung in den Kursen schon sichtbar? Nenne die Veränderung aus den Kursen.
- "sicherheit": "hoch" nur bei mindestens drei übereinstimmenden Quellen UND direktem Zusammenhang;
  "niedrig" bei dünner Lage oder starken Gegenkräften; sonst "mittel".
- "gegenkraefte": zuerst, was die Meldungen selbst nennen, dann aus der Wissensbasis.
- "folgen_offen": wähle aus der Liste weiterer Folgen die aus, die nach den Meldungen plausibel sind,
  mit Plausibilität hoch/mittel/niedrig und einem Satz Begründung aus den Meldungen.
{"- \"klassifikation\": Art (Sabotage, Cyber, Drohnen, Navigation, Desinformation, Migration als Druckmittel, Sonstiges), Ziel (Energie, Kommunikation, Verkehr, Militär, Staat und Verwaltung, Politik und Öffentlichkeit, Wirtschaft), Zuschreibung (bestätigt, vermutet, unklar) und Akteur nur, wenn die Meldungen ihn nennen." if hybrid else ""}
- Keine Anlageempfehlung, keine Floskeln.

Antworte NUR mit JSON, ohne Code-Zaun:
{{"passt": true/false, "grund": "ein Satz", "titel": "Ereignis → Wirkung, höchstens 90 Zeichen",
  "ereignis": "…", "mechanismus": "…", "wirkung": "…", "annahmen": ["…"], "gegenkraefte": ["…"],
  "eingepreist": "…", "sicherheit": "niedrig|mittel|hoch",
  "richtung": "+ oder − (nur bei Auswahl der Richtung)", "messpunkt": "Kürzel (nur bei Auswahl des Werts)",
  "folgen": [{{"id": "…", "text": "ein Satz"}}],
  "folgen_offen": [{{"text": "aus der Liste", "plausibilitaet": "hoch|mittel|niedrig", "begruendung": "…"}}],
  "klassifikation": {{"art": "…", "ziel": "…", "zuschreibung": "…", "akteur": "…"}},
  "quellen": [1, 2]}}"""
    antwort = frage(anbieter, SYSTEM_WK, auftrag, max_tokens=900)
    return _json_aus(antwort), meldungen


def vorschlaege_schreiben(anbieter, artikel, basis, kurse):
    """Themen mit breiter Berichterstattung, die kein Eintrag der Basis trifft –
    Kandidaten für neue Zusammenhänge. Werden NICHT als Kette gezeigt."""
    muster = [[re.compile(g, re.I) for g in e.get("stichworte") or []] for e in basis]
    ungedeckt = []
    for a in artikel:
        if ki.alter_stunden(a.get("date", "")) > 36:
            continue
        t = (a.get("title") or "") + " " + (a.get("desc") or "")[:300]
        if any(m and all(x.search(t) for x in m) for m in muster):
            continue
        ungedeckt.append(a)
    zaehler = {}
    for a in ungedeckt:
        for e in (a.get("ents") or [])[:6]:
            k = str(e).strip()
            if len(k) < 4:
                continue
            z = zaehler.setdefault(k, {"n": 0, "h": set(), "titel": []})
            z["n"] += 1
            z["h"].add(hauskern(a.get("source", "")))
            if len(z["titel"]) < 3:
                z["titel"].append(a.get("title", ""))
    themen = sorted((kv for kv in zaehler.items() if len(kv[1]["h"]) >= 3), key=lambda kv: -len(kv[1]["h"]))[:8]
    if not themen:
        return []
    verfuegbar = ", ".join(f"{w['sym']} ({w['n']})" for w in list(kurse.values())[:60])
    liste = "\n".join(f"- {k} ({len(v['h'])} Häuser): " + " | ".join(v["titel"]) for k, v in themen)
    auftrag = f"""Diese Themen werden gerade breit berichtet, aber kein Eintrag unserer Wissensbasis trifft sie:
{liste}

Schlage höchstens DREI neue Wirkungszusammenhänge vor, die für Märkte oder Preise in Deutschland und Europa
relevant sind – nur, wenn ein bekannter, in der Wirtschaftsforschung belegter Mechanismus dahinter steht.
Lieber keiner als ein spekulativer. Messpunkt nur aus dieser Liste: {verfuegbar}

Antworte NUR mit JSON: [{{"thema": "…", "ausloeser": "…", "wirkung": "…", "mechanismus": "…",
"zeitraum": "…", "messpunkt": "Kürzel oder leer", "richtung": "+ oder −", "begruendung": "ein Satz"}}]"""
    d = _json_aus(frage(anbieter, SYSTEM_WK, auftrag, max_tokens=700))
    return [x for x in (d or []) if isinstance(x, dict) and x.get("ausloeser")][:3]


def tagesbild_schreiben(anbieter, ketten):
    if not ketten:
        return ""
    liste = "\n".join(f"- {k['titel']} (Sicherheit {k['sicherheit']}, {k['zeitraum']})" for k in ketten)
    auftrag = f"""Heutige geprüfte Einschätzungen:
{liste}

Schreibe zwei bis drei Sätze Einleitung für die Rubrik "Vorausschau": Was ist heute der wichtigste
Zusammenhang, wo verstärken sich Ketten gegenseitig, worauf lohnt es in den nächsten Tagen zu achten?
Nur aus der Liste, keine neuen Fakten, keine Anlageempfehlung. Nur der Text."""
    return (frage(anbieter, SYSTEM_WK, auftrag, max_tokens=300) or "").strip()[:700]


# ─────────────────────────────────────────────────────────────
# BILANZ: fällige Ketten prüfen
# ─────────────────────────────────────────────────────────────
SCHWELLE = {"%": 0.03}          # Renditen in Prozentpunkten; sonst 0,5 % (Devisen 0,3 %)


def pruefen(archiv, kurse, heute):
    for k in archiv:
        mp = k.get("messpunkt") or {}
        if k.get("ergebnis") or not mp.get("sym") or not mp.get("pruefdatum"):
            continue
        if mp["pruefdatum"] > heute:
            continue
        w = kurse.get(mp["sym"])
        if not w or mp.get("startwert") in (None, 0):
            k["ergebnis"] = "nicht prüfbar"
            continue
        start, jetzt = float(mp["startwert"]), float(w["last"])
        if w["unit"] == "%":
            diff = jetzt - start
            schwelle = SCHWELLE["%"]
        else:
            diff = (jetzt - start) / start * 100
            schwelle = 0.3 if "=X" in mp["sym"] else 0.5
        richtung = mp.get("richtung") or 0
        if abs(diff) < schwelle:
            ergebnis = "unverändert"
        elif diff * richtung > 0:
            ergebnis = "eingetreten"
        else:
            ergebnis = "gegenläufig"
        k.update({"ergebnis": ergebnis, "endwert": jetzt, "veraenderung": round(diff, 2), "geprueft_am": heute})


def bilanz(archiv):
    geprueft = [k for k in archiv if k.get("ergebnis") in ("eingetreten", "gegenläufig", "unverändert")]
    def werte(liste):
        n = len(liste)
        treffer = sum(1 for k in liste if k["ergebnis"] == "eingetreten")
        raten = [k["messpunkt"].get("grundrate") for k in liste if k["messpunkt"].get("grundrate") is not None]
        # Brier-Wert: mittlere quadratische Abweichung zwischen p und Ergebnis
        # (1 = eingetreten, 0 = nicht). Kleiner ist besser; die Grundrate allein
        # ist die Messlatte.
        mit_p = [k for k in liste if (k.get("wahrscheinlichkeit") or {}).get("p") is not None]
        y = lambda k: 1.0 if k["ergebnis"] == "eingetreten" else 0.0
        brier = round(sum((k["wahrscheinlichkeit"]["p"] - y(k)) ** 2 for k in mit_p) / len(mit_p), 3) if mit_p else None
        brier0 = round(sum((k["wahrscheinlichkeit"]["grundrate"] - y(k)) ** 2 for k in mit_p) / len(mit_p), 3) if mit_p else None
        return {"geprueft": n, "eingetreten": treffer, "quote": round(treffer / n, 3) if n else None,
                "grundrate": round(sum(raten) / len(raten), 3) if raten else None,
                "brier": brier, "brier_grundrate": brier0, "mit_p": len(mit_p)}
    nach_bereich = {}
    for k in geprueft:
        nach_bereich.setdefault(k.get("bereich", ""), []).append(k)
    return {**werte(geprueft),
            "nach_bereich": {b: werte(l) for b, l in nach_bereich.items()},
            "liste": sorted(geprueft, key=lambda k: k.get("geprueft_am", ""), reverse=True)[:30],
            "offen": sum(1 for k in archiv if not k.get("ergebnis"))}


# ─────────────────────────────────────────────────────────────
# ABLAUF
# ─────────────────────────────────────────────────────────────
def main():
    jetzt = datetime.now(BERLIN)
    heute = jetzt.strftime("%Y-%m-%d")
    alt = _laden(OUT, {})
    if not FORCE:
        if alt.get("datum") == heute:
            print("Vorausschau: heute schon erstellt – nichts zu tun.")
            return 0
        if jetzt.hour < STUNDE:
            print(f"Vorausschau: vor {STUNDE} Uhr – später.")
            return 0

    basis = _laden(BASIS, None) or _laden(ALT[BASIS], None)
    if not isinstance(basis, dict) or not basis.get("eintraege"):
        basis = {"_hinweis": "Wissensbasis der Vorausschau. Einträge streichen, ändern oder ergänzen; "
                             "Felder siehe Kopf von vorausschau.py.",
                 "_version": BASIS_VERSION, "eintraege": DEFAULT_BASIS}
        _speichern(BASIS, basis)
        print(f"  {BASIS} angelegt ({len(DEFAULT_BASIS)} Einträge).")
    elif int(basis.get("_version") or 1) < BASIS_VERSION:
        # Einmalig neue Einträge der Vorgabe ergänzen (etwa die hybriden
        # Bedrohungen). Gelöschte Einträge kommen danach nie wieder.
        da = {e["id"] for e in basis["eintraege"]}
        neu = [e for e in DEFAULT_BASIS if e["id"] not in da]
        basis["eintraege"] += neu
        basis["_version"] = BASIS_VERSION
        _speichern(BASIS, basis)
        print(f"  {BASIS}: {len(neu)} neue Einträge ergänzt (Version {BASIS_VERSION}).")
    eintraege = basis["eintraege"]
    nach_id = {e["id"]: e for e in eintraege}

    artikel = artikel_laden()
    kurse = kurse_laden()
    archiv = _laden(ARCHIV, None)
    if archiv is None:
        archiv = _laden(ALT[ARCHIV], [])
    # Je Kürzel das schlechteste Ergebnis (Bestand und Abruf werden getrennt geprüft)
    rang = {"fehler": 0, "warnung": 1, "ok": 2}
    status = {}
    for q in (_laden(QUELLEN_STATUS, {}).get("quellen") or []):
        if q.get("sym") and rang.get(q.get("status"), 2) < rang.get((status.get(q["sym"]) or {}).get("status"), 3):
            status[q["sym"]] = q
    pruefen(archiv, kurse, heute)
    print(f"Vorausschau: {len(artikel)} Meldungen, {len(kurse)} Kurse, {len(archiv)} Einschätzungen im Archiv")

    # Signale je Eintrag
    signale = []
    for e in eintraege:
        s = signal_fuer(e, artikel)
        if s:
            signale.append((e, s))
    rang = {"stark": 0, "mittel": 1, "schwach": 2}
    signale.sort(key=lambda es: (rang[es[1]["staerke"]], -len(es[1]["haeuser"]), -es[1]["jung"]))
    kandidaten = [es for es in signale if es[1]["staerke"] != "schwach"][:MAX_KETTEN]
    beobachtung = [{"id": e["id"], "titel": e["titel"], "bereich": BEREICHE.get(e["id"][0], ""),
                    "haeuser": len(s["haeuser"]),
                    "meldungen": [{"titel": a.get("title", ""), "quelle": a.get("source", ""), "id": a.get("id"),
                                   "url": a.get("link", "")} for a in s["treffer"][:3]]}
                   for e, s in signale if s["staerke"] == "schwach"][:12]
    print(f"  {len(signale)} Einträge mit Treffern, {len(kandidaten)} zur Prüfung, {len(beobachtung)} unter Beobachtung")

    anbieter = [list(a) for a in ki.ANBIETER]
    ketten, verworfen = [], []
    offene = {k["kb_id"]: k for k in archiv if not k.get("ergebnis")}
    for e, s in kandidaten:
        if not anbieter:
            print("  Kein Anbieter mehr verfügbar.")
            break
        d, meldungen = kette_schreiben(anbieter, e, s, kurse, nach_id)
        if not isinstance(d, dict):
            continue
        if not d.get("passt"):
            verworfen.append({"id": e["id"], "titel": e["titel"], "grund": str(d.get("grund", ""))[:200]})
            print(f"  – {e['id']} verworfen: {str(d.get('grund',''))[:80]}")
            continue
        # Messpunkt: vom Skript gesetzt; bei Auswahl nur aus der erlaubten Liste
        mp = dict(e.get("messpunkt") or {})
        sym = None
        if mp.get("wahl"):
            sym = d.get("messpunkt") if d.get("messpunkt") in mp["wahl"] else mp["wahl"][0]
        elif mp.get("sym"):
            sym = mp["sym"]
        richtung = mp.get("richtung")
        if richtung == "auswahl":
            r = str(d.get("richtung", "")).strip()
            richtung = 1 if r.startswith("+") else (-1 if r[:1] in ("-", "−") else None)
        messpunkt = None
        wkeit = None
        sicherheit = d.get("sicherheit") if d.get("sicherheit") in ("niedrig", "mittel", "hoch") else "mittel"
        gd = gdelt_volumen(e.get("gdelt"))
        if sym and richtung in (1, -1):
            w = kurse.get(sym)
            kal = math.ceil(int(mp.get("tage", 5)) * 7 / 5)
            messpunkt = {"sym": sym, "name": (w or {}).get("n", sym), "richtung": richtung,
                         "tage": int(mp.get("tage", 5)),
                         "pruefdatum": (jetzt + timedelta(days=kal)).strftime("%Y-%m-%d"),
                         "startwert": (w or {}).get("last"), "einheit": (w or {}).get("unit", ""),
                         "grundrate": grundrate(w, int(mp.get("tage", 5)), richtung) if w else None}
            gestoert = (status.get(sym) or {}).get("status") == "fehler"
            if w and not gestoert:
                wkeit = wahrscheinlichkeit(e, w, int(mp.get("tage", 5)), richtung,
                                           {"haeuser_n": len(s["haeuser"]), "gdelt": gd}, sicherheit, archiv)
            elif gestoert:
                messpunkt["hinweis"] = "Kursquelle laut Quellenprüfung gestört – keine Wahrscheinlichkeit gerechnet"
            if not w:
                # Wert steht (noch) nicht in markets.json – etwa bevor die
                # Zusatzwerte das erste Mal geholt wurden. Kein Startwert, keine
                # Prüfung; die App zeigt das offen an.
                messpunkt["hinweis"] = f"Kurs {sym} fehlt noch in den Marktdaten – keine Wahrscheinlichkeit gerechnet"
        nummern = [n for n in (d.get("quellen") or []) if isinstance(n, int) and 1 <= n <= len(meldungen)] or [1]
        quellen = [{"id": meldungen[n - 1].get("id"), "titel": meldungen[n - 1].get("title", ""),
                    "quelle": meldungen[n - 1].get("source", ""), "url": meldungen[n - 1].get("link", ""),
                    "datum": str(meldungen[n - 1].get("date", ""))[:10]} for n in nummern]
        folgen = [{"id": f.get("id"), "titel": nach_id[f["id"]]["titel"], "text": str(f.get("text", ""))[:240]}
                  for f in (d.get("folgen") or []) if isinstance(f, dict) and f.get("id") in (e.get("fuehrt_zu") or [])]
        erlaubt_offen = e.get("folgen_offen") or []
        folgen_offen = [{"text": f["text"], "plausibilitaet": f.get("plausibilitaet") if f.get("plausibilitaet") in ("hoch", "mittel", "niedrig") else "mittel",
                         "begruendung": str(f.get("begruendung", ""))[:240]}
                        for f in (d.get("folgen_offen") or []) if isinstance(f, dict) and f.get("text") in erlaubt_offen][:5]
        kl = d.get("klassifikation") if isinstance(d.get("klassifikation"), dict) and e["id"].startswith("S") else None
        if kl:
            kl = {k: str(kl.get(k, ""))[:60] for k in ("art", "ziel", "zuschreibung", "akteur")}
        kette = {"id": f"vs-{heute}-{e['id']}", "kb_id": e["id"], "bereich": BEREICHE.get(e["id"][0], ""),
                 "datum": heute, "titel": str(d.get("titel") or e["titel"])[:120],
                 "ereignis": str(d.get("ereignis", ""))[:500], "mechanismus": str(d.get("mechanismus", ""))[:500],
                 "wirkung": str(d.get("wirkung") or e["wirkung"])[:300], "zeitraum": e["zeitraum"],
                 "annahmen": [str(x)[:200] for x in (d.get("annahmen") or [])][:4],
                 "gegenkraefte": [str(x)[:200] for x in (d.get("gegenkraefte") or [])][:4],
                 "eingepreist": str(d.get("eingepreist", ""))[:300],
                 "sicherheit": sicherheit,
                 "messpunkt": messpunkt, "wahrscheinlichkeit": wkeit, "quellen": quellen, "folgen": folgen,
                 "folgen_offen": folgen_offen, "klassifikation": kl, "belege": e.get("belege") or [],
                 "signal": {"haeuser": len(s["haeuser"]), "meldungen": len(s["treffer"]), "jung": s["jung"],
                            "aelter": s["aelter"], "staerke": s["staerke"], "gdelt": gd}}
        # Läuft zu diesem Zusammenhang schon eine offene Kette, wird sie
        # fortgeschrieben statt doppelt gezählt: Startwert und Prüfdatum bleiben.
        if e["id"] in offene:
            vorige = offene[e["id"]]
            kette["fortgeschrieben_seit"] = vorige.get("datum")
            kette["messpunkt"] = vorige.get("messpunkt") or kette["messpunkt"]
        else:
            archiv.append({k: kette[k] for k in ("id", "kb_id", "bereich", "datum", "titel", "messpunkt",
                                                 "sicherheit", "wahrscheinlichkeit")})
        ketten.append(kette)
        print(f"  + {e['id']} {kette['titel'][:70]} ({kette['sicherheit']}"
              + (f", p={round(wkeit['p']*100)} % gegen Grundrate {round(wkeit['grundrate']*100)} %" if wkeit else "") + ")")
        time.sleep(0.4)

    vorschlaege = vorschlaege_schreiben(anbieter, artikel, eintraege, kurse) if anbieter else []
    tagesbild = tagesbild_schreiben(anbieter, ketten) if anbieter else ""

    out = {"updated": datetime.now(timezone.utc).isoformat(), "datum": heute,
           "stand": jetzt.strftime("%d.%m.%Y, %H:%M Uhr"), "tagesbild": tagesbild,
           "einschaetzungen": ketten, "beobachtung": beobachtung, "verworfen": verworfen,
           "vorschlaege": vorschlaege, "bilanz": bilanz(archiv),
           "basis": [{k: e.get(k) for k in ("id", "titel", "ausloeser", "wirkung", "mechanismus", "zeitraum",
                                            "messpunkt", "gegenkraefte", "belege", "fuehrt_zu", "stichworte",
                                            "folgen_offen", "effekt")}
                     for e in eintraege],
           "bereiche": BEREICHE, "literatur": LITERATUR, "werkzeuge": WERKZEUGE,
           "quellen_status": {"zusammenfassung": _laden(QUELLEN_STATUS, {}).get("zusammenfassung"),
                              "geprueft": _laden(QUELLEN_STATUS, {}).get("geprueft"),
                              "auffaellig": [q for q in (_laden(QUELLEN_STATUS, {}).get("quellen") or [])
                                             if q.get("status") != "ok"][:40]},
           "modell": {"effekt": EFFEKT, "horizonte": HORIZONTE,
                      "text": "p = logistisch(logit(Grundrate) + Stärke × Belege × Sicherheit × Eingepreist); "
                              "nach drei Prüfungen verrechnet mit der beobachteten Trefferquote (8 Pseudobeobachtungen)."},
           "hinweis": "Hypothesen aus geprüften Zusammenhängen, keine Vorhersagen und keine Anlageempfehlung."}
    _speichern(OUT, out)
    _speichern(ARCHIV, archiv[-500:])
    print(f"→ {OUT}: {len(ketten)} Einschätzungen, {len(verworfen)} verworfen, {len(beobachtung)} unter Beobachtung, "
          f"{len(vorschlaege)} Vorschläge; Bilanz {out['bilanz']['eingetreten']}/{out['bilanz']['geprueft']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
