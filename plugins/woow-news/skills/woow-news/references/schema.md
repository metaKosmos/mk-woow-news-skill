# Schema dos arquivos de estado (woow-news)

**Id da edição (v1.8.0).** `<data>` na campanha padrão (`daily-drops`), `<slug>--<data>` nas
demais — ex.: `2026-09-15` e `woow-beauty--2026-09-15`. O campo `date` espelha a data (o
broker preenche em `run_stage`), e `queue.json` passa a trazê-lo sempre que o id o carrega,
para nenhum consumidor precisar parsear o id.

A campanha padrão fica com o id nu por retrocompat: as 35+ edições existentes, os HTMLs já
publicados (`nl/<id>.html`) e os links já enviados continuam válidos, sem migração. O
separador é `--` porque não pode ser `/` (o `GcsStore.list_editions` faz
`name.split("/")[-1]`, e a edição sumiria da fila com o state existindo no bucket) nem `.`
(chave de RTDB não aceita). O parse ancora na DATA, nunca em `split("--")`, porque slug
aceita hífen duplo interno.

**Quem manda na campanha é o CAMPO `campanha` do state, não o id.** O id só carrega. Onde há
state, `campanha_da_edicao(st)` decide; `split_edition_id` serve para quando ainda não há
(criação) e para extrair a data. Divergência entre os dois é recusada na criação, nunca
resolvida em silêncio.

Chave legada sem data (`2026-wNN`, `webinar-*`, `teste-remetente-*`) continua funcionando
como funciona hoje: não carrega data, não carrega campanha, e é lida como a padrão.

## queue.json (no GCS, espelhado pro Firebase)
```json
{
  "updated_at": "2026-06-17T08:00:00-03:00",
  "editions": [
    {"edition": "2026-06-17", "type": "news_auto", "campanha": "daily-drops",
     "date": "2026-06-17", "stage": "sent", "subject": "Assunto", "image_ready": true,
     "open_rate": 0.31, "html_versions": 2, "barrados": 3}
  ]
}
```
`html_versions` = nº de versões de HTML publicadas da edição (badge do histórico no painel).
`stage` ∈ `empty | researched | generated | ready | sent` (progresso da gaveta).
`type` ∈ `news_auto | manual_html` (tipo da campanha; campo, não estágio). Edições legadas
sem `type` são lidas como `news_auto` (retrocompat).
`campanha` = de qual newsletter recorrente a edição é, e portanto de qual regra de curadoria
ela herda. Edição sem `campanha` é lida como `daily-drops`, **do mesmo jeito** que `type`
ausente é lido como `news_auto`.
`barrados` = quantos itens a trava de repetição tirou da pauta na última pesquisa daquela
edição (cópia de `health.barrados`; `null` em edição pesquisada antes da trava existir).
Fica na queue porque é ela que o `woow status` e o painel leem — no estado da edição ninguém
olha por conta.

## editions/<ed>.state.json (no GCS)
```json
{
  "edition": "2026-06-17", "type": "news_auto", "campanha": "daily-drops",
  "date": "2026-06-17", "stage": "sent",
  "subject": "Assunto", "image_ready": true, "campaign_key": "...", "preview_url": "...",
  "preheader": "", "list_key": "3z...",
  "timestamps": {"researched_at": "...", "generated_at": "...", "ready_at": "...", "sent_at": "..."},
  "tokens": {"classify": {"input": 0, "output": 0}, "score": {}, "write": {}, "art_director": {}, "image": {}},
  "cost": {"per_step_brl": {}, "total_usd": 0.0, "total_brl": 0.0},
  "metrics": {"open_rate": 0.31, "click_rate": 0.05, "bounce_rate": 0.01,
              "sent": 200, "delivered": 198, "opened": 62, "clicked": 10, "bounced": 2,
              "fetched_at": "..."}
}
```
As contagens absolutas em `metrics` (`sent/delivered/opened/clicked/bounced`) só aparecem
quando o relatório do ZMA as fornece; o painel usa `clicked` para o total de cliques.

Campos por campanha (usados em `manual_html`): `type`, `preheader` (preview text) e `list_key`
(lista ZMA por campanha, override do alvo global no `send`).

