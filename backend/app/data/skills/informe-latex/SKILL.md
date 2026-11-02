---
name: informe-latex
description: Úsala para crear y compilar a PDF informes técnicos, artículos, memorias o TFG/TFM en LaTeX (español o inglés) con figuras, tablas, ecuaciones, unidades y bibliografía, y para arreglar errores de compilación.
license: MIT
---
# Informes y artículos en LaTeX

En el entorno hay TeX Live con pdflatex, xelatex, lualatex, latexmk y biber, y los paquetes habituales
(babel español/inglés, siunitx, booktabs, graphicx, hyperref, biblatex, tikz/pgfplots, amsmath, cleveref...).

## Pasos

1. Crea una carpeta para el documento (p. ej. `informe/`) con `main.tex`, `referencias.bib` y `figuras/`.
2. Parte de la plantilla `plantilla-informe.tex` (léela con read_skill_file; en un workspace está copiada en
   `.skills/informe-latex/`). Para un documento largo, divide en `capitulos/*.tex` e inclúyelos con `\input{}`.
3. Escribe el contenido. Los archivos largos, por partes (ws_write + ws_append).
4. Compila desde la carpeta del documento:
   ```bash
   cd informe && latexmk -pdf -interaction=nonstopmode -halt-on-error main.tex
   ```
   latexmk ejecuta pdflatex, biber y las pasadas necesarias. Usa `-xelatex` o `-lualatex` si necesitas fuentes
   OpenType (fontspec) o muchos caracteres Unicode.
5. Comprueba el resultado: `grep -n -A3 "^!" main.log` (errores con su línea `l.NN`) y los avisos con
   `grep -nE "undefined|Overfull" main.log | head -30`. Corrige y recompila.
6. Limpia auxiliares si se pide: `latexmk -c`.

## Preámbulo mínimo correcto (pdflatex)

```latex
\documentclass[11pt,a4paper]{article}   % report/book para capítulos; para revistas usa su clase
\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}             % ya es el valor por defecto en LaTeX moderno; inofensivo
\usepackage[spanish,es-tabla]{babel}    % "Tabla" en vez de "Cuadro"; para inglés: [english]
\usepackage{lmodern,microtype}
\usepackage[margin=2.5cm]{geometry}
\usepackage{amsmath,amssymb}
\usepackage{graphicx,booktabs,array,multirow}
\usepackage[separate-uncertainty=true]{siunitx}
\sisetup{output-decimal-marker={,}}     % coma decimal en español
\usepackage[style=apa,backend=biber]{biblatex}   % o style=ieee, style=numeric-comp, authoryear
\addbibresource{referencias.bib}
\usepackage[hidelinks]{hyperref}        % casi siempre el último
\usepackage[spanish,noabbrev]{cleveref} % después de hyperref
```

Con babel español, `\%`, `~` y `<`/`>` funcionan normalmente; si un paquete se queja de `<` o `>` en modo
matemático, añade `\usepackage[spanish,es-noshorthands]{babel}`.

## Elementos frecuentes

```latex
\begin{figure}[htbp]
  \centering
  \includegraphics[width=0.8\linewidth]{figuras/resultado.pdf}
  \caption{Curva tensión-deformación de las probetas A1--A5.}
  \label{fig:curva}
\end{figure}

\begin{table}[htbp]
  \centering
  \caption{Propiedades medidas (media $\pm$ DE, $n=5$).}   % en tablas el pie va ARRIBA
  \label{tab:prop}
  \begin{tabular}{l S[table-format=3.1(2)] S[table-format=2.2]}
    \toprule
    Material & {$E$ (\si{\giga\pascal})} & {$\varepsilon_r$ (\si{\percent})} \\
    \midrule
    Acero S275 & 210.0(35) & 22.50 \\
    Al 6061    &  69.0(12) & 12.00 \\
    \bottomrule
  \end{tabular}
\end{table}

Como muestra la \cref{fig:curva}, el módulo es \SI{210}{\giga\pascal} \parencite{garcia2021}.
\begin{equation}
  \sigma = E\,\varepsilon \label{eq:hooke}
\end{equation}
```

Dentro de columnas `S` el texto va entre llaves `{...}`. En `siunitx` v3 `\SI` sigue funcionando, pero lo moderno es
`\qty{210}{\giga\pascal}` y `\num{12345.6}`.

## Errores típicos y su arreglo

| Mensaje en el log | Causa y solución |
|---|---|
| `! LaTeX Error: File 'x.sty' not found.` | Paquete inexistente o mal escrito; comprueba con `kpsewhich x.sty`. |
| `! Undefined control sequence.` | Comando mal escrito o falta su paquete; mira la línea `l.NN`. |
| `! Missing $ inserted.` | `_`, `^` o símbolos matemáticos fuera de `$...$`; escapa `\_` en texto. |
| `! Misplaced alignment tab character &.` | `&` fuera de una tabla: escribe `\&`. |
| `File ended while scanning use of ...` | Falta una llave `}` de cierre. |
| `Citation 'x' undefined` | La clave no está en el .bib o no se ejecutó biber (usa latexmk). |
| `Reference 'fig:x' undefined` / `??` | `\label` inexistente o puesto antes de `\caption`. |
| `Package inputenc Error: Unicode character` | Carácter raro (emoji, símbolo); quítalo o compila con xelatex. |
| `Overfull \hbox` | Línea demasiado larga: URL/palabra larga; usa `\url{}`, `\sloppy` local o reformula. |

## Buenas prácticas

- Una frase por línea en el .tex facilita los cambios y las revisiones.
- Figuras en PDF/SVG (vectoriales) generadas por script; PNG solo para fotos (≥300 ppp).
- Usa `\label{}` siempre después de `\caption{}` y referencia con `\cref`/`\ref`, nunca a mano.
- No pongas `\\` para separar párrafos: deja una línea en blanco.
- Las clases de revista (IEEEtran, elsarticle, llncs, svjour3, acmart, MDPI…) NO están instaladas: pide al usuario
  la plantilla oficial (zip de la revista u Overleaf) y copia su `.cls`/`.bst` junto al `.tex`; compila igual.
  Mientras tanto, `article` con el preámbulo de arriba sirve de borrador. Esas plantillas suelen usar BibTeX
  (`\bibliographystyle{...}` + `\bibliography{referencias}`), no biblatex: respeta lo que traiga la plantilla.
