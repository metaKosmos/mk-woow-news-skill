# broker/tests/test_gemini_retry.py — MAR-194: uma instabilidade de minutos não pode
# matar a edição do dia.
#
# O incidente de 2026-07-07 foi output malformado INTERMITENTE do Gemini: a chamada
# seguinte teria funcionado, e não houve chamada seguinte. Por isso os testes cobrem as
# duas famílias juntas, e não só a de rede: status transitório E corpo que não vira JSON.
#
# O que NÃO se repete é tão importante quanto o que se repete. 400/401/403/404 são defeito
# de quem chama (prompt inválido, chave errada, modelo que não existe): repetir três vezes
# só atrasa o erro e gasta cota.
import io
import json
import pathlib
import sys
import urllib.error

import pytest

BROKER = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BROKER))
sys.path.insert(0, str(BROKER / "pipeline"))

import generate_content as gc  # noqa: E402
import generate_image as gi  # noqa: E402


CFG = {"endpoint": "https://exemplo.invalido/v1", "temperature": 0.4}


def _envelope(texto):
    """Resposta 200 do Gemini com `texto` no lugar onde o modelo escreve."""
    return json.dumps({
        "candidates": [{"content": {"parts": [{"text": texto}]}}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 20},
    })


def _envelope_imagem():
    return json.dumps({
        "candidates": [{"content": {"parts": [{"inlineData": {"data": "eyJhIjogMX0="}}]}}],
    })


class _Resposta:
    def __init__(self, corpo):
        self._corpo = corpo.encode("utf-8") if isinstance(corpo, str) else corpo
        self.status = 200

    def read(self):
        return self._corpo

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _http_error(code, corpo="erro"):
    return urllib.error.HTTPError("https://exemplo.invalido", code, "erro", {},
                                  io.BytesIO(corpo.encode("utf-8")))


class _Sequencia:
    """urlopen falso que devolve/levanta um item por chamada, e conta as chamadas.

    Item callable é CHAMADO a cada vez, e não reusado: `HTTPError.read()` consome o corpo,
    então um mesmo objeto de erro devolvido duas vezes daria corpo vazio na segunda. Em
    produção cada tentativa constrói um erro novo, e o fake precisa imitar isso, ou o teste
    do teto afirmaria sobre um artefato do dublê.
    """

    def __init__(self, *itens):
        self.itens = list(itens)
        self.chamadas = 0

    def __call__(self, req, timeout=None):
        self.chamadas += 1
        item = self.itens[min(self.chamadas - 1, len(self.itens) - 1)]
        if callable(item) and not isinstance(item, Exception):
            item = item()
        if isinstance(item, Exception):
            raise item
        return _Resposta(item)


@pytest.fixture
def sem_espera(monkeypatch):
    """Registra os backoffs sem pagá-los. O teste afirma sobre a lista, não sobre o relógio."""
    esperas = []
    monkeypatch.setattr(gc.time, "sleep", esperas.append)
    monkeypatch.setattr(gi.time, "sleep", esperas.append)
    return esperas


def _patch_urlopen(monkeypatch, modulo, seq):
    monkeypatch.setattr(modulo.urllib.request, "urlopen", seq)
    return seq


def _chama_json(**kw):
    return gc.gemini_json(CFG, "chave", "modelo-x", "sistema", "dados", dict, **kw)


# ---------------------------------------------------------------- caminho feliz

def test_sucesso_de_primeira_nao_repete(monkeypatch, sem_espera):
    """Controle positivo: sem retry, o teto de tentativas não significaria nada."""
    seq = _patch_urlopen(monkeypatch, gc, _Sequencia(_envelope('{"ok": 1}')))
    assert _chama_json() == {"ok": 1}
    assert seq.chamadas == 1
    assert sem_espera == []


# ---------------------------------------------------------------- o que se repete

def test_status_transitorio_repete_e_entrega(monkeypatch, sem_espera):
    seq = _patch_urlopen(monkeypatch, gc,
                         _Sequencia(_http_error(503), _envelope('{"ok": 1}')))
    assert _chama_json() == {"ok": 1}
    assert seq.chamadas == 2


def test_texto_malformado_repete_e_entrega(monkeypatch, sem_espera):
    """O incidente de 07/07: o modelo devolveu 200 com texto que não vira JSON."""
    seq = _patch_urlopen(monkeypatch, gc,
                         _Sequencia(_envelope("desculpe, não consigo"),
                                    _envelope('{"ok": 1}')))
    assert _chama_json() == {"ok": 1}
    assert seq.chamadas == 2


def test_envelope_nao_json_repete(monkeypatch, sem_espera):
    """200 com HTML no corpo (proxy/CDN). O json.loads do envelope não tinha guarda."""
    seq = _patch_urlopen(monkeypatch, gc,
                         _Sequencia("<html>502 Bad Gateway</html>", _envelope('{"ok": 1}')))
    assert _chama_json() == {"ok": 1}
    assert seq.chamadas == 2


def test_resposta_sem_candidates_repete(monkeypatch, sem_espera):
    """Bloqueio de safety devolve 200 sem candidates, e costuma não se repetir."""
    seq = _patch_urlopen(monkeypatch, gc,
                         _Sequencia(json.dumps({"promptFeedback": {"blockReason": "OTHER"}}),
                                    _envelope('{"ok": 1}')))
    assert _chama_json() == {"ok": 1}
    assert seq.chamadas == 2


