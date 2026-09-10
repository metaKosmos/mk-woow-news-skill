# broker/tests/test_publicados_research.py — a pesquisa para de trazer o que já saiu.
#
# A memória (`config/publicados.json`) é INJETADA pelo orchestrator já recortada pela
# janela e pela campanha. O research só lê, aplica e relata — por isso todo teste aqui
# roda offline, sem GCS e sem rede.
#
# O que estes testes protegem, em ordem de importância: (1) a guarda barra o repetido E
# deixa passar o vizinho, (2) memória quebrada não barra ninguém, (3) a régua de URL é uma
# só, compartilhada com o dedup, pelo `liberar` e pela camada de título, (4) `titulo_modo`
# faz o que promete nos três modos, (5) a seção de barrados cabe no resumo de 4000 chars
# que o orchestrator devolve no Checkpoint 1.
import json
import sys, pathlib

BROKER = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BROKER))
sys.path.insert(0, str(BROKER / "pipeline"))

import pytest  # noqa: E402
import research  # noqa: E402


def _cand(titulo, link, fonte="Glossy", data="2026-09-09", conteudo="resumo"):
    return {"title": titulo, "content": conteudo, "date": data, "link": link,
            "source": fonte, "categories": ""}


def _mem(link, edicao="2026-09-08", data="2026-09-08", titulo="", campo="manchete"):
    return {"link": link, "edition": edicao, "date": data, "campo": campo,
            "source": "Glossy", "titulo": titulo}


def _doc(*entradas, campanha="daily-drops", janela=14, liberados=None, titulo_modo=None):
    """O `config/publicados.json` que o orchestrator injeta. `liberados` e `titulo_modo` só
    entram quando pedidos: o doc SEM eles é o caso legado (e o do fallback do orchestrator,
    que não tem os dois), e ele precisa seguir valendo como identidade."""
    doc = {"campanha": campanha, "janela_dias": janela, "aviso_ready": 0,
           "links": list(entradas)}
    if liberados is not None:
        doc["liberados"] = liberados
    if titulo_modo is not None:
        doc["titulo_modo"] = titulo_modo
    return doc


# ------------------------------------------------------------------------------ a régua
def test_tracking_sai_e_identidade_fica():
    """`utm_source` diz de onde veio o clique; `p=123` diz QUAL é a matéria."""
    assert research.canonical_url("https://x.com/?p=123&utm_source=nl") == "x.com?p=123"


def test_query_de_identidade_nao_colapsa():
    """Guarda contra normalizar demais: publisher que usa `?p=N` como identidade teria
    toda a pauta do dia colapsada numa chave só, e a trava barraria a edição inteira."""
    assert research.canonical_url("https://x.com/?p=123") != research.canonical_url("https://x.com/?p=124")


def test_esquema_www_porta_e_barra_final_sao_a_mesma_materia():
    assert (research.canonical_url("HTTP://WWW.X.com:80/Materia/")
            == research.canonical_url("https://x.com/Materia"))


def test_fragmento_e_descartado():
    assert (research.canonical_url("https://x.com/materia#topo")
            == research.canonical_url("https://x.com/materia"))


def test_link_vazio_nulo_e_lixo_nao_levantam_nem_barram_um_ao_outro():
    """Chave vazia casaria com todo candidato sem link. O que importa não é o valor da
    chave, é que nenhum destes barre nenhum dos outros."""
    for entrada in ("", None, "nada"):
        research.canonical_url(entrada)  # não levanta
    assert research.canonical_url("") == research.canonical_url(None) == ""
    assert research.canonical_url("nada") == "nada"

    idx = research.indice_publicados(_doc(_mem(""), _mem(None), _mem("nada")))
    assert list(idx) == ["nada"]  # as chaves vazias ficam de fora do índice
    novos, barrados = research.separa_publicados([_cand("A", ""), _cand("B", None)], idx)
    assert barrados == [] and len(novos) == 2


def test_porta_invalida_nao_derruba_a_pesquisa():
    """`urlsplit` NÃO valida a porta: quem valida é o acesso a `.port`, e ele é lazy. Um
    único `:99999` na pauta levantava ValueError dentro de `canonical_url` e derrubava a
    pesquisa inteira, com o log acusando falha do research.py sem relação com o link."""
    chave = research.canonical_url("https://x.com:99999/a")  # não levanta
    assert chave and chave != research.canonical_url("")     # cai no fallback, não em ""
    assert chave != research.canonical_url("https://x.com/a")

    novos, barrados = research.separa_publicados(
        [_cand("A", "https://x.com:99999/a"), _cand("B", "https://x.com/b")],
        research.indice_publicados(_doc(_mem("https://x.com/b"))))
    assert len(novos) == 1 and len(barrados) == 1  # a pesquisa segue, e a guarda segue valendo


