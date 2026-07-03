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
