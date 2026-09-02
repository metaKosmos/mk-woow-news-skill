# broker/tests/test_provenance.py — MAR-483: o link da matéria vem do feed, não do Gemini.
#
# O defeito que originou estes testes: o Escritor escrevia o próprio <a href> dentro do
# corpo, e três edições saíram com link inventado (2 raízes de domínio e 3 404). Agora o
# Escritor devolve `source_id` e uma frase marcada; o href é copiado do research.json.
import pathlib
import sys

import pytest

BROKER = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BROKER))
sys.path.insert(0, str(BROKER / "pipeline"))

import generate_content as gc  # noqa: E402

TEMPLATES = BROKER / "templates"
# Uma bandeirinha separadora de bloco são 6 células de 4px. As outras faixas coloridas do
# template (topo e CTA) usam outra altura, então contar por aqui só pega as separadoras.
CELULA_DE_BANDEIRINHA = 'height="4"'
CELULAS_POR_BANDEIRINHA = 6


def _pool(n=5):
    """Itens como saem do score(): id estável, link real do feed."""
    return [{"id": i, "title": f"Título {i}", "source": f"Fonte {i}", "score": 100 - i,
             "link": f"https://exemplo{i}.com.br/materia-{i}"} for i in range(n)]


def _bloco(source_id, texto="frase clicável", corpo=None):
    return {"headline": f"Headline {source_id}", "source_id": source_id,
            "corpo": corpo if corpo is not None else
            f"<p>Abertura com 42% de gancho.</p><p><strong data-link>{texto}</strong></p>"}


def _content(source_ids, sumario=None):
    c = {"cabecalho": "[WDD] WooW! Daily Drops · quarta-feira, 02 de setembro de 2026",
         "titulo_edicao": "Título da edição",
         "sumario": sumario if sumario is not None else
         [f"item {i}" for i in range(len(source_ids))]}
    for campo, sid in zip(gc.BLOCK_FIELDS, source_ids):
        c[campo] = _bloco(sid)
    return c


# --------------------------------------------------------------- controle positivo
def test_controle_positivo_cinco_itens_sadios_passam_intactos():
    """Sem este teste, uma guarda que derruba tudo imita uma guarda que funciona."""
    pool = _pool(5)
    out, prov = gc.apply_provenance(_content([0, 1, 2, 3, 4]), pool)
    assert [p["campo"] for p in prov["itens"]] == gc.BLOCK_FIELDS
    assert prov["descartados"] == []
    assert len(out["sumario"]) == 5
    for campo, item in zip(gc.BLOCK_FIELDS, pool):
        # href copiado do feed, byte a byte
        assert f'<a href="{item["link"]}">' in out[campo]["corpo"]
        assert "data-link" not in out[campo]["corpo"]


def test_link_publicado_e_o_do_item_apontado_nao_o_da_posicao():
    """source_id 3 na manchete tem que trazer o link do item 3, não o do topo do pool."""
    pool = _pool(5)
    out, _ = gc.apply_provenance(_content([3, 0, 1, 2, 4]), pool)
    assert f'href="{pool[3]["link"]}"' in out["manchete"]["corpo"]


# --------------------------------------------------------------- procedência
def test_source_id_fora_do_pool_derruba_so_aquele_bloco():
    out, prov = gc.apply_provenance(_content([0, 1, 99, 3, 4]), _pool(5))
    assert len(prov["itens"]) == 4
    assert [d["motivo"] for d in prov["descartados"]] == ["source_id_fora_do_pool"]
    assert prov["descartados"][0]["campo"] == "secundaria_2"
    assert "sinal_2" not in out  # 4 itens ocupam os 4 primeiros campos


@pytest.mark.parametrize("sid", [None, "", "sete", -1, 3.5])
def test_source_id_invalido_derruba(sid):
    c = _content([0, 1, 2, 3, 4])
    c["sinal_1"]["source_id"] = sid
    _, prov = gc.apply_provenance(c, _pool(5))
    assert len(prov["itens"]) == 4
    assert prov["descartados"][0]["motivo"] in ("source_id_ausente", "source_id_fora_do_pool")


