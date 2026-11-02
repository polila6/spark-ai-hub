---
name: presentacion-beamer
description: Úsala para preparar presentaciones académicas en LaTeX Beamer (congresos, defensas de TFG/TFM/tesis, clases) con ecuaciones, figuras, bloques y bibliografía, compiladas a PDF.
license: MIT
---
# Presentaciones con Beamer

Beamer está instalado (TeX Live). La plantilla `plantilla-beamer.tex` (read_skill_file) compila con pdflatex.
Para PowerPoint editable usa la skill presentacion-pptx; Beamer es mejor con muchas ecuaciones.

## Pasos

1. Pide (o deduce) la duración: regla práctica **1 diapositiva por minuto** (defensa de 15 min ≈ 12-15 diapositivas
   sin contar portada y final).
2. Estructura típica de defensa/congreso: portada → motivación/problema (1-2) → objetivos (1) → método (2-4) →
   resultados (3-6) → conclusiones (1) → trabajo futuro (opcional) → agradecimientos/financiación → preguntas.
3. Escribe `presentacion.tex` desde la plantilla y compila:
   ```bash
   latexmk -pdf -interaction=nonstopmode -halt-on-error presentacion.tex
   ```
4. Revisa visualmente: convierte unas páginas a PNG para comprobar que nada se sale del marco:
   `pdftoppm -png -r 50 -f 1 -l 4 presentacion.pdf prev` (crea prev-01.png, prev-02.png…).
5. Versión para repartir: `\documentclass[handout,aspectratio=169]{beamer}` genera una página por diapositiva
   sin las pausas (`\pause`, `<2->`).

## Reglas de diseño

- Una idea por diapositiva; el título de la diapositiva es la conclusión ("El método reduce el error un 35 %"),
  no el tema ("Resultados").
- Máximo ~6 viñetas de ≤ 2 líneas; mejor una figura grande que mucho texto.
- Fuente de figuras ≥ 14 pt efectivos: genera las figuras con el tamaño real de la diapositiva (aprox. 5 × 3 in)
  o usa `width=\textwidth` con figuras con texto grande.
- Formato 16:9: `\documentclass[aspectratio=169]{beamer}`.
- Temas seguros (incluidos en beamer): `Madrid`, `Boadilla`, `CambridgeUS`, `metropolis` (paquete
  beamertheme-metropolis, en TeX Live; con pdflatex funciona sin las fuentes Fira). Colores: `\usecolortheme{beaver}`,
  `seahorse`, `dolphin`…
- Numera las diapositivas (los tribunales las citan): en temas sin número usa
  `\setbeamertemplate{footline}[frame number]`.
- `\pause` y `<2->` con moderación; en defensas, mejor revelar por bloques.
- Diapositivas de reserva (backup) tras `\appendix` para preguntas previsibles.

## Fragmentos útiles

```latex
\begin{frame}{El método reduce el error un 35 \%}
  \begin{columns}[T]
    \begin{column}{0.48\textwidth}
      \begin{itemize}
        \item Dato 1 \item<2-> Dato 2 (aparece en el segundo paso)
      \end{itemize}
    \end{column}
    \begin{column}{0.48\textwidth}
      \includegraphics[width=\linewidth]{figuras/error.pdf}
    \end{column}
  \end{columns}
\end{frame}

\begin{frame}[fragile]{Código}   % [fragile] obligatorio con verbatim/listings
\begin{verbatim}
python analisis.py --n 100
\end{verbatim}
\end{frame}

\begin{frame}{Bloques}
  \begin{block}{Hipótesis} ... \end{block}
  \begin{alertblock}{Limitación} ... \end{alertblock}
  \begin{exampleblock}{Resultado} ... \end{exampleblock}
\end{frame}
```

## Errores frecuentes

- `! Illegal parameter number in definition` o errores raros con código → falta `[fragile]` en el frame.
- Texto que se sale por abajo: Beamer no avisa; revisa las PNG o usa `\begin{frame}[shrink=10]` como último recurso
  (mejor dividir en dos diapositivas).
- Bibliografía: con biblatex usa `\begin{frame}[allowframebreaks]{Referencias}\printbibliography\end{frame}` y
  cita con `\footfullcite{}` o `\parencite{}`.
- Logos: `\logo{\includegraphics[height=0.8cm]{figuras/logo.png}}`; usa solo logos que el usuario aporte.
