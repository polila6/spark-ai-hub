---
name: articulo-cientifico-imrad
description: Úsala para planificar, redactar o mejorar un artículo científico (estructura IMRaD): título, resumen, introducción, métodos, resultados, discusión, conclusiones, figuras, carta al editor y elección de revista.
license: MIT
---
# Artículo científico (IMRaD)

Principio: el artículo responde a UNA pregunta. Todo lo que no ayuda a responderla sobra.
Nunca inventes datos, resultados ni referencias: deja marcadores visibles `[DATO: …]`, `[REF: …]`.

## Orden de trabajo recomendado

1. Escribe en una frase el **mensaje principal** (hallazgo + por qué importa).
2. Prepara **figuras y tablas** (cuentan la historia; ver skill graficos-publicacion).
3. Métodos → Resultados → Discusión → Introducción → Conclusiones → Resumen → Título.
4. Ajusta a la **guía de autores** de la revista (extensión, estructura del resumen, estilo de citas, declaraciones).

## Sección por sección

**Título** (10-15 palabras): específico, con las palabras clave que alguien buscaría; mejor el hallazgo que el
tema ("La adición de X reduce un 30 % la fisuración de morteros de cal" > "Estudio sobre morteros").
Evita abreviaturas y "Estudio de…", "Análisis de…".

**Resumen** (150-250 palabras, sin citas): contexto (1 frase) → hueco/objetivo (1) → método (2) → resultados
principales CON cifras (2-3) → conclusión e implicación (1). Si la revista pide resumen estructurado:
Antecedentes/Objetivo/Métodos/Resultados/Conclusiones. 4-6 palabras clave que no repitan el título.

**Introducción** (embudo, 3-5 párrafos): 1) problema general e importancia; 2) qué se sabe (literatura reciente
y relevante, sintetizada, no lista de resúmenes); 3) qué falta (el hueco concreto); 4) objetivo/hipótesis y,
opcionalmente, aproximación y aportación. Termina con el objetivo, no con resultados.

**Materiales y métodos**: suficiente para que otro lo reproduzca. Diseño, muestra (criterios de inclusión,
cálculo del tamaño), materiales (proveedor, referencia), equipos (modelo, fabricante), procedimiento, variables y
cómo se midieron, análisis estadístico (pruebas, α, software y versión), ética (comité, consentimiento),
disponibilidad de datos y código. Tiempo pasado, subapartados con títulos.

**Resultados**: en el mismo orden que los métodos; texto que guía ("La resistencia aumentó con… (Fig. 2a)") sin
repetir todos los números de las tablas; sin interpretar ni citar literatura. Estadística completa (efecto, IC,
p). Incluye los resultados negativos relevantes.

**Discusión**: 1) respuesta a la pregunta en 1-2 frases; 2) comparación con estudios previos (coincidencias y
discrepancias, con explicación); 3) mecanismos o interpretación; 4) implicaciones prácticas/teóricas;
5) limitaciones honestas y cómo afectan; 6) trabajo futuro. No exageres ("demuestra" → "sugiere" cuando proceda);
no introduzcas resultados nuevos.

**Conclusiones**: breves, responden a los objetivos, sin repetir el resumen.

**Otras partes**: contribuciones (taxonomía CRediT), financiación (código del proyecto), conflictos de interés,
disponibilidad de datos (repositorio, p. ej. Zenodo o el institucional, con DOI), uso de IA si la revista lo pide.

## Figuras y tablas

- Cada una se entiende sola: pie completo (qué, condiciones, n, qué son las barras de error, abreviaturas).
- Tabla: pie arriba; figura: pie abajo. Numeradas en orden de aparición y citadas en el texto.
- Lo que cabe en una frase no necesita tabla.

## Estilo

- Frases cortas, una idea por párrafo con frase temática al inicio. Voz activa ("medimos") aceptada en la mayoría
  de revistas; mantén la coherencia.
- Tiempos: pasado para métodos y resultados propios; presente para conocimiento establecido y para lo que muestran
  figuras ("La Fig. 2 muestra…").
- Terminología constante (no alternes sinónimos para la misma variable); define abreviaturas en el primer uso.
- Unidades del SI separadas del número por un espacio (25 °C, 10 mm). En español también "50 %" con espacio y
  coma decimal (3,5); en inglés punto decimal (3.5) y casi todas las revistas escriben "50%".
- Español académico: evita "a nivel de" (→ "en"), "en base a" (→ "a partir de", "según"), gerundios de
  posterioridad y anglicismos innecesarios ("remarcable" → "notable", "chequear" → "comprobar").

## Checklists de comunicación (cítalas si aplican)

CONSORT (ensayos clínicos), STROBE (observacionales), PRISMA 2020 (revisiones sistemáticas), ARRIVE (animales),
STARD (diagnóstico), CHEERS (económicas), TRIPOD (modelos predictivos). Para ingeniería/ciencias: describe el
protocolo y la incertidumbre de medida con detalle.

## Elegir revista y carta al editor

- Revistas donde publican los trabajos que citas; comprueba ámbito, indexación (JCR/Scopus), tiempos, APC y
  política de acceso abierto. Desconfía de invitaciones por correo y revistas sin indexación clara.
- Carta de presentación (≤ 1 página): título y tipo de artículo; problema y hallazgo principal en 2-3 frases; por
  qué encaja en la revista; declaración de originalidad y de que no está en evaluación en otra revista;
  conflictos; revisores sugeridos si los piden (sin conflictos de interés).

## Revisión final

- [ ] El título y el resumen reflejan exactamente los resultados.
- [ ] Objetivo de la introducción = lo que responden resultados y conclusiones.
- [ ] Todas las figuras/tablas citadas y en orden; cifras del texto = cifras de las tablas.
- [ ] Todas las citas en la bibliografía y viceversa (skill citas-bibtex).
- [ ] Extensión y formato según la guía de autores; declaraciones obligatorias incluidas.