def test_source_id_ausente_derruba():
    c = _content([0, 1, 2, 3, 4])
    del c["sinal_2"]["source_id"]
    _, prov = gc.apply_provenance(c, _pool(5))
    assert [d["motivo"] for d in prov["descartados"]] == ["source_id_ausente"]


def test_item_do_pool_sem_link_derruba():
    pool = _pool(5)
    pool[2]["link"] = ""
    _, prov = gc.apply_provenance(_content([0, 1, 2, 3, 4]), pool)
    assert [d["motivo"] for d in prov["descartados"]] == ["item_sem_link"]


@pytest.mark.parametrize("corpo,motivo", [
    ("<p>Nota inteira sem marcador nenhum.</p>", "sem_marcador_de_link"),
    ("<p><strong data-link>uma</strong> e <strong data-link>outra</strong></p>",
     "marcadores_de_link_demais"),
])
def test_marcador_de_link_fora_do_contrato_derruba(corpo, motivo):
    c = _content([0, 1, 2, 3, 4])
    c["secundaria_1"]["corpo"] = corpo
    _, prov = gc.apply_provenance(c, _pool(5))
    assert [d["motivo"] for d in prov["descartados"]] == [motivo]


def test_href_alucinado_remanescente_derruba_o_bloco():
    """Rede de segurança: o prompt proíbe <a>, mas se o Escritor escrever um assim mesmo,
    o destino não está no pool e o item cai. É a assinatura exata do defeito de 31/08."""
    c = _content([0, 1, 2, 3, 4])
    c["sinal_1"]["corpo"] = ('<p>Gancho.</p><p><strong data-link>frase</strong> '
                             'e <a href="https://news.shopify.com">inventado</a></p>')
    out, prov = gc.apply_provenance(c, _pool(5))
    assert [d["motivo"] for d in prov["descartados"]] == ["link_fora_do_pool"]
    assert "news.shopify.com" not in str(out)


def test_href_de_outro_item_do_pool_sobrevive_por_estar_na_pauta():
    """Contraprova do teste acima: a guarda mede procedência, não formatação. Um link que
    ESTÁ no pool passa, senão 'tudo cai' se disfarçaria de guarda funcionando."""
    pool = _pool(5)
    c = _content([0, 1, 2, 3, 4])
    c["sinal_1"]["corpo"] = (f'<p>Gancho.</p><p><strong data-link>frase</strong> '
                             f'e <a href="{pool[4]["link"]}">vizinho</a></p>')
    _, prov = gc.apply_provenance(c, pool)
    assert prov["descartados"] == []


def test_link_com_aspas_nao_quebra_o_atributo():
    pool = _pool(5)
    pool[0]["link"] = 'https://exemplo.com/a?b="x"&c=1'
    out, prov = gc.apply_provenance(_content([0, 1, 2, 3, 4]), pool)
    assert prov["descartados"] == []
    assert '&quot;' in out["manchete"]["corpo"]
    assert 'href="https://exemplo.com/a?b=&quot;x&quot;&amp;c=1"' in out["manchete"]["corpo"]


# --------------------------------------------------------------- edição encolhe
def test_sumario_perde_o_item_do_bloco_derrubado_nao_o_ultimo():
    c = _content([0, 1, 99, 3, 4], sumario=["um", "dois", "três", "quatro", "cinco"])
    out, _ = gc.apply_provenance(c, _pool(5))
    assert out["sumario"] == ["um", "dois", "quatro", "cinco"]


def test_tres_elegiveis_publicam_tres_e_validate_passa():
    c = _content([0, 1, 2, 88, 99])
    out, prov = gc.apply_provenance(c, _pool(5))
    assert prov["publicados"] == 3
    assert set(gc.BLOCK_FIELDS[:3]) <= set(out)
    assert "sinal_1" not in out and "sinal_2" not in out
    assert len(out["sumario"]) == 3
    gc.validate(out)  # não levanta


def test_dois_elegiveis_derrubam_a_edicao_inteira():
    """Abaixo do piso o generate falha, e é isso que impede o send: run_daily chama os
    estágios em sequência e a exceção interrompe antes do envio."""
    c = _content([0, 1, 77, 88, 99])
    out, prov = gc.apply_provenance(c, _pool(5))
    assert prov["publicados"] == 2
    with pytest.raises(SystemExit):
        gc.validate(out)


