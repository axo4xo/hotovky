# 🍱 Hotovky

Statický blog s recenzemi hotových jídel a rychlých obědů. Recenze se píší
jako obyčejné markdownové soubory (klidně v Obsidianu) a `build.py` z nich
vygeneruje hotový web do složky `dist/`.

## Struktura

```
.
├─ src/            # recenze v markdownu (jeden soubor = jeden článek)
│  └─ sushi.md
├─ assets/         # obrázky a styl webu
│  ├─ img/
│  └─ style.css
├─ build.py        # generátor webu
├─ requirements.txt
└─ dist/           # vygenerovaný web (přepisuje se při buildu)
```

## Sestavení

```bash
# 1) jednorázově: virtuální prostředí + závislosti
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2) sestavení webu do dist/
.venv/bin/python build.py

# 3) lokální náhled na http://localhost:8000
.venv/bin/python build.py --serve
```

Vygenerovaný web je čistě statický (HTML + CSS), takže ho lze nahrát kamkoli —
Vercel, GitHub Pages, Netlify, Cloudflare Pages, vlastní hosting…

## Nasazení (Vercel — výchozí)

Repozitář obsahuje `vercel.json`, takže Vercel ví, jak web sestavit:

```json
{ "installCommand": "pip install -r requirements.txt",
  "buildCommand": "python3 build.py",
  "outputDirectory": "dist" }
```

**Jednorázové nastavení** (v prohlížeči, na vercel.com):

1. **Add New… → Project** a naimportuj repozitář `axo4xo/hotovky`.
2. Framework Preset nech na **Other** (vše ostatní si Vercel přečte z `vercel.json`).
3. **Deploy**.

Od té chvíle Vercel po každém pushi do `main` web sám sestaví a nasadí.
Protože web používá relativní cesty, funguje z kořene domény i z podadresáře.

## Nasazení (GitHub Pages — ruční záloha)

Workflow `.github/workflows/deploy.yml` umí web nasadit i na GitHub Pages, ale
automatické spouštění je vypnuté (běhalo pomalu a zasekávalo se ve frontě).
Spustit se dá ručně přes **Actions → Build & deploy to GitHub Pages → Run
workflow** (vyžaduje **Settings → Pages → Source: GitHub Actions**).

## Jak napsat recenzi

Vytvoř nový soubor `src/nazev.md`. Začni YAML hlavičkou (frontmatter) a pak
piš obyčejný markdown.

```markdown
---
title: "Recenze: Název jídla"
date: 2026-06-30
rating: 7.5          # hodnocení 0–10 (barva odznaku se přizpůsobí)
shop: Lidl
brand: Značka
price: "49 Kč"
price_per_100g: "16 Kč"
weight: "300 g"
cuisine: Italská
type: Chlazená
image: /assets/img/neco.jpg
summary: "Jednou větou, proč to (ne)stojí za to."
tags: [hotovky, recenze]
draft: false         # true = článek se nezveřejní
---

Text recenze…
```

### Šablonové proměnné

Kdekoli v textu můžeš odkázat na hodnoty z hlavičky pomocí `{{ page.klic }}`:

```markdown
Cena: {{ page.price }} · Hmotnost: {{ page.weight }}
```

### Plusy a mínusy

Vlastní bloky se vykreslí jako barevné karty (zelená / červená):

```markdown
::: pro
- Co se povedlo
:::

::: con
- Co se nepovedlo
:::
```

Podporované bloky: `pro`, `con`, `note`, `tip`, `warning`, `info`.

## Co `build.py` umí

- Načte všechny `src/*.md` (kromě `draft: true`).
- Vyřeší `{{ page.* }}` proměnné z frontmatteru.
- Vykreslí markdown včetně tabulek a `::: pro` / `::: con` bloků.
- Vytvoří stránku každého článku + úvodní přehled seřazený podle data.
- Zkopíruje `assets/` a obrázky; absolutní cesty `/assets/…` převede na
  relativní, takže web funguje i z `file://`.