`campanha` é o slug da newsletter recorrente à qual a edição pertence (`campanhas.json`).
**Ausente = `daily-drops`**, mesma retrocompat de `type`. É gravado por
`create-campaign --campanha <slug>`; não existe comando para mudar a curadoria de uma edição
isolada, porque a regra é da campanha e a edição só herda. `campanha` e `type` são
ortogonais: duas campanhas podem ser as duas `news_auto` e ter janelas diferentes.

**`health`** — o resumo da última pesquisa daquela edição, escrito pelo `research.py`:
```json
  "health": {
    "candidates": 41, "feeds_total": 7, "feed_errors": [{"source": "...", "error": "403"}],
    "researched_at": "2026-06-17T08:00:00-03:00",
    "barrados": 3,
    "barrados_itens": [
      {"titulo": "Título no feed", "fonte": "Glossy", "link": "https://...",
       "publicado_em": "2026-06-15", "publicado_date": "2026-06-15"}
    ],
    "parecidos": 2,
    "pool_alerta": true,
    "alerta": "pauta curta depois da trava"
  }
```
- `barrados` = quantos candidatos a trava tirou da pauta por já terem sido publicados na
  janela daquela campanha. É a contagem que a queue copia.
- `barrados_itens` = os itens em questão, com o dia em que cada um saiu, **cortados em 20**
  (o state é lido inteiro a cada consulta de edição). Existe para o operador conseguir
  responder "por que essa matéria não apareceu" **sem** abrir o log do Cloud Run, e por isso
  viaja na resposta do `run --stage research`, fora do `summary`: o summary é cortado em
  4000 caracteres, e o dia de muitos barrados é justamente o dia em que o corte comeria o
  que interessa. As chaves são `titulo` e `fonte`, e só elas. Os dois nomes chegaram a circular, e o
  consumidor no broker lia `source`: a coluna de barradas por fonte do `sources list`
  voltava 0 para sempre, sem erro nenhum, e uma fonte que só produz repetição ficava
  indistinguível de uma que nunca repete. Quem escrever outro consumidor lê `fonte`.
- `parecidos` = quantos títulos ficaram parecidos com algo já publicado. **Só relatório**
  enquanto `titulo_modo` for `relatorio`: contar não é barrar.
- `pool_alerta` = a pauta ficou curta depois da trava (é a condição); `alerta` = o texto que
  o CLI mostra no Checkpoint 1. Um é o sinal, o outro é a frase, e ficam separados para o
  painel poder alertar sem depender de parsear texto.
- `last_error` continua sendo escrito por um estágio que falhou, e não apaga o resto do
  `health` (merge raso).

**Histórico de HTML** (`html_history`): cada publicação de HTML (news_auto, manual_html ou
`set-html`) grava um snapshot **imutável** em `nl/hist/<ed>/<stamp>.html` no bucket público e
anexa uma entrada aqui. O `preview_url` sempre aponta para o "latest" estável (`nl/<ed>.html`);
o histórico guarda as versões anteriores para o painel listar. Cortado nas últimas 20.
```json
  "preview_url": "https://storage.googleapis.com/mk-woow-news-public/nl/2026-06-17.html",
  "html_history": [
    {"url": ".../nl/hist/2026-06-17/20260617T101500.html", "at": "2026-06-17T10:15:00-03:00",
     "source": "manual_html", "by": "patrick@metakosmos.com.br", "stamp": "20260617T101500"},
    {"url": ".../nl/hist/2026-06-17/20260617T143000.html", "at": "2026-06-17T14:30:00-03:00",
     "source": "set_html", "by": "patrick@metakosmos.com.br", "stamp": "20260617T143000"}
  ]
```
`source` ∈ `news_auto | manual_html | set_html` (de onde veio aquela versão).