def test_edicao_que_ja_nasce_com_tres_blocos_e_valida():
    """Pool curto no dia: o Escritor entrega 3 campos e não há descarte nenhum."""
    c = _content([0, 1, 2])
    out, prov = gc.apply_provenance(c, _pool(3))
    assert prov["descartados"] == [] and prov["publicados"] == 3
    gc.validate(out)


def test_sumario_de_tamanho_errado_e_recusado():
    c = _content([0, 1, 2, 3, 4], sumario=["um", "dois"])
    out, _ = gc.apply_provenance(c, _pool(5))
    with pytest.raises(SystemExit):
        gc.validate(out)


# --------------------------------------------------------------- o Escritor não vê URL
def test_write_edition_nao_manda_link_ao_escritor(monkeypatch):
    """A alucinação aconteceu COM os links reais no prompt. Agora eles não vão."""
    capturado = {}

    def _fake(cfg, key, model, system_prompt, user_data, expect, **kw):
        capturado["user_data"] = user_data
        return {}

    monkeypatch.setattr(gc, "gemini_json", _fake)
    pool = _pool(5)
    # `content` e `score` existem no pool real (vêm do feed e do score()); sem eles a
    # asserção passaria por vacuidade, sem provar que o campo certo foi retirado.
    for i, c in enumerate(pool):
        c["content"] = f"Resumo da matéria {i}, com números e contexto."
        c["score_justification"] = "cabe no território"
    gc.write_edition({"model_write": "m", "write_thinking_budget": 0}, "k", "prompt",
                     pool, "quarta-feira, 02 de setembro de 2026")
    enviado = capturado["user_data"]
    for item in pool:
        assert item["link"] not in enviado
    assert "exemplo0.com.br" not in enviado
    assert "link" not in enviado
    # controle positivo: tudo o mais continua indo. O prompt decide a manchete pelo maior
    # score em cinco pontos, e o corpo da nota sai do `content`.
    assert '"id": 0' in enviado
    assert '"score": 100' in enviado
    assert "Resumo da matéria 0" in enviado


# --------------------------------------------------------------- checagem de link (registra)
def test_check_links_registra_e_nao_derruba(monkeypatch):
    """403 de publisher que bloqueia IP de datacenter não pode virar veto: ebay.com deu
    403 na medição de 02/09 e é link legítimo, vindo do feed."""
    monkeypatch.setattr(gc, "_http_status", lambda url, timeout: (403, url))
    rel = gc.check_links([{"campo": "manchete", "link": "https://www.ebay.com/x"}])
    assert rel["itens"][0]["status"] == 403
    assert rel["suspeitos"] == []          # 403 não é suspeita, é bloqueio de bot
    assert rel["sem_path"] == []


def test_check_links_marca_raiz_de_dominio_e_404(monkeypatch):
    monkeypatch.setattr(gc, "_http_status",
                        lambda url, timeout: (404 if "morta" in url else 200, url))
    rel = gc.check_links([{"campo": "manchete", "link": "https://news.shopify.com"},
                          {"campo": "sinal_1", "link": "https://x.com/materia-morta"}])
    assert rel["sem_path"] == ["manchete"]
    assert [s["campo"] for s in rel["suspeitos"]] == ["manchete", "sinal_1"]


def test_check_links_nao_explode_quando_a_rede_cai(monkeypatch):
    def _boom(url, timeout):
        raise OSError("sem rede")
    monkeypatch.setattr(gc, "_http_status", _boom)
    rel = gc.check_links([{"campo": "manchete", "link": "https://x.com/a"}])
    assert rel["itens"][0]["erro"]
    assert rel["suspeitos"] == []  # falha de instrumento não vira acusação


# --------------------------------------------------------------- template encolhe junto
def _render(content):
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=False)
    return env.get_template("woow-daily-drops.html.j2").render(
        content=content, imagem_manchete_url="", unsubscribe_url="#")


def _content_render(n):
    c = {"cabecalho": "[WDD]", "titulo_edicao": "T", "sumario": [f"i{i}" for i in range(n)]}
    for campo in gc.BLOCK_FIELDS[:n]:
        c[campo] = {"headline": f"H {campo}", "corpo": f"<p>corpo {campo}</p>"}
    return c


