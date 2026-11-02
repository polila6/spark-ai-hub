---
name: citas-bibtex
description: Úsala para crear, corregir o convertir referencias bibliográficas (BibTeX/biblatex, APA 7, IEEE, Vancouver), obtener el BibTeX de un DOI, citar en LaTeX, Markdown/pandoc o Word, y revisar una lista de referencias.
license: MIT
---
# Referencias y citas (BibTeX, biblatex, APA, IEEE)

Regla de oro: **nunca inventes una referencia, autor, año, páginas ni DOI**. Si un dato no se ha visto en una
fuente (DOI, web de la editorial, Crossref, arXiv, PubMed), déjalo fuera o márcalo `% VERIFICAR`.

## Obtener el BibTeX de un DOI

Con fetch_url (o desde un script con red) estas URLs devuelven la referencia de Crossref:
- `https://api.crossref.org/works/<DOI>/transform/application/x-bibtex` → entrada BibTeX.
- `https://api.crossref.org/works/<DOI>` → JSON con title, author, container-title, issued, volume, page.
Para arXiv: `https://arxiv.org/abs/<id>` (título, autores, fecha; usa `@misc` o `@online` con `eprint`).
Revisa siempre la entrada recibida: mayúsculas del título, nombres con tildes, páginas con `--`.

## Tipos de entrada y campos mínimos

| Tipo | Campos obligatorios | Recomendados |
|---|---|---|
| `@article` | author, title, journal, year | volume, number, pages, doi |
| `@inproceedings` | author, title, booktitle, year | pages, publisher, address, doi |
| `@book` | author/editor, title, publisher, year | edition, address, isbn |
| `@incollection` | author, title, booktitle, publisher, year | editor, pages |
| `@phdthesis` / `@mastersthesis` | author, title, school, year | url |
| `@techreport` | author, title, institution, year | number |
| `@misc` / `@online` (biblatex) | author, title, year/date | url, urldate, note |

Convenciones:
- Autores separados por `and`: `author = {García López, Ana and Smith, John}` (apellidos, nombre; los dos
  apellidos españoles juntos antes de la coma).
- Autor corporativo entre llaves dobles: `author = {{Organización Mundial de la Salud}}`.
- Proteger mayúsculas en estilos que las bajan: `title = {Deep learning for {MRI} in {Spain}}`.
- Rangos de páginas con `--`: `pages = {45--67}`. Caracteres especiales: con biber y UTF-8 escribe `á`, `ñ`
  directamente (con BibTeX clásico usa `{\'a}`, `{\~n}`).
- Claves legibles y únicas: `garcia2023deep` (apellido + año + palabra del título).
- `doi = {10.1016/j.xxx.2023.01.002}` sin `https://doi.org/`.

## Citar en LaTeX

**biblatex + biber (recomendado)**
```latex
\usepackage[spanish]{babel}
\usepackage{csquotes}
\usepackage[backend=biber,style=apa]{biblatex}   % style=ieee | numeric-comp | authoryear | vancouver
\addbibresource{referencias.bib}
...
\textcite{garcia2023deep} demostró… / … como se ha visto \parencite{garcia2023deep,smith2020}.
\printbibliography[title={Referencias}]
```
Compila con `latexmk -pdf main.tex` (ejecuta biber). Los estilos `apa`, `ieee` y `vancouver` de biblatex están en
TeX Live (biblatex-apa, biblatex-ieee, biblatex-vancouver).

**BibTeX clásico** (plantillas de revista; sus `.cls`/`.bst`, p. ej. IEEEtran o elsarticle, no vienen instalados:
cópialos de la plantilla oficial junto al `.tex`):
```latex
\bibliographystyle{plainnat}   % instalados: plain, unsrt, alpha, abbrv, plainnat, unsrtnat, apalike
\bibliography{referencias}     % sin .bib
```
Con natbib: `\citet{}` (narrativa) y `\citep{}` (entre paréntesis).

## Citar en Markdown → Word/PDF con pandoc

```markdown
Según @garcia2023deep, … [@smith2020; @lee2021, p. 12].
```
```bash
pandoc texto.md --citeproc --bibliography=referencias.bib -o texto.docx
```
Sin `--csl` pandoc usa Chicago autor-fecha. Para APA o IEEE hace falta el archivo .csl (repositorio
citation-style-language/styles en GitHub); sin internet en el entorno, pide al usuario que lo suba o usa LaTeX.

## Formatos de salida a mano (cuando el usuario no usa gestor)

- **APA 7**: García López, A., & Smith, J. (2023). Título del artículo en minúsculas salvo nombres propios.
  *Nombre de la Revista*, *12*(3), 45–67. https://doi.org/10.xxxx/yyyy
  (hasta 20 autores; en el texto: (García López & Smith, 2023); 3 o más autores: (García López et al., 2023)).
- **IEEE**: [1] A. García López and J. Smith, "Título del artículo," *Abrev. Revista*, vol. 12, no. 3,
  pp. 45–67, 2023, doi: 10.xxxx/yyyy. (Numeración por orden de aparición en el texto.)
- **Vancouver** (biomedicina): 1. García López A, Smith J. Título. Rev Abrev. 2023;12(3):45-67.

## Revisar una lista de referencias (checklist)

1. Cada cita del texto está en la lista y viceversa (en LaTeX: `grep -o "\\\\[a-z]*cite[a-z]*{[^}]*}" *.tex`).
2. Un único estilo y coherente (mayúsculas, abreviatura de revistas, DOI en todas las que lo tengan).
3. Sin duplicados con claves distintas (mismo DOI o título).
4. Fuentes primarias y recientes; preprints marcados como tales; webs con fecha de consulta.
5. Comprueba con Crossref/DOI cualquier referencia sospechosa (año, revista o autores que no cuadran).
