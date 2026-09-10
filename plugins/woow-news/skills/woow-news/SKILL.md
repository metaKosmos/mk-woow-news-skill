---
name: woow-news
description: Opera a newsletter WooW! Daily Drops da metaKosmos — gaveta editorial, pesquisa/curadoria/disparo e métricas. Use quando o usuário disser "/woow-news", "newsletter WooW", "Daily Drops", "gaveta da newsletter", "disparar newsletter mK", "rodar a WooW" ou "métricas da newsletter".
---

# WooW! Daily Drops — Skill operacional

Opera a newsletter diária da mK via broker autenticado por email mK. Os segredos (Gemini, Zoho) ficam no GCP, nunca na máquina. Você (João/Patrick/David) só precisa estar logado com a conta @metakosmos.com.br.

## Versão da skill (o aviso vem sozinho)
Não precisa lembrar de checar: a partir da v1.5.0 o **broker** manda o aviso no corpo de
toda resposta autenticada, e o CLI imprime antes do resultado do comando. Se aparecer
`[!] woow-news v1.4.0 instalada, v1.5.0 publicada`, rode `/plugin marketplace update mk-skills`.

Antes disso o aviso dependia de alguém rodar `scripts/version_check.py`, então quem não
rodava operava desatualizado sem saber. O script continua existindo para checar sem
chamar rota autenticada, mas deixou de ser o único caminho.

## Login (uma vez)
`bash scripts/setup.sh` instala deps + faz login Google mK (loopback). Depois, `python3 scripts/auth.py --status` mostra quem está logado.

## Convenção da edição
WooW! **Daily Drops** = uma edição por dia, e o identificador dela é a **data de publicação
no formato `YYYY-MM-DD`** (ex.: `2026-06-17`). Isso não muda.

**Campanha que não é a padrão tem id próprio: `<slug>--<data>`** (ex.:
`woow-beauty--2026-09-15`). É o que permite duas newsletters no mesmo dia sem uma escrever
por cima da outra — antes da v1.8.0, `create-campaign --edition 2026-09-15 --campanha
woow-beauty` reescrevia a edição do Daily Drops daquele dia, em silêncio.

Na prática você não precisa montar o id: passe `--edition <data> --campanha <slug>` e o
broker compõe. Passar o id composto pronto também funciona; o que ele **recusa** é o id de
uma campanha com `--campanha` de outra.

O broker preenche o campo `date` a partir do id nos dois formatos. (Edições legadas com chave
semanal `2026-wNN` ainda funcionam, mas use o formato de data para as novas.)