def test_template_com_cinco_itens_mostra_tudo():
    html = _render(_content_render(5))
    assert "Sinais do dia" in html
    for campo in gc.BLOCK_FIELDS:
        assert f"H {campo}" in html


def test_template_sem_sinais_nao_mostra_o_cabecalho_da_secao():
    html = _render(_content_render(3))
    assert "Sinais do dia" not in html
    assert "H secundaria_2" in html and "H sinal_1" not in html


@pytest.mark.parametrize("n,bandeirinhas", [(1, 0), (2, 1), (3, 2), (5, 2)])
def test_bandeirinha_some_junto_com_o_bloco_que_ela_precede(n, bandeirinhas):
    """A listra colorida pertence ao bloco que ela anuncia. Fora do condicional, a edição
    curta terminaria numa faixa de cor anunciando notícia que não existe."""
    html = _render(_content_render(n))
    assert html.count(CELULA_DE_BANDEIRINHA) == bandeirinhas * CELULAS_POR_BANDEIRINHA


def test_template_nao_deixa_titulo_vazio_quando_encolhe():
    import re
    html = _render(_content_render(3))
    assert not re.search(r"<h[23][^>]*>\s*</h[23]>", html)


# ------------------------------------------------- a procedência chega ao painel
def test_generate_espelha_procedencia_e_links_no_estado(tmp_path, monkeypatch):
    """Descarte silencioso é o mesmo que descarte nenhum: quem opera precisa ver no
    `queue` que a edição saiu com 4 itens e por que o quinto caiu."""
    import json
    import orchestrator
    from state_manager import StateManager, LocalStore

    sm = StateManager(LocalStore(tmp_path))
    wd = tmp_path / "wd"
    (wd / "renders").mkdir(parents=True)
    (wd / "content").mkdir()
    prov = {"pool_size": 8, "publicados": 4,
            "itens": [{"campo": "manchete", "source_id": 2, "source": "AR Insider",
                       "link": "https://arinsider.co/x", "titulo_fonte": "T"}],
            "descartados": [{"campo": "sinal_2", "headline": "H", "source_id": 99,
                             "motivo": "source_id_fora_do_pool", "detalhe": ""}]}
    checagem = {"checked_at": "2026-09-02T10:00:00", "sem_path": [],
                "suspeitos": [{"campo": "manchete", "link": "https://x", "status": 404,
                               "motivo": "http_4xx"}]}

    def _fake_run(w, script, args):
        if script == "generate_content.py":
            (w / "content" / "2026-09-03.json").write_text(json.dumps(
                {"meta": {"subject": "S", "edition_date": "2026-09-03"},
                 "content": {}, "provenance": prov, "link_check": checagem}), encoding="utf-8")
            (w / "content" / "2026-09-03.usage.json").write_text("{}", encoding="utf-8")
        return ""

    monkeypatch.setattr(orchestrator, "_sm", lambda: sm)
    monkeypatch.setattr(orchestrator, "_workdir", lambda ed: wd)
    monkeypatch.setattr(orchestrator, "_restore_content", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator, "_run_script", _fake_run)
    monkeypatch.setattr(orchestrator, "_publish_edition_html",
                        lambda s, w, ed, src, by="": (f"https://pub/{ed}.html", ""))

    out = orchestrator.run_stage("2026-09-03", "generate", {})
    assert out["itens"] == 4
    assert out["descartados"] == ["source_id_fora_do_pool"]
    assert out["links_suspeitos"][0]["status"] == 404
    st = sm.get_state("2026-09-03")
    assert st["provenance"]["publicados"] == 4
    assert st["link_check"]["suspeitos"][0]["campo"] == "manchete"