## settings.json (no GCS) — config mutável de envio
```json
{
  "active_list_key": "3z...", "active_list_name": "Time mK Daily Drops",
  "set_by": "joao@metakosmos.com.br", "set_at": "2026-06-30T10:00:00-03:00",
  "active_from_email": "patrick@metakosmos.com.br", "active_from_name": "WooW! Daily Drops",
  "sender_set_by": "joao@metakosmos.com.br", "sender_set_at": "2026-07-01T10:00:00-03:00"
}
```
Lista-alvo do envio diário (`active_list_*`, editado por `set-list`) e remetente ativo global
(`active_from_*`, editado por `set-sender`). Ambos têm precedência sobre `newsletter.yaml`.

## schedules/<campanha>.json (no GCS) — agendamento, um por campanha
```json
{
  "enabled": false,
  "send_time": "10:00",
  "weekdays": [0, 1, 2, 3, 4, 5, 6],
  "auto_send": false,
  "until": null,
  "last_run_date": null,
  "set_by": "david@metakosmos.com.br", "set_at": "2026-06-30T10:00:00-03:00"
}
```
- `send_time`: HH:MM em **BRT**.
- `weekdays`: dias em que roda; `0=seg .. 6=dom` (`datetime.weekday()`).
- `auto_send`: `false` = modo revisão (gera e para em `ready`); `true` = dispara sozinho.
- `until`: data limite opcional `YYYY-MM-DD` (janela; ex.: piloto de 7 dias). `null` = sem fim.
- `last_run_date`: dedup — o tick "claima" o dia antes de rodar; não roda 2x no mesmo dia.

Um blob POR CAMPANHA, e não um documento único, pelo mesmo motivo que fez `clients/` virar
um blob por pessoa: `last_run_date` é um claim escrito no meio do tick, e documento único faz
dois ticks de campanhas diferentes se sobrescreverem. `campanha` não fica DENTRO do
documento: ela é o nome do blob.

**Migração sem passo de migração.** Enquanto `schedules/daily-drops.json` não existir, a
campanha padrão lê o `schedule.json` legado INTEIRO, `last_run_date` incluso — perder o claim
faria o tick rodar de novo no dia da migração, e com `auto_send` isso é um segundo envio. O
primeiro claim grava o documento resolvido inteiro no caminho novo. O legado é da padrão e de
mais ninguém: herdá-lo numa campanha nova a faria nascer `enabled: true` e mandar e-mail
sozinha no primeiro tick.

Campanha nova nasce com os defaults: `enabled: false`, `auto_send: false`.

O `POST /cron/tick` (Cloud Scheduler, a cada ~15 min) roda **uma campanha por tick** — a
vencida há mais tempo (`last_run_date` vazio primeiro). Uma, e não N em série, porque cada
pipeline tem timeout de 600s e N deles numa request estouram o attempt-deadline de 900s do
Scheduler. O retorno traz `campanha` (a que rodou), `pendentes` (as aprovadas que ficaram
para o próximo tick — é este número que torna o atraso visível), `ignoradas` (o diagnóstico:
desligada, fora do horário, já rodou hoje) e `erros` (campanha cuja agenda não pôde ser lida;
cada uma no seu try, para uma não derrubar as seguintes).

Editado por `schedule set/on/off/auto-send [--campanha X]`. `GET /schedule?campanha=X`.

## sources.json (no GCS) — fontes RSS da pesquisa
```json
{
  "feeds": [
    {
      "source": "Fast Company",
      "url": "https://www.fastcompany.com/latest/rss",
      "enabled": true,
      "added_by": "patrick@metakosmos.com.br",
      "added_at": "2026-08-24T20:00:00-03:00",
      "note": ""
    }
  ],
  "set_by": "patrick@metakosmos.com.br", "set_at": "2026-08-24T20:00:00-03:00"
}
```
Lista viva das fontes, editada por `sources add|set-url|enable|disable|remove`. Tem
precedência sobre `broker/config/feeds.yaml`, que é só o seed: sem este arquivo, vale o YAML
do container. Antes de cada pesquisa, o broker escreve as fontes com `enabled: true` no
`config/feeds.yaml` do workdir, então mudança aqui vale na pesquisa seguinte, sem redeploy.

- `enabled`: `false` mantém a fonte cadastrada e fora da pesquisa.
- `set_by`/`set_at`: quem editou a lista. **Só edição escreve este arquivo.**

