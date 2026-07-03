# broker/tests/test_send_args.py — MAR-177: montagem de args do send_zma (função pura)
import sys, pathlib
BROKER = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BROKER))
import orchestrator

DELIV = {"topic_id": "TOPIC1", "from_email": "cfg@x", "from_name": "cfg"}
SENDER = {"from_email": "sender@metakosmos.com.br", "from_name": "mK Sender"}


def _pair(args, flag):
    """Devolve o valor imediatamente após `flag` na lista de args (ou None)."""
    return args[args.index(flag) + 1] if flag in args else None


def test_per_campaign_list_key_wins_over_active(tmp_path):
    st = {"preview_url": "https://pub/e.html", "list_key": "CAMP-LK"}
    args = orchestrator._build_send_args("e", st, DELIV, SENDER, {"list_key": "ACTIVE-LK"})
    assert _pair(args, "--list-key") == "CAMP-LK"

def test_falls_back_to_active_list_key(tmp_path):
    st = {"preview_url": "https://pub/e.html"}  # sem list_key por campanha
    args = orchestrator._build_send_args("e", st, DELIV, SENDER, {"list_key": "ACTIVE-LK"})
    assert _pair(args, "--list-key") == "ACTIVE-LK"

def test_falls_back_to_active_list_name(tmp_path):
    st = {"preview_url": "https://pub/e.html"}
    args = orchestrator._build_send_args("e", st, DELIV, SENDER,
                                         {"list_key": None, "list_name": "Time mK"})
    assert _pair(args, "--list-name") == "Time mK"
    assert "--list-key" not in args

def test_uses_active_sender_from_email(tmp_path):
    st = {"preview_url": "https://pub/e.html", "list_key": "LK"}
    args = orchestrator._build_send_args("e", st, DELIV, SENDER, {})
    assert _pair(args, "--from-email") == "sender@metakosmos.com.br"
    assert _pair(args, "--from-name") == "mK Sender"
    assert _pair(args, "--topic-id") == "TOPIC1"
    assert _pair(args, "--content-url") == "https://pub/e.html"

def test_manual_html_gets_distinct_campaign_name(tmp_path):
    st = {"preview_url": "u", "type": "manual_html", "list_key": "LK"}
    args = orchestrator._build_send_args("2026-07-01", st, DELIV, SENDER, {})
    assert _pair(args, "--campaign-name") == "mK Campanha 2026-07-01"

def test_manual_html_custom_campaign_name(tmp_path):
    st = {"preview_url": "u", "type": "manual_html", "campaign_name": "mK Lançamento", "list_key": "LK"}
    args = orchestrator._build_send_args("e", st, DELIV, SENDER, {})
    assert _pair(args, "--campaign-name") == "mK Lançamento"

def test_news_auto_has_no_campaign_name(tmp_path):
    st = {"preview_url": "u", "list_key": "LK"}  # sem type -> news_auto
    args = orchestrator._build_send_args("2026-07-01", st, DELIV, SENDER, {})
    assert "--campaign-name" not in args  # não quebra o send agendado/news_auto
    assert args[0] == "content/2026-07-01.md" and "--send" in args