## Comandos (sempre via scripts/woow.py)
- `python3 scripts/woow.py status` — a gaveta: enviado, pronto, gerado, pesquisado, vazio + cobertura.
- `python3 scripts/woow.py run --edition 2026-06-17` — pipeline completo COM checkpoints: pesquisa, mostra pauta, pergunta pauta manual, gera conteúdo+imagem+HTML, mostra preview + custo, pergunta se dispara. O disparo NUNCA é automático.
- `python3 scripts/woow.py run --edition 2026-06-17 --stage research|generate|send` — roda um estágio isolado.
- `python3 scripts/woow.py add-pauta --edition 2026-06-17 --title "..." --content "..." --link "..."` — injeta pauta manual no próximo research.
- `python3 scripts/woow.py queue` — fila detalhada (JSON).
- `python3 scripts/woow.py metrics` — métricas ZMA (open/click/bounce) + custo das últimas edições.
- `python3 scripts/woow.py sync` — força o espelho do estado pro Firebase. (A aba `#newsletter` do painel mK **não existe mais**: o espelho segue sendo escrito, mas hoje não tem consumidor conhecido.)
- `python3 scripts/woow.py versions` — versão instalada aqui, versão publicada, o que mudou nela e quem do time está atrasado.
- `python3 scripts/woow.py release --notes "..."` — **admin**: grava a nota da versão publicada (é ela que aparece no aviso de quem está atrasado) e imprime o texto de anúncio pronto para colar no Slack, já com a lista de quem ainda não atualizou.
- `python3 scripts/woow.py sources list [--campanha X]` — as fontes RSS da pesquisa (✓ = ativa), o último teste de cada uma e **quanto cada uma entregou de verdade**: quantas matérias publicou e quantas foram barradas por repetição. Fonte ativa com **0 publicadas** sai marcada, com uma ressalva que importa: a contagem sai da memória da curadoria, e a procedência que alimenta essa memória só existe desde 02/09/2026. Enquanto a memória alcançar menos de 20 edições, a tela escreve "0 nas N edições que a memória alcança" e **não** sugere desativar, porque ali o zero fala do tamanho da memória e não da fonte. Depois disso, 0 publicadas é achado de verdade: foi assim que descobrimos uma fonte cadastrada, ativa e que nunca tinha publicado nada.
- `python3 scripts/woow.py sources test [--name "Fast Company"]` — baixa os feeds **de dentro do broker** e mostra itens encontrados, itens dentro da janela e o erro real por fonte.
- `python3 scripts/woow.py sources add --name "Retail Dive" --url https://www.retaildive.com/feeds/news/` — testa a URL antes de cadastrar, mostra o resultado e pede confirmação.
- `python3 scripts/woow.py sources set-url --name "Fast Company" --url https://www.fastcompany.com/latest/rss` — conserta URL que mudou de lugar.
- `python3 scripts/woow.py sources enable | disable | remove --name "E-Commerce Brasil"` — `disable` tira da pesquisa e mantém cadastrada; `remove` apaga da lista.
- `python3 scripts/woow.py curadoria list | status | historico | set | criar | remover | bloquear | liberar` — a regra do que não pode voltar à pauta, **por campanha**. Detalhe na seção "Por que uma matéria some da pauta".
- `python3 scripts/woow.py curadoria rebuild` — **admin**: reconstrói a memória do que já foi publicado a partir dos states das edições enviadas (índice 100% derivado; nada se perde).
- `python3 scripts/woow.py list-lists` — lista as listas de envio do ZMA (nome + listkey + contatos) e marca (→) qual é o alvo do envio diário.
- `python3 scripts/woow.py create-list --name "Time mK Daily Drops" --emails-file team.txt` — cria uma lista de envio no ZMA com os contatos (CSV via `--emails` também serve). Confirma antes de criar e devolve o `listkey`.
- `python3 scripts/woow.py set-list --list-key <KEY>` (ou `--name "..."`) — troca a lista-alvo do envio diário. Mostra o alvo atual + o novo (com nº de contatos) e pede confirmação antes de gravar.
- `python3 scripts/woow.py create-campaign --edition <ID> --type manual_html --html arquivo.html --subject "..." --preheader "..." --list-key <KEY> [--campanha <slug>]` — cria uma **campanha manual**: sobe um HTML pronto + a copy, publica, mostra o preview e pergunta se dispara (checkpoint humano). `--type news_auto` (default) só registra a edição para o pipeline de notícias. `--list-key` escolhe a lista ZMA por campanha (override do alvo global). `--campanha` diz de qual **campanha recorrente** a edição herda a regra de curadoria (omitido: `daily-drops`); a campanha precisa existir antes, via `curadoria criar`.
- `python3 scripts/woow.py list-senders` — lista os Senders do ZMA (✓ = verificado) + o remetente ativo do envio.
- `python3 scripts/woow.py set-sender --from-email <email> [--from-name "..."]` — troca o **remetente** de TODOS os envios (news diária + campanhas). Avisa se o endereço não estiver verificado no ZMA (erro 6610) e pede confirmação. Autosserviço, sem redeploy.
- `python3 scripts/woow.py set-html --edition <ID> --html arquivo.html` — substitui o HTML publicado (preview) de uma edição, sem redeploy. Cada troca guarda um snapshot **imutável** no histórico (visível no painel); o `preview_url` aponta pro mais recente. O template versionado no Git segue como fonte canônica; isto é override pontual por edição.
- `python3 scripts/woow.py schedule status` — mostra o agendamento (ligado?, horário, dias, modo, janela, alvo).
- `python3 scripts/woow.py schedule set --time 10:00 --days diario [--until 2026-07-07]` — grava horário/dias/janela. `--days` aceita `diario`, `util` (seg-sex) ou nomes (`seg,ter,qua,qui,sex,sab,dom`).
- `python3 scripts/woow.py schedule on | off` — liga/desliga o agendamento.
- `python3 scripts/woow.py schedule auto-send on | off` — liga/desliga o disparo automático (sem revisão). Pede confirmação explícita ao ligar.