def test_erro_de_rede_repete(monkeypatch, sem_espera):
    seq = _patch_urlopen(monkeypatch, gc,
                         _Sequencia(urllib.error.URLError("conexão recusada"),
                                    _envelope('{"ok": 1}')))
    assert _chama_json() == {"ok": 1}
    assert seq.chamadas == 2


def test_timeout_repete(monkeypatch, sem_espera):
    seq = _patch_urlopen(monkeypatch, gc,
                         _Sequencia(TimeoutError("demorou"), _envelope('{"ok": 1}')))
    assert _chama_json() == {"ok": 1}
    assert seq.chamadas == 2


# ---------------------------------------------------------------- o que NÃO se repete

@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_erro_do_chamador_sai_na_primeira(monkeypatch, sem_espera, code):
    seq = _patch_urlopen(monkeypatch, gc, _Sequencia(_http_error(code, "detalhe do erro")))
    with pytest.raises(SystemExit) as exc:
        _chama_json()
    assert seq.chamadas == 1, "repetir erro do chamador só atrasa o erro e gasta cota"
    assert str(code) in str(exc.value)
    assert "detalhe do erro" in str(exc.value), "a mensagem da API precisa chegar ao log"
    assert sem_espera == []


# ---------------------------------------------------------------- o teto

def test_teto_de_tentativas_e_backoff_crescente(monkeypatch, sem_espera):
    seq = _patch_urlopen(monkeypatch, gc,
                         _Sequencia(lambda: _http_error(503, "instável")))
    with pytest.raises(SystemExit) as exc:
        _chama_json()
    assert seq.chamadas == gc.MAX_TENTATIVAS
    assert len(sem_espera) == gc.MAX_TENTATIVAS - 1, "espera entre tentativas, não depois da última"
    assert sem_espera == sorted(sem_espera) and len(set(sem_espera)) == len(sem_espera), \
        "backoff constante não dá tempo de a instabilidade passar"
    assert "instável" in str(exc.value), "o último erro precisa sobreviver ao retry"


def test_malformado_persistente_esgota_e_sai(monkeypatch, sem_espera):
    seq = _patch_urlopen(monkeypatch, gc, _Sequencia(_envelope("nunca vira json")))
    with pytest.raises(SystemExit):
        _chama_json()
    assert seq.chamadas == gc.MAX_TENTATIVAS


# ---------------------------------------------------------------- a imagem

def test_art_director_repete_transitorio(monkeypatch, sem_espera):
    seq = _patch_urlopen(monkeypatch, gi,
                         _Sequencia(_http_error(500), _envelope("um prompt de arte")))
    assert gi.gemini_text(CFG["endpoint"], "chave", "m", "sistema", "texto") == "um prompt de arte"
    assert seq.chamadas == 2


def test_art_director_nao_repete_400(monkeypatch, sem_espera):
    seq = _patch_urlopen(monkeypatch, gi, _Sequencia(_http_error(400)))
    with pytest.raises(SystemExit):
        gi.gemini_text(CFG["endpoint"], "chave", "m", "sistema", "texto")
    assert seq.chamadas == 1


def test_nano_banana_repete_transitorio(monkeypatch, sem_espera):
    seq = _patch_urlopen(monkeypatch, gi,
                         _Sequencia(_http_error(429), _envelope_imagem()))
    assert gi.gemini_image(CFG["endpoint"], "chave", "m", "prompt", "16:9")
    assert seq.chamadas == 2


def test_nano_banana_sem_imagem_repete(monkeypatch, sem_espera):
    """finishReason sem imagem é intermitente do mesmo jeito que o texto malformado."""
    seq = _patch_urlopen(monkeypatch, gi,
                         _Sequencia(json.dumps({"candidates": [{"finishReason": "RECITATION"}]}),
                                    _envelope_imagem()))
    assert gi.gemini_image(CFG["endpoint"], "chave", "m", "prompt", "16:9")
    assert seq.chamadas == 2


def test_nano_banana_nao_repete_403(monkeypatch, sem_espera):
    seq = _patch_urlopen(monkeypatch, gi, _Sequencia(_http_error(403)))
    with pytest.raises(SystemExit):
        gi.gemini_image(CFG["endpoint"], "chave", "m", "prompt", "16:9")
    assert seq.chamadas == 1


# ---------------------------------------------------------------- guarda do próprio teto

def test_teto_permite_mais_de_uma_tentativa_e_os_dois_blocos_andam_juntos():
    """Os testes acima se referem a `MAX_TENTATIVAS` em vez de um número cravado, para não
    quebrarem quando o teto mudar de valor. O preço disso é que um teto reduzido a 1, que é
    o retry DESLIGADO, passaria por `test_teto_de_tentativas_e_backoff_crescente` sem que
    nada reclamasse: ele compara `seq.chamadas` com a própria constante mutada.

    As outras asserções guardam o risco que a duplicação do bloco criou: os dois arquivos do
    pipeline têm cópias independentes, e nada além disto impede que uma mude sem a outra.
    """
    assert gc.MAX_TENTATIVAS >= 2, "com teto 1 não existe retry"
    assert gi.MAX_TENTATIVAS == gc.MAX_TENTATIVAS
    assert gi.BACKOFF_BASE == gc.BACKOFF_BASE
    assert gi.STATUS_TRANSITORIO == gc.STATUS_TRANSITORIO