def test_generate_emite_o_stdout_do_pipeline_no_log(tmp_path, monkeypatch, capsys):
    """Os números do funil (candidatos -> território -> pool -> publicados) e cada DESCARTADO
    só existem no stdout do subprocess. Capturado e descartado, como era antes, não dava para
    responder 'quantos itens o Escritor recebeu no dia em que inventou uma notícia'."""
    import json
    import orchestrator
    from state_manager import StateManager, LocalStore

    sm = StateManager(LocalStore(tmp_path))
    wd = tmp_path / "wd"
    (wd / "renders").mkdir(parents=True)
    (wd / "content").mkdir()

    def _fake_run(w, script, args):
        if script == "generate_content.py":
            (w / "content" / "2026-09-03.json").write_text(json.dumps(
                {"meta": {}, "content": {}, "provenance": {"publicados": 4, "descartados": []},
                 "link_check": None}), encoding="utf-8")
            (w / "content" / "2026-09-03.usage.json").write_text("{}", encoding="utf-8")
            return "Candidatos: 72\nNo território: 9\n  DESCARTADO sinal_2 (source_id_fora_do_pool): H"
        return ""

    monkeypatch.setattr(orchestrator, "_sm", lambda: sm)
    monkeypatch.setattr(orchestrator, "_workdir", lambda ed: wd)
    monkeypatch.setattr(orchestrator, "_restore_content", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator, "_run_script", _fake_run)
    monkeypatch.setattr(orchestrator, "_publish_edition_html",
                        lambda s, w, ed, src, by="": ("https://pub/x.html", ""))

    orchestrator.run_stage("2026-09-03", "generate", {})
    saida = capsys.readouterr().out
    assert "Candidatos: 72" in saida
    assert "DESCARTADO sinal_2" in saida


# ------------------------------------------- achados da revisão adversarial (MAR-483)
def test_source_id_repetido_derruba_a_segunda_nota():
    """Cinco notas apontando o mesmo item sairiam com o MESMO link e `descartados: []`:
    a edição pareceria ter cinco fontes tendo uma só."""
    c = _content([2, 2, 2, 2, 2])
    out, prov = gc.apply_provenance(c, _pool(5))
    assert prov["publicados"] == 1
    assert [d["motivo"] for d in prov["descartados"]] == ["source_id_repetido"] * 4
    with pytest.raises(SystemExit):
        gc.validate(out, prov)


def test_source_id_repetido_preserva_a_primeira_ocorrencia():
    c = _content([0, 1, 1, 3, 4])
    out, prov = gc.apply_provenance(c, _pool(5))
    assert prov["publicados"] == 4
    assert [i["source_id"] for i in prov["itens"]] == [0, 1, 3, 4]


def test_href_sem_aspas_nao_atravessa_a_guarda():
    """<a href=https://x> é HTML que o cliente de e-mail abre. A regex antiga só via
    href entre aspas, então esse link inventado saía clicável."""
    c = _content([0, 1, 2, 3, 4])
    c["sinal_1"]["corpo"] = ('<p>g</p><p><strong data-link>f</strong> '
                             '<a href=https://news.shopify.com>x</a></p>')
    out, prov = gc.apply_provenance(c, _pool(5))
    assert [d["motivo"] for d in prov["descartados"]] == ["link_fora_do_pool"]
    assert "news.shopify.com" not in str(out)


@pytest.mark.parametrize("campo", ["titulo_edicao", "cabecalho"])
def test_tag_em_campo_de_texto_e_removida(campo):
    """O template interpola estes campos SEM escape (autoescape off), então tag aqui vira
    HTML vivo no e-mail."""
    c = _content([0, 1, 2, 3, 4])
    c[campo] = "antes <a href='https://news.shopify.com'>clique</a> depois"
    out, _ = gc.apply_provenance(c, _pool(5))
    assert "<a" not in out[campo] and "news.shopify.com" not in out[campo]
    assert "antes" in out[campo] and "clique" in out[campo]


def test_tag_no_sumario_e_na_headline_e_removida():
    c = _content([0, 1, 2, 3, 4],
                 sumario=["um", "<a href='https://news.shopify.com'>dois</a>", "três", "quatro", "cinco"])
    c["manchete"]["headline"] = "<a href='https://x.com'>manchete</a>"
    out, _ = gc.apply_provenance(c, _pool(5))
    assert "<a" not in " ".join(out["sumario"])
    assert "news.shopify.com" not in " ".join(out["sumario"])
    assert "<a" not in out["manchete"]["headline"]


