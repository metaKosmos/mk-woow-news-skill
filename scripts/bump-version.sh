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

# Regressão é recusada por padrão. O aviso de update só dispara quando publicada > local,
# então baixar a versão não dá erro em lugar nenhum: apaga o aviso de todo mundo em
# silêncio. Foi assim que a versão publicada voltou para 1.0.0. Rollback consciente passa
# --permitir-regressao.
_num() { IFS=. read -r a b c <<< "$1"; echo $((10#$a * 1000000 + 10#$b * 1000 + 10#$c)); }
if [[ "${2:-}" != "--permitir-regressao" ]] && (( $(_num "$NOVA") <= $(_num "$ATUAL") )); then
  echo "recusado: $NOVA não é maior que a versão atual ($ATUAL)." >&2
  echo "Se for rollback de propósito: bash scripts/bump-version.sh $NOVA --permitir-regressao" >&2
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
echo "  cd <clone em main>/broker && gcloud functions deploy woow-news-broker --gen2 \\"
echo "    --region=southamerica-east1 --project=mk-ai-first-ops --source=. \\"
echo "    --update-env-vars=\"SKILL_VERSION=$NOVA\""
echo
echo "  (--update-env-vars mexe SÓ nessa variável: não rotaciona o CRON_TOKEN nem"
echo "   devolve OPERATOR_EMAILS ao valor cravado, como --set-env-vars faria.)"
BRANCH="$(git -C "$ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
if [[ "$BRANCH" != "main" ]]; then
  echo
  echo "  ⚠ você está em '$BRANCH', não em main. --source=. deploya a árvore ONDE VOCÊ RODAR"
  echo "    o comando, então rode-o de um clone/worktree em main, não daqui."
fi
echo
echo "E confira:  curl -s \"\$BROKER_URL/version\"   # -> {\"version\":\"$NOVA\"}"
