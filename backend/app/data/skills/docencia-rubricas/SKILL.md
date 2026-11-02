---
name: docencia-rubricas
description: Úsala para preparar material docente universitario: resultados de aprendizaje, rúbricas analíticas, exámenes, preguntas tipo test con buenos distractores (exportables a Moodle en formato GIFT), ejercicios resueltos y guías de prácticas.
license: MIT
---
# Material docente: rúbricas, exámenes y preguntas

## 1. Alineamiento

Parte siempre de los **resultados de aprendizaje** (RA) de la guía docente: "Al finalizar, el estudiante será
capaz de + verbo observable + contenido + condición". Verbos por nivel (Bloom revisada): recordar (definir,
enumerar) → comprender (explicar, clasificar) → aplicar (calcular, resolver) → analizar (comparar, diagnosticar)
→ evaluar (justificar, valorar) → crear (diseñar, proponer). Cada actividad y pregunta se asocia a un RA y nivel.

## 2. Rúbrica analítica

- 4-6 criterios observables e independientes (no "calidad general"), con peso (suman 100 %).
- 3-4 niveles (p. ej. Excelente 4 · Adecuado 3 · Mejorable 2 · Insuficiente 1) con **descriptores concretos** de
  lo que se ve en el trabajo, no adjetivos vagos.
- Nota = Σ (nivel/nivel máximo × peso). Comparte la rúbrica con los estudiantes antes de la tarea.

| Criterio (peso) | Excelente (4) | Adecuado (3) | Mejorable (2) | Insuficiente (1) |
|---|---|---|---|---|
| Planteamiento (20 %) | Identifica todas las variables y supuestos y los justifica | Identifica las variables principales; supuestos sin justificar | Omite variables relevantes | Planteamiento incorrecto o ausente |
| Cálculo (30 %) | Correcto, con unidades y orden de magnitud comprobado | Errores menores que no afectan al resultado | Errores que afectan al resultado | No llega a resultado |
| Análisis (30 %) | Interpreta, compara con alternativas y discute limitaciones | Interpreta correctamente | Interpretación superficial | Sin interpretación |
| Comunicación (20 %) | Estructura clara, figuras con pie y unidades, sin errores | Clara con fallos menores | Desordenada | Difícil de seguir |

Puedes generar la rúbrica en Excel (skill excel-con-graficos) con fórmula de nota, o en Word (documento-word).

## 3. Preguntas tipo test de calidad

- Enunciado completo y claro (se puede responder sin ver las opciones), en positivo (si usas negación,
  **NO** en mayúsculas).
- Una única respuesta correcta indiscutible; 3-4 distractores **plausibles** basados en errores frecuentes
  (signo, unidad, confundir conceptos, fórmula parecida).
- Opciones homogéneas en longitud y forma; sin "todas/ninguna de las anteriores"; sin pistas gramaticales.
- Mezcla niveles: no solo memoria; incluye aplicar y analizar (mini-casos, datos, gráficas).
- Penalización por error habitual: −1/(k−1) con k opciones (si la normativa lo permite).
- Calcula y comprueba numéricamente la respuesta correcta y cada distractor (run_code).

## 4. Formato GIFT (importación en Moodle)

Guarda en un `.txt` UTF-8 y en Moodle: Banco de preguntas → Importar → formato GIFT.

```
$CATEGORY: $course$/Tema 3 - Termodinámica

// Opción múltiple (una correcta). Comentarios de retroalimentación tras #
::T3-01 Primer principio:: En un proceso adiabático cerrado, el trabajo realizado sobre el sistema es igual a… {
=la variación de energía interna #Correcto, Q \= 0 y por tanto W \= ΔU.
~el calor intercambiado #En un proceso adiabático Q \= 0.
~la variación de entalpía #Confunde trabajo con variación de entalpía.
~cero #Eso exigiría ΔU \= 0, que no es el caso general.
}

// Verdadero/falso
::T3-02:: La entropía de un sistema aislado nunca disminuye. {T}

// Respuesta numérica con tolerancia: valor:tolerancia
::T3-03:: Calor (en kJ) para calentar 2 kg de agua de 20 a 30 °C (c \= 4,18 kJ/kg·K). {#83.6:0.5}

// Respuesta corta (varias aceptadas)
::T3-04:: ¿Cómo se llama el ciclo ideal de las centrales de vapor? {=Rankine =ciclo de Rankine}

// Emparejamiento
::T3-05:: Relaciona cada proceso con la magnitud constante. {
=isobárico -> presión
=isocórico -> volumen
=isotérmico -> temperatura
}

// Varias correctas con porcentajes (deben sumar 100; las incorrectas en negativo)
::T3-06:: Son funciones de estado: {
~%50%entalpía
~%50%entropía
~%-100%calor
~%-100%trabajo
}
```
Caracteres especiales que hay que escapar con `\` en enunciados, respuestas y comentarios: `~ = # { } :`
(por eso arriba se escribe `Q \= 0`). Los números del formato
numérico usan **punto** decimal. Una línea en blanco separa preguntas.

## 5. Examen escrito / ejercicios

- Indica puntuación por apartado y tiempo estimado; el total debe caber en ~70 % del tiempo disponible.
- Apartados encadenados con "si no obtuviste el resultado anterior, usa X = …" para no arrastrar errores.
- Solucionario paso a paso con resultados intermedios, unidades y criterios de corrección (qué puntúa).
- Varias versiones: cambia datos con un script (mismo enunciado, datos aleatorios con semilla) y genera las
  soluciones automáticamente.

## 6. Guía de prácticas

Objetivos (RA) · material y seguridad · fundamento breve · procedimiento numerado · datos a registrar (tabla) ·
cálculos y preguntas · entregable y rúbrica · referencias.
