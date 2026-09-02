#!/usr/bin/env bash
# auditar-edicoes.sh — confere os links de matéria de edições já publicadas.
#
# Lê o HTML do bucket PÚBLICO, então roda sem login e sem tocar no broker. Serve para
# varrer o que já saiu (o gate de procedência só vale para edição gerada depois dele) e
# como controle positivo do próprio gate: rodado contra 2026-08-31 tem que acusar os 3
# links 404 que motivaram a MAR-483.
#
# Uso:  bash broker/scripts/auditar-edicoes.sh 2026-09-02 2026-08-31
#
# Veredito por link:
#   RAIZ    o link é só o domínio, sem caminho de matéria (assinatura do link inventado)
#   MORTO   4xx que não seja 403
#   bloqueio  403: publisher barra o cliente. NÃO é acusação, o link pode estar certo
#   ok      responde e tem caminho de matéria
set -uo pipefail

BUCKET="${PUBLIC_BUCKET:-mk-woow-news-public}"
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
# Links fixos do template (CTA, descadastro, redes, site): não são fonte de matéria.
IGNORAR='wa\.me|UNSUBSCRIBE|mailto:|metakosmos\.com\.br|linkedin\.com|instagram\.com|facebook\.com|youtube\.com'

[ $# -gt 0 ] || { echo "uso: $0 <edição> [edição...]   (ex: 2026-09-02)" >&2; exit 2; }

total=0; suspeitos=0; falhas=0
for ed in "$@"; do
  url="https://storage.googleapis.com/${BUCKET}/nl/${ed}.html"
  # Falha de download NÃO pode virar "0 suspeitos": silêncio por instrumento quebrado é
  # indistinguível de edição limpa, e é essa confusão que o script existe para não fazer.
  if ! html="$(curl -fsS --max-time 30 "$url")"; then
    echo "=== $ed === NÃO BAIXOU ($url)"; falhas=$((falhas + 1)); continue
  fi
  echo "=== $ed ==="
  links="$(printf '%s' "$html" | grep -oE "href=['\"][^'\"]+['\"]" | sed "s/^href=//; s/[\"']//g" \
           | grep -Ev "$IGNORAR" | sort -u)"
  if [ -z "$links" ]; then
    echo "  NENHUM link de matéria encontrado (edição vazia ou HTML inesperado)"
    falhas=$((falhas + 1)); continue
  fi
  while IFS= read -r link; do
    total=$((total + 1))
    caminho="$(printf '%s' "$link" | sed -E 's#^https?://[^/]+##')"
    code="$(curl -sS -o /dev/null -L --max-time 25 -A "$UA" -w '%{http_code}' "$link" 2>/dev/null)" || code="erro"
    final="$(curl -sS -o /dev/null -L --max-time 25 -A "$UA" -w '%{url_effective}' "$link" 2>/dev/null)" || final="$link"
    if [ -z "${caminho//\//}" ]; then
      veredito="RAIZ    "; suspeitos=$((suspeitos + 1))
    elif [ "$code" = "403" ]; then
      veredito="bloqueio"
    elif [ "${code:0:1}" = "4" ] || [ "${code:0:1}" = "5" ]; then
      veredito="MORTO   "; suspeitos=$((suspeitos + 1))
    else
      veredito="ok      "
    fi
    printf '  %s %s  %s\n' "$veredito" "$code" "$link"
    [ "$final" = "$link" ] || printf '                 -> %s\n' "$final"
  done <<< "$links"
done
echo
echo "$total link(s) auditado(s), $suspeitos suspeito(s), $falhas edição(ões) não auditada(s)"
[ "$suspeitos" -eq 0 ] && [ "$falhas" -eq 0 ]