def test_dedup_e_memoria_usam_a_mesma_regua():
    """Duas normas divergentes seriam duas respostas para 'esta matéria é a mesma?'."""
    limpo = "https://exemplo.com/materia"
    marcado = "https://exemplo.com/materia?utm_source=x"
    assert len(research.dedup([_cand("A", limpo), _cand("A rebatida", marcado)])) == 1

    idx = research.indice_publicados(_doc(_mem(limpo)))
    novos, barrados = research.separa_publicados([_cand("A rebatida", marcado)], idx)
    assert novos == [] and len(barrados) == 1


# -------------------------------------------------------------------- controle positivo
def test_controle_positivo_barra_o_repetido_e_deixa_passar_o_vizinho():
    """O teste mais importante do arquivo. Mesma fonte, mesmo dia, mesmo domínio, mudando
    só o slug: sem o vizinho que continua passando, uma guarda que recusa TUDO (memória
    lida errado, chave colapsando demais) é indistinguível de uma guarda que funciona."""
    repetido = _cand("Zara abre flagship em Milão",
                     "https://www.glossy.co/fashion/zara-flagship-milao/")
    vizinho = _cand("Zara testa espelho inteligente em Madri",
                    "https://www.glossy.co/fashion/zara-espelho-madri/")
    antes = dict(vizinho)

    idx = research.indice_publicados(_doc(_mem(repetido["link"], titulo=repetido["title"])))
    novos, barrados = research.separa_publicados([repetido, vizinho], idx)

    assert len(barrados) == 1 and barrados[0]["title"] == repetido["title"]
    assert len(novos) == 1
    assert novos[0] is vizinho and novos[0] == antes  # o aprovado sai intacto


def test_memoria_vazia_e_identidade():
    itens = [_cand("A", "https://x.com/a"), _cand("B", "https://x.com/b")]
    novos, barrados = research.separa_publicados(itens, research.indice_publicados({}))
    assert barrados == []
    assert novos == itens and [id(i) for i in novos] == [id(i) for i in itens]


def test_barrado_diz_quando_saiu():
    """Sem a edição e a data, o relatório manda o operador procurar à mão em qual edição
    a matéria foi, e ele para de ler o relatório."""
    c = _cand("A", "https://x.com/a")
    idx = research.indice_publicados(_doc(_mem("https://x.com/a", edicao="2026-08-30",
                                               data="2026-08-30")))
    _, barrados = research.separa_publicados([c], idx)
    assert barrados[0]["publicado_em"] == "2026-08-30"
    assert barrados[0]["publicado_date"] == "2026-08-30"
    assert barrados[0]["motivo"] == "url"  # e não "titulo": foi o link que casou
    assert "publicado_em" not in c  # função pura: o item de entrada não foi mexido


# --------------------------------------------------------------------------- liberar
# A campanha manda a lista de `liberados` DENTRO do doc injetado, e quem compara é aqui.
# Enquanto o orchestrator filtrava por igualdade de string, o operador que digitava a URL
# como ela aparece no site não liberava nada — e nada avisava.
def test_liberar_normaliza_os_dois_lados():
    """O caso real, reproduzido: o operador copia o link do site, a memória guardou a
    versão do feed (com `www.`, barra final e `utm_source`). Mesma matéria, mesma chave
    canônica; comparadas como string, nunca casam."""
    digitado = "https://glossy.co/beauty/materia-x"
    guardado = "https://www.glossy.co/beauty/materia-x/?utm_source=rss"
    idx = research.indice_publicados(_doc(_mem(guardado), liberados=[digitado]))
    assert idx == {}

    novos, barrados = research.separa_publicados([_cand("Matéria X", guardado)], idx)
    assert barrados == [] and len(novos) == 1


def test_liberar_um_link_nao_libera_os_vizinhos():
    """O vizinho que continua barrado. Sem ele, um `liberados` que esvaziasse o índice
    inteiro passaria no teste de cima exatamente igual."""
    solto = _cand("Zara abre flagship", "https://www.glossy.co/fashion/zara-flagship/")
    preso = _cand("Zara testa espelho", "https://www.glossy.co/fashion/zara-espelho/")
    idx = research.indice_publicados(
        _doc(_mem(solto["link"]), _mem(preso["link"]),
             liberados=["https://glossy.co/fashion/zara-flagship"]))

    novos, barrados = research.separa_publicados([solto, preso], idx)
    assert [n["link"] for n in novos] == [solto["link"]]
    assert [b["link"] for b in barrados] == [preso["link"]]