⚠ Enquanto este arquivo não existir, o `feeds.yaml` do container é a lista viva e um deploy
novo pode mudá-la. Depois da primeira edição pela skill, ele passa a ser só semente: fonte
acrescentada no YAML versionado não entra mais sozinha.

## sources-tests.json (no GCS) — resultado do último `sources test`
```json
{
  "por_fonte": {
    "fast company": {"status": "ok", "found": 20, "kept": 6, "error": null,
                     "at": "2026-08-24T21:00:00-03:00"}
  },
  "tested_by": "joao@metakosmos.com.br", "tested_at": "2026-08-24T21:00:00-03:00"
}
```
Chave separada de propósito: `sources test` é diagnóstico e **não pode materializar a
lista**. Guardar o resultado dentro do `sources.json` fazia o primeiro teste congelar o
`feeds.yaml` do container para sempre, sem erro e sem aviso. O `get_sources` cola o
`last_test` de cada fonte na hora de responder.

`found` = itens no feed, `kept` = itens dentro da janela de recência, `error` = o erro real
(403/404/timeout), medido de dentro do broker.

## campanhas.json (no GCS) — a regra de curadoria, por campanha
```json
{
  "default": "daily-drops",
  "campanhas": {
    "daily-drops": {
      "nome": "WooW! Daily Drops",
      "janela_dias": 14,
      "titulo_modo": "relatorio",
      "bloqueados": [{"link": "https://...", "por": "patrick@metakosmos.com.br",
                      "em": "2026-09-08T10:00:00-03:00", "motivo": "matéria paga"}],
      "liberados": [{"link": "https://...", "por": "patrick@metakosmos.com.br",
                     "em": "2026-09-08T10:00:00-03:00"}],
      "set_by": "patrick@metakosmos.com.br", "set_at": "2026-09-08T10:00:00-03:00"
    }
  }
}
```
Editado por `curadoria criar|set|bloquear|liberar|remover`, sem redeploy — mesmo padrão do
`sources.json`. É a metade **editada** da curadoria; a metade **derivada** é o
`publicados/<campanha>.json` abaixo. Ficam separadas pelo mesmo motivo que `sources.json` e
`sources-tests.json` ficam: gravar derivado junto de editado congela a fonte na primeira
escrita.

- `janela_dias`: quantos dias uma matéria já enviada fica fora da pauta. **`0` desliga a
  trava** daquela campanha (aceita repetição). A campanha nasce com o
  `research.published_memory_days` do `newsletter.yaml` (14 dias quando a chave não está
  lá). Faixa aceita: 0 a 3650.
- `titulo_modo` ∈ `relatorio | on | off`: a camada de similaridade de **título**.
  `relatorio` (default) mede e reporta sem barrar; `on` barra; `off` nem mede. Nasce em
  `relatorio` porque, nas 35 edições publicadas, todo par de headline parecida tinha a mesma
  URL por trás — ou seja, o ganho sobre a trava de URL foi zero, e promover filtro sem caso
  medido é como a pauta encolhe sem ninguém saber por quê.
- `bloqueados`: veto manual e permanente. **Vale mesmo com `janela_dias: 0`** — é decisão do
  operador, não memória. `liberados`: o inverso, tira o link da memória.
- O mesmo link nunca fica nas duas listas: entrar numa remove da outra, senão o resultado
  dependeria da ordem de aplicação.
- A campanha padrão nasce sozinha na primeira leitura (não existe estado "sem regra" para o
  resto do código adivinhar) e **não pode ser removida**.

## publicados/<campanha>.json (no GCS) — a memória do que já saiu
```json
{
  "campanha": "daily-drops",
  "updated_at": "2026-09-08T10:00:00-03:00",
  "edicoes": 35,
  "links": [
    {"link": "https://...", "edition": "2026-09-08", "date": "2026-09-08",
     "campo": "manchete", "source": "Glossy", "titulo": "Título no feed"}
  ]
}
```
Um blob **por campanha**, 100% **derivado** dos states: só edição em `sent` conta (o que não
foi enviado não gastou pauta), e a fonte de cada link é o `provenance` da edição. Como a
queue, nada aqui é fonte — `curadoria rebuild` (admin) refaz tudo do zero sem perda.

