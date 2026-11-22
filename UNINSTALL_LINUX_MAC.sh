#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
echo 'This removes UHV Sentinel but preserves pressure recordings and settings.'
echo 'Close the app before uninstalling.'
python3 installer.py uninstall "$@"
