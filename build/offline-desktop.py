#!/usr/bin/env python3
"""Prepara il desktop di Revv per funzionare senza internet:
caratteri e lettori di file (PDF, Word, Excel) copiati in locale.
Uso: offline-desktop.py <index.html> <cartella npm estratta> <destinazione>"""
import pathlib, re, shutil, sys

src, npm, dest = map(pathlib.Path, sys.argv[1:4])
(dest / 'fonts').mkdir(parents=True, exist_ok=True)
(dest / 'lib').mkdir(parents=True, exist_ok=True)

def pkg(prefix):
    hits = sorted(npm.glob(prefix + '*'))
    if not hits:
        sys.exit(f'Pacchetto mancante: {prefix}')
    return hits[-1] / 'package'

FONTS = [('Onest', 'fontsource-onest', 'onest', [(w, 'normal') for w in (400, 500, 600, 700)]),
         ('Unbounded', 'fontsource-unbounded', 'unbounded', [(500, 'normal'), (700, 'normal')]),
         ('JetBrains Mono', 'fontsource-jetbrains-mono', 'jetbrains-mono', [(400, 'normal'), (500, 'normal')]),
         ('Source Serif 4', 'fontsource-source-serif-4', 'source-serif-4', [(400, 'normal'), (600, 'normal'), (400, 'italic')])]
css = []
for family, pk, slug, faces in FONTS:
    base = pkg(pk) / 'files'
    shutil.copy(base.parent / 'LICENSE', dest / 'lib' / f'LICENZA-{pk}.txt')
    for w, st in faces:
        # intervalli di caratteri di ogni variante, presi dal CSS del pacchetto
        sheet = base.parent / (f'{w}.css' if st == 'normal' else f'{w}-italic.css')
        ranges = {}
        if sheet.exists():
            for m in re.finditer(r'/\* \S+?-(latin(?:-ext)?)-\d+-\w+ \*/.*?unicode-range:\s*([^;]+);', sheet.read_text(), re.S):
                ranges[m.group(1)] = m.group(2).strip()
        for sub in ('latin', 'latin-ext'):
            f = base / f'{slug}-{sub}-{w}-{st}.woff2'
            if f.exists():
                shutil.copy(f, dest / 'fonts')
                ur = f';unicode-range:{ranges[sub]}' if sub in ranges else ''
                css.append(f"@font-face{{font-family:'{family}';font-style:{st};font-weight:{w};"
                           f"font-display:swap;src:url(fonts/{f.name}) format('woff2'){ur}}}")
(dest / 'fonts.css').write_text('\n'.join(css) + '\n')

LIBS = {'https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js': ('xlsx', 'dist/xlsx.full.min.js'),
        'https://cdn.jsdelivr.net/npm/mammoth@1.6.0/mammoth.browser.min.js': ('mammoth', 'mammoth.browser.min.js'),
        'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js': ('pdfjs-dist', 'build/pdf.min.js'),
        'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js': ('pdfjs-dist', 'build/pdf.worker.min.js')}

s = src.read_text()
s = re.sub(r'<link rel="preconnect"[^>]*>\n', '', s)
s = re.sub(r'<link rel="stylesheet" href="https://fonts.googleapis.com[^"]*">', '<link rel="stylesheet" href="fonts.css">', s)
for url, (pk, rel) in LIBS.items():
    p = pkg(pk)
    shutil.copy(p / rel, dest / 'lib' / pathlib.Path(rel).name)
    lic = next(iter(sorted(p.glob('LICENSE*'))), None)
    if lic:
        shutil.copy(lic, dest / 'lib' / f'LICENZA-{pk}.txt')
    s = s.replace(url, 'lib/' + pathlib.Path(rel).name)
s = s.replace('Per aprire PDF, Word ed Excel il prototipo scarica un lettore da internet, e adesso non ci riesce. '
              'Controlla la connessione e riprova.', 'Non riesco a caricare il lettore per questo formato.')
for bad in ('googleapis', 'cdnjs', 'jsdelivr'):
    if bad in s:
        sys.exit(f'Rimasto un collegamento esterno: {bad}')
if '<!doctype' not in s.lower():
    s = s.replace('<title>', '<!doctype html>\n<html lang="it">\n<head>\n<meta charset="utf-8">\n'
                  '<meta name="viewport" content="width=device-width, initial-scale=1">\n<title>', 1)
    s = s.replace('</style>\n', '</style>\n</head>\n<body>\n', 1) + '\n</body>\n</html>\n'
(dest / 'index.html').write_text(s)
print('Desktop offline pronto in', dest)
