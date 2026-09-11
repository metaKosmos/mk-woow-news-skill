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


def test_bump_recusa_regressao(tmp_path):
    """Baixar a versão publicada apaga o aviso de update de todo mundo sem gerar erro —
    é o modo de falha que esta PR existe para matar, então o próprio bump tem que recusar."""
    import subprocess
    arvore = tmp_path / "repo"
    (arvore / "plugins/woow-news/skills/woow-news").mkdir(parents=True)
    (arvore / "plugins/woow-news/.claude-plugin").mkdir(parents=True)
    (arvore / "scripts").mkdir()
    (arvore / "plugins/woow-news/skills/woow-news/VERSION").write_text("1.5.0\n", encoding="utf-8")
    (arvore / "plugins/woow-news/.claude-plugin/plugin.json").write_text(
        '{\n  "name": "woow-news",\n  "version": "1.5.0"\n}\n', encoding="utf-8")
    script = arvore / "scripts/bump-version.sh"
    script.write_text((REPO / "scripts/bump-version.sh").read_text(encoding="utf-8"), encoding="utf-8")

    def _bump(*args):
        return subprocess.run(["bash", str(script), *args], capture_output=True, text=True)

    def _versao_da_arvore():
        return (arvore / "plugins/woow-news/skills/woow-news/VERSION").read_text().strip()

    assert _bump("1.0.0").returncode != 0, "regressão passou"
    assert _versao_da_arvore() == "1.5.0", "regressão recusada mas o arquivo mudou"
    assert _bump("1.5.0").returncode != 0, "mesma versão passou"
    assert _bump("1.0.0", "--permitir-regressao").returncode == 0, "rollback explícito bloqueado"
    assert _versao_da_arvore() == "1.0.0"
    assert _bump("1.6.0").returncode == 0
    assert _versao_da_arvore() == "1.6.0"


# --------------------------------------------------------------------- carimbo do commit
# A versão sozinha não identifica o código. Entre o bump da v1.8.0 (7a78895) e a ponta
# publicada (a50de1e) entraram seis commits, três deles mexendo em produção, e todos
# respondem "1.8.0" no /version. O DEPLOY.md já avisa do caso pior, no rollback: a env var
# é da revisão, então voltar o tráfego deixa a versão certa apontando para outro código.
import sys  # noqa: E402

sys.path.insert(0, str(REPO / "broker"))
import main  # noqa: E402


def test_version_body_leva_o_commit_quando_o_deploy_injeta():
    assert main.version_body("1.8.0", "a50de1e") == {"version": "1.8.0", "commit": "a50de1e"}


def test_version_body_omite_o_commit_quando_ele_nao_veio():
    """O vizinho que continua passando. Deploy anterior a esta mudança e execução local não
    têm SKILL_COMMIT, e /version é rota pública que o CI consulta: ela não pode quebrar.
    Campo ausente diz "não sei qual commit"; valor fixo ou inventado mentiria."""
    assert main.version_body("1.8.0") == {"version": "1.8.0"}
    assert main.version_body("1.8.0", "") == {"version": "1.8.0"}
    assert main.version_body("1.8.0", "   ") == {"version": "1.8.0"}


def test_deploy_passa_o_commit_por_variavel_e_nunca_cravado():
    """Mesmo cuidado que o SKILL_VERSION tem desde a MAR-427, e pelo mesmo motivo: SHA
    cravado no script mente em silêncio, apontando para um commit que não é o deployado."""
    for rel in ("broker/provision.sh", "broker/DEPLOY.md"):
        texto = (REPO / rel).read_text(encoding="utf-8")
        cravados = re.findall(r"SKILL_COMMIT=[0-9a-f]{7,}", texto)
        assert not cravados, f"{rel} tem commit cravado: {cravados}"
        assert "SKILL_COMMIT=$" in texto, f"{rel} não passa SKILL_COMMIT por variável"


def test_o_despacho_do_version_chama_o_version_body():
    """O ponto de produção. As três asserções acima passam com o despacho montando o dict
    na mão, e a suíte não veria: o handler mora dentro de _handlers(), que importa
    functions_framework e não roda no CI. Por isso a checagem é sobre a fonte."""
    fonte = (REPO / "broker/main.py").read_text(encoding="utf-8")
    trecho = re.search(r'if path == "/version" and method == "GET":\n([^\n]+)', fonte)
    assert trecho, "o despacho de /version mudou de forma; reveja este teste"
    assert "version_body(" in trecho.group(1), (
        "o despacho de /version não chama version_body, então o commit não sairia na "
        f"resposta real. Linha: {trecho.group(1).strip()!r}")
