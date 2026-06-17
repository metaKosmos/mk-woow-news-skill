#!/usr/bin/env python3
"""Imagem da manchete da WooW! Daily Drops: art-director + Nano Banana Pro.

Uso:
    python3 generate_image.py --edition 2026-w25 [--aspect 16:9]

Lê content/<edition>.json (a manchete), roda o agente diretor de arte (Gemini texto,
prompt em config/prompts/art_director.md) para construir o prompt de imagem em inglês,
e gera a imagem com o modelo Nano Banana Pro. Salva em
renders/woow-<edition>-manchete.<ext>.

NÃO publica nem envia. A imagem precisa ir para uma URL pública no passo de publish
(o render monta {media.public_base_url}/<arquivo>). Para preview local, use o render com
--embed-image, que embute a imagem em base64.
"""
import argparse
import base64
import json
import re
import sys
import urllib.request
import urllib.error
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("Faltam dependências. Rode: pip3 install pyyaml")

BASE = Path(__file__).resolve().parent
CONFIG = BASE / "config"
PROMPTS = CONFIG / "prompts"
CONTENT = BASE / "content"
RENDERS = BASE / "renders"

USAGE = {}


def load_env(start: Path) -> dict:
    """Lê o .envmk subindo os diretórios pais (igual ao generate_content.py)."""
    for parent in [start] + list(start.parents):
        env_path = parent / ".envmk"
        if env_path.exists():
            env = {}
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
            return env
    sys.exit("Não encontrei .envmk subindo a partir de " + str(start))


def load_yaml(name):
    return yaml.safe_load((CONFIG / name).read_text(encoding="utf-8"))


def strip_html(raw: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw or "")).strip()


def gemini_text(endpoint, api_key, model, system_prompt, user_text):
    """Chamada de texto ao Gemini: devolve a string crua (o prompt de imagem)."""
    body = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 1024,
                             "thinkingConfig": {"thinkingBudget": 0}},
    }
    req = urllib.request.Request(
        f"{endpoint}/{model}:generateContent",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        sys.exit(f"Art-director HTTP {e.code} ({model}): {e.read().decode('utf-8')[:400]}")
    um = payload.get("usageMetadata", {})
    USAGE["art_director"] = {"input": um.get("promptTokenCount", 0),
                             "output": um.get("candidatesTokenCount", 0)}
    try:
        return payload["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError):
        sys.exit(f"Art-director sem texto ({model}): {json.dumps(payload)[:400]}")


def gemini_image(endpoint, api_key, model, prompt, aspect):
    """Chamada de imagem (Nano Banana Pro): devolve (bytes, mime_type)."""
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseModalities": ["IMAGE"],
                            "imageConfig": {"aspectRatio": aspect}},
    }
    req = urllib.request.Request(
        f"{endpoint}/{model}:generateContent",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=240) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        sys.exit(f"Nano Banana HTTP {e.code} ({model}): {e.read().decode('utf-8')[:400]}")
    um = payload.get("usageMetadata", {})
    USAGE["image"] = {"input": um.get("promptTokenCount", 0),
                      "output": um.get("candidatesTokenCount", 0)}
    parts = payload.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    img = next((p for p in parts if "inlineData" in p), None)
    if not img:
        reason = payload.get("candidates", [{}])[0].get("finishReason", "?")
        sys.exit(f"Nano Banana não retornou imagem (finishReason={reason}): {json.dumps(payload)[:300]}")
    data = base64.b64decode(img["inlineData"]["data"])
    return data, img["inlineData"].get("mimeType", "image/png")


def main():
    ap = argparse.ArgumentParser(description="Imagem da manchete WooW! Daily Drops")
    ap.add_argument("--edition", required=True, help="rótulo da edição, ex: 2026-w25")
    ap.add_argument("--aspect", default=None, help="override da proporção, ex: 16:9, 3:2, 1:1")
    args = ap.parse_args()

    env = load_env(BASE)
    nl_cfg = load_yaml("newsletter.yaml")
    endpoint = nl_cfg["gemini"]["endpoint"]
    key = env.get(nl_cfg["gemini"]["api_key_env"])
    if not key:
        sys.exit(f"{nl_cfg['gemini']['api_key_env']} não está no .envmk")
    mcfg = nl_cfg["media"]

    json_path = CONTENT / f"{args.edition}.json"
    if not json_path.exists():
        sys.exit(f"Não encontrei {json_path}. Rode generate_content.py --edition {args.edition} primeiro.")
    content = json.loads(json_path.read_text(encoding="utf-8")).get("content", {})
    manchete = content.get("manchete", {})
    if not manchete.get("headline"):
        sys.exit("content sem manchete. Gere o conteúdo antes da imagem.")

    user = f"Manchete: {manchete['headline']}\n\n{strip_html(manchete.get('corpo', ''))}"
    art_prompt = (PROMPTS / "art_director.md").read_text(encoding="utf-8")
    image_prompt = gemini_text(endpoint, key, mcfg["art_director_model"], art_prompt, user)
    print(f"Prompt de imagem:\n  {image_prompt}\n")

    aspect = args.aspect or mcfg.get("aspect_ratio", "16:9")
    data, mime = gemini_image(endpoint, key, mcfg["image_model"], image_prompt, aspect)
    ext = "jpg" if "jpeg" in mime else "png"

    RENDERS.mkdir(exist_ok=True)
    out = RENDERS / f"woow-{args.edition}-manchete.{ext}"
    out.write_bytes(data)

    usage_path = CONTENT / f"{args.edition}.usage.json"
    out_usage = {}
    if usage_path.exists():
        out_usage = json.loads(usage_path.read_text(encoding="utf-8"))
    out_usage.update(USAGE)
    usage_path.write_text(json.dumps(out_usage, ensure_ascii=False, indent=2), encoding="utf-8")

    public_url = f"{mcfg['public_base_url'].rstrip('/')}/{out.name}"
    print(f"OK imagem: {out} ({len(data)} bytes, {mime}, {aspect})")
    print(f"URL pública (após publish): {public_url}")
    print(f"Preview local: python3 render_newsletter.py --edition {args.edition} --embed-image")


if __name__ == "__main__":
    main()