@pytest.mark.parametrize("liberados", [
    None,                      # campo ausente (doc legado e fallback do orchestrator)
    [],                        # lista vazia
    ["", None, "nada", 7],     # lixo: nada disso pode liberar nem levantar
    [{"link": ""}, {}],        # forma de dicionário, também vazia
    "https://x.com/a",         # nem lista é: iterar caractere a caractere viraria chave
])
def test_liberados_ausente_vazio_ou_com_lixo_e_identidade(liberados):
    guardado = "https://www.glossy.co/fashion/zara-flagship/"
    doc = _doc(_mem(guardado), liberados=liberados)
    idx = research.indice_publicados(doc)          # não levanta
    assert list(idx) == ["glossy.co/fashion/zara-flagship"]

    _, barrados = research.separa_publicados([_cand("Zara", guardado)], idx)
    assert len(barrados) == 1                      # segue barrado


def test_liberar_aceita_a_forma_de_dicionario():
    """O contrato é lista de strings, mas quem guarda guarda `{"link": ...}`. Errar a forma
    aqui é falha silenciosa — nada liberado, nenhum aviso —, e tolerar as duas custa uma
    linha."""
    guardado = "https://www.glossy.co/beauty/materia-x/?utm_source=rss"
    idx = research.indice_publicados(
        _doc(_mem(guardado), liberados=[{"link": "https://glossy.co/beauty/materia-x"}]))
    assert idx == {}


def test_liberar_alcanca_tambem_a_camada_de_titulo():
    """Liberar tem que liberar de verdade: com `titulo_modo: on`, a MESMA entrada que saiu
    do índice de URL voltaria a barrar o candidato pela headline, e o comando do operador
    seria engolido de novo, agora por outro caminho."""
    guardado = "https://www.glossy.co/zara-provador/"
    volta = _cand(_TIT_MEM, guardado)
    doc = _doc(_mem(guardado, titulo=_TIT_MEM), titulo_modo="on",
               liberados=["https://glossy.co/zara-provador"])

    novos, barrados = research.separa_publicados([volta], research.indice_publicados(doc))
    assert barrados == []                                   # a trava de URL liberou
    novos, por_titulo, parecidos = research.aplica_camada_titulo(novos, doc)
    assert por_titulo == [] and parecidos == []             # e a de título não o repescou
    assert [n["link"] for n in novos] == [guardado]


# ------------------------------------------------------------------------- fail-open
@pytest.mark.parametrize("bruto", ["{{{", "[]", "null"])
def test_memoria_quebrada_nao_barra_ninguem(tmp_path, monkeypatch, capsys, bruto):
    """Fail-open é obrigatório: memória quebrada significa 'não barra ninguém', nunca
    'barra tudo'. Uma trava que recusa a pauta inteira quando o arquivo corrompe é
    indistinguível, no log, de uma trava funcionando bem num dia sem matéria nova."""
    monkeypatch.setattr(research, "CONFIG", tmp_path)
    (tmp_path / "publicados.json").write_text(bruto, encoding="utf-8")

    doc = research.load_publicados()
    assert doc == {}
    assert "AVISO" in capsys.readouterr().out

    itens = [_cand("A", "https://x.com/a"), _cand("B", "https://x.com/b")]
    novos, barrados = research.separa_publicados(itens, research.indice_publicados(doc))
    assert barrados == [] and novos == itens


