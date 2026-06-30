#!/usr/bin/env python3
"""
Hotovky — statický generátor blogu s recenzemi hotových jídel.

Načte markdownové recenze z ./src, vyřeší šablonové proměnné typu
``{{ page.x }}`` z YAML frontmatteru, vykreslí markdown (včetně vlastních
bloků ``::: pro`` / ``::: con``) a vygeneruje statický web do ./dist.

Použití:
    python build.py            # sestaví web do ./dist
    python build.py --serve    # sestaví a spustí lokální náhled na :8000
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import re
import shutil
import sys
import textwrap
from pathlib import Path

import markdown
import yaml

# --------------------------------------------------------------------------- #
# Konfigurace
# --------------------------------------------------------------------------- #

ROOT = Path(__file__).resolve().parent
SRC_DIR = ROOT / "src"
ASSETS_DIR = ROOT / "assets"
OUT_DIR = ROOT / "dist"

SITE_TITLE = "Hotovky"
SITE_TAGLINE = "Recenze hotových jídel a rychlých obědů"
SITE_DESCRIPTION = "Poctivé recenze chlazených, mražených a hotových jídel z českých obchodů."

MONTHS_CS = [
    "", "ledna", "února", "března", "dubna", "května", "června",
    "července", "srpna", "září", "října", "listopadu", "prosince",
]

# Mapování názvu callout bloku -> (titulek, ikona)
CALLOUTS = {
    "pro": ("Plusy", "👍"),
    "con": ("Mínusy", "👎"),
    "note": ("Poznámka", "📝"),
    "tip": ("Tip", "💡"),
    "warning": ("Pozor", "⚠️"),
    "info": ("Info", "ℹ️"),
}

# ::: name ... :::  (víceřádkový blok)
CALLOUT_RE = re.compile(
    r"^:::[ \t]*([A-Za-z][\w-]*)[ \t]*\n(.*?)\n^:::[ \t]*$",
    re.DOTALL | re.MULTILINE,
)

# {{ page.key }} nebo {{ key }}
VAR_RE = re.compile(r"\{\{\s*(?:page\.)?([A-Za-z_][\w-]*)\s*\}\}")

# Odkazy na /assets/... -> relativní cesta (funguje přes file:// i HTTP)
ASSET_REF_RE = re.compile(r'(src|href)="/(assets/[^"]+)"')


# --------------------------------------------------------------------------- #
# Načtení a parsování příspěvků
# --------------------------------------------------------------------------- #

def split_frontmatter(text: str) -> tuple[dict, str]:
    """Rozdělí text na (frontmatter dict, tělo)."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            meta = yaml.safe_load(parts[1]) or {}
            return meta, parts[2].lstrip("\n")
    return {}, text


