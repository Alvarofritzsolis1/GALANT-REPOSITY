"""Pruebas funcionales con datos ficticios. Ejecutar: python -m pytest -q."""
import io
import pandas as pd
import pytest

from auditoria_laboral import (
    aplicar_motor_auditoria,
    clave_rut,
    convertir_monto,
    ejecutar_pipeline,
    limpiar_y_transformar,
    normalizar_periodo,
    rut_chileno_valido,
    _extraer_contexto_pdf,
    cargar_topes_csv,
    monto_es_valido,
    monto_es_entero,
)


def fila_base():
    return {
        "Periodo": "08/2026",
        "Razón Social": "Empresa Demo SpA",
        "RUT Empleador": "76.123.456-0",
        "Nombre Trabajador": "Trabajador Demo",
        "RUT Trabajador": "12.345.678-5",
        "Días Trabajados": "30",
        "Renta Imponible Salud": "$1.000.000",
        "Renta Imponible AFP": "$1.000.000",
        "Renta Imponible Mutual": "$1.000.000",
        "Mutual": "ACHS",
    }


def auditar(registro, nombre="remuneraciones_2026-08.xlsx", incluir_mutual=True):
    limpio = limpiar_y_transformar(pd.DataFrame([registro]), nombre)
    return aplicar_motor_auditoria(limpio, incluir_mutual=incluir_mutual)


