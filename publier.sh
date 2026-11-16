#!/bin/bash
# Met la tâche en ligne, une seule fois : crée le dépôt public sur GitHub, active GitHub Pages et lance le premier cycle.
# Avant : gh auth login -h github.com -p https -s workflow -w
set -euo pipefail
cd "$(dirname "$0")"
QUI=$(gh api user --jq .login)
DEPOT="$QUI/serenair-meteo"
if ! gh repo view "$DEPOT" >/dev/null 2>&1; then
  gh repo create "$DEPOT" --public --source . --remote origin --push \
    --description "Turbulence-forecast tiles for the Serenair app, from NOAA GFS"
else
  git remote get-url origin >/dev/null 2>&1 || git remote add origin "https://github.com/$DEPOT.git"
  git push -u origin main
fi
# Pages publié par la tâche elle-même (pas par une branche).
gh api -X POST "repos/$DEPOT/pages" -f build_type=workflow >/dev/null 2>&1 || gh api -X PUT "repos/$DEPOT/pages" -f build_type=workflow >/dev/null
gh workflow run meteo.yml -R "$DEPOT"
echo "Lancé. Suivre : gh run watch -R $DEPOT   ·   fichiers : https://$(echo "$QUI" | tr '[:upper:]' '[:lower:]').github.io/serenair-meteo/index.json"