def test_manchete_descartada_troca_o_assunto_do_email():
    """O assunto foi escrito para a manchete. Se ela cai, outra notícia sobe ao topo e o
    assunto passa a prometer matéria que não está dentro."""
    c = _content([0, 1, 2, 3, 4])
    c["titulo_edicao"] = "Amazon liga provador 3D em 12 mil SKUs"
    c["manchete"]["corpo"] = "<p>nota sem marcador</p>"
    out, prov = gc.apply_provenance(c, _pool(5))
    assert prov["titulo_substituido"] is True
    assert out["titulo_edicao"] == out["manchete"]["headline"]
    assert "Amazon" not in out["titulo_edicao"]


def test_assunto_intacto_quando_a_manchete_sobrevive():
    """Controle positivo: sem descarte na manchete, o título trabalhado do Escritor fica."""
    c = _content([0, 1, 2, 3, 4])
    c["titulo_edicao"] = "Um título bem trabalhado"
    out, prov = gc.apply_provenance(c, _pool(5))
    assert prov["titulo_substituido"] is False
    assert out["titulo_edicao"] == "Um título bem trabalhado"


def test_zero_blocos_cai_na_mensagem_do_piso_e_acusa_o_prompt():
    """Formato antigo (<strong><a href>) é o que o modelo produziu todo dia até 02/09: é o
    modo de falha mais provável na primeira manhã. A mensagem tem que dizer isso, não
    mandar ampliar a janela de dias."""
    c = _content([0, 1, 2, 3, 4])
    for campo in gc.BLOCK_FIELDS:
        c[campo]["corpo"] = "<p>x</p><p><strong><a href='https://ex0.com.br/m-0'>frase</a></strong></p>"
    out, prov = gc.apply_provenance(c, _pool(5))
    assert prov["publicados"] == 0
    with pytest.raises(SystemExit) as e:
        gc.validate(out, prov)
    msg = str(e.value)
    assert "piso" in msg and "sem_marcador_de_link" in msg
    assert "não seguiu o prompt" in msg
    assert "manchete" not in msg  # a mensagem velha mandava procurar campo faltando


def test_diagnostico_nao_culpa_o_prompt_quando_o_motivo_e_outro():
    prov = {"descartados": [{"motivo": "source_id_fora_do_pool"}, {"motivo": "sem_marcador_de_link"}]}
    msg = gc._diagnostico(prov)
    assert "não seguiu o prompt" not in msg
    assert "source_id_fora_do_pool" in msg


def test_stderr_do_pipeline_chega_ao_health_e_o_stdout_nao(tmp_path):
    """Controle positivo do canal: é por isso que os DESCARTADO também vão para stderr.
    Quando validate() derruba o script, _run_script só anexa proc.stderr ao RuntimeError,
    então o motivo impresso apenas em stdout desaparece justamente no caso em que importa."""
    import orchestrator
    (tmp_path / "pipeline").mkdir()
    (orchestrator.PIPELINE / "_teste_canal.py").write_text(
        "import sys\n"
        "print('SO_NO_STDOUT')\n"
        "print('  DESCARTADO sinal_2 (sem_marcador_de_link): H', file=sys.stderr)\n"
        "sys.exit('Só 0 item(ns) com fonte confirmada')\n", encoding="utf-8")
    try:
        with pytest.raises(RuntimeError) as e:
            orchestrator._run_script(tmp_path, "_teste_canal.py", [])
        msg = str(e.value)
        assert "DESCARTADO sinal_2" in msg
        assert "SO_NO_STDOUT" not in msg
    finally:
        (orchestrator.PIPELINE / "_teste_canal.py").unlink()


def test_relata_descartes_escreve_nos_dois_canais(capsys):
    """Prova que o pipeline USA o canal que o teste acima prova existir. Sem esta asserção,
    trocar `file=sys.stderr` por um print comum passaria despercebido."""
    gc.relata_descartes({"publicados": 4, "titulo_substituido": True,
                         "descartados": [{"campo": "sinal_2", "motivo": "source_id_repetido",
                                          "headline": "H"}]})
    cap = capsys.readouterr()
    assert "DESCARTADO sinal_2 (source_id_repetido)" in cap.err
    assert "DESCARTADO sinal_2 (source_id_repetido)" in cap.out
    assert "manchete original caiu" in cap.err
    assert "4 de 5 redigidos" in cap.out


