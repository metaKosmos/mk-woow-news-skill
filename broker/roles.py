"""roles.py — lógica pura de papéis do broker woow-news (admin/operador).

Sem dependência de GCP/rede: as env vars e o roles.json (do GCS) são injetados pelo
chamador. Modelo:

- `ADMIN_EMAILS` (env) é o "floor" de admins (David): sempre admin, NUNCA removível
  pela API (break-glass anti-lockout, só muda por deploy/manage-roles.sh).
- `roles.json` (GCS) é a camada mutável. Enquanto não existe, os papéis vêm das env
  vars (comportamento legado). Quando existe, ele é autoritativo para os operadores;
  o floor de env continua somando aos admins.
- Admin é sempre também operador.

Promover/rebaixar passa por /admin/roles no broker (só admin) — nunca exige gcloud
nem deploy, então quem administra papéis NÃO ganha poder de editar/deployar a skill.
"""
import re

ACTIONS = {"add-operator", "remove-operator", "add-admin", "remove-admin"}
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize(email):
    return (email or "").strip().lower()


def split_emails(raw):
    """Aceita lista separada por vírgula OU ponto e vírgula (convenção do gcloud)."""
    return {e.strip().lower() for e in re.split(r"[,;]", raw or "") if e.strip()}


def resolve_effective(env_admins, env_operators, stored):
    """Devolve (admins, operators) efetivos.

    `stored` None  -> roles.json ainda não materializado: papéis dirigidos por env.
    `stored` dict  -> autoritativo p/ operadores; env_admins continua sendo floor.
    """
    floor = {normalize(e) for e in env_admins}
    if stored is None:
        admins = set(floor)
        operators = {normalize(e) for e in env_operators} | admins
    else:
        admins = floor | {normalize(e) for e in stored.get("admins", [])}
        operators = {normalize(e) for e in stored.get("operators", [])} | admins
    return admins, operators


def seed_from_env(env_admins, env_operators):
    """Snapshot inicial do roles.json a partir das env vars (na 1ª mutação)."""
    return {"admins": sorted({normalize(e) for e in env_admins}),
            "operators": sorted({normalize(e) for e in env_operators})}


def apply_change(stored, action, email, floor_admins, domain="metakosmos.com.br"):
    """Aplica uma mutação e devolve o novo dict {admins, operators}. `stored` já deve
    estar materializado (dict). Levanta ValueError em entrada inválida ou regra violada."""
    if action not in ACTIONS:
        raise ValueError(f"ação inválida: {action!r} (use {sorted(ACTIONS)})")
    email = normalize(email)
    if not _EMAIL_RE.match(email):
        raise ValueError(f"email inválido: {email!r}")
    if not email.endswith("@" + domain):
        raise ValueError(f"email fora do domínio @{domain}: {email}")

    admins = {normalize(e) for e in stored.get("admins", [])}
    operators = {normalize(e) for e in stored.get("operators", [])}
    floor = {normalize(e) for e in floor_admins}

    if action == "add-operator":
        operators.add(email)
    elif action == "remove-operator":
        operators.discard(email)
    elif action == "add-admin":
        admins.add(email)
    elif action == "remove-admin":
        if email in floor:
            raise ValueError(f"{email} é admin-base (env), não removível pela skill "
                             "— mude por manage-roles.sh/deploy")
        admins.discard(email)
        if not (admins | floor):
            raise ValueError("não dá pra remover o último admin")

    return {"admins": sorted(admins), "operators": sorted(operators)}
