#!/usr/bin/env bash
# bump-version.sh — sobe a versão da skill nos dois lugares que este repo controla.
#
# O número tem TRÊS leitores: o arquivo VERSION (o cliente, no aviso de update), o
# plugin.json (o marketplace) e a env SKILL_VERSION do Cloud Run (a rota /version).
# Os dois primeiros são deste repo e mudam juntos aqui. O terceiro entra no deploy, e
# este script imprime o comando já com o número certo, para não sobrar valor digitado
# à mão — foi assim que as três fontes divergiram (1.4.0 / 1.4.0 / 1.3.0 em ago/2026).
#
# Uso:  bash scripts/bump-version.sh 1.5.0
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION_FILE="$ROOT/plugins/woow-news/skills/woow-news/VERSION"
PLUGIN_JSON="$ROOT/plugins/woow-news/.claude-plugin/plugin.json"
ATUAL="$(tr -d '[:space:]' < "$VERSION_FILE")"
NOVA="${1:-}"

if [[ -z "$NOVA" ]]; then
  echo "uso: bash scripts/bump-version.sh <x.y.z>   (versão atual: $ATUAL)" >&2
  exit 1
fi
if [[ ! "$NOVA" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "versão inválida: '$NOVA' (esperado x.y.z, só números)" >&2
  exit 1
fi

printf '%s\n' "$NOVA" > "$VERSION_FILE"
python3 - "$PLUGIN_JSON" "$NOVA" <<'PY'
import re, sys
caminho, nova = sys.argv[1], sys.argv[2]
texto = open(caminho, encoding="utf-8").read()
# troca só a linha do campo, para o diff não reformatar o arquivo inteiro
novo, n = re.subn(r'("version"\s*:\s*)"[^"]*"', lambda m: m.group(1) + '"%s"' % nova,
                  texto, count=1)
if n != 1:
    sys.exit(f"não achei o campo 'version' em {caminho}")
open(caminho, "w", encoding="utf-8").write(novo)
PY

echo "[OK] $ATUAL -> $NOVA  (VERSION + plugin.json)"
echo
echo "A terceira fonte é o deploy. Depois de mergear em main, rode (com confirmação do David):"
echo
echo "  cd $ROOT/broker && gcloud functions deploy woow-news-broker --gen2 \\"
echo "    --region=southamerica-east1 --source=. --update-env-vars=\"SKILL_VERSION=$NOVA\""
echo
echo "E confira:  curl -s \"\$BROKER_URL/version\"   # -> {\"version\":\"$NOVA\"}"