def load_posts() -> list[dict]:
    posts = []
    for path in sorted(SRC_DIR.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        meta, body = split_frontmatter(raw)
        if meta.get("draft"):
            print(f"  · přeskakuji koncept: {path.name}")
            continue
        meta.setdefault("title", path.stem)
        posts.append(
            {
                "slug": slugify(path.stem),
                "meta": meta,
                "body": body,
                "source": path.name,
            }
        )
    # Nejnovější nahoře
    posts.sort(key=lambda p: str(p["meta"].get("date", "")), reverse=True)
    return posts


# --------------------------------------------------------------------------- #
# Pomocné funkce
# --------------------------------------------------------------------------- #

def slugify(value: str) -> str:
    value = value.lower().strip()
    repl = {"á": "a", "č": "c", "ď": "d", "é": "e", "ě": "e", "í": "i",
            "ň": "n", "ó": "o", "ř": "r", "š": "s", "ť": "t", "ú": "u",
            "ů": "u", "ý": "y", "ž": "z"}
    value = "".join(repl.get(ch, ch) for ch in value)
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "post"


def fmt_date(value) -> str:
    if isinstance(value, (dt.date, dt.datetime)):
        return f"{value.day}. {MONTHS_CS[value.month]} {value.year}"
    return str(value) if value else ""


def fmt_date_iso(value) -> str:
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()[:10]
    return str(value) if value else ""


def rating_class(rating) -> str:
    try:
        r = float(rating)
    except (TypeError, ValueError):
        return "rating-na"
    if r >= 8:
        return "rating-great"
    if r >= 6:
        return "rating-good"
    if r >= 4:
        return "rating-ok"
    return "rating-bad"


def webpath(p: str) -> str:
    """Absolutní cestu /assets/... převede na relativní (assets/...)."""
    if not p:
        return ""
    return p[1:] if p.startswith("/") else p


def substitute_vars(text: str, meta: dict) -> str:
    """Nahradí {{ page.key }} hodnotami z frontmatteru."""
    def repl(m: re.Match) -> str:
        key = m.group(1)
        if key in meta and meta[key] is not None:
            return str(meta[key])
        return m.group(0)  # neznámé ponecháme beze změny
    return VAR_RE.sub(repl, text)


# --------------------------------------------------------------------------- #
# Vykreslení markdownu (+ callout bloky)
# --------------------------------------------------------------------------- #

def make_md() -> markdown.Markdown:
    return markdown.Markdown(
        extensions=["extra", "sane_lists", "smarty", "admonition"],
        extension_configs={"smarty": {"smart_dashes": True}},
        output_format="html5",
    )


def render_callout(name: str, inner_html: str) -> str:
    label, icon = CALLOUTS.get(name.lower(), (name.capitalize(), "•"))
    return (
        f'<aside class="callout callout-{html.escape(name.lower())}">'
        f'<p class="callout-title"><span class="callout-icon" aria-hidden="true">{icon}</span>'
        f"{html.escape(label)}</p>"
        f'<div class="callout-body">{inner_html}</div>'
        f"</aside>"
    )


def render_markdown(text: str, md: markdown.Markdown) -> str:
    """Vykreslí markdown a vlastní ::: callout bloky."""
    out: list[str] = []
    pos = 0
    for m in CALLOUT_RE.finditer(text):
        before = text[pos:m.start()]
        if before.strip():
            md.reset()
            out.append(md.convert(before))
        name = m.group(1)
        inner = textwrap.dedent(m.group(2))
        md.reset()
        out.append(render_callout(name, md.convert(inner)))
        pos = m.end()
    tail = text[pos:]
    if tail.strip():
        md.reset()
        out.append(md.convert(tail))
    rendered = "\n".join(out)
    return ASSET_REF_RE.sub(r'\1="\2"', rendered)


# --------------------------------------------------------------------------- #
# HTML šablony
# --------------------------------------------------------------------------- #

def page_shell(title: str, body: str, *, description: str = "", is_home: bool = False) -> str:
    desc = html.escape(description or SITE_DESCRIPTION)
    home_class = " is-home" if is_home else ""
    return f"""<!DOCTYPE html>
<html lang="cs">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="{desc}">
<title>{html.escape(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="assets/style.css">
</head>
<body class="page{home_class}">
<header class="site-header">
  <div class="container site-header__inner">
    <a class="brand" href="index.html">
      <span class="brand__mark" aria-hidden="true">🍱</span>
      <span class="brand__text">
        <span class="brand__title">{html.escape(SITE_TITLE)}</span>
        <span class="brand__tagline">{html.escape(SITE_TAGLINE)}</span>
      </span>
    </a>
  </div>
</header>
<main class="container">
{body}
</main>
<footer class="site-footer">
  <div class="container">
    <p>{html.escape(SITE_TITLE)} — {html.escape(SITE_TAGLINE)}.</p>
    <p class="muted">Generováno staticky pomocí <code>build.py</code>.</p>
  </div>
</footer>
</body>
</html>
"""


def rating_badge(rating, *, big: bool = False) -> str:
    if rating is None:
        return ""
    cls = rating_class(rating)
    size = " rating--big" if big else ""
    try:
        val = f"{float(rating):g}"
    except (TypeError, ValueError):
        val = html.escape(str(rating))
    return (
        f'<span class="rating {cls}{size}">'
        f'<span class="rating__value">{val}</span>'
        f'<span class="rating__max">/&#8202;10</span></span>'
    )


def tags_html(tags) -> str:
    if not tags:
        return ""
    chips = "".join(f'<li class="tag">#{html.escape(str(t))}</li>' for t in tags)
    return f'<ul class="tags">{chips}</ul>'


def meta_chips(meta: dict) -> str:
    rows = [
        ("Datum", fmt_date(meta.get("date"))),
        ("Kuchyně", meta.get("cuisine")),
        ("Typ", meta.get("type")),
        ("Obchod", meta.get("shop")),
        ("Cena", meta.get("price")),
        ("Hmotnost", meta.get("weight")),
    ]
    chips = "".join(
        f'<div class="metabar__item"><dt>{html.escape(label)}</dt>'
        f"<dd>{html.escape(str(value))}</dd></div>"
        for label, value in rows
        if value
    )
    return f'<dl class="metabar">{chips}</dl>' if chips else ""


def render_post(post: dict, md: markdown.Markdown) -> str:
    meta = post["meta"]
    body = substitute_vars(post["body"], meta)
    content = render_markdown(body, md)
    image = webpath(meta.get("image", ""))

    hero = ""
    if image:
        hero = (
            f'<figure class="post-hero">'
            f'<img src="{html.escape(image)}" alt="{html.escape(str(meta.get("title", "")))}" loading="eager">'
            f"</figure>"
        )

    summary = meta.get("summary", "")
    summary_html = f'<p class="post-summary">{html.escape(str(summary))}</p>' if summary else ""

    article = f"""
<article class="post">
  <a class="back-link" href="index.html">← Zpět na přehled</a>
  <header class="post-header">
    {rating_badge(meta.get("rating"), big=True)}
    <h1 class="post-title">{html.escape(str(meta.get("title", "")))}</h1>
    {summary_html}
    {meta_chips(meta)}
  </header>
  {hero}
  <div class="post-body">
{content}
  </div>
  <footer class="post-footer">
    {tags_html(meta.get("tags"))}
    <a class="back-link" href="index.html">← Zpět na přehled</a>
  </footer>
</article>
"""
    return page_shell(
        f'{meta.get("title", post["slug"])} — {SITE_TITLE}',
        article,
        description=str(summary or SITE_DESCRIPTION),
    )


def render_card(post: dict) -> str:
    meta = post["meta"]
    image = webpath(meta.get("image", ""))
    href = f'{post["slug"]}.html'
    thumb = (
        f'<a class="card__media" href="{href}">'
        f'<img src="{html.escape(image)}" alt="" loading="lazy"></a>'
        if image else ""
    )
    summary = meta.get("summary", "")
    return f"""
<article class="card">
  {thumb}
  <div class="card__body">
    <div class="card__top">
      {rating_badge(meta.get("rating"))}
      <time class="card__date" datetime="{fmt_date_iso(meta.get("date"))}">{fmt_date(meta.get("date"))}</time>
    </div>
    <h2 class="card__title"><a href="{href}">{html.escape(str(meta.get("title", "")))}</a></h2>
    <p class="card__summary">{html.escape(str(summary))}</p>
    {tags_html(meta.get("tags"))}
  </div>
</article>
"""


def render_index(posts: list[dict]) -> str:
    if posts:
        cards = "\n".join(render_card(p) for p in posts)
        grid = f'<div class="card-grid">{cards}</div>'
    else:
        grid = '<p class="empty">Zatím tu nejsou žádné recenze.</p>'
    body = f"""
<section class="hero">
  <h1 class="hero__title">{html.escape(SITE_TAGLINE)}</h1>
  <p class="hero__lead">{html.escape(SITE_DESCRIPTION)}</p>
</section>
{grid}
"""
    return page_shell(f"{SITE_TITLE} — {SITE_TAGLINE}", body, is_home=True)


# --------------------------------------------------------------------------- #
# Sestavení
# --------------------------------------------------------------------------- #

def build() -> int:
    print(f"Hotovky → sestavuji do {OUT_DIR.relative_to(ROOT)}/")
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)

    # Kopie assetů
    if ASSETS_DIR.exists():
        shutil.copytree(ASSETS_DIR, OUT_DIR / "assets", dirs_exist_ok=True)

    # Styl: zkopíruj vlastní, jinak zapiš výchozí
    style_src = ASSETS_DIR / "style.css"
    style_dst = OUT_DIR / "assets" / "style.css"
    style_dst.parent.mkdir(parents=True, exist_ok=True)
    if style_src.exists():
        shutil.copy2(style_src, style_dst)
    else:
        style_dst.write_text(DEFAULT_CSS, encoding="utf-8")
        print("  · použit vestavěný výchozí styl")

    posts = load_posts()
    md = make_md()

    for post in posts:
        out = OUT_DIR / f'{post["slug"]}.html'
        out.write_text(render_post(post, md), encoding="utf-8")
        print(f"  · {post['source']} → {out.name}")

    (OUT_DIR / "index.html").write_text(render_index(posts), encoding="utf-8")
    print(f"  · index.html ({len(posts)} recenz{'e' if len(posts)==1 else 'í'})")

    # Zabrání tomu, aby GitHub Pages hnal výstup přes Jekyll.
    (OUT_DIR / ".nojekyll").write_text("", encoding="utf-8")

    print("Hotovo.")
    return 0


def serve(port: int = 8000) -> None:
    import functools
    import http.server
    import socketserver

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(OUT_DIR))
    with socketserver.TCPServer(("", port), handler) as httpd:
        print(f"Náhled běží na http://localhost:{port}  (Ctrl+C pro ukončení)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nKonec.")


# Vestavěný styl jako záloha, když chybí assets/style.css (skutečný styl je v souboru).
DEFAULT_CSS = "/* assets/style.css nebyl nalezen */\nbody{font-family:system-ui;max-width:48rem;margin:2rem auto;padding:0 1rem}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Statický generátor blogu Hotovky.")
    parser.add_argument("--serve", action="store_true", help="po sestavení spustí lokální náhled")
    parser.add_argument("--port", type=int, default=8000, help="port pro --serve (výchozí 8000)")
    args = parser.parse_args()

    rc = build()
    if rc == 0 and args.serve:
        serve(args.port)
    return rc


if __name__ == "__main__":
    sys.exit(main())