- O link é guardado **cru, não normalizado**: a régua de comparação mora no `research.py` e
  ainda vai mudar. Guardar já normalizado faria cada ajuste da régua invalidar a memória
  inteira, que é justamente o que não se reconstrói de fora.
- Cortado nas **120 edições** mais recentes, por campanha.
- Edição enviada antes da provenance existir não contribui link nenhum, e isso não derruba o
  índice das outras.
- O recorte pela janela é feito na leitura, não aqui: a edição sendo pesquisada compara com
  `corte <= date < data_da_edicao`. O `<` estrito é o que impede rerodar o research de uma
  edição já enviada barrar os links dela mesma.

### config/publicados.json (no workdir da edição) — o que o research recebe
O broker recorta a memória e injeta este arquivo no workdir, do mesmo jeito que já injeta o
`config/feeds.yaml`. O `research.py` **não fala com o GCS e não sabe o que é campanha**.
```json
{
  "campanha": "daily-drops", "janela_dias": 14, "titulo_modo": "relatorio",
  "aviso_ready": 0,
  "links": [{"link": "https://...", "edition": "2026-09-08", "date": "2026-09-08",
             "campo": "manchete", "source": "Glossy", "titulo": "Título no feed"}],
  "liberados": ["https://glossy.co/materia-x"]
}
```
- `links` já vem recortado pela janela, e já inclui os **bloqueios manuais** da campanha,
  como entradas com `edition: "bloqueado"`. Bloqueio é veto explícito, não memória: ele vale
  mesmo com `janela_dias: 0`.
- `liberados` vem **cru e sem filtrar**, e quem aplica é o research. Não é distração: quem
  guarda pode guardar link cru, mas quem COMPARA tem que normalizar. Filtrar por igualdade
  de string do lado do broker fazia o operador que digita a URL como vê no site não liberar
  nada, porque a memória guarda o link publicado, com `www.`, barra final e `utm_source`, e
  nada avisava que o comando não teve efeito.
- `titulo_modo` decide a camada de similaridade de título: `off` não calcula, `relatorio`
  calcula e reporta sem barrar, `on` barra. Modo desconhecido cai em `relatorio`: barrar por
  heurística tem que ser escolha explícita.
- `aviso_ready` conta edições da mesma campanha que estão em `ready` dentro da janela, ou
  seja, geradas e ainda não enviadas. Elas **não** estão na memória (só `sent` conta), e o
  número existe para o relatório dizer isso em vez de o operador descobrir sozinho.

## release.json (no GCS) — nota da versão publicada
```json
{
  "version": "1.5.0",
  "notes": "fontes RSS viraram autosserviço: sources add/test/set-url",
  "by": "david@metakosmos.com.br",
  "at": "2026-08-25T09:00:00-03:00"
}
```
Escrito por `woow.py release --notes "..."` (admin). É o texto que aparece no aviso de quem
está desatualizado: número de versão sozinho não diz se vale a pena atualizar agora. A nota
só é mostrada quando `version` bate com a versão publicada pelo broker — nota de release
velha confunde mais do que ajuda.

## clients.json (no GCS) — quem opera e em que versão
```json
{
  "clients": {
    "patrick@metakosmos.com.br": {
      "version": "1.4.0",
      "last_path": "/queue",
      "last_seen": "2026-08-24T10:12:00-03:00"
    }
  }
}
```
Preenchido sozinho: toda chamada autenticada manda `X-Skill-Version`, e o broker grava
**só quando a versão muda ou o dia vira** (o registro serve para saber quem está atrasado,
não para auditar cada chamada). Cliente anterior à v1.5.0 não manda o header e aparece com
`version` vazio, o que já o classifica como atrasado.

Lido por `woow.py versions`: operador vê a si mesmo e quantos estão atrasados, admin vê a
tabela inteira.

Quem está no roster (`ADMIN_EMAILS` + `OPERATOR_EMAILS`) e **nunca** aparece aqui também
conta como atrasado, marcado `nunca_chamou`. Sem isso, no dia do deploy o relatório diria
"todo mundo em dia" justamente quando ninguém tinha atualizado ainda.
