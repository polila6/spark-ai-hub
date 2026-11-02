---
name: python-tests
description: Úsala para organizar un proyecto Python de investigación de forma reproducible (estructura, dependencias, semillas) y para escribir y ejecutar pruebas con pytest o unittest, incluidas pruebas numéricas con tolerancias.
license: MIT
---
# Proyecto Python reproducible y con pruebas

## Estructura recomendada

```
proyecto/
├── README.md            # qué hace, cómo instalar y ejecutar, de dónde salen los datos
├── requirements.txt     # dependencias con versión: numpy==2.*, pandas==3.*
├── src/miproyecto/      # código importable (funciones puras, sin rutas fijas)
│   ├── __init__.py
│   └── modelo.py
├── scripts/             # puntos de entrada: python scripts/analisis.py --entrada datos/x.csv
├── tests/
│   └── test_modelo.py
├── datos/crudo/         # nunca se modifica
├── datos/procesado/
└── resultados/          # figuras, tablas: regenerables desde los scripts
```

Para que `tests/` importe `src/`: ejecuta con `PYTHONPATH=src python -m pytest`, o crea un `pyproject.toml`
mínimo y añade `pythonpath = ["src"]` en `[tool.pytest.ini_options]` (pytest ≥ 7).

```toml
[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

## Ejecutar pruebas en este entorno

- **pytest no viene instalado.** En un workspace con paquetes habilitados: `pip install -q pytest` y después
  `python -m pytest -q` (añade `-x` para parar en el primer fallo, `-k nombre` para filtrar, `-vv` para detalle).
- Sin red (run_code, o paquetes deshabilitados): usa **unittest** de la biblioteca estándar:
  `python -m unittest discover -s tests -v` (las clases heredan de `unittest.TestCase`).
- Cobertura (opcional): `pip install pytest-cov` y `python -m pytest --cov=src --cov-report=term-missing`.

## Plantilla pytest

```python
# tests/test_modelo.py
import math

import numpy as np
import pytest

from miproyecto.modelo import media_movil, normalizar


def test_media_movil_caso_basico():
    assert media_movil([1, 2, 3, 4], ventana=2) == pytest.approx([1.5, 2.5, 3.5])


@pytest.mark.parametrize("datos, esperado", [
    ([0, 5, 10], [0.0, 0.5, 1.0]),
    ([2, 2, 2], [0.0, 0.0, 0.0]),      # constant input: defined behaviour, no division by zero
])
def test_normalizar(datos, esperado):
    np.testing.assert_allclose(normalizar(np.array(datos)), esperado, atol=1e-12)


def test_ventana_invalida():
    with pytest.raises(ValueError, match="ventana"):
        media_movil([1, 2], ventana=0)


def test_nan_se_propaga():
    assert math.isnan(media_movil([1, float("nan")], ventana=2)[0])


@pytest.fixture
def csv_ejemplo(tmp_path):
    ruta = tmp_path / "datos.csv"
    ruta.write_text("x;y\n1;2,5\n", encoding="utf-8")
    return ruta
```

## Plantilla unittest (sin instalar nada)

```python
import unittest

import numpy as np

from miproyecto.modelo import normalizar


class TestNormalizar(unittest.TestCase):
    def test_rango(self):
        np.testing.assert_allclose(normalizar(np.array([0, 5, 10])), [0, 0.5, 1])

    def test_vacio(self):
        with self.assertRaises(ValueError):
            normalizar(np.array([]))


if __name__ == "__main__":
    unittest.main()
```

## Qué probar en código científico

1. **Casos con solución conocida** (analítica, de un libro, de un ejemplo publicado).
2. **Propiedades/invariantes**: conservación (la suma de probabilidades es 1), simetrías, monotonía, unidades.
3. **Casos límite**: vacío, un elemento, ceros, negativos, NaN/inf, valores muy grandes, tipos inesperados.
4. **Regresión**: guarda un resultado validado (pequeño) y compara con tolerancia tras cada cambio.
5. **Errores esperados**: entradas inválidas lanzan excepciones claras.
6. Comparaciones de flotantes SIEMPRE con tolerancia: `pytest.approx(x, rel=1e-6)`,
   `np.testing.assert_allclose(a, b, rtol=1e-7, atol=0)`; nunca `==` entre floats calculados.
7. Aleatoriedad: semilla fija (`rng = np.random.default_rng(42)`) pasada como argumento, no global.
8. DataFrames: `pd.testing.assert_frame_equal(obtenido, esperado, check_dtype=False)`.

## Reproducibilidad

- Fija versiones: `pip freeze > requirements.txt` (o solo las directas con versión).
- Registra semillas, versión de Python y de bibliotecas en los resultados.
- Nada de rutas absolutas ni `os.chdir`; usa `pathlib.Path(__file__).parent` o argumentos.
- Los scripts no deben depender de haber ejecutado celdas en un orden (cuidado con notebooks).
- Git (en el equipo del usuario; aquí no está instalado): commits pequeños con mensaje en imperativo,
  `.gitignore` con `__pycache__/`, `.venv/`, `datos/crudo/` si son grandes o confidenciales.
