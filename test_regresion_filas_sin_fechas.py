"""Regresiones sintéticas: no contienen nombres ni RUT de trabajadores reales."""
from previred_pdf import _parsear_fila, _totales_resumen


def test_filas_de_trabajador_sin_movimiento_conservadas():
    rut = "12.345.678-5"
    nombre = "PEREZ GONZALEZ PERSONA PRUEBA"
    casos = {
        "mutual": f"{rut} {nombre} 553.553 19.264 0",
        "afp": f"{rut} {nombre} 553.553 63.935 0 0 0 0 553.553 3.321 13.285 0",
        "salud": f"2 {rut} {nombre} 30 AFP 553.553 38.749 0",
        "seguro_social": f"2 {rut} {nombre} 553.553 0 3.986 4.982 9.853 JC 30 0",
    }
    for fuente, linea in casos.items():
        dato = _parsear_fila(linea, fuente)
        assert dato is not None, f"La fuente {fuente} descartó una fila sin fechas"
        assert dato["renta"] == 553553
        if fuente in {"salud", "seguro_social"}:
            assert dato["dias"] == 30


def test_una_fecha_es_legal_y_se_conserva():
    # Previred: alta con fecha de inicio, sin fecha término (no es error de formato).
    row=_parsear_fila(
        "12.345.678-5 PEREZ GONZALEZ PERSONA PRUEBA 553.553 19.264 20 05/08/2026",
        "mutual",
    )
    assert row is not None and row["renta"]==553553


def test_resumen_mutual_de_seguridad_no_se_confunde_con_pago():
    fuentes = [
        "COMPROBANTE DE PAGO DE COTIZACIONES PREVISIONALES\n"
        "Mutual de Seguridad CChC\n"
        "TOTAL REMUNERACIONES 1.051.751\n"
        "TOTAL A PAGAR A LA MUTUAL 36.601"
    ]
    assert _totales_resumen(fuentes) == {"mutual": 1051751}


def test_cero_no_es_base_ni_cotizacion_positiva():
    linea = "12.345.678-5 PEREZ GONZALEZ PERSONA PRUEBA 0 0 0"
    assert _parsear_fila(linea, "mutual")["renta"] == 0
