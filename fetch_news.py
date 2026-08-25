name: RSS Feeds holen
on:
  schedule:
    - cron: '*/25 * * * *'
  workflow_dispatch:

# Nie zwei Läufe gleichzeitig – sonst blockieren sie sich beim Commit
concurrency:
  group: fetch
  cancel-in-progress: false

jobs:
  fetch:
    runs-on: ubuntu-latest
    timeout-minutes: 25          # harte Obergrenze für den gesamten Job
    permissions:
      contents: write
    steps:
      - name: Repository auschecken
        uses: actions/checkout@v4

      - name: Python einrichten
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Feeds holen (News, EU, Bundestag, Bundesländer, USA, Dokumente)
        timeout-minutes: 12
        run: python fetch_news.py

      # Verzeichnis, DIP, Kalender, Newsletter.
      # Läuft nur im ersten Lauf jeder Stunde und hat ein eigenes Zeitbudget:
      # Was nicht hineinpasst (Landtage, MEP-Details, Profilfotos), macht der nächste Lauf.
      - name: Verzeichnis, DIP & Kalender
        timeout-minutes: 10
        env:
          DIP_KEY: ${{ secrets.DIP_KEY }}
          TAGESLAGE_FEED: ${{ secrets.TAGESLAGE_FEED }}
          TIME_BUDGET_MIN: '8'      # Skript beendet sich selbst nach 8 Minuten
          LANDTAGE_PER_RUN: '4'     # 16 Landtage in 4 Läufen
          EP_DETAILS_PER_RUN: '150' # ~720 MEPs in 5 Läufen
          ENRICH_PER_RUN: '40'
        run: |
          MIN=$(date -u +%M)
          if [ "$MIN" -lt 25 ] || [ "${{ github.event_name }}" = "workflow_dispatch" ]; then
            python fetch_directory.py
          else
            echo "Verzeichnis übersprungen (läuft im ersten Lauf jeder Stunde)"
          fi

      - name: JSON-Dateien committen
        if: always()               # auch committen, wenn ein Schritt vorher abbrach
        run: |
          git config user.name  "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git stash
          git pull --rebase origin main
          git stash pop || true
          git add articles.json eu_articles.json bundestag_articles.json documents.json docs_files/ \
                  laender_articles.json us_articles.json \
                  people.json dip.json calendar.ics newsletters.json \
                  people_cache.json landtage_cache.json 2>/dev/null || true
          git diff --cached --quiet || git commit -m "chore: update feeds [$(date -u '+%Y-%m-%d %H:%M UTC')]"
          git push
