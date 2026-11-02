---
name: propuesta-investigacion
description: Úsala para preparar propuestas de proyectos de investigación (Horizonte Europa, Plan Estatal/AEI, autonómicas, contratos): objetivos, estado del arte, metodología, paquetes de trabajo, cronograma, riesgos, impacto, presupuesto y gestión de datos.
license: MIT
---
# Propuestas de proyectos de investigación

**Antes de escribir**: la convocatoria manda. Lee (o pide) las bases, la plantilla oficial, los criterios de
evaluación y sus pesos, el límite de páginas y el formato. Copia los encabezados de la plantilla tal cual: los
evaluadores puntúan siguiendo esa estructura. No inventes requisitos: si un dato de la convocatoria no está en los
documentos del usuario, búscalo en la web oficial o márcalo `[COMPROBAR EN LA CONVOCATORIA]`.

## Esqueleto común (adáptalo a la plantilla)

1. **Resumen** (público no especialista + evaluador): problema, objetivo, enfoque, resultados esperados, impacto.
2. **Antecedentes y estado del arte**: qué se sabe, qué falta (hueco concreto), resultados previos del equipo que
   demuestran viabilidad. Referencias recientes y propias relevantes.
3. **Hipótesis y objetivos**: un objetivo general + 3-5 específicos **SMART** (específicos, medibles,
   alcanzables, relevantes, con plazo). Cada objetivo específico ↔ un paquete de trabajo y ≥ 1 indicador.
4. **Metodología y plan de trabajo**: por paquete de trabajo (WP): objetivo, tareas, responsable, meses,
   entregables (D) e hitos (M). Justifica por qué ese método y no otro. Incluye un WP de gestión y otro de
   difusión/explotación si la convocatoria lo espera.
5. **Cronograma** (Gantt): tareas × meses/trimestres con hitos.
6. **Riesgos y contingencia**: tabla riesgo / probabilidad / impacto / medida de mitigación.
7. **Equipo y capacidades**: por qué este equipo puede hacerlo; papel de cada miembro; infraestructuras.
8. **Impacto**: científico (publicaciones, datos), formación (tesis, TFM), económico/social, transferencia,
   ODS relevantes; vías concretas y medibles (KPI), no frases genéricas.
9. **Difusión, comunicación y explotación**: público, canales, calendario; acceso abierto; protección de
   resultados (patentes, software).
10. **Plan de gestión de datos** (DMP, principios FAIR): qué datos, formatos, metadatos, repositorio (Zenodo,
    repositorio institucional), licencias, datos personales (RGPD) y conservación.
11. **Presupuesto**: personal, equipamiento, fungible, viajes, publicaciones (APC), subcontratación, costes
    indirectos según las reglas de la convocatoria; cada partida justificada por una tarea.
12. **Aspectos éticos y de igualdad de género** (dimensión de género en el contenido si aplica).

## Horizonte Europa (RIA/IA) — Parte B

Los tres criterios de evaluación son la estructura de la Parte B:
1. **Excellence**: objetivos y ambición (más allá del estado del arte), metodología (incluye enfoque
   interdisciplinar, dimensión de género, ciencia abierta, gestión de datos).
2. **Impact**: vías hacia el impacto (resultados → efectos → impactos esperados del *topic*), medidas de
   difusión, explotación y comunicación (incluido el plan "DEC").
3. **Quality and efficiency of the implementation**: plan de trabajo (WP, entregables, hitos, Gantt), riesgos,
   recursos (persona-mes por socio y WP), consorcio y capacidades.
Escribe contra el texto del *topic* del programa de trabajo: cita sus "expected outcomes" y explica cómo los
cubres. Comprueba siempre en el *template* de la convocatoria el límite de páginas y las tablas obligatorias.

## Plan Estatal (AEI, p. ej. Proyectos de Generación de Conocimiento)

Sigue el modelo de memoria científico-técnica de la convocatoria del año (suele pedir: antecedentes y estado
actual, resultados previos del equipo, hipótesis y objetivos, metodología y plan de trabajo con cronograma,
recursos, impacto esperado y plan de difusión, capacidad formativa) y los CV abreviados (CVA) en el formato
indicado. Respeta el límite de páginas y el tipo de letra: la AEI puede excluir memorias que no lo cumplan.

## Tablas útiles (Markdown → Word con pandoc, o LaTeX)

| WP | Título | Líder | Inicio | Fin | Entregables |
|---|---|---|---|---|---|
| WP1 | Gestión | Socio 1 (coordinador) | M1 | M36 | D1.1 Plan de gestión de datos (M6) |

| Riesgo | Prob. | Impacto | Mitigación |
|---|---|---|---|
| Retraso en la adquisición del equipo | Media | Alto | Ensayos iniciales con equipo del grupo; pedido en M1 |

Gantt con matplotlib: `ax.barh(tareas, duraciones, left=inicios)`, hitos con `ax.plot(mes, fila, "kD")`,
eje x en meses (M1…M36), una barra por tarea con el color de su WP.

## Consejos de evaluador

- La primera página decide: objetivo y aportación claros en el primer párrafo.
- Coherencia total: objetivos ↔ WP ↔ entregables ↔ presupuesto ↔ impacto.
- Viabilidad demostrada con resultados preliminares y datos concretos.
- Figuras explicativas (esquema del enfoque, Gantt) ahorran texto.
- Sin palabrería ("innovador", "disruptivo") sin pruebas; cifras y KPI verificables.
- Revisa con la plantilla de evaluación: puntúate tú mismo criterio a criterio antes de enviar.