def main():
    assert clave_rut("12.345.678-5") == "123456785"
    assert rut_chileno_valido("12.345.678-5") is True
    assert normalizar_periodo("08/2026") == "2026-08"
    assert normalizar_periodo("Agosto 2026") == "2026-08"
    assert convertir_monto("$1.234.567") == 1234567
    assert convertir_monto("1.234,50") == 1235  # redondeo monetario half-up
    assert convertir_monto("N/A") == 0
    assert monto_es_valido("$1.234.567") is True
    assert monto_es_valido("1.234,50") is True
    assert monto_es_valido("dato incorrecto") is False
    assert monto_es_valido("1.2.3") is False
    assert monto_es_entero("30,0") is True
    assert monto_es_entero("30,5") is False

    # Caso conforme: todos los datos están, RUTs pasan DV y bases cuadran.
    matriz, detalle = auditar(fila_base())
    assert matriz.loc[0, "estado_auditoria"] == "CONFORME"
    assert len(matriz.columns) == 11
    assert detalle.empty

    # Cero días con rentas positivas debe alertar.
    registro = fila_base()
    registro["Días Trabajados"] = "0"
    matriz, detalle = auditar(registro)
    assert matriz.loc[0, "estado_auditoria"] == "ALERTA_DIAS_CERO"
    assert not detalle.empty

    # Diferencia de bases superior a la tolerancia debe alertar.
    registro = fila_base()
    registro["Renta Imponible AFP"] = "$900.000"
    matriz, _ = auditar(registro)
    assert matriz.loc[0, "estado_auditoria"] == "ALERTA_DESCUADRE_RENTAS"

    # N/A se muestra como cero, pero no debe generar un falso CONFORME.
    registro = fila_base()
    registro["Días Trabajados"] = "N/A"
    matriz, _ = auditar(registro)
    assert matriz.loc[0, "dias_reales_pagados"] == 0
    assert matriz.loc[0, "estado_auditoria"] == "REVISAR_DATOS"

    # Los días fraccionarios no se redondean para emitir una conclusión definitiva.
    registro = fila_base()
    registro["Días Trabajados"] = "30,5"
    matriz, detalle = auditar(registro)
    assert matriz.loc[0, "estado_auditoria"] == "REVISAR_DATOS"
    assert "los días deben ser enteros" in detalle.loc[0, "detalle_tecnico"].lower()

    # Si existen días contractuales y reales, el mapeo debe preferir el campo que indica días reales.
    registro = fila_base()
    del registro["Días Trabajados"]
    registro["Días pagados base contractual"] = "30"
    registro["Días trabajados reales"] = "18"
    limpio = limpiar_y_transformar(pd.DataFrame([registro]), "remuneraciones_2026-08.xlsx")
    assert limpio.loc[0, "dias_reales_pagados"] == 18

    # Un texto malformado en una renta no puede convertirse silenciosamente en cero y aprobarse.
    registro = fila_base()
    registro["Renta Imponible Salud"] = "dato incorrecto"
    matriz, detalle = auditar(registro)
    assert matriz.loc[0, "estado_auditoria"] == "REVISAR_DATOS"
    assert "valores numéricos inválidos" in detalle.loc[0, "detalle_tecnico"].lower()

    # Topes contradictorios para el mismo período deben detener la carga, no usar el último sin avisar.
    with pytest.raises(ValueError, match="topes distintos"):
        cargar_topes_csv(
            b"periodo_remuneracion,tope_imponible_clp\n2026-08,3000000\n2026-08,3200000\n"
        )

    # Un texto que parece fórmula Excel debe exportarse como texto, no como fórmula activa.
    registro = fila_base()
    registro["Nombre Trabajador"] = '=HYPERLINK("https://example.com","abrir")'
    limpio = limpiar_y_transformar(pd.DataFrame([registro]), "remuneraciones_2026-08.xlsx")
    matriz, detalle = aplicar_motor_auditoria(limpio)
    from auditoria_laboral import exportar_excel
    excel_seguro = exportar_excel(matriz, detalle, pd.DataFrame([{"resultado": "PROCESADO"}]))
    libro_seguro = pd.ExcelFile(io.BytesIO(excel_seguro))
    assert libro_seguro.sheet_names[0] == "Matriz_Auditoria"
    import openpyxl
    wb_seguro = openpyxl.load_workbook(io.BytesIO(excel_seguro), data_only=False)
    assert wb_seguro["Matriz_Auditoria"]["D4"].data_type == "s"

    # No se inventa una mutual cuando no está en la fuente.
    registro = fila_base()
    del registro["Mutual"]
    matriz, detalle = auditar(registro)
    assert matriz.loc[0, "mutual_adherida"] == "NO IDENTIFICADA"
    assert matriz.loc[0, "estado_auditoria"] == "REVISAR_DATOS"
    assert "mutual" in detalle.loc[0, "detalle_tecnico"].lower()

    # El período se puede inferir de un nombre explícito si la columna no existe.
    registro = fila_base()
    del registro["Periodo"]
    matriz, _ = auditar(registro, nombre="remuneraciones_2026-08.xlsx")
    assert matriz.loc[0, "periodo_remuneracion"] == "2026-08"
    assert matriz.loc[0, "estado_auditoria"] == "CONFORME"

    # Un tope del período solo se aplica cuando se entrega explícitamente.
    registro = fila_base()
    registro["Renta Imponible Salud"] = 3_000_000
    registro["Renta Imponible AFP"] = 3_500_000
    registro["Renta Imponible Mutual"] = 3_400_000
    limpio = limpiar_y_transformar(pd.DataFrame([registro]), "remuneraciones_2026-08.xlsx")
    matriz_sin_tope, _ = aplicar_motor_auditoria(limpio, topes_por_periodo={})
    matriz_con_tope, detalle_tope = aplicar_motor_auditoria(limpio, topes_por_periodo={"2026-08": 2_000_000})
    assert matriz_sin_tope.loc[0, "estado_auditoria"] == "ALERTA_DESCUADRE_RENTAS"
    assert matriz_con_tope.loc[0, "estado_auditoria"] == "REVISAR_DATOS"
    assert "supera el tope informado" in detalle_tope.loc[0, "detalle_tecnico"].lower()

    # Metadatos explícitos del PDF se pueden detectar sin fijar una mutual por defecto.
    contexto = _extraer_contexto_pdf(
        "Razón Social o Nombre: Empresa Demo SpA\nR.U.T.: 76.123.456-0\n"
        "Periodo de Remuneraciones: 08/2026\nMutual de Seguridad C.CH.C."
    )
    assert contexto["periodo_remuneracion"] == "2026-08"
    assert contexto["contratista_rut"] == "761234560"
    assert contexto["mutual_adherida"] == "Mutual de Seguridad"

    # Pipeline completo y validación de las pestañas del Excel exportado.
    buffer = io.BytesIO()
    pd.DataFrame([fila_base()]).to_excel(buffer, index=False)
    matriz, excel, detalle, log = ejecutar_pipeline([(buffer.getvalue(), "remuneraciones_2026-08.xlsx")])
    libro = pd.ExcelFile(io.BytesIO(excel))
    assert libro.sheet_names == ["Matriz_Auditoria", "Detalle_Revision", "Diagnostico_OCR", "Resumen_Control", "Log_Procesamiento"]
    assert len(matriz) == 1 and matriz.loc[0, "estado_auditoria"] == "CONFORME"
    assert log.loc[0, "resultado"] == "PROCESADO"

    # Un registro duplicado se marca para revisión: no se suman montos automáticamente.
    duplicado = pd.DataFrame([fila_base(), fila_base()])
    limpio = limpiar_y_transformar(duplicado, "remuneraciones_2026-08.xlsx")
    matriz, detalle = aplicar_motor_auditoria(limpio)
    assert set(matriz["estado_auditoria"]) == {"REVISAR_DATOS"}
    assert len(detalle) == 2

    # También detecta claves duplicadas entre archivos diferentes que se cargan juntos.
    csv_a = pd.DataFrame([fila_base()]).to_csv(index=False).encode("utf-8-sig")
    csv_b = pd.DataFrame([fila_base()]).to_csv(index=False).encode("utf-8-sig")
    matriz, _, detalle, log = ejecutar_pipeline([
        (csv_a, "nomina_2026-08_a.csv"),
        (csv_b, "nomina_2026-08_b.csv"),
    ])
    assert set(matriz["estado_auditoria"]) == {"REVISAR_DATOS"}
    assert len(detalle) == 2

    print("Pruebas completadas correctamente.")


if __name__ == "__main__":
    main()


def test_core_regression_suite():
    """Expone al recolector pytest las pruebas funcionales escritas en main()."""
    main()


def test_streamlit_app_smoke():
    """Comprueba que la app renderiza sin excepciones en entornos con Streamlit instalado."""
    import pytest
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file("app.py", default_timeout=20).run()
    assert not app.exception, [str(error) for error in app.exception]
