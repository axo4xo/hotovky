#!/usr/bin/env python3
"""
Hotovky - statický generátor blogu s recenzemi hotových jídel.

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
SITE_KICKER = "Posudky hotových jídel"
SITE_TAGLINE = "Recenze hotových jídel a rychlých obědů"
SITE_DESCRIPTION = "Hotová jídla z českých regálů - oloupnutá, ochutnaná a obodovaná na stupnici do deseti."
SITE_REPO = "https://github.com/axo4xo/hotovky"

# Texty domovské stránky
HERO_TITLE_HTML = "Hotovky"
HERO_LEDE = ("Hotová jídla z českých regálů - oloupnutá, ochutnaná a obodovaná "
             "na stupnici do deseti. Žádné filtry, jen vanička a verdikt.")

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
    """Stupnice -> třída semaforu (zelená / oranžová / červená)."""
    try:
        r = float(rating)
    except (TypeError, ValueError):
        return "score-na"
    if r >= 6.5:
        return "score-hi"
    if r >= 4:
        return "score-mid"
    return "score-lo"


def score_word(rating) -> str:
    """Deklamovaný verdikt jedním slovem."""
    try:
        r = float(rating)
    except (TypeError, ValueError):
        return "-"
    if r >= 8:
        return "Výborné"
    if r >= 6.5:
        return "Solidní"
    if r >= 5:
        return "Ujde"
    if r >= 3.5:
        return "Slabé"
    return "Mimo"


def plural_polozka(n: int) -> str:
    if n == 1:
        return "položka"
    if 2 <= n <= 4:
        return "položky"
    return "položek"


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

FONTS_HREF = (
    "https://fonts.googleapis.com/css2?"
    "family=IBM+Plex+Mono:wght@400;500;600&"
    "family=IBM+Plex+Sans:wght@400;500;600;700&"
    "family=Saira+Condensed:wght@500;600;700;800&display=swap"
)


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
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{FONTS_HREF}">
<link rel="stylesheet" href="assets/style.css">
</head>
<body class="page{home_class}">
<a class="skip-link" href="#obsah">Přeskočit na obsah</a>
<header class="site-header">
  <div class="container site-header__inner">
    <a class="brand" href="index.html">{html.escape(SITE_TITLE)}</a>
    <span class="site-header__tag">{html.escape(SITE_KICKER)}</span>
  </div>
</header>
<main class="container" id="obsah">
{body}
</main>
<footer class="site-footer">
  <div class="container site-footer__inner">
    <div class="barcode" aria-hidden="true"></div>
    <p class="site-footer__line">{html.escape(SITE_TITLE)} - Spotřebujte dle uvážení</p>
    <p class="site-footer__meta"><a class="site-footer__repo" href="{html.escape(SITE_REPO)}" rel="noopener">Zdrojový kód na GitHubu →</a></p>
  </div>
</footer>
</body>
</html>
"""


def rating_stamp(rating, *, big: bool = False) -> str:
    """Hodnocení jako úředně odstupňovaný štítek (semafor + verdikt)."""
    if rating is None:
        return ""
    cls = rating_class(rating)
    size = " stamp--big" if big else ""
    try:
        val = f"{float(rating):g}"
    except (TypeError, ValueError):
        val = html.escape(str(rating))
    word = score_word(rating)
    return (
        f'<span class="stamp {cls}{size}" role="img" '
        f'aria-label="Hodnocení {val} z 10 - {word}">'
        f'<span class="stamp__num">{val}</span>'
        f'<span class="stamp__meta">'
        f'<span class="stamp__max">/ 10</span>'
        f'<span class="stamp__grade">{html.escape(word)}</span>'
        f'</span></span>'
    )


def price_tag(meta: dict) -> str:
    price = meta.get("price")
    if not price:
        return ""
    return (
        f'<span class="pricetag"><span class="pricetag__v">'
        f'{html.escape(str(price))}</span></span>'
    )


