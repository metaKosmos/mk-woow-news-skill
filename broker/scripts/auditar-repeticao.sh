#!/usr/bin/env bash
# auditar-repeticao.sh — confere se a mesma matéria saiu em edições DIFERENTES da
# WooW! Daily Drops.
#
# Irmão do auditar-edicoes.sh: aquele acusa repetição DENTRO de uma edição (mesmo link
# duas vezes na mesma newsletter); este cruza edições entre si. Existe para medir se a
# trava contra matéria repetida (histórico de já-publicados) está funcionando — e o
# instrumento de medição precisa provar que funciona antes de alguém confiar nele.
#
# Lê o HTML do bucket PÚBLICO (mesmo bucket, mesmo caminho nl/<edição>.html) e roda sem
# login e sem tocar no broker.
#
# Uso:
#   bash broker/scripts/auditar-repeticao.sh 2026-09-07 2026-09-08 2026-09-09
#   bash broker/scripts/auditar-repeticao.sh --todas
#   bash broker/scripts/auditar-repeticao.sh --todas --desde 2026-08-01
#
# --todas descobre as edições listando o bucket (API JSON pública do GCS) e filtrando só
# nl/YYYY-MM-DD.html — o bucket também guarda nl/hist/... (rascunhos internos) e
# nl/YYYY-wNN.html (semanais), que não são edição diária e ficam de fora.
#
# NORMALIZAÇÃO: usa a MESMA régua de identidade de matéria que o pipeline usa em
# broker/pipeline/research.py (função canonical_url) — descarta esquema, "www.", porta
# padrão, barra final e fragmento; da querystring remove só TRACKING_PARAMS (rastreio) e
# preserva o resto, ordenado. Duas réguas diferentes seriam duas respostas diferentes
# para "é a mesma matéria?", e aqui só pode haver uma.
#
# Por que python3 (e não sed/awk) só nesta etapa: a régua envolve parsing de URL (host,
# porta, querystring com parse + reordenação estável) e decode de entidade HTML (as
# edições saem com "&amp;" cru dentro do href) — reimplementar isso em regex de shell
# arrisca divergir da fonte da verdade em algum canto (uma porta, um "%2F", uma entidade)
# sem que ninguém perceba, o que é pior do que não normalizar. O bloco abaixo NÃO importa
# research.py (evitar depender de pyyaml/feedparser só pra isso, quando a normalização
# usa só a stdlib); é uma reimplementação standalone, conferida linha a linha contra
# research.canonical_url — inclusive nos casos de borda (porta explícita, host maiúsculo,
# link relativo, IPv6 malformado, valor de query vazio). Uma diferença deliberada: porta
# fora de 0-65535 faz o research.py explodir (ValueError não tratado na property .port,
# fora do try/except que só cobre a chamada de urlsplit); aqui cai no mesmo fallback
# "host sem porta" que a própria função já usa para outras entradas malformadas — não
# deve ocorrer em link de matéria real, e existe só para o auditor nunca morrer no meio
# da rodada por causa de um href ruim.
#
# Falha de download NÃO pode virar "0 repetições": conte e reporte as edições não
# auditadas separadamente, do mesmo jeito que o auditar-edicoes.sh já faz. Silêncio por
# instrumento quebrado é indistinguível de trava funcionando, e essa confusão é o que
# este script existe para não fazer.
set -uo pipefail

BUCKET="${PUBLIC_BUCKET:-mk-woow-news-public}"
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
# Links fixos do template (CTA, descadastro, redes, site): não são fonte de matéria.
IGNORAR='wa\.me|UNSUBSCRIBE|mailto:|metakosmos\.com\.br|linkedin\.com|instagram\.com|facebook\.com|youtube\.com'

uso() {
  echo "uso: $0 <edição> [edição...]        (ex: 2026-09-07 2026-09-08)" >&2
  echo "     $0 --todas [--desde YYYY-MM-DD]" >&2
}

todas=0
desde=""
edicoes_arg=()
while [ $# -gt 0 ]; do
  case "$1" in
    --todas) todas=1; shift ;;
    --desde)
      [ $# -ge 2 ] || { echo "--desde precisa de uma data (YYYY-MM-DD)" >&2; exit 2; }
      desde="$2"; shift 2 ;;
    -h|--help) uso; exit 0 ;;
    --) shift; while [ $# -gt 0 ]; do edicoes_arg+=("$1"); shift; done ;;
    -*) echo "opção desconhecida: $1" >&2; uso; exit 2 ;;
    *) edicoes_arg+=("$1"); shift ;;
  esac
done

