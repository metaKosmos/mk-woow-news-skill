# broker/tests/test_version_sources.py — MAR-427: o número da versão tem uma fonte só.
#
# Em ago/2026 as três fontes divergiram (VERSION 1.4.0, plugin.json 1.4.0, broker 1.3.0)
# e o aviso de update passou a anunciar número errado. Estes testes seguram as duas
# fontes que vivem no repo; a terceira (env do Cloud Run) é do deploy, e o CI a reporta.
import json
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
VERSION_FILE = REPO / "plugins/woow-news/skills/woow-news/VERSION"
PLUGIN_JSON = REPO / "plugins/woow-news/.claude-plugin/plugin.json"


def _versao():
    return VERSION_FILE.read_text(encoding="utf-8").strip()


def test_version_e_semver_de_tres_numeros():
    """version_check compara tupla de int por '.', então letra ou sufixo mata o aviso
    em silêncio (o _parse devolve None e a checagem não fala nada)."""
    assert re.fullmatch(r"\d+\.\d+\.\d+", _versao()), f"VERSION inválido: {_versao()!r}"


def test_plugin_json_acompanha_o_version():
    """As duas fontes do repo mudam juntas. Use scripts/bump-version.sh, nunca na mão."""
    plugin = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
    assert plugin["version"] == _versao(), (
        f"plugin.json={plugin['version']!r} != VERSION={_versao()!r}; "
        "rode: bash scripts/bump-version.sh " + _versao())


def test_deploy_nao_crava_versao():
    """provision.sh e DEPLOY.md cravavam SKILL_VERSION=1.0.0: re-rodar REGREDIA a versão
    publicada e, como o aviso só dispara quando remoto > local, ninguém era avisado."""
    for rel in ("broker/provision.sh", "broker/DEPLOY.md"):
        texto = (REPO / rel).read_text(encoding="utf-8")
        cravadas = re.findall(r"SKILL_VERSION=[0-9][^,\"'\s]*", texto)
        assert not cravadas, f"{rel} tem versão cravada: {cravadas}"
        assert "SKILL_VERSION=$" in texto, f"{rel} não passa SKILL_VERSION por variável"


def test_bump_version_existe_e_e_executavel():
    script = REPO / "scripts/bump-version.sh"
    assert script.exists(), "scripts/bump-version.sh sumiu; é o único jeito de subir versão"
    assert script.stat().st_mode & 0o111, "scripts/bump-version.sh sem bit de execução"
