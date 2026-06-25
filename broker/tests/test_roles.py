# broker/tests/test_roles.py — lógica pura de papéis (sem GCP) + store local.
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import roles
from state_manager import StateManager, LocalStore

ENV_ADMINS = {"david@metakosmos.com.br"}
ENV_OPS = {"joao@metakosmos.com.br", "patrick@metakosmos.com.br"}


def test_legacy_behavior_when_no_rolesjson():
    """Sem roles.json: papéis vêm das env vars (comportamento de hoje)."""
    admins, ops = roles.resolve_effective(ENV_ADMINS, ENV_OPS, None)
    assert admins == {"david@metakosmos.com.br"}
    assert ops == {"david@metakosmos.com.br", "joao@metakosmos.com.br",
                   "patrick@metakosmos.com.br"}


def test_rolesjson_is_authoritative_but_env_admin_is_floor():
    """Com roles.json: ele manda nos operadores; o admin de env continua sendo floor."""
    stored = {"admins": ["joao@metakosmos.com.br"],
              "operators": ["tales@metakosmos.com.br"]}
    admins, ops = roles.resolve_effective(ENV_ADMINS, ENV_OPS, stored)
    assert admins == {"david@metakosmos.com.br", "joao@metakosmos.com.br"}
    assert "tales@metakosmos.com.br" in ops
    assert "david@metakosmos.com.br" in ops and "joao@metakosmos.com.br" in ops
    # patrick não está no roles.json autoritativo -> deixou de ser operador
    assert "patrick@metakosmos.com.br" not in ops


def test_promote_joao_to_admin():
    stored = roles.seed_from_env(ENV_ADMINS, ENV_OPS)
    new = roles.apply_change(stored, "add-admin", "Joao@metakosmos.com.br", ENV_ADMINS)
    assert "joao@metakosmos.com.br" in new["admins"]  # normalizado p/ minúsculo
    admins, _ = roles.resolve_effective(ENV_ADMINS, ENV_OPS, new)
    assert "joao@metakosmos.com.br" in admins


def test_cannot_remove_env_floor_admin():
    stored = roles.seed_from_env(ENV_ADMINS, ENV_OPS)
    try:
        roles.apply_change(stored, "remove-admin", "david@metakosmos.com.br", ENV_ADMINS)
        assert False, "deveria recusar remover o admin-base de env"
    except ValueError:
        pass


def test_add_and_remove_operator():
    stored = roles.seed_from_env(ENV_ADMINS, ENV_OPS)
    s2 = roles.apply_change(stored, "add-operator", "nova@metakosmos.com.br", ENV_ADMINS)
    assert "nova@metakosmos.com.br" in s2["operators"]
    s3 = roles.apply_change(s2, "remove-operator", "patrick@metakosmos.com.br", ENV_ADMINS)
    assert "patrick@metakosmos.com.br" not in s3["operators"]


def test_rejects_invalid_email_or_foreign_domain():
    stored = roles.seed_from_env(ENV_ADMINS, ENV_OPS)
    for bad in ("intruso@gmail.com", "not-an-email", ""):
        try:
            roles.apply_change(stored, "add-operator", bad, ENV_ADMINS)
            assert False, f"deveria rejeitar {bad!r}"
        except ValueError:
            pass


def test_rejects_unknown_action():
    stored = roles.seed_from_env(ENV_ADMINS, ENV_OPS)
    try:
        roles.apply_change(stored, "make-god", "x@metakosmos.com.br", ENV_ADMINS)
        assert False
    except ValueError:
        pass


def test_store_roundtrip(tmp_path):
    sm = StateManager(LocalStore(str(tmp_path)))
    assert sm.read_roles() is None
    sm.write_roles({"admins": ["david@metakosmos.com.br"],
                    "operators": ["joao@metakosmos.com.br"]})
    got = sm.read_roles()
    assert got["operators"] == ["joao@metakosmos.com.br"]