if [ -n "$desde" ] && ! [[ "$desde" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "--desde precisa estar em YYYY-MM-DD, veio: $desde" >&2; exit 2
fi
if [ "$todas" -eq 1 ] && [ ${#edicoes_arg[@]} -gt 0 ]; then
  echo "--todas não combina com lista explícita de edições" >&2; exit 2
fi
if [ "$todas" -eq 0 ] && [ ${#edicoes_arg[@]} -eq 0 ]; then
  uso; exit 2
fi

PYNORM="$(mktemp -t auditrep-norm)" || { echo "não consegui criar temp" >&2; exit 1; }
ACUM="$(mktemp -t auditrep-acum)" || { echo "não consegui criar temp" >&2; exit 1; }
LISTAGEM="$(mktemp -t auditrep-list)" || { echo "não consegui criar temp" >&2; exit 1; }
INTERVALOS="$(mktemp -t auditrep-intervalos)" || { echo "não consegui criar temp" >&2; exit 1; }
trap 'rm -f "$PYNORM" "$ACUM" "$LISTAGEM" "$INTERVALOS"' EXIT

# Reimplementação standalone (só stdlib) de canonical_url — ver justificativa no
# cabeçalho. Lê uma URL por linha em stdin, devolve a chave canônica por linha em stdout.
cat > "$PYNORM" << 'PYEOF'
import html, sys
from urllib.parse import urlsplit, parse_qsl, urlencode

TRACKING_PARAMS = frozenset((
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref",
))
_PORTA_PADRAO = {"http": "80", "https": "443"}


def canonical_url(u):
    bruto = html.unescape((u or "").strip())
    if not bruto:
        return ""
    try:
        partes = urlsplit(bruto)
    except ValueError:
        return bruto.lower()
    if not partes.netloc:
        return bruto.lower()
    host = (partes.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    try:
        porta = partes.port
    except ValueError:
        porta = None  # divergência deliberada do research.py p/ nunca morrer; ver cabeçalho
    padrao = _PORTA_PADRAO.get(partes.scheme.lower(), "")
    if porta is not None and str(porta) != padrao:
        host = "%s:%s" % (host, porta)
    caminho = partes.path.rstrip("/")
    mantidos = [(k, v) for k, v in parse_qsl(partes.query, keep_blank_values=True)
                if k not in TRACKING_PARAMS]
    chave = host + caminho
    if mantidos:
        chave += "?" + urlencode(sorted(mantidos))
    return chave


for linha in sys.stdin:
    linha = linha.rstrip("\n")
    if linha == "":
        continue
    sys.stdout.write(canonical_url(linha) + "\n")
PYEOF

if [ "$todas" -eq 1 ]; then
  : > "$LISTAGEM"
  token=""
  while :; do
    u="https://storage.googleapis.com/storage/v1/b/${BUCKET}/o?prefix=nl/&fields=items(name)%2CnextPageToken&maxResults=500"
    [ -n "$token" ] && u="${u}&pageToken=${token}"
    resp="$(curl -fsS --max-time 30 "$u")" || { echo "não consegui listar o bucket ${BUCKET}" >&2; exit 1; }
    printf '%s\n' "$resp" >> "$LISTAGEM"
    token="$(printf '%s' "$resp" | grep -o '"nextPageToken": *"[^"]*"' | sed -E 's/.*"([^"]*)"$/\1/')"
    [ -n "$token" ] || break
  done
  edicoes=()
  while IFS= read -r ed; do
    [ -z "$ed" ] && continue
    edicoes+=("$ed")
  done < <(grep -o '"name": *"nl/[0-9]\{4\}-[0-9]\{2\}-[0-9]\{2\}\.html"' "$LISTAGEM" \
            | sed -E 's#.*"nl/([0-9]{4}-[0-9]{2}-[0-9]{2})\.html"#\1#' \
            | sort -u)
  if [ -n "$desde" ]; then
    filtradas=()
    for ed in "${edicoes[@]}"; do
      [[ "$ed" < "$desde" ]] && continue
      filtradas+=("$ed")
    done
    edicoes=("${filtradas[@]}")
  fi
  if [ ${#edicoes[@]} -eq 0 ]; then
    echo "nenhuma edição encontrada no bucket ${BUCKET} (prefix nl/, --desde ${desde:-<nenhum>})" >&2
    exit 1
  fi
else
  edicoes=()
  while IFS= read -r ed; do
    [ -z "$ed" ] && continue
    edicoes+=("$ed")
  done < <(printf '%s\n' "${edicoes_arg[@]}" | sort -u)
fi

falhas=0
falhas_lista=()
processadas=0
: > "$ACUM"

for ed in "${edicoes[@]}"; do
  url="https://storage.googleapis.com/${BUCKET}/nl/${ed}.html"
  # Falha de download NÃO pode virar "0 repetições": ver cabeçalho.
  if ! html_edicao="$(curl -fsS --max-time 30 -A "$UA" "$url")"; then
    echo "=== $ed === NÃO BAIXOU ($url)"
    falhas=$((falhas + 1)); falhas_lista+=("$ed"); continue
  fi
  todos_raw="$(printf '%s' "$html_edicao" | grep -oE "href=['\"][^'\"]+['\"]" | sed "s/^href=//; s/[\"']//g" \
              | grep -Ev "$IGNORAR")"
  links_unicos="$(printf '%s\n' "$todos_raw" | sort -u)"
  if [ -z "$links_unicos" ]; then
    echo "=== $ed === NENHUM link de matéria encontrado (edição vazia ou HTML inesperado)"
    falhas=$((falhas + 1)); falhas_lista+=("$ed"); continue
  fi
  # "%Y-%m-%d" sozinho deixa hora/minuto/segundo por conta do relógio ATUAL no BSD date
  # (não zera): duas edições convertidas em momentos diferentes da rodada carregam
  # segundos residuais diferentes, e a diferença de epoch deixa de ser múltiplo exato de
  # 86400 — intervalo de "1 dia" sai como 1.00001. Fixar 00:00:00 explícito e -u (UTC,
  # sem DST) elimina isso: toda edição vira meia-noite exata do seu dia.
  epoch="$(date -j -u -f "%Y-%m-%d %H:%M:%S" "${ed} 00:00:00" "+%s" 2>/dev/null)"
  if [ -z "$epoch" ]; then
    echo "=== $ed === data de edição não reconhecida (esperado YYYY-MM-DD), pulando" >&2
    falhas=$((falhas + 1)); falhas_lista+=("$ed"); continue
  fi
  canon="$(printf '%s\n' "$links_unicos" | python3 "$PYNORM" | sort -u)"
  processadas=$((processadas + 1))
  while IFS= read -r c; do
    [ -z "$c" ] && continue
    printf '%s\t%s\t%s\n' "$c" "$ed" "$epoch" >> "$ACUM"
  done <<< "$canon"
done

# O awk só IMPRIME o relatório (stdout); os intervalos em dias vão para $INTERVALOS, um
# inteiro por linha. Reprocessar a linha já formatada por regex de shell pra extrair os
# mesmos números de volta é frágil (qualquer mudança de formato quebra a contagem sem
# avisar) — o número certo é o que o awk já tinha em mãos ao calculá-lo.
relatorio=""
: > "$INTERVALOS"
if [ -s "$ACUM" ]; then
  relatorio="$(sort -t "$(printf '\t')" -k1,1 -k3,3n "$ACUM" | awk -F'\t' -v intfile="$INTERVALOS" '
    function flush(   i, datas, intervalos, dias) {
      if (n < 2) return
      datas = ed[1]
      intervalos = ""
      for (i = 2; i <= n; i++) {
        datas = datas "," ed[i]
        dias = (ep[i] - ep[i-1]) / 86400
        intervalos = (intervalos == "" ? dias : intervalos "," dias)
        print dias >> intfile
      }
      printf "REPETIDO %dx  edicoes=%s  intervalos=%sd  %s\n", n, datas, intervalos, atual
    }
    {
      if ($1 != atual) { flush(); atual = $1; n = 0 }
      n++; ed[n] = $2; ep[n] = $3
    }
    END { flush() }
  ')"
fi

[ -n "$relatorio" ] && printf '%s\n' "$relatorio"

distintos=0
pares=0
distribuicao=""
if [ -n "$relatorio" ]; then
  distintos=$(printf '%s\n' "$relatorio" | grep -c '^REPETIDO')
fi
if [ -s "$INTERVALOS" ]; then
  pares=$(wc -l < "$INTERVALOS" | tr -d ' ')
  distribuicao="$(sort -n "$INTERVALOS" | uniq -c | awk '{printf "%sd:%s  ", $2, $1}')"
fi

echo
echo "$processadas edição(ões) processada(s), $falhas edição(ões) não auditada(s)"
if [ "$falhas" -gt 0 ]; then
  echo "  não auditadas: ${falhas_lista[*]}"
fi
echo "$distintos link(s) distinto(s) repetido(s) entre edições, $pares par(es) de repetição"
[ -n "$distribuicao" ] && echo "  distribuição de intervalos (dias): $distribuicao"

[ "$pares" -eq 0 ] && [ "$falhas" -eq 0 ]