# ---------------------------------------------- o prompt é a metade sem teste de execução
def _prompt():
    return (BROKER / "config" / "prompts" / "write.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("proibido", ["href='URL", 'href="URL', "URL_DA_MATERIA"])
def test_prompt_nao_pede_mais_que_o_modelo_escreva_url(proibido):
    """A instrução que causou a MAR-483 não pode voltar por edição descuidada."""
    assert proibido not in _prompt()


def test_prompt_ensina_a_marcacao_e_o_source_id():
    p = _prompt()
    assert "<strong data-link>" in p
    assert "source_id" in p
    # o exemplo completo de nota precisa estar no formato que o pipeline aceita
    assert '"source_id": 23' in p and "<p>" in p


def test_prompt_nao_usa_o_mesmo_source_id_de_exemplo_em_todos_os_blocos():
    """Placeholder repetido é copiado literalmente, e com a guarda de id repetido isso
    derrubaria 4 das 5 notas."""
    import re
    ids = re.findall(r'"source_id":\s*(\d+)', _prompt())
    assert len(ids) >= 5
    assert len(set(ids)) >= 5, f"ids de exemplo repetidos: {ids}"


def test_prompt_admite_edicao_com_menos_de_cinco():
    p = _prompt()
    assert "até 5 notícias" in p
    assert "UM item por notícia escrita" in p


def test_queue_carrega_a_procedencia(tmp_path):
    """O descarte precisa aparecer onde alguém olha. `woow status` e o painel leem a queue,
    não o estado da edição, então sem estes campos a guarda trabalha em silêncio."""
    from state_manager import StateManager, LocalStore
    sm = StateManager(LocalStore(tmp_path))
    sm.upsert_edition("2026-09-03", {
        "stage": "ready",
        "provenance": {"publicados": 4, "descartados": [{"campo": "sinal_2", "motivo": "x"}]},
        "link_check": {"suspeitos": [{"campo": "manchete"}]}})
    linha = next(r for r in sm.get_queue()["editions"] if r["edition"] == "2026-09-03")
    assert linha["itens"] == 4
    assert linha["descartados"] == 1
    assert linha["links_suspeitos"] == 1


def test_queue_de_edicao_sem_procedencia_nao_quebra(tmp_path):
    """Edição legada (gerada antes desta versão) não tem provenance nenhum."""
    from state_manager import StateManager, LocalStore
    sm = StateManager(LocalStore(tmp_path))
    sm.upsert_edition("2026-08-24", {"stage": "sent"})
    linha = next(r for r in sm.get_queue()["editions"] if r["edition"] == "2026-08-24")
    assert linha["itens"] is None and linha["descartados"] == 0


def test_headline_vazia_derruba_o_bloco():
    """O template imprime a headline num <h2> fixo: vazia, o e-mail sai com título em branco."""
    c = _content([0, 1, 2, 3, 4])
    c["secundaria_1"]["headline"] = "   "
    out, prov = gc.apply_provenance(c, _pool(5))
    assert [d["motivo"] for d in prov["descartados"]] == ["headline_vazia"]
    assert prov["publicados"] == 4


def test_headline_que_e_so_tag_conta_como_vazia():
    c = _content([0, 1, 2, 3, 4])
    c["sinal_1"]["headline"] = "<a href='https://news.shopify.com'></a>"
    _, prov = gc.apply_provenance(c, _pool(5))
    assert [d["motivo"] for d in prov["descartados"]] == ["headline_vazia"]


def test_template_nao_deixa_titulo_vazio_quando_encolhe_de_verdade():
    """O teste anterior com esse nome não podia falhar: a entrada sempre trazia headline.
    Aqui a headline vazia entra no render de propósito, para provar que o <h2> em branco
    seria detectado se algum dia escapasse da guarda."""
    import re
    c = _content_render(3)
    c["secundaria_1"]["headline"] = ""
    html = _render(c)
    assert re.search(r"<h[23][^>]*>\s*</h[23]>", html), "o detector de título vazio não detecta"