## De onde vem o link de cada notícia (e por que a edição às vezes sai com 4)
O link da matéria **não é escrito pelo modelo**. O Escritor devolve o `id` do item de pauta
e marca a frase clicável; o endereço é copiado do feed RSS depois. Notícia que não prova de
qual item veio é descartada da edição, e a edição sai com menos itens em vez de sair com um
item inventado. O piso é 3: abaixo disso o `generate` falha de propósito e nada é enviado.

Isso existe porque, até 02/09/2026, o modelo escrevia o próprio `<a href>` e três edições
saíram com link para matéria que não existe (MAR-483). No `woow.py status`, a edição passa a
mostrar `4 itens · 1 descartado(s): source_id_fora_do_pool` quando houve corte. Isso é a
guarda funcionando, não erro. O detalhe por bloco (qual headline caiu, qual era o link) está
em `queue`, no campo `provenance` de cada edição.

A checagem de HTTP **registra e não bloqueia**. Publisher que barra IP de datacenter
devolve 403 para link legítimo, então 403 aparece no relatório sem derrubar a notícia.

Para auditar edições já publicadas (roda sem login, lê o bucket público):
`bash broker/scripts/auditar-edicoes.sh 2026-09-02 2026-08-31`

## Por que uma matéria some da pauta (a trava de repetição)
A pesquisa tira da pauta a matéria **que já saiu**. Cada campanha tem uma **janela** em
dias: link que foi publicado dentro dela não volta. Existe porque a news repetia matéria em
dias seguidos: medimos **39 links repetidos em 35 edições**, todos com **1 ou 2 dias** de
intervalo. A comparação é por **URL**, não por texto.

**A regra é da CAMPANHA, e a edição herda.** Não existe ajuste por edição: `curadoria set`
muda a janela de `daily-drops` (ou de outra campanha) e vale da próxima pesquisa em diante,
para todas as edições dela. Para furar a trava num dia específico, use `add-pauta` — ele
injeta a matéria **depois** da pesquisa e, por definição, não passa pela memória.

**`curadoria` e `create-campaign` são coisas diferentes com nomes parecidos, e é aqui que
todo mundo se confunde:** `curadoria` governa a **regra** de uma campanha recorrente (a
newsletter inteira); `create-campaign` e `set-html` governam **uma edição** (um dia). A
única ponte entre as duas é `create-campaign --campanha <slug>`, que diz de qual campanha
aquela edição herda a regra. Edição sem `--campanha` herda `daily-drops`.

Comandos, todos de operador menos o `rebuild`:
- `curadoria list` — as campanhas, a regra de cada uma e o tamanho da memória.
- `curadoria status [--campanha X]` — **é o comando que responde "por que essa matéria não
  apareceu hoje"**: mostra a janela em vigor, o modo da camada de título, quantos itens a
  última pesquisa barrou e de que fontes, os bloqueios manuais e o que já saiu na janela.
- `curadoria historico [--campanha X] [--dias N]` — o que já foi enviado, com link e data.
- `curadoria set [--campanha X] [--janela N] [--titulo relatorio|on|off]` — muda a regra.
- `curadoria bloquear --link URL [--motivo "..."]` — veto permanente daquele link naquela
  campanha. Não expira e vale mesmo com a janela em 0. `curadoria liberar --link URL` desfaz
  e ainda tira o link da memória, deixando a matéria voltar à pauta.
- `curadoria criar --campanha <slug> [--nome "..."] [--copiar-de daily-drops]` — nova
  newsletter recorrente com regra própria. `curadoria remover --campanha <slug>` apaga a
  regra (a padrão não pode ser removida).
- `curadoria rebuild` — **admin**: reconstrói a memória a partir dos states enviados.

`bloquear`, `liberar`, `remover` e `rebuild` pedem confirmação e mostram o estado atual
antes. `curadoria set --janela 0` é o **kill switch**: a campanha volta a aceitar repetição,
e o comando avisa em destaque e confirma antes de gravar.

