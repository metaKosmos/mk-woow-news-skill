# broker/tests/test_empacotamento_deploy.py — o que o `--source=.` sobe, e o que o
# DEPLOY.md promete sobre rollback.
#
# Existe porque o deploy do broker é `gcloud functions deploy --source=.` de dentro de
# `broker/`, e sem `.gcloudignore` isso sobe TODO arquivo do diretório: a suíte, os caches e
# qualquer credencial que estivesse ali no momento. A presença do arquivo DESLIGA os padrões
# que o gcloud aplicaria sozinho, então tudo o que precisa ficar fora tem que estar escrito
# nele. Apagar o arquivo, ou tirar uma linha dele, não produz erro nenhum no deploy.
import fnmatch
import pathlib

BROKER = pathlib.Path(__file__).resolve().parent.parent
IGNORE = BROKER / ".gcloudignore"


def _padroes():
    assert IGNORE.exists(), "broker/.gcloudignore desapareceu: o deploy volta a subir tudo"
    return [l.strip() for l in IGNORE.read_text().splitlines()
            if l.strip() and not l.strip().startswith("#")]


def _ignorado(caminho, padroes):
    """Subconjunto da semântica de .gcloudignore que este arquivo usa: glob no nome, glob no
    caminho, e prefixo de diretório terminado em `/`."""
    partes = pathlib.PurePosixPath(caminho).parts
    for p in padroes:
        if p.endswith("/"):
            if p.rstrip("/") in partes:
                return True
        elif fnmatch.fnmatch(caminho, p) or fnmatch.fnmatch(partes[-1], p):
            return True
        elif p in partes:
            return True
    return False


def test_o_que_nao_pode_subir():
    padroes = _padroes()
    for fora in ("tests/test_publicados_state.py", "__pycache__/main.cpython-312.pyc",
                 "pipeline/__pycache__/research.cpython-312.pyc", ".pytest_cache/CACHEDIR.TAG",
                 "requirements-dev.txt", ".git", ".envmk", "woow-sa.json",
                 ".woow-news-auth.json", "config.local", "main 2.py", "__probe.sh"):
        assert _ignorado(fora, padroes), f"{fora} subiria no deploy"


def test_o_que_precisa_subir():
    """O caminho que segue aberto. Sem isto, um `.gcloudignore` com `*` passaria no teste de
    cima e produziria um container sem código, e o modo de falha seria em produção."""
    padroes = _padroes()
    runtime = [str(p.relative_to(BROKER)) for p in BROKER.rglob("*.py")
               if "tests" not in p.parts and "__pycache__" not in p.parts]
    assert len(runtime) >= 8, f"achei só {len(runtime)} módulos de runtime, cenário errado"
    for dentro in runtime + ["requirements.txt", "config/newsletter.yaml",
                             "config/feeds.yaml", "config/prompts/write.md",
                             "templates/woow-daily-drops.html.j2"]:
        assert not _ignorado(dentro, padroes), f"{dentro} NÃO subiria, e o runtime precisa dele"


def test_deploy_md_ensina_o_rollback_certo():
    """O rollback tem duas armadilhas medidas: `gcloud functions deploy` sem `--source`
    republica o diretório corrente em silêncio, e voltar para a versão nova não refaz o
    índice de publicados sozinho, porque só blob AUSENTE dispara reconstrução."""
    txt = (BROKER / "DEPLOY.md").read_text()
    assert "update-traffic" in txt
    assert "curadoria rebuild" in txt
    assert "Nunca faça rollback com `gcloud functions deploy`" in txt
