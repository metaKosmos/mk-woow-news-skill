#!/usr/bin/env python3
"""Render de newsletter mK: Markdown + front-matter YAML -> HTML pronto pra email.

Uso:
    python3 render_newsletter.py content/2026-w24.md

Lê o front-matter (subject, title_html, cta_text, cta_url, edition_label,
edition_date, list_name), quebra o corpo Markdown em parágrafos e injeta no
template Jinja templates/mk-newsletter.html.j2. Saída em renders/.

O HTML resultante é o corpo do email — CSS inline, layout em tabela, sem web
fonts (regra de compatibilidade Gmail). Esse arquivo precisa virar uma URL
pública para o passo de envio (content_url), ver send_zma.py.
"""
import argparse
import base64
import json
import sys
from pathlib import Path

try:
    import yaml
    from jinja2 import Environment, FileSystemLoader, select_autoescape
except ImportError:
    sys.exit("Faltam dependências. Rode: pip3 install pyyaml jinja2")

BASE = Path(__file__).resolve().parent
TEMPLATES = BASE / "templates"
RENDERS = BASE / "renders"
CONTENT = BASE / "content"
CONFIG = BASE / "config"

# Imagem default da manchete (placeholder, igual ao n8n). Sobrescreva com --image-url
# ou, no futuro, com a imagem gerada pelo Nano Banana.
DEFAULT_IMAGE = "https://images.unsplash.com/photo-1556761175-5973dc0f32e7?w=1200&h=600&fit=crop&q=80"
# Merge tag do ZMA: o link real de descadastro é injetado pela plataforma no envio.
UNSUBSCRIBE_TAG = "$[UNSUBSCRIBE]$"


def jinja_env():
    """Autoescape OFF: os campos `corpo` já vêm como HTML válido do Escritor."""
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        autoescape=select_autoescape(enabled_extensions=(), default=False),
    )


def parse_front_matter(raw: str):
    """Separa o front-matter YAML (entre --- ---) do corpo Markdown."""
    if not raw.startswith("---"):
        sys.exit("Arquivo sem front-matter YAML (precisa começar com ---).")
    _, fm, body = raw.split("---", 2)
    meta = yaml.safe_load(fm) or {}
    return meta, body.strip()


def body_to_paragraphs(body: str):
    """Cada bloco separado por linha em branco vira um parágrafo do email."""
    blocks = [b.strip().replace("\n", " ") for b in body.split("\n\n")]
    return [b for b in blocks if b]


def render(content_path: Path):
    raw = content_path.read_text(encoding="utf-8")
    meta, body = parse_front_matter(raw)
    paragraphs = body_to_paragraphs(body)

    template = jinja_env().get_template("mk-newsletter.html.j2")
    html = template.render(paragraphs=paragraphs, **meta)

    RENDERS.mkdir(exist_ok=True)
    out = RENDERS / f"newsletter-{content_path.stem}.html"
    out.write_text(html, encoding="utf-8")
    return out


def resolve_image(edition: str, image_url=None, embed_image=False):
    """Decide a URL da imagem da manchete.

    Prioridade: --image-url explícito > imagem gerada (generate_image.py) > placeholder.
    Para a imagem gerada: por padrão usa a URL pública {media.public_base_url}/<arquivo>
    (que o publish vai hospedar); com embed_image=True embute em base64 (preview local).
    """
    if image_url:
        return image_url
    for ext in ("jpg", "png"):
        local = RENDERS / f"woow-{edition}-manchete.{ext}"
        if local.exists():
            if embed_image:
                mime = "image/jpeg" if ext == "jpg" else "image/png"
                b64 = base64.b64encode(local.read_bytes()).decode("ascii")
                return f"data:{mime};base64,{b64}"
            try:
                cfg = yaml.safe_load((CONFIG / "newsletter.yaml").read_text(encoding="utf-8"))
                base = cfg["media"]["public_base_url"].rstrip("/")
                return f"{base}/{local.name}"
            except (KeyError, OSError):
                return DEFAULT_IMAGE
    return DEFAULT_IMAGE


def render_woow(edition: str, image_url=None, embed_image=False):
    """Render da WooW! Daily Drops a partir do content/<edition>.json estruturado.

    Preenche o template do João com os 5 drops (manchete + 2 secundárias + 2 sinais).
    A imagem da manchete é resolvida por resolve_image; o link de descadastro usa o
    merge tag do ZMA.
    """
    json_path = CONTENT / f"{edition}.json"
    if not json_path.exists():
        sys.exit(f"Não encontrei {json_path}. Rode generate_content.py --edition {edition} primeiro.")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    content = data.get("content", data)

    template = jinja_env().get_template("woow-daily-drops.html.j2")
    html = template.render(
        content=content,
        imagem_manchete_url=resolve_image(edition, image_url, embed_image),
        unsubscribe_url=UNSUBSCRIBE_TAG,
    )
    RENDERS.mkdir(exist_ok=True)
    out = RENDERS / f"woow-{edition}.html"
    out.write_text(html, encoding="utf-8")
    return out


def main():
    ap = argparse.ArgumentParser(description="Render da newsletter mK / WooW! Daily Drops")
    ap.add_argument("content", nargs="?", help="caminho do .md (modo legado, template mk)")
    ap.add_argument("--edition", help="edição WooW: content/<ed>.json -> renders/woow-<ed>.html")
    ap.add_argument("--image-url", default=None, help="URL da imagem da manchete (default: gerada/placeholder)")
    ap.add_argument("--embed-image", action="store_true", help="embute a imagem gerada em base64 (preview local)")
    args = ap.parse_args()

    if args.edition:
        out = render_woow(args.edition, args.image_url, args.embed_image)
        print(f"OK render WooW: {out}")
        return
    if args.content:
        content_path = Path(args.content)
        if not content_path.is_absolute():
            content_path = BASE / content_path
        if not content_path.exists():
            sys.exit(f"Não encontrei {content_path}")
        out = render(content_path)
        print(f"OK render: {out}")
        return
    ap.error("informe --edition <ed> (WooW) ou um caminho .md (modo legado)")


if __name__ == "__main__":
    main()