def _num(value):
    """Vytáhne první číslo z hodnoty (podporuje desetinnou čárku)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(value))
    return float(m.group(0).replace(",", ".")) if m else None


def value_info(meta: dict):
    """Poměr cena / energie -> Kč za 100 kcal + verdikt hodnoty."""
    price = _num(meta.get("price_czk", meta.get("price")))
    weight = _num(meta.get("weight_g", meta.get("weight")))
    kcal100 = _num(meta.get("kcal_100g"))
    if not (price and weight and kcal100):
        return None
    total_kcal = kcal100 * weight / 100.0
    if total_kcal <= 0:
        return None
    per_100kcal = price / total_kcal * 100.0
    if per_100kcal <= 12:
        cls, word = "score-hi", "Výhodné"
    elif per_100kcal <= 22:
        cls, word = "score-mid", "Fér"
    else:
        cls, word = "score-lo", "Předražené"
    return {"per_100kcal": per_100kcal, "cls": cls, "word": word}


def value_chip(meta: dict) -> str:
    """Štítek 'Hodnota' = cena přepočtená na energetickou hodnotu."""
    info = value_info(meta)
    if not info:
        return ""
    return (
        f'<span class="value {info["cls"]}" '
        f'title="Cena přepočtená na energetickou hodnotu">'
        f'<span class="value__k">Hodnota</span>'
        f'<span class="value__v">≈ {info["per_100kcal"]:.0f} Kč / 100 kcal'
        f' · {html.escape(info["word"])}</span></span>'
    )


def data_strip(fields, *, extra_class: str = "") -> str:
    """Řádek dat ve stylu obalového kódu (mono)."""
    items = "".join(
        f'<span class="data__item"><span class="data__k">{html.escape(label)}</span>'
        f'<span class="data__v">{html.escape(str(value))}</span></span>'
        for label, value in fields
        if value
    )
    if not items:
        return ""
    cls = ("datastrip " + extra_class).strip()
    return f'<div class="{cls}">{items}</div>'


def tags_html(tags) -> str:
    if not tags:
        return ""
    chips = "".join(f'<li class="tag">{html.escape(str(t))}</li>' for t in tags)
    return f'<ul class="tags">{chips}</ul>'


def render_post(post: dict, md: markdown.Markdown) -> str:
    meta = post["meta"]
    body = substitute_vars(post["body"], meta)
    content = render_markdown(body, md)
    image = webpath(meta.get("image", ""))
    title = str(meta.get("title", ""))

    eyebrow = " · ".join(str(b) for b in (meta.get("type"), meta.get("cuisine")) if b)
    eyebrow_html = f'<p class="eyebrow">{html.escape(eyebrow)}</p>' if eyebrow else ""

    summary = meta.get("summary", "")
    lede = f'<p class="post-lede">{html.escape(str(summary))}</p>' if summary else ""

    hero = ""
    if image:
        hero = (
            f'<figure class="tray">'
            f'<img class="tray__img" src="{html.escape(image)}" alt="{html.escape(title)}" loading="eager">'
            f'<span class="tray__seal" aria-hidden="true">Zde otevřít</span>'
            f"</figure>"
        )

    strip = data_strip(
        [
            ("DATUM", fmt_date(meta.get("date"))),
            ("OBCHOD", meta.get("shop")),
            ("ZNAČKA", meta.get("brand")),
            ("HMOTNOST", meta.get("weight")),
        ],
        extra_class="post-data",
    )

    article = f"""
<article class="post">
  <a class="back-link" href="index.html">← Zpět do regálu</a>
  <header class="post-head">
    {eyebrow_html}
    <h1 class="post-title">{html.escape(title)}</h1>
    {lede}
    <div class="verdict">
      {rating_stamp(meta.get("rating"), big=True)}
      {price_tag(meta)}
      {value_chip(meta)}
    </div>
    {strip}
  </header>
  {hero}
  <div class="post-body">
{content}
  </div>
  <footer class="post-foot">
    {tags_html(meta.get("tags"))}
    <a class="back-link" href="index.html">← Zpět do regálu</a>
  </footer>
</article>
"""
    return page_shell(
        f'{title or post["slug"]} - {SITE_TITLE}',
        article,
        description=str(summary or SITE_DESCRIPTION),
    )


def render_card(post: dict) -> str:
    meta = post["meta"]
    image = webpath(meta.get("image", ""))
    href = f'{post["slug"]}.html'
    title = str(meta.get("title", ""))
    rating_num = _num(meta.get("rating"))
    data_rating = "" if rating_num is None else f"{rating_num:g}"
    data_date = fmt_date_iso(meta.get("date"))
    media = (
        f'<div class="card__media">'
        f'<img src="{html.escape(image)}" alt="" loading="lazy">'
        f"{price_tag(meta)}</div>"
        if image else ""
    )
    summary = meta.get("summary", "")
    strip = data_strip(
        [
            ("HMOTNOST", meta.get("weight")),
            ("OBCHOD", meta.get("shop")),
            ("TYP", meta.get("type")),
        ],
        extra_class="card__data",
    )
    return f"""