### A camada de título hoje só reporta
Além da URL, a pesquisa compara **títulos parecidos** — e por enquanto ela **não barra
nada**, só conta e reporta. O motivo é medido: nas 35 edições publicadas, todo par de
headline parecida tinha **a mesma URL** por trás, então a camada de título não pegaria nada
que a trava de URL já não pegue, e barrar por ela seria assumir risco de falso positivo sem
ganho. Ela existe para descobrir **a mesma história vinda de dois publishers diferentes**,
que a comparação por URL não vê. Quando o relatório mostrar um caso real desses, vire a
chave com `curadoria set --titulo on`; `--titulo off` desliga até a medição.

### Quando a pauta ficar curta
Se a trava tirar itens demais, o alerta aparece no **Checkpoint 1 do `run`** (junto com a
lista do que foi barrado e de quando cada matéria saiu), e a gaveta mostra
`3 barrado(s) por repetição` na linha da edição. Três saídas, nessa ordem: `add-pauta` para
injetar a matéria na mão naquele dia; `curadoria set --janela N` menor, se a janela estiver
larga demais para o ritmo do setor; `curadoria set --janela 0` para desligar a trava daquela
campanha, aceitando que a repetição volta.

## Listas e destinatários (ZMA, NÃO Zoho CRM)
A lista de envio da newsletter vive no **Zoho Marketing Automation (ZMA)**, e a skill cria/lista/troca essas listas via broker (comandos acima). **Esta skill não usa Zoho CRM.** Se o seu ambiente Claude tiver algum conector `ZohoCRM_*` conectado, **ignore-o** — ele não tem nada a ver com a newsletter; criar algo no módulo "Campaigns" do CRM não vira lista de disparo. Para qualquer operação de lista, use sempre `scripts/woow.py` (list-lists / create-list / set-list), nunca ferramentas de CRM.

Criar uma lista e trocar o destinatário do envio são **ações de operador** (você consegue fazer sozinho) — não precisam de admin nem de redeploy. O `set-list` grava o alvo em estado mutável; a `newsletter.yaml` é só o fallback.

## Fontes da pesquisa (feeds RSS)
As fontes que alimentam o Daily Drops são **autosserviço de operador**: a lista viva fica em
`sources.json` no GCS (mesmo padrão do `set-list`) e vale na pesquisa seguinte, sem redeploy.
O `broker/config/feeds.yaml` versionado é só o seed, usado enquanto ninguém editou pela skill.

**O teste roda no broker, não na sua máquina, e isso importa.** A pesquisa baixa os feeds de
dentro do Cloud Run, e vários publishers bloqueiam IP de datacenter: uma fonte pode abrir no seu
navegador e devolver 403 para o pipeline. Por isso `sources test` e o teste automático do
`sources add` executam lá, e é o resultado deles que vale.

Um 403 ou 404 numa fonte quase nunca é header faltando — o fetch já manda User-Agent de navegador
e tem fallback. É bloqueio do publisher (às vezes conteúdo pago) ou URL que mudou. Para URL que
mudou, `sources set-url`; para bloqueio persistente, `sources disable` e procure outra fonte.

A skill recusa desativar ou remover a **última fonte ativa**: sem fonte, a pesquisa roda vazia
e a edição sai sem pauta, em vez de dar erro.

## Tipos de campanha (news_auto | manual_html)
Cada edição carrega um `type` no estado (campo, não estágio):
- **`news_auto`** (default, retrocompat): o Daily Drops de notícias — pesquisa + curadoria + geração
  + HTML, o pipeline de sempre. Edições antigas sem `type` são tratadas como `news_auto`.
- **`manual_html`**: campanha avulsa em que o operador sobe um **HTML pronto** e escreve a **copy**
  (subject + preheader), sem pesquisa/geração. O broker publica o HTML no bucket público e a campanha
  vai pro ZMA como uma campanha distinta do Daily Drops. Use `create-campaign --type manual_html`.
  O disparo continua exigindo confirmação humana (mesmo checkpoint do `send`).

## Autosserviço de remetente e HTML (sem redeploy)
Trocar o **remetente** e trocar o **HTML** de uma edição deixaram de ser tarefa de dev — são
**ações de operador**, gravadas em estado mutável no GCS (mesmo padrão do `set-list`), sem redeploy:
- **`set-sender`** grava o `from_email`/`from_name` ativos (precedência sobre o `newsletter.yaml`).
  **É GLOBAL:** vale para as campanhas manuais **e** para a News diária/agendada. Só endereços
  **verificados no ZMA** funcionam; um remetente não verificado faz o disparo falhar com **erro 6610**
  (hoje só `patrick@` está verificado). O comando avisa e pede confirmação quando o endereço não
  consta como verificado. Cuidado: setar um remetente não verificado quebra também o auto-send
  agendado (o erro fica no `health`, sem notificação ativa) — confira com `list-senders` e valide em
  DRAFT antes de ligar o auto-send.
