# broker/tests/test_edition_date_brt.py
#
# O container (Dockerfile/provision.sh) não fixa TZ e roda em UTC. `_agora_brt()` é o ponto
# único do generate_content.py que lê o relógio (edition_date do prompt, edition_date
# gravado, checked_at, generated_at). Este teste congela `datetime` DENTRO do módulo e prova
# que o resultado segue o dia de Brasília, não o de UTC, no caso em que os dois divergem.
import pathlib
import sys
from datetime import datetime, timedelta, timezone

BROKER = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BROKER))
sys.path.insert(0, str(BROKER / "pipeline"))

import generate_content as gc  # noqa: E402


class _RelogioCongelado(datetime):
    """Substitui `datetime` dentro do módulo gc. Só `now(tz)` importa aqui: `_agora_brt()`
    chama exatamente `datetime.now(BRT)`, então fixar `now` é o suficiente para controlar
    o instante que os quatro pontos de uso do arquivo enxergam."""
    _instante_utc = None  # definido por _congela()

    @classmethod
    def now(cls, tz=None):
        instante = cls._instante_utc
        return instante.astimezone(tz) if tz else instante.replace(tzinfo=None)


def _congela(monkeypatch, instante_utc):
    _RelogioCongelado._instante_utc = instante_utc
    monkeypatch.setattr(gc, "datetime", _RelogioCongelado)


def test_geracao_noturna_em_brt_nao_avanca_para_o_dia_utc_seguinte(monkeypatch):
    # 2026-09-09 22:30 BRT == 2026-09-10 01:30 UTC. Este é o caso do bug: em UTC cru, a
    # edição de 09/09 gerada à noite gravaria "2026-09-10", e a trava de repetição perderia
    # a véspera inteira da janela (46 dos 50 pares medidos no histórico eram este caso).
    instante_utc = datetime(2026, 9, 10, 1, 30, tzinfo=timezone.utc)
    _congela(monkeypatch, instante_utc)

    assert gc._agora_brt().strftime("%Y-%m-%d") == "2026-09-09"
    assert gc.ptbr_date(gc._agora_brt()) == "quarta-feira, 09 de setembro de 2026"
    # não vale só acertar a data: teria que ser mesmo BRT, não outro fuso qualquer que por
    # acaso desse a mesma data.
    assert gc._agora_brt().utcoffset() == timedelta(hours=-3)


def test_meio_do_dia_os_dois_fusos_concordam(monkeypatch):
    # Vizinho que continua certo: 2026-09-09 15:00 BRT == 2026-09-09 18:00 UTC, mesma data
    # nos dois fusos. Sem este caso, uma implementação que sempre subtraísse um dia (em vez
    # de converter fuso de verdade) passaria no teste acima e não seria pega aqui.
    instante_utc = datetime(2026, 9, 9, 18, 0, tzinfo=timezone.utc)
    _congela(monkeypatch, instante_utc)

    assert gc._agora_brt().strftime("%Y-%m-%d") == "2026-09-09"
    assert gc.ptbr_date(gc._agora_brt()) == "quarta-feira, 09 de setembro de 2026"