<article class="card" data-date="{html.escape(data_date)}" data-rating="{html.escape(data_rating)}">
  {media}
  <div class="card__body">
    <div class="card__row">
      {rating_stamp(meta.get("rating"))}
      <time class="card__date" datetime="{fmt_date_iso(meta.get("date"))}">{fmt_date(meta.get("date"))}</time>
    </div>
    <h2 class="card__title"><a href="{href}">{html.escape(title)}</a></h2>
    <p class="card__summary">{html.escape(str(summary))}</p>
    {strip}
    {tags_html(meta.get("tags"))}
  </div>
</article>
"""


# Volby řazení regálu: (hodnota, popisek, klíč dat, směr)
SORT_OPTIONS = [
    ("date-desc", "Nejnovější", "date", "desc"),
    ("date-asc", "Nejstarší", "date", "asc"),
    ("rating-desc", "Nejlepší hodnocení", "rating", "desc"),
    ("rating-asc", "Nejhorší hodnocení", "rating", "asc"),
]


def sorter_html() -> str:
    """Ovládací prvek řazení (řadí se na klientovi v JS)."""
    opts = "".join(
        f'<option value="{val}" data-key="{key}" data-dir="{dr}">{html.escape(label)}</option>'
        for val, label, key, dr in SORT_OPTIONS
    )
    return (
        '<label class="sorter">'
        '<span class="sorter__label">Řadit</span>'
        '<span class="sorter__field">'
        f'<select class="sorter__select" id="sort-select" aria-label="Řadit recenze">{opts}</select>'
        "</span></label>"
    )


SORT_SCRIPT = """
<script>
(function () {
  var grid = document.getElementById('card-grid');
  var select = document.getElementById('sort-select');
  if (!grid || !select) return;
  var cards = Array.prototype.slice.call(grid.querySelectorAll('.card'));

  function sortCards() {
    var opt = select.options[select.selectedIndex];
    var key = opt.getAttribute('data-key');
    var dir = opt.getAttribute('data-dir') === 'asc' ? 1 : -1;
    cards.slice().sort(function (a, b) {
      if (key === 'rating') {
        var ar = parseFloat(a.getAttribute('data-rating'));
        var br = parseFloat(b.getAttribute('data-rating'));
        var an = isNaN(ar), bn = isNaN(br);
        if (an || bn) return an === bn ? 0 : (an ? 1 : -1); // bez hodnocení dolů
        return (ar - br) * dir;
      }
      var ad = a.getAttribute('data-date') || '';
      var bd = b.getAttribute('data-date') || '';
      if (ad === bd) return 0;
      if (!ad) return 1; // bez data dolů
      if (!bd) return -1;
      return ad < bd ? -dir : dir;
    }).forEach(function (card) { grid.appendChild(card); });
  }

  select.addEventListener('change', sortCards);
})();
</script>
"""


def render_index(posts: list[dict]) -> str:
    n = len(posts)
    if posts:
        cards = "\n".join(render_card(p) for p in posts)
        grid = f"""
<div class="shelf-head">
  <span class="shelf-head__label">Regál</span>
  <div class="shelf-head__tools">
    <span class="shelf-head__count">{n} {plural_polozka(n)}</span>
    {sorter_html()}
  </div>
</div>
<div class="card-grid" id="card-grid">{cards}</div>
{SORT_SCRIPT}"""
    else:
        grid = '<p class="empty">Regál je zatím prázdný - nic jsme neoloupli.</p>'
    body = f"""
<section class="hero">
  <h1 class="hero__title">{HERO_TITLE_HTML}</h1>
  <p class="hero__lede">{html.escape(HERO_LEDE)}</p>
</section>
{grid}
"""
    return page_shell(f"{SITE_TITLE} - {SITE_KICKER}", body, is_home=True)


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
