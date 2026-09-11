# broker/tests/test_auth_roles.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import main

def test_admin_can_everything():
    admins = {"david@metakosmos.com.br"}; ops = {"joao@metakosmos.com.br"}
    assert main.authorize("david@metakosmos.com.br", "/run", admins, ops)
    assert main.authorize("david@metakosmos.com.br", "/admin/reset", admins, ops)

def test_operator_can_operate_not_admin():
    admins = {"david@metakosmos.com.br"}
    ops = {"joao@metakosmos.com.br", "patrick@metakosmos.com.br"}
    assert main.authorize("joao@metakosmos.com.br", "/run", admins, ops)
    assert main.authorize("joao@metakosmos.com.br", "/metrics", admins, ops)
    assert not main.authorize("joao@metakosmos.com.br", "/admin/reset", admins, ops)

def test_unknown_email_denied():
    admins = {"david@metakosmos.com.br"}; ops = {"joao@metakosmos.com.br"}
    assert not main.authorize("intruso@metakosmos.com.br", "/run", admins, ops)

def test_operator_can_manage_schedule():
    admins = {"david@metakosmos.com.br"}
    ops = {"joao@metakosmos.com.br", "patrick@metakosmos.com.br"}
    # agendamento (inclui ligar auto-send) é ação de operador — não está em ADMIN_ONLY
    assert main.authorize("joao@metakosmos.com.br", "/schedule", admins, ops)
    assert main.authorize("joao@metakosmos.com.br", "/schedule/set", admins, ops)
    assert main.authorize("patrick@metakosmos.com.br", "/cron/tick", admins, ops)

def test_operator_can_manage_sources():
    admins = {"david@metakosmos.com.br"}
    ops = {"joao@metakosmos.com.br", "patrick@metakosmos.com.br"}
    # fontes RSS são ação de operador (MAR-426): Patrick edita sem acesso ao GCP
    assert main.authorize("patrick@metakosmos.com.br", "/sources", admins, ops)
    assert main.authorize("patrick@metakosmos.com.br", "/sources/set", admins, ops)
    assert main.authorize("patrick@metakosmos.com.br", "/sources/test", admins, ops)
    assert not main.authorize("estranho@metakosmos.com.br", "/sources/set", admins, ops)

def test_operator_can_manage_campanhas():
    admins = {"david@metakosmos.com.br"}
    ops = {"joao@metakosmos.com.br", "patrick@metakosmos.com.br"}
    # Config de campanha é ação de operador (MAR-523): Patrick edita fontes, entrega,
    # formato e agenda de uma newsletter sem acesso ao GCP.
    for rota in ("/campanhas", "/campanhas/set", "/campanhas/status"):
        assert main.authorize("patrick@metakosmos.com.br", rota, admins, ops), rota
    assert not main.authorize("estranho@metakosmos.com.br", "/campanhas/set", admins, ops)
    # o rebuild da memória continua sendo de admin, e a rota nova não abriu buraco nele
    assert not main.authorize("patrick@metakosmos.com.br", "/admin/publicados/rebuild",
                              admins, ops)

def test_rota_nova_de_campanha_nao_entra_em_admin_only_por_prefixo():
    """ADMIN_ONLY é conjunto de strings EXATAS, testado com `path in ADMIN_ONLY`. Rota nova
    que comece com `/admin/` fica acessível a qualquer operador se não for adicionada
    literalmente lá — e este teste é o que faria isso aparecer."""
    assert "/admin/campanhas" not in main.ADMIN_ONLY
    for rota in main.ADMIN_ONLY:
        assert rota.startswith("/admin/"), rota

def test_nome_antigo_das_rotas_de_campanha_continua_alcancavel():
    """A v1.7.0 publicou `/curadoria`; há CLI instalado no time apontando para lá. Renomear
    sem alias transformaria um comando que funciona num 404, que o CLI traduz como 'broker
    fora do ar' — o operador procuraria falha de infra por causa de uma renomeação."""
    assert "/curadoria" in main.ROTAS_CAMPANHA
    assert "/campanhas" in main.ROTAS_CAMPANHA
    assert "/curadoria/set" in main.ROTAS_CAMPANHA_SET
    assert "/campanhas/set" in main.ROTAS_CAMPANHA_SET