def test_memoria_ausente_avisa_e_devolve_vazio(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(research, "CONFIG", tmp_path)
    assert research.load_publicados() == {}
    assert "publicados.json" in capsys.readouterr().out


def test_memoria_boa_e_lida_inteira(tmp_path, monkeypatch):
    monkeypatch.setattr(research, "CONFIG", tmp_path)
    (tmp_path / "publicados.json").write_text(
        json.dumps(_doc(_mem("https://x.com/a"))), encoding="utf-8")
    doc = research.load_publicados()
    assert doc["campanha"] == "daily-drops" and doc["janela_dias"] == 14
    assert list(research.indice_publicados(doc)) == ["x.com/a"]


# ----------------------------------------------------------------------------- o alerta
def test_alerta_quando_barrados_passam_os_aprovados():
    """Sinal indireto de janela grande demais ou de chave colapsando matérias distintas:
    nos dois casos a pauta encolhe sem erro nenhum aparecer no log."""
    alerta = research.avalia_pool([{}] * 4, [{}] * 5, 3)
    assert alerta and "5 barrado(s) contra 4 aprovado(s)" in alerta


def test_alerta_calado_no_caso_normal():
    assert research.avalia_pool([{}] * 8, [{}] * 2, 3) is None


def test_alerta_com_pool_vazio_e_com_pool_curto():
    assert "VAZIO" in research.avalia_pool([], [{}] * 3, 3)
    assert "abaixo do piso" in research.avalia_pool([{}] * 2, [{}], 3)


def test_avalia_pool_nao_devolve_item_nenhum():
    """Ela grita e só. Quem falha no piso real é o generate (MIN_BLOCOS), onde o contrato
    de não publicar edição capenga já está escrito."""
    assert isinstance(research.avalia_pool([], [], 3), str)


# --------------------------------------------------------------------------- parecidos
_TIT_MEM = "Zara amplia o provador virtual com realidade aumentada na Europa"


def test_parecidos_acha_o_par_obvio_e_nao_remove():
    quase = _cand("Zara amplia provador virtual com realidade aumentada na Europa",
                  "https://outro-publisher.com/zara-provador-virtual")
    doc = _doc(_mem("https://www.glossy.co/zara-provador/", titulo=_TIT_MEM))

    # a URL é outra, então a trava não pega: o item SEGUE aprovado.
    novos, barrados = research.separa_publicados([quase], research.indice_publicados(doc))
    assert barrados == [] and novos == [quase]

    achados = research.parecidos_no_historico(novos, doc)
    assert len(achados) == 1
    assert achados[0]["parecido_com"] == _TIT_MEM
    assert achados[0]["publicado_em"] == "2026-09-08"
    assert achados[0]["fonte"] == "Glossy"
    assert achados[0]["jaccard"] >= 0.45 or achados[0]["seq"] >= 0.72


def test_parecidos_nao_acusa_titulos_sem_relacao():
    longe = _cand("Nubank lança conta remunerada para menores de idade",
                  "https://x.com/nubank")
    doc = _doc(_mem("https://www.glossy.co/zara-provador/", titulo=_TIT_MEM))
    assert research.parecidos_no_historico([longe], doc) == []


def test_parecidos_ignora_memoria_sem_titulo():
    doc = _doc(_mem("https://www.glossy.co/zara-provador/", titulo=""))
    assert research.parecidos_no_historico([_cand(_TIT_MEM, "https://y.com/a")], doc) == []


# ------------------------------------------------------------------------- titulo_modo
def _quase():
    return _cand("Zara amplia provador virtual com realidade aumentada na Europa",
                 "https://outro-publisher.com/zara-provador-virtual")


def _doc_titulo(modo):
    return _doc(_mem("https://www.glossy.co/zara-provador/", titulo=_TIT_MEM),
                titulo_modo=modo)


@pytest.mark.parametrize("modo", ["sim", "ligado", "true", "", "   ", 7, None, ["on"]])
def test_modo_desconhecido_cai_em_relatorio_nunca_em_on(modo):
    """Fail-open: um typo na config não pode ligar um filtro que encolhe a pauta."""
    assert research.modo_titulo(dict(_doc_titulo("on"), titulo_modo=modo)) == "relatorio"


def test_modo_ausente_e_relatorio_e_a_caixa_nao_importa():
    assert research.modo_titulo(_doc()) == "relatorio"
    assert research.modo_titulo({}) == "relatorio"
    assert research.modo_titulo(dict(_doc(), titulo_modo=" ON ")) == "on"
    assert research.modo_titulo(dict(_doc(), titulo_modo="Off")) == "off"


def test_titulo_off_nao_calcula_e_nao_relata(monkeypatch):
    """`off` existe para poupar o cálculo, não só para calar o relatório: se ele ainda
    rodasse a similaridade, o botão estaria mentindo de novo, agora sobre o custo."""
    def explode(*a, **k):
        raise AssertionError("off não pode calcular similaridade")
    monkeypatch.setattr(research, "_acha_parecidos", explode)

    q = _quase()
    novos, barrados, parecidos = research.aplica_camada_titulo([q], _doc_titulo("off"))
    assert barrados == [] and parecidos == []
    assert [n["link"] for n in novos] == [q["link"]]


def test_titulo_relatorio_calcula_relata_e_nao_remove():
    q = _quase()
    novos, barrados, parecidos = research.aplica_camada_titulo([q], _doc_titulo("relatorio"))
    assert barrados == []
    assert novos == [q] and novos[0] is q          # o aprovado sai intacto e é o mesmo
    assert len(parecidos) == 1 and parecidos[0]["parecido_com"] == _TIT_MEM


def test_titulo_on_barra_diz_o_motivo_e_deixa_o_vizinho_passar():
    q = _quase()
    vizinho = _cand("Nubank lança conta remunerada para menores", "https://x.com/nubank")
    novos, barrados, parecidos = research.aplica_camada_titulo([q, vizinho],
                                                               _doc_titulo("on"))
    assert [n["link"] for n in novos] == [vizinho["link"]]
    assert len(barrados) == 1 and len(parecidos) == 1
    b = barrados[0]
    assert b["link"] == q["link"] and b["motivo"] == "titulo"
    assert b["parecido_com"] == _TIT_MEM          # o operador precisa saber com o quê casou
    assert b["publicado_em"] == "2026-09-08" and b["publicado_date"] == "2026-09-08"
    assert "motivo" not in q                      # função pura: a entrada não foi mexida


def test_titulo_desconhecido_nao_barra():
    """O par do fail-open, agora no comportamento e não só na leitura do campo."""
    q = _quase()
    novos, barrados, _ = research.aplica_camada_titulo([q], _doc_titulo("meia-boca"))
    assert barrados == [] and [n["link"] for n in novos] == [q["link"]]


def test_titulo_sem_memoria_com_titulo_nao_barra_ninguem():
    """Memória sem `titulo` é o caso do bloqueio manual e do legado: com `on` ligado, ela
    não pode virar uma trava que recusa tudo."""
    doc = _doc(_mem("https://www.glossy.co/zara-provador/", titulo=""), titulo_modo="on")
    novos, barrados, parecidos = research.aplica_camada_titulo([_quase()], doc)
    assert barrados == [] and parecidos == [] and len(novos) == 1


# ------------------------------------------------------------------------------- health
def test_build_health_aceita_dois_posicionais():
    """Chamada antiga (test_orchestrator_health.py) não pode quebrar."""
    h = research.build_health([("BoF", 10, 3, None)], [{"title": "a"}])
    assert h["candidates"] == 1 and h["barrados"] == 0
    assert h["barrados_itens"] == [] and h["pool_alerta"] is False
    assert h["alerta"] is None and h["parecidos"] == 0


def test_health_corta_barrados_itens_em_20():
    """O state é lido inteiro a cada consulta de edição e espelhado no Firebase: lista sem
    teto incharia o documento justamente no dia em que a memória barrasse a pauta toda."""
    barrados = [dict(_cand(f"T{i}", f"https://x.com/{i}"), publicado_em="2026-09-01",
                     publicado_date="2026-09-01") for i in range(35)]
    h = research.build_health([], [], barrados, "ALERTA: x", [{"titulo": "a"}])
    assert h["barrados"] == 35 and len(h["barrados_itens"]) == 20
    assert h["barrados_itens"][0] == {"titulo": "T0", "fonte": "Glossy",
                                      "link": "https://x.com/0",
                                      "publicado_em": "2026-09-01",
                                      "publicado_date": "2026-09-01",
                                      # o painel precisa saber POR QUE o item saiu: o
                                      # barrado por título traz um link que não está na
                                      # memória, e sem este campo o operador procura em vão.
                                      "motivo": "url"}
    assert h["pool_alerta"] is True and h["alerta"] == "ALERTA: x" and h["parecidos"] == 1


# ---------------------------------------------------------------------- main() offline
def _prepara(tmp_path, monkeypatch, publicados=None):
    cfg = tmp_path / "config"
    cfg.mkdir(exist_ok=True)
    (cfg / "feeds.yaml").write_text("feeds:\n  - source: Glossy\n    url: https://g/feed\n",
                                    encoding="utf-8")
    (cfg / "newsletter.yaml").write_text(
        "research:\n  days_lookback: 3\n  max_per_source: 25\n", encoding="utf-8")
    if publicados is not None:
        (cfg / "publicados.json").write_text(publicados, encoding="utf-8")
    monkeypatch.setattr(research, "BASE", tmp_path)
    monkeypatch.setattr(research, "CONFIG", cfg)
    monkeypatch.setattr(research, "CONTENT", tmp_path / "content")
    monkeypatch.setattr(research, "feedparser", object())  # main() só checa se existe
    return cfg


def _roda(monkeypatch, argv, candidatos, report=None):
    if report is None:
        report = [("Glossy", len(candidatos), len(candidatos), None)]
    monkeypatch.setattr(research, "collect",
                        lambda feeds, days, mps: ([dict(c) for c in candidatos], report))
    monkeypatch.setattr(sys, "argv", ["research.py", *argv])
    research.main()


def test_main_offline_tira_o_barrado_dos_tres_arquivos(tmp_path, monkeypatch, capsys):
    barrado = _cand("Zara abre flagship", "https://www.glossy.co/zara-flagship/")
    passa = _cand("Zara testa espelho", "https://www.glossy.co/zara-espelho/")
    _prepara(tmp_path, monkeypatch,
             json.dumps(_doc(_mem(barrado["link"], titulo=barrado["title"]))))
    _roda(monkeypatch, ["--edition", "2026-w37"], [barrado, passa])

    content = tmp_path / "content"
    pauta = json.loads((content / "2026-w37.research.json").read_text(encoding="utf-8"))
    assert [c["link"] for c in pauta] == [passa["link"]]

    health = json.loads((content / "2026-w37.research.health.json").read_text(encoding="utf-8"))
    assert health["barrados"] == 1
    assert health["barrados_itens"][0]["link"] == barrado["link"]
    assert health["barrados_itens"][0]["publicado_em"] == "2026-09-08"

    md = (content / "2026-w37.research.md").read_text(encoding="utf-8")
    assert "## Já publicados (barrados)" in md
    assert "daily-drops" in md and "14 dias" in md
    assert barrado["link"] in md and "2026-09-08" in md

    saida = capsys.readouterr().out
    assert "JÁ PUBLICADO em 2026-09-08" in saida  # vai para o log do Cloud Run


def test_main_grita_o_alerta_em_stdout_e_stderr(tmp_path, monkeypatch, capsys):
    """Quando o stage falha adiante, o orchestrator anexa só o `proc.stderr` ao
    health.last_error: alerta impresso apenas em stdout sumiria no caso em que importa."""
    itens = [_cand(f"T{i}", f"https://www.glossy.co/{i}/") for i in range(5)]
    _prepara(tmp_path, monkeypatch,
             json.dumps(_doc(*[_mem(c["link"]) for c in itens[:4]])))
    _roda(monkeypatch, ["--edition", "2026-w37"], itens)

    cap = capsys.readouterr()
    assert "ALERTA:" in cap.out and "ALERTA:" in cap.err
    health = json.loads(
        (tmp_path / "content" / "2026-w37.research.health.json").read_text(encoding="utf-8"))
    assert health["pool_alerta"] is True and "ALERTA:" in health["alerta"]
    md = (tmp_path / "content" / "2026-w37.research.md").read_text(encoding="utf-8")
    assert md.splitlines()[2].startswith("> **ALERTA:")  # em destaque, no topo


def test_secao_de_barrados_cabe_no_resumo_de_4000(tmp_path, monkeypatch):
    """O orchestrator devolve `md[:4000]` como resumo do Checkpoint 1. No fim do arquivo,
    esta seção seria cortada exatamente no dia de muitos barrados — o dia em que alguém
    precisa lê-la. A lista é cortada por orçamento; a seção nunca."""
    barrados = [_cand(f"Marca {i} amplia o provador virtual com realidade aumentada nas "
                      f"lojas da Europa e anuncia parceria número {i}",
                      f"https://www.glossy.co/fashion/marca-{i}-provador-virtual-realidade-"
                      f"aumentada-europa-parceria-{i}/")
                for i in range(40)]
    passa = [_cand(f"Notícia nova {i}", f"https://www.modernretail.co/nova-{i}/") for i in range(4)]
    _prepara(tmp_path, monkeypatch,
             json.dumps(_doc(*[_mem(c["link"], titulo=c["title"]) for c in barrados])))
    _roda(monkeypatch, ["--edition", "2026-w37"], barrados + passa)

    md = (tmp_path / "content" / "2026-w37.research.md").read_text(encoding="utf-8")
    inicio = md.index("## Já publicados (barrados)")
    fim = md.index("## Cobertura por fonte")
    assert inicio < fim <= 4000, f"seção termina em {fim}, fora do resumo de 4000"
    assert "e mais" in md[inicio:fim]           # a lista foi cortada, a seção não
    assert md[inicio:fim].count("- **") >= 5    # e ainda sobrou item legível
    # o corte é só do relatório: o health e o stdout continuam sabendo de todos.
    health = json.loads(
        (tmp_path / "content" / "2026-w37.research.health.json").read_text(encoding="utf-8"))
    assert health["barrados"] == 40


def test_secao_de_parecidos_aparece_e_nao_remove(tmp_path, monkeypatch):
    quase = _cand("Zara amplia provador virtual com realidade aumentada na Europa",
                  "https://outro-publisher.com/zara-provador-virtual")
    outros = [_cand(f"Assunto {i}", f"https://x.com/{i}") for i in range(3)]
    _prepara(tmp_path, monkeypatch,
             json.dumps(_doc(_mem("https://www.glossy.co/zara-provador/", titulo=_TIT_MEM))))
    _roda(monkeypatch, ["--edition", "2026-w37"], [quase] + outros)

    pauta = json.loads(
        (tmp_path / "content" / "2026-w37.research.json").read_text(encoding="utf-8"))
    assert quase["link"] in [c["link"] for c in pauta]  # relata, não remove
    md = (tmp_path / "content" / "2026-w37.research.md").read_text(encoding="utf-8")
    assert "## Parecidos com o já publicado (não barrados)" in md
    health = json.loads(
        (tmp_path / "content" / "2026-w37.research.health.json").read_text(encoding="utf-8"))
    assert health["parecidos"] == 1


def test_test_feeds_ignora_a_memoria(tmp_path, monkeypatch, capsys):
    """`--test-feeds` é o diagnóstico por fonte que o operador lê no `sources test`: ele
    responde sobre a saúde do feed, nunca sobre curadoria. Filtrar ali faria fonte saudável
    aparecer vazia e mandaria alguém trocar uma URL que estava certa."""
    itens = [_cand("A", "https://www.glossy.co/a/"), _cand("B", "https://www.glossy.co/b/")]
    cfg = _prepara(tmp_path, monkeypatch)

    _roda(monkeypatch, ["--test-feeds"], itens)
    sem_memoria = capsys.readouterr().out

    (cfg / "publicados.json").write_text(
        json.dumps(_doc(*[_mem(c["link"]) for c in itens])), encoding="utf-8")
    _roda(monkeypatch, ["--test-feeds"], itens)
    com_memoria = capsys.readouterr().out

    assert sem_memoria == com_memoria
    assert json.loads(sem_memoria)["report"] == [{"source": "Glossy", "found": 2,
                                                  "kept": 2, "error": None}]


def test_alerta_de_pauta_magra_avisa_antes_do_piso():
    """O piso duro (o generate recusar a edição) chega tarde demais para ser útil: quando o
    pool tem 3 itens não há mais o que fazer naquele dia. `alerta_em` é o aviso com folga."""
    cands = [{"title": f"t{i}", "link": f"https://x.com/{i}"} for i in range(9)]
    assert research.avalia_pool(cands, [], 3) is None          # sem folga configurada, cala
    magro = research.avalia_pool(cands, [], 3, alerta_em=12)
    assert magro and "pool magro" in magro
    assert "A edição sai" in magro                             # avisa sem alarmar demais
    farto = [{"title": f"t{i}", "link": f"https://x.com/{i}"} for i in range(20)]
    assert research.avalia_pool(farto, [], 3, alerta_em=12) is None


def test_pauta_magra_nao_encobre_o_alerta_de_piso():
    """Ordem importa: pool abaixo do piso é 'a edição NÃO sai', e não pode ser rebaixado
    para o aviso ameno de pauta magra só porque também está abaixo da folga."""
    dois = [{"title": "t", "link": "https://x.com/1"}, {"title": "u", "link": "https://x.com/2"}]
    msg = research.avalia_pool(dois, [], 3, alerta_em=12)
    assert "ALERTA" in msg and "não sai" in msg.replace("A edição não sai", "não sai")


def test_main_le_o_pool_min_alerta_do_newsletter_yaml(tmp_path, monkeypatch, capsys):
    """A ligação entre a config e a função, exercida pelo `main`.

    `avalia_pool` tinha teste próprio e `main` tinha teste próprio, e mesmo assim tirar o
    `pool_min_alerta` da chamada em `main` não derrubava nada: a chave podia estar no YAML,
    documentada, e simplesmente não ser lida. Este teste roda `main` com um limiar alto o
    bastante para disparar e exige o aviso no health e no relatório."""
    cfg = _prepara(tmp_path, monkeypatch)
    (cfg / "newsletter.yaml").write_text(
        "research:\n  days_lookback: 3\n  max_per_source: 25\n  pool_min_alerta: 99\n",
        encoding="utf-8")
    cands = [{"title": f"t{i}", "content": "", "date": "", "link": f"https://x.com/{i}",
              "source": "Glossy", "categories": ""} for i in range(5)]
    _roda(monkeypatch, ["--edition", "2026-09-10"], cands)
    health = json.loads((tmp_path / "content" / "2026-09-10.research.health.json")
                        .read_text(encoding="utf-8"))
    assert health["pool_alerta"] is True
    assert "pool magro" in health["alerta"]
    assert "pool magro" in (tmp_path / "content" / "2026-09-10.research.md").read_text(
        encoding="utf-8")


def test_main_sem_pool_min_alerta_no_yaml_nao_alerta(tmp_path, monkeypatch):
    """O vizinho que continua passando: sem a chave, a mesma pauta não vira alerta. Sem ele,
    um `avalia_pool` que alertasse sempre passaria pelo teste de cima."""
    _prepara(tmp_path, monkeypatch)  # newsletter.yaml sem pool_min_alerta
    cands = [{"title": f"t{i}", "content": "", "date": "", "link": f"https://x.com/{i}",
              "source": "Glossy", "categories": ""} for i in range(5)]
    _roda(monkeypatch, ["--edition", "2026-09-10"], cands)
    health = json.loads((tmp_path / "content" / "2026-09-10.research.health.json")
                        .read_text(encoding="utf-8"))
    assert health["pool_alerta"] is False
    assert health["alerta"] is None


def test_main_com_titulo_on_barra_de_ponta_a_ponta(tmp_path, monkeypatch, capsys):
    """A ligação entre a config e a camada, exercida pelo `main`.

    Teste de unidade sozinho deixaria passar o caso em que o `main` não repassa o modo —
    o botão confirmando `titulo_modo: on` no relatório e nada mudando na pauta. É o mesmo
    furo que já apareceu nesta branch com o `pool_min_alerta`, e o vizinho que fecha a
    dupla é `test_secao_de_parecidos_aparece_e_nao_remove`: doc sem `titulo_modo`, mesma
    pauta, item nenhum removido."""
    quase = _cand("Zara amplia provador virtual com realidade aumentada na Europa",
                  "https://outro-publisher.com/zara-provador-virtual")
    outros = [_cand(f"Assunto {i}", f"https://x.com/{i}") for i in range(3)]
    _prepara(tmp_path, monkeypatch,
             json.dumps(_doc(_mem("https://www.glossy.co/zara-provador/", titulo=_TIT_MEM),
                             titulo_modo="on")))
    _roda(monkeypatch, ["--edition", "2026-w37"], [quase] + outros)
    content = tmp_path / "content"

    pauta = json.loads((content / "2026-w37.research.json").read_text(encoding="utf-8"))
    assert [c["link"] for c in pauta] == [o["link"] for o in outros]  # saiu da pauta

    health = json.loads((content / "2026-w37.research.health.json").read_text(encoding="utf-8"))
    assert health["barrados"] == 1 and health["parecidos"] == 1
    item = health["barrados_itens"][0]
    assert item["link"] == quase["link"] and item["motivo"] == "titulo"
    assert item["publicado_em"] == "2026-09-08"

    md = (content / "2026-w37.research.md").read_text(encoding="utf-8")
    assert "## Já publicados (barrados)" in md
    assert "barrado por TÍTULO" in md and _TIT_MEM[:40] in md
    assert quase["link"] in md                      # o link está na lista, achável
    # a seção de "não barrados" seria mentira aqui: eles foram barrados.
    assert "## Parecidos com o já publicado (não barrados)" not in md
    assert "JÁ PUBLICADO (por TÍTULO" in capsys.readouterr().out


def test_main_com_titulo_off_nao_relata_nada(tmp_path, monkeypatch):
    """O terceiro modo pelo mesmo caminho: nem seção, nem contagem, nem remoção."""
    quase = _cand("Zara amplia provador virtual com realidade aumentada na Europa",
                  "https://outro-publisher.com/zara-provador-virtual")
    outros = [_cand(f"Assunto {i}", f"https://x.com/{i}") for i in range(3)]
    _prepara(tmp_path, monkeypatch,
             json.dumps(_doc(_mem("https://www.glossy.co/zara-provador/", titulo=_TIT_MEM),
                             titulo_modo="off")))
    _roda(monkeypatch, ["--edition", "2026-w37"], [quase] + outros)
    content = tmp_path / "content"

    pauta = json.loads((content / "2026-w37.research.json").read_text(encoding="utf-8"))
    assert len(pauta) == 4                                   # nada removido
    health = json.loads((content / "2026-w37.research.health.json").read_text(encoding="utf-8"))
    assert health["parecidos"] == 0 and health["barrados"] == 0
    md = (content / "2026-w37.research.md").read_text(encoding="utf-8")
    assert "Parecidos com o já publicado" not in md


def test_main_aplica_o_liberados_do_doc_injetado(tmp_path, monkeypatch):
    """`main` passa o doc INTEIRO para o índice: um recorte só com `links` mataria o
    `liberar` sem erro nenhum, que é a forma como este defeito nasceu."""
    guardado = "https://www.glossy.co/beauty/materia-x/?utm_source=rss"
    volta = _cand("Matéria X", guardado)
    outro = _cand("Matéria Y", "https://www.glossy.co/beauty/materia-y/")
    _prepara(tmp_path, monkeypatch,
             json.dumps(_doc(_mem(guardado), _mem(outro["link"]),
                             liberados=["https://glossy.co/beauty/materia-x"])))
    _roda(monkeypatch, ["--edition", "2026-w37"], [volta, outro])
    content = tmp_path / "content"

    pauta = json.loads((content / "2026-w37.research.json").read_text(encoding="utf-8"))
    assert [c["link"] for c in pauta] == [guardado]          # liberado voltou à pauta
    health = json.loads((content / "2026-w37.research.health.json").read_text(encoding="utf-8"))
    assert health["barrados"] == 1                           # e o vizinho segue barrado
    assert health["barrados_itens"][0]["link"] == outro["link"]
