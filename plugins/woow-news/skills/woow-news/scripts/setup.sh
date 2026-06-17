#!/usr/bin/env bash
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 -m pip install --user -r "$DIR/../requirements.txt"
echo "[OK] deps instaladas. Fazendo login mK..."
python3 "$DIR/auth.py"
echo "[OK] pronto. Rode: python3 $DIR/woow.py status"
