#!/bin/bash
# Avvio locale, senza dipendenze o modifiche al sistema.
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 non trovato. Installa Python 3 e riprova."
  read -r -p "Premi Invio per chiudere…"
  exit 1
fi
echo "Avvio ByMe Lab. Il browser si aprirà automaticamente."
python3 -u byme_lab.py
status=$?
echo "ByMe Lab terminato (codice: $status)."
read -r -p "Premi Invio per chiudere…"