"""state_manager.py — estado durável da WooW News (queue + por-edição) + espelho Firebase.

Store abstrato: LocalStore (filesystem, p/ teste) e GcsStore (produção, bucket privado).
queue.json é derivado dos states por edição; o painel lê o espelho no Firebase.
"""
import json
import os
import re
from datetime import datetime, timezone, timedelta

BRT = timezone(timedelta(hours=-3))
STAGE_RANK = {"empty": 0, "researched": 1, "generated": 2, "ready": 3, "sent": 4}

CAMPANHA_PADRAO = "daily-drops"
PUBLICADOS_PREFIX = "publicados/"
PUBLICADOS_MAX_EDICOES = 120  # teto POR campanha


def _now_brt():
    return datetime.now(BRT).isoformat(timespec="seconds")


def campanha_da_edicao(st):
    """Campanha de um state, com a retrocompat em UM lugar só.

    Edição anterior à trava de repetição não tem o campo `campanha` (mesmo caso do `type`
    ausente, que se lê como news_auto). Três chamadores decidindo isso por conta é onde uma
    edição legada acaba contada numa campanha e procurada em outra.

    O valor devolvido vira NOME DE BLOB (`publicados/<campanha>.json`), então ele é validado
    aqui, no ponto de uso, e não só na porta HTTP. Hoje nenhuma rota grava `campanha` sem
    validar e o GcsStore tem nome de objeto plano, sem travessia; a guarda existe para o
    LocalStore (testes e dev, onde a escrita sairia da raiz e sobrescreveria state de outra
    edição) e para o próximo escritor do campo que esqueça de validar. Slug fora do padrão
    cai na campanha padrão e denuncia no log, em vez de virar caminho de arquivo."""
    bruto = st.get("campanha")
    if not bruto:
        return CAMPANHA_PADRAO
    if not isinstance(bruto, str) or not _SLUG_RE.match(bruto):
        print(f"[publicados] campanha inválida no state ({bruto!r}); lendo como "
              f"{CAMPANHA_PADRAO}")
        return CAMPANHA_PADRAO
    return bruto


# Mesmo padrão que o orchestrator valida na porta HTTP. Duplicado de propósito: state_manager
# é a camada de baixo e não importa o orchestrator, e um slug que chega aqui já passou por
# fora da porta.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def _publicados_vazio(campanha):
    return {"campanha": campanha, "updated_at": _now_brt(), "edicoes": 0, "links": []}


class LocalStore:
    """Store em filesystem (testes / dev offline)."""
    def __init__(self, root):
        self.root = str(root)
        os.makedirs(os.path.join(self.root, "editions"), exist_ok=True)

    def read(self, key):
        path = os.path.join(self.root, key)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return f.read()

    def write(self, key, data):
        path = os.path.join(self.root, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)

    def list_editions(self):
        d = os.path.join(self.root, "editions")
        if not os.path.isdir(d):
            return []
        return [f[:-len(".state.json")] for f in os.listdir(d) if f.endswith(".state.json")]

    def list_keys(self, prefix):
        d = os.path.join(self.root, prefix.rstrip("/"))
        if not os.path.isdir(d):
            return []
        return sorted(f"{prefix.rstrip('/')}/{f}" for f in os.listdir(d))


class GcsStore:
    """Store em bucket GCS privado (produção). Lazy-importa a lib só quando usado."""
    def __init__(self, bucket=None):
        from google.cloud import storage
        self.bucket_name = bucket or os.environ.get("STATE_BUCKET", "mk-woow-news-state")
        self._bucket = storage.Client().bucket(self.bucket_name)

    def read(self, key):
        blob = self._bucket.blob(key)
        if not blob.exists():
            return None
        return blob.download_as_text()

    def write(self, key, data):
        self._bucket.blob(key).upload_from_string(data, content_type="application/json")

    def list_editions(self):
        out = []
        for blob in self._bucket.list_blobs(prefix="editions/"):
            name = blob.name.split("/")[-1]
            if name.endswith(".state.json"):
                out.append(name[:-len(".state.json")])
        return out

    def list_keys(self, prefix):
        return sorted(b.name for b in self._bucket.list_blobs(prefix=prefix))


class StateManager:
    def __init__(self, store):
        self.store = store

    # -- por edição --
    def get_state(self, edition):
        raw = self.store.read(f"editions/{edition}.state.json")
        return json.loads(raw) if raw else {"edition": edition, "stage": "empty"}

    def upsert_edition(self, edition, patch):
        """Merge raso do patch no state da edição (não rebaixa stage). Atualiza queue."""
        st = self.get_state(edition)
        new_stage = patch.get("stage")
        for k, v in patch.items():
            if k == "stage":
                continue
            st[k] = v
        if new_stage is not None and STAGE_RANK.get(new_stage, -1) >= STAGE_RANK.get(st.get("stage", "empty"), 0):
            st["stage"] = new_stage
            st.setdefault("timestamps", {})[f"{new_stage}_at"] = _now_brt()
        st["edition"] = edition
        self.store.write(f"editions/{edition}.state.json", json.dumps(st, ensure_ascii=False, indent=2))
        # Protegido pelo MESMO motivo do rebuild de publicados abaixo, e a ordem importava:
        # este roda primeiro, então toda falha compartilhada pelos dois (a listagem de
        # edições e a leitura de cada state, que é a maior parte das operações) estourava
        # aqui, antes de a outra guarda existir. Metade da superfície ficava descoberta, e
        # era a metade que cresce com o número de edições. Um único state ilegível bastava:
        # `get_state` faz json.loads sem guarda, e o JSONDecodeError subia até virar 502 num
        # envio que já tinha saído, de forma determinística e permanente.
        try:
            self._rebuild_queue()
        except Exception as e:  # noqa: BLE001 — queue é derivada; o envio já saiu
            print(f"[queue] rebuild falhou (upsert {edition}): {e}")
        # Só envio, provenance e troca de campanha mexem no índice de publicados. A condição
        # não é economia de zelo: _refresh_metrics faz um upsert por edição em laço, e
        # _rebuild_queue já relê todos os states a cada chamada (O(N²) de leituras). Sem a
        # guarda, todo sync de métricas ganharia um segundo termo quadrático. `campanha`
        # entra na lista porque move a edição inteira de blob: sem ela, os links de uma
        # edição já enviada seguem indexados na campanha antiga (barrando a pauta dela) e
        # faltam na nova, até que um envio qualquer dispare um rebuild por outro motivo.
        if patch.get("stage") == "sent" or "provenance" in patch or "campanha" in patch:
            self._rebuild_publicados_protegido(f"upsert {edition}")
        return st

    def _rebuild_publicados_protegido(self, origem):
        """Rebuild que registra a falha e segue, em vez de derrubar quem o chamou.

        O patch `stage="sent"` é gravado DEPOIS de send_zma.py já ter disparado a newsletter.
        Se o rebuild levantasse, o `except` do run_stage devolveria 502 num envio que já
        saiu, o operador reenviaria e a lista receberia duas vezes. O custo dos dois erros é
        assimétrico: índice desatualizado se conserta sozinho no próximo envio (ou na rota
        de admin), envio duplicado não se desfaz."""
        try:
            return self._rebuild_publicados()
        except Exception as e:  # noqa: BLE001 — índice é derivado; envio já saiu
            print(f"[publicados] rebuild falhou ({origem}): {e}")
            return None

    # -- queue derivado --
    def _rebuild_queue(self):
        rows = []
        for ed in self.store.list_editions():
            st = self.get_state(ed)
            rows.append({
                "edition": ed,
                "type": st.get("type", "news_auto"),  # campo, não estágio: edições legadas = news_auto
                "campanha": campanha_da_edicao(st),
                "date": st.get("date", ""),
                "stage": st.get("stage", "empty"),
                "subject": st.get("subject", ""),
                "image_ready": st.get("image_ready", False),
                "open_rate": (st.get("metrics") or {}).get("open_rate"),
                "html_versions": len(st.get("html_history") or []),  # nº de versões p/ o painel
                # Procedência (MAR-483): quantas notícias saíram com fonte confirmada, quantas
                # foram descartadas e quantos links ficaram suspeitos. Na queue porque é ela que
                # o `woow status` e o painel leem — no estado da edição ninguém olha por conta.
                "itens": (st.get("provenance") or {}).get("publicados"),
                "descartados": len(((st.get("provenance") or {}).get("descartados")) or []),
                "motivos": sorted({d.get("motivo", "") for d in
                                   (((st.get("provenance") or {}).get("descartados")) or [])}),
                "links_suspeitos": len(((st.get("link_check") or {}).get("suspeitos")) or []),
                "barrados": (st.get("health") or {}).get("barrados"),
            })
        rows.sort(key=lambda r: r["edition"])
        queue = {"updated_at": _now_brt(), "editions": rows}
        self.store.write("queue.json", json.dumps(queue, ensure_ascii=False, indent=2))
        return queue

    def get_queue(self):
        raw = self.store.read("queue.json")
        return json.loads(raw) if raw else self._rebuild_queue()

    # -- publicados derivado (memória da curadoria) --
    def _rebuild_publicados_com_status(self):
        """Reconstrói o índice do que já saiu, um blob por campanha.

        Derivado dos states como a queue: nada aqui é fonte, tudo se refaz. Só edição em
        `sent` conta — o que ainda não foi enviado não gastou pauta. Devolve
        {campanha: doc gravado}, que é o que a rota de admin mostra ao operador."""
        todas = self.store.list_editions()
        por_campanha = {}
        for ed in todas:
            st = self.get_state(ed)
            if st.get("stage") != "sent":
                continue
            por_campanha.setdefault(campanha_da_edicao(st), []).append((ed, st))

        docs = {}
        for campanha, edicoes in por_campanha.items():
            edicoes.sort(key=lambda par: par[0], reverse=True)
            edicoes = edicoes[:PUBLICADOS_MAX_EDICOES]
            links = []
            com_link = 0
            for ed, st in edicoes:
                antes = len(links)
                # Edição enviada antes da v1.6.0 não tem provenance: não contribui link
                # nenhum e não é motivo para derrubar o índice das outras.
                for item in ((st.get("provenance") or {}).get("itens") or []):
                    link = (item.get("link") or "").strip()
                    if not link:
                        continue
                    # Link CRU, não normalizado: a régua de comparação mora no research.py e
                    # ainda vai mudar. Guardar já normalizado faria cada ajuste da régua
                    # invalidar a memória inteira, que é justo o que não se pode reconstruir
                    # a partir de fora.
                    links.append({"link": link, "edition": ed,
                                  "date": st.get("date") or ed,
                                  "campo": item.get("campo", ""),
                                  "source": item.get("source", ""),
                                  "titulo": item.get("titulo_fonte", "")})
                if len(links) > antes:
                    com_link += 1
            # `edicoes` é quantas edições a memória COBRE, não quantas foram consideradas.
            # Procedência só entrou em 02/09/2026: a maioria das enviadas não contribui link
            # nenhum, e contar as consideradas faz o `curadoria list` anunciar uma cobertura
            # que a memória não tem — o operador calibraria a janela pelo número errado.
            blob = {"campanha": campanha, "updated_at": _now_brt(),
                    "edicoes": com_link, "links": links}
            self.store.write(f"{PUBLICADOS_PREFIX}{campanha}.json",
                             json.dumps(blob, ensure_ascii=False, indent=2))
            docs[campanha] = blob

        # Zero edição é indistinguível de instrumento apontado para o lugar errado: bucket
        # ou prefixo trocado, permissão perdida em `editions/` com `publicados/` ainda
        # gravável. E o custo dos dois erros é assimétrico — não esvaziar deixa um índice
        # velho barrando pauta que talvez já pudesse repetir (recuperável no próximo envio),
        # enquanto esvaziar apaga a trava inteira em silêncio e ela NÃO volta sozinha, porque
        # get_publicados só reconstrói no blob ausente, nunca no vazio. Sem edição nenhuma
        # não há o que indexar: não se apaga nada.
        if not todas:
            return docs, False

        # Campanha que existia e ficou sem nenhuma edição enviada (reset, expurgo) some do
        # laço acima e o blob velho sobreviveria barrando pauta para sempre. Esvaziar, não
        # deixar para trás: memória que não corresponde a nada tem que soltar, não travar.
        for key in self.store.list_keys(PUBLICADOS_PREFIX):
            if not key.endswith(".json"):
                continue
            campanha = key[len(PUBLICADOS_PREFIX):-len(".json")]
            if campanha in docs:
                continue
            vazio = _publicados_vazio(campanha)
            self.store.write(key, json.dumps(vazio, ensure_ascii=False, indent=2))
            docs[campanha] = vazio
        return docs, True

    def _rebuild_publicados(self):
        """Só os índices, para quem não decide nada com base na confiança da listagem."""
        return self._rebuild_publicados_com_status()[0]

    def rebuild_publicados(self):
        """Rebuild manual do índice (rota de admin). Mesmo dict do interno."""
        return self._rebuild_publicados()

    def get_publicados(self, campanha=CAMPANHA_PADRAO):
        """Memória de links já publicados na campanha.

        Blob ausente reconstrói e relê (igual get_queue), então a memória nasce sozinha no
        primeiro deploy, sem migração. Fail-open em TUDO: store fora do ar, rebuild que
        levanta ou blob ilegível devolvem o shape vazio, e nada daqui sobe — uma trava que
        barra tudo quando quebra é indistinguível de uma trava que funciona, e esta função é
        chamada no caminho de todo estágio, envio incluído."""
        key = f"{PUBLICADOS_PREFIX}{campanha}.json"
        blob = None
        try:
            raw = self.store.read(key)
            if raw is None:
                _docs, listagem_confiavel = self._rebuild_publicados_com_status()
                raw = self.store.read(key)
                if raw is None:
                    # Campanha sem nenhuma edição enviada não ganha blob no rebuild, e a
                    # AUSÊNCIA do blob é justo o gatilho da reconstrução: sem materializar o
                    # vazio, toda chamada refaz o índice inteiro (uma listagem mais uma
                    # leitura por edição), para sempre. Como o workdir é montado em todo
                    # estágio, uma campanha nova pagaria isso a cada estágio do pipeline até
                    # o primeiro envio dela.
                    #
                    # MAS só materializa se a listagem de edições for confiável. O rebuild
                    # desiste sem gravar quando `list_editions()` vem vazia (ver a guarda
                    # dele), e as duas coisas precisam concordar: gravar o vazio aqui depois
                    # daquela desistência é a MESMA falha que a guarda existe para impedir,
                    # entrando pela outra porta. O blob passaria a existir vazio, e como só o
                    # blob AUSENTE reconstrói, a trava morreria em silêncio e para sempre:
                    # uma única piscada na listagem devolveria à pauta tudo que já saiu.
                    # Medido pelas funções reais: com a piscada, matéria publicada na véspera
                    # volta à pauta; sem ela, é barrada.
                    #
                    # O status vem da MESMA leitura que o rebuild usou, não de uma listagem
                    # nova. Uma segunda listagem pode discordar da primeira, e aí a decisão
                    # de gravar sai de uma leitura enquanto a de desistir saiu de outra: o
                    # blob vazio volta a nascer, e o conserto teria um furo do mesmo tamanho.
                    if not listagem_confiavel:
                        return _publicados_vazio(campanha)
                    vazio = _publicados_vazio(campanha)
                    self.store.write(key, json.dumps(vazio, ensure_ascii=False, indent=2))
                    return vazio
            blob = json.loads(raw)
        except Exception:  # noqa: BLE001 — store fora do ar ou JSON podre não derrubam a pauta
            blob = None
        if not isinstance(blob, dict) or not isinstance(blob.get("links"), list):
            return _publicados_vazio(campanha)
        return blob

    def coverage(self):
        cov = {k: 0 for k in STAGE_RANK}
        for e in self.get_queue()["editions"]:
            stage = e["stage"] if e["stage"] in STAGE_RANK else "empty"
            cov[stage] += 1
        return cov

    def reset_edition(self, edition):
        """Zera o state da edição p/ 'empty' e reconstrói a queue e os publicados.

        Devolve (queue, indice_reconstruido). O booleano existe porque a guarda que protege o
        envio é a razão errada aqui: no reset nada foi enviado, então não há assimetria a
        respeitar, e engolir a falha em silêncio trocava um 502 honesto por um 200 que esconde
        índice velho ainda barrando a pauta das edições seguintes. A guarda fica (o reset em si
        aconteceu e o operador precisa saber disso), mas o sinal sobe com ela."""
        self.store.write(f"editions/{edition}.state.json",
                         json.dumps({"edition": edition, "stage": "empty"}))
        # Sem reconstruir aqui, os links de uma edição resetada seguiriam travando a pauta
        # para sempre enquanto o state dela diz 'empty'.
        indice = self._rebuild_publicados_protegido(f"reset {edition}")
        try:
            queue = self._rebuild_queue()
        except Exception as e:  # noqa: BLE001 — o reset já aconteceu no disco
            print(f"[queue] rebuild falhou (reset {edition}): {e}")
            queue = None
        return queue, indice is not None

    # -- espelho Firebase (produção; devolve erro estruturado se firebase_admin ausente) --
    def sync_to_firebase(self):
        try:
            import firebase_admin
            from firebase_admin import credentials, db
            try:
                firebase_admin.get_app()
            except ValueError:
                # Credencial do secret (SA do projeto que dona o RTDB, ex: mk-ops-dashboard).
                # Sem isso o admin SDK cai na ADC (SA de runtime do broker, em mk-ai-first-ops),
                # que NAO tem acesso ao RTDB -> "Unauthorized request." (import lazy: testes
                # locais nao chamam sync, entao secrets_store nao precisa estar instalado).
                import secrets_store
                cred = credentials.Certificate(secrets_store.get_firebase_credentials())
                firebase_admin.initialize_app(cred, {
                    "databaseURL": os.environ.get("FIREBASE_DB_URL", "")})
            payload = {"queue": self.get_queue(),
                       "editions": {e: self.get_state(e) for e in self.store.list_editions()}}
            db.reference("/woow_news").set(payload)
            return {"synced": len(payload["editions"]), "at": _now_brt()}
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}
