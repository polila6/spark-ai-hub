---
name: traduccion-tecnica
description: Úsala para traducir o revisar traducciones de textos técnicos y académicos entre español, inglés y valenciano/catalán (artículos, resúmenes, memorias, documentación, LaTeX/Markdown/código), con terminología coherente y formato intacto.
license: MIT
---
# Traducción técnica y académica (es · en · ca/va)

## Principios

1. **Fidelidad**: ni resumir ni añadir; misma información, mismo nivel de certeza ("may" ≠ "debe").
2. **Naturalidad**: que parezca escrito originalmente en la lengua de destino (orden de la frase, longitud, voz).
3. **Coherencia terminológica**: un término = una traducción en todo el documento. Crea un glosario al empezar
   (término origen → término destino → nota) y respétalo; si el usuario da un glosario, manda el suyo.
4. **Registro** académico/técnico; en caso de duda, el término consolidado en la disciplina (normas UNE/ISO,
   Termcat para catalán/valenciano, IATE para terminología de la UE).

## Formato que NO se traduce ni se rompe

- LaTeX: comandos, entornos, `\label{}`, `\ref{}`, `\cite{}`, opciones; sí se traduce el texto de `\caption{}`,
  `\section{}`, etc. Revisa que las llaves queden equilibradas.
- Markdown: enlaces (traduce el texto, no la URL), tablas (mismo número de columnas), bloques de código.
- Código: nombres de variables y funciones (salvo petición expresa); comentarios y cadenas visibles sí.
- Fórmulas, símbolos, unidades del SI, nombres propios, nombres de software, referencias bibliográficas (no se
  traducen los títulos de las obras citadas).

Para archivos largos, traduce por secciones conservando la estructura y comprueba al final (p. ej. compilar el
LaTeX o comparar el número de `\cite`/`\ref` entre original y traducción).

## Convenciones por idioma

| | Español | Valencià / Català | English |
|---|---|---|---|
| Decimal / miles | 3,5 · 12 500 (o 12.500) | 3,5 · 12 500 (o 12.500) | 3.5 · 12,500 |
| Porcentaje | 50 % | 50 % | 50% |
| Comillas | « » (luego " ", ' ') | « » (luego " ") | " " (UK: ' ') |
| Mayúsculas en títulos | solo la inicial y nombres propios | ídem | Title Case habitual en títulos (según estilo) |
| Fechas | 3 de noviembre de 2026 | 3 de novembre de 2026 | 3 November 2026 / November 3, 2026 |

## Falsos amigos y calcos frecuentes (en → es)

actual → real, efectivo · actually → en realidad · eventually → finalmente · sensible → sensato ·
sensitive → sensible · assume → suponer · consistent → coherente/constante (según contexto) ·
library → biblioteca (no "librería") · data are → los datos son ·
control (to control for) → tener en cuenta / ajustar por · evidence → pruebas, datos, indicios (no siempre
"evidencia") · performance → rendimiento, desempeño, prestaciones · rate → tasa, velocidad, ritmo ·
significant → significativo (solo si es estadístico; si no, "importante", "notable") ·
remark → observación · approach → enfoque, método · framework → marco · issue → problema, cuestión ·
"It is worth noting that" → (a menudo se puede eliminar).

## es → en: errores típicos

- Frases demasiado largas: divide. Evita pasivas encadenadas y nominalizaciones ("the realization of the
  measurement of" → "measuring").
- Artículos: sin artículo con plurales/incontables genéricos ("Concrete is…", no "The concrete is…" en sentido
  general).
- "Investigation" para investigación científica → "research" (incontable: "two studies", no "two researches").
- "Formation" → "training"; "realize" ≠ realizar ("carry out", "perform").
- Inglés británico o americano: sigue la revista y sé coherente (analyse/analyze, colour/color).

## Valenciano

Por defecto sigue la normativa de l'Acadèmia Valenciana de la Llengua (AVL) y sus formas preferentes en textos
institucionales de la Comunitat Valenciana (p. ej. "este/esta", "eixe", "servici",
incoatius en -ix: "servix", "produïx"). Si el usuario pide catalán estándar (IEC), usa "aquest/aquesta",
"servei", "serveix". Las universidades valencianas publican guías de estilo y terminología: síguelas si el
usuario las indica. Terminología: Termcat y diccionarios de la AVL.

## Entrega

1. Texto traducido con el formato original.
2. Lista breve de decisiones: términos con varias opciones, adaptaciones de unidades o convenciones, frases
   ambiguas del original (con la interpretación elegida).
3. Si se pidió revisión de una traducción existente: cambios propuestos en tabla (original · traducción actual
   · propuesta · motivo).