- **`set-html`** republica o HTML de uma edição (override por edição). O template no Git segue
  canônico. **Cada versão fica registrada** (snapshot imutável em `nl/hist/<ed>/`); o painel lista
  o histórico de HTML da edição e o `preview_url` sempre aponta pro mais recente.

## Agendamento (automação de envio)
A News pode rodar sozinha, todo dia no horário, sem sessão Claude logada. O agendamento
vive em `schedule.json` no GCS (estado mutável, igual ao alvo de lista) e é editado pelos
comandos `schedule` acima, sem redeploy. Um job de infra (Cloud Scheduler) bate de tempos
em tempos no broker (`POST /cron/tick`); o broker lê o `schedule.json` e roda a edição de
hoje quando dá o horário (dedup por dia, não dispara 2x).

Dois modos:
- **Revisão (default, `auto_send=false`):** o tick roda pesquisa + geração e **para em
  `ready`**. Ninguém é avisado ativamente (notificação é só painel/gaveta): confira em
  `schedule status` / `status` ou no painel, e dispare com `run --stage send`.
- **Auto-send (`auto_send=true`):** o tick roda o pipeline inteiro e **dispara sozinho**
  para a lista-alvo, sem revisão humana. É opt-in explícito (o comando confirma ao ligar) e
  **bypassa o checkpoint de aprovação** abaixo — use com consciência.

Ligar/ajustar o agendamento (horário/dias/janela/on-off/auto-send) é **ação de operador**;
admin e operadores podem ligar o auto-send. Confira sempre o alvo em `list-lists` (→) antes.

## Checkpoint obrigatório
- **Disparo (`send`):** no fluxo manual, SEMPRE pede confirmação humana explícita. Nunca dispare sem conferir o preview. Antes do envio, confira em `list-lists` qual lista está marcada como alvo (→). **Única exceção:** o agendamento com `auto-send` ligado (opt-in explícito), que dispara sem revisão por decisão de quem o ligou.
- **Trocar destinatário (`set-list`):** redireciona QUEM recebe a news diária. O comando confirma antes; só responda `s` se for essa a lista certa.

## Papéis
- Admin (david@): muda lógica, allowlist, deploy, **release**, **curadoria rebuild**.
- Operadores (joão@, patrick@): run, add-pauta, queue, metrics, sync, **versions, sources (list/test/add/set-url/enable/disable/remove), curadoria (list/status/historico/set/criar/remover/bloquear/liberar), list-lists, create-list, set-list, create-campaign, list-senders, set-sender, set-html**.
Erro 403 significa conta não autorizada ou rota de admin. Confira `python3 scripts/auth.py --status`.

> **Mexer nas fontes não é mais tarefa de dev.** Antes exigia editar `config/feeds.yaml` e
> redeployar o broker (só admin). Agora é autosserviço de operador via `sources`, com o teste
> rodando de dentro do broker. O `feeds.yaml` no Git segue como seed.

> **Trocar remetente / trocar HTML não é mais tarefa de dev.** Antes exigiam editar `newsletter.yaml`
> ou os templates `.j2` e redeployar o broker (só admin). Agora são autosserviço de operador via
> `set-sender` e `set-html` (estado mutável no GCS). O template no Git continua sendo a fonte canônica.

## Quem opera, em que versão
O broker registra em `clients.json` quem chamou, em qual versão e quando (grava só quando
a versão muda ou o dia vira). `versions` mostra isso: era a pergunta que ninguém conseguia
responder, e por isso dava para alguém operar meses numa versão antiga sem ninguém notar.

O broker **avisa, nunca bloqueia**. Não existe versão mínima que recuse chamada: travar um
operador no meio de um envio é pior do que ele rodar uma versão antiga.

## Onde as coisas moram
Estado autoritativo: bucket GCS (via broker). Painel: espelho Firebase. Schema dos arquivos em `references/schema.md`.
