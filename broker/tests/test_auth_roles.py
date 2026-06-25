# broker/tests/test_auth_roles.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import main
import roles

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


def test_admin_roles_route_is_admin_only():
    admins = {"david@metakosmos.com.br"}; ops = {"joao@metakosmos.com.br"}
    assert main.authorize("david@metakosmos.com.br", "/admin/roles", admins, ops)
    assert not main.authorize("joao@metakosmos.com.br", "/admin/roles", admins, ops)


def test_promoted_joao_clears_admin_gate_via_rolesjson():
    """Com roles.json promovendo joão a admin, ele passa em /admin/reset e /admin/roles —
    sem nenhuma mudança nas env vars."""
    admins, ops = roles.resolve_effective(
        {"david@metakosmos.com.br"},
        {"joao@metakosmos.com.br", "patrick@metakosmos.com.br"},
        {"admins": ["joao@metakosmos.com.br"], "operators": ["patrick@metakosmos.com.br"]})
    assert main.authorize("joao@metakosmos.com.br", "/admin/reset", admins, ops)
    assert main.authorize("joao@metakosmos.com.br", "/admin/roles", admins, ops)
    # patrick segue operador, mas não admin
    assert main.authorize("patrick@metakosmos.com.br", "/run", admins, ops)
    assert not main.authorize("patrick@metakosmos.com.br", "/admin/roles", admins, ops)
