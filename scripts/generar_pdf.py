#!/usr/bin/env python
"""
Genera el PDF del documento del sprint.

`docs/sprint-2.md` es la fuente; el PDF es una salida, asi que no se edita a
mano: se regenera con este script. No van dos copias que se desincronizan.

No se usa reportlab porque escribir el PDF a mano mantiene un segundo
formato al lado del markdown, y las tablas y los enlaces salen mejor si el
navegador las renderiza. El camino es markdown -> HTML -> Chrome headless
imprimiendo a PDF, que es lo mismo que hace Chrome con Ctrl+P.

Uso:
    python scripts/generar_pdf.py            # docs/sprint-2.md -> docs/sprint-2.pdf
    python scripts/generar_pdf.py otro.md    # -> otro.pdf, al lado de la fuente
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import markdown

RAIZ = Path(__file__).resolve().parent.parent

# Chrome y Edge estan en estas rutas en Windows. Primero el primero que
# aparezca: es para imprimir, no importa cual de los dos.
NAVEGADORES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def navegador():
    for ruta in NAVEGADORES:
        if Path(ruta).exists():
            return ruta
    return shutil.which("chrome") or shutil.which("msedge")


def html_de(markdown_de: str, titulo: str) -> str:
    cuerpo = markdown.markdown(
        markdown_de,
        extensions=["tables", "toc", "fenced_code", "sane_lists", "smarty"],
        output_format="html5",
    )
    return HTML_TEMPLATE.replace("__TITULO__", _escapar(titulo)).replace("__CUERPO__", cuerpo)


def _escapar(texto: str) -> str:
    return (
        texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def generar(origen: Path, destino: Path) -> None:
    binario = navegador()
    if not binario:
        sys.exit("No hay Chrome ni Edge en el equipo: no se puede imprimir a PDF.")

    texto = origen.read_text(encoding="utf-8")
    # El primer H1 del documento es el titulo del PDF y del tablero.
    titulo = next(
        (l.lstrip("#").strip() for l in texto.splitlines() if l.startswith("# ")),
        origen.stem,
    )

    # Temporal: si algo falla a mitad de camino, no queda un HTML huerfano
    # al lado del .md.
    with tempfile.TemporaryDirectory() as tmp:
        html = Path(tmp) / "documento.html"
        html.write_text(html_de(texto, titulo), encoding="utf-8")

        # `file://` con / (slash) siempre, aunque Windows use backslash: con
        # file:///C:/... Chrome lo resuelve y con file://C:\... no.
        url = html.resolve().as_uri()

        # --no-pdf-header-footer saca el "página X de Y / fecha / URL" que Chrome
        # pone por defecto: para un documento que se imprime lo ensucia.
        cmd = [
            binario,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--no-pdf-header-footer",
            "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=2000",
            f"--print-to-pdf={destino}",
            url,
        ]
        # --no-sandbox no lo toma Edge. Falla silenciosamente? No: este flag no
        # produce error si no se conoce.
        try:
            subprocess.run(
                cmd, check=True, capture_output=True, text=True, timeout=120
            )
        except subprocess.CalledProcessError as e:
            sys.exit(f"Chrome no pudo imprimir el PDF:\n{e.stderr}")
        except subprocess.TimeoutExpired:
            sys.exit("Se quedó colgado el renderer del PDF.")


HTML_TEMPLATE = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>__TITULO__</title>
<style>
  /* A4, con margen de corte real. Chrome solo honra @page, no el
     `page` del `@page` margin-box para numeros de pagina. */
  @page {
    size: A4;
    margin: 18mm 16mm 20mm 16mm;
  }

  html {
    font-family: "Segoe UI", "Calibri", "Helvetica Neue", Arial, sans-serif;
    font-size: 10.5pt;
    line-height: 1.55;
    color: #1c1e21;
    /* Negro sobre blanco: se imprime casi seguro en tinta. */
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }

  body {
    max-width: 100%;
    margin: 0;
  }

  p { margin: 0 0 8pt 0; text-align: justify; hyphens: auto; }

  /* Un heading huérfano al final de página deja un hueco raro. */
  h1, h2, h3, h4 { break-after: avoid; page-break-after: avoid; }
  h1, h2, h3 { break-inside: avoid; }

  h1 {
    font-size: 22pt;
    line-height: 1.2;
    margin: 0 0 6pt 0;
    color: #0b3d63;
    border-bottom: 2.5pt solid #0b3d63;
    padding-bottom: 8pt;
  }

  h2 {
    font-size: 15pt;
    margin: 22pt 0 9pt 0;
    color: #0b3d63;
    border-bottom: 0.75pt solid #b9c6d1;
    padding-bottom: 3pt;
    break-before: page;
    page-break-before: always;
  }

  h2:first-of-type { break-before: auto; page-break-before: auto; }

  h3 {
    font-size: 12pt;
    margin: 16pt 0 6pt 0;
    color: #17384f;
  }

  h4 { font-size: 10.5pt; margin: 12pt 0 4pt 0; color: #17384f; }

  ul, ol { margin: 0 0 8pt 0; padding-left: 18pt; }
  li { margin-bottom: 3pt; }
  li p { margin-bottom: 3pt; }

  strong { color: #0b3d63; }

  /* Blockquote = nota aclaratoria sobre el texto. */
  blockquote {
    margin: 10pt 0;
    padding: 7pt 11pt;
    background: #f1f5f8;
    border-left: 3pt solid #4b7ea8;
    color: #24313b;
    break-inside: avoid;
  }
  blockquote p { margin: 0; font-size: 9.8pt; }

  code {
    font-family: Consolas, "SF Mono", "Liberation Mono", monospace;
    font-size: 9pt;
    background: #eef1f4;
    border: 0.5pt solid #d5dbe1;
    border-radius: 2.5pt;
    padding: 0.5pt 3pt;
    overflow-wrap: anywhere;
  }

  pre {
    background: #f6f8fa;
    border: 0.75pt solid #d5dbe1;
    border-radius: 3pt;
    padding: 8pt 10pt;
    overflow-x: auto;
    break-inside: avoid;
    page-break-inside: avoid;
  }
  pre code {
    background: none;
    border: none;
    padding: 0;
    font-size: 8.5pt;
    overflow-wrap: normal;
    white-space: pre-wrap;
  }

  hr {
    border: none;
    border-top: 1pt solid #b9c6d1;
    margin: 18pt 0;
  }

  table {
    border-collapse: collapse;
    width: 100%;
    margin: 8pt 0 12pt 0;
    font-size: 9.2pt;
    /* La tabla entera no parte: se corta entre filas, no a la mitad de una. */
    break-inside: auto;
  }
  thead { display: table-header-group; }
  tr { break-inside: avoid; page-break-inside: avoid; }

  th, td {
    border: 0.75pt solid #c3cdd6;
    padding: 4pt 6pt;
    text-align: left;
    vertical-align: top;
  }
  th {
    background: #e7eef4;
    font-weight: 600;
    color: #0b3d63;
  }
  tbody tr:nth-child(even) td { background: #fafcfd; }

  /* Los enlaces internos del índice no aportan en papel y pueden verse rotos. */
  a { color: #0b3d63; text-decoration: none; overflow-wrap: anywhere; }
  a[href^="#"] { color: inherit; }
</style>
</head>
<body>
__CUERPO__
</body>
</html>
"""


def main():
    if len(sys.argv) > 1:
        origen = Path(sys.argv[1]).resolve()
    else:
        origen = RAIZ / "docs" / "sprint-2.md"

    if not origen.exists():
        sys.exit(f"No existe la fuente: {origen}")

    destino = origen.with_suffix(".pdf")
    generar(origen, destino)
    print(f"{destino}  ({destino.stat().st_size:,} bytes)".replace(",", "."))


if __name__ == "__main__":
    main()
