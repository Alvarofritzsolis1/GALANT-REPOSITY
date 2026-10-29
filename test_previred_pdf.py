"""Regresión sobre estructura Previred simulada, sin incorporar datos personales reales."""
from __future__ import annotations

import pytest
import fitz

from auditoria_laboral import ejecutar_pipeline
from previred_pdf import _parsear_fila, _periodos


def _pdf_sintetico() -> bytes:
    doc = fitz.open()
    paginas = [
        [
            "COMPROBANTE DE PREVIRED", "Identificacion del Empleador: Empresa",
            "Nombre o Razon Social RUT", "EMPRESA DEMO SPA 76.123.456-0",
        ],
        [
            "Pago Electronico Periodo de Remuneraciones: 02/2026",
            "Seguro Social PREVIRED", "Identificacion del Trabajador Cotizaciones Tipo Dias Movimiento de Personal",
            "Jornada Trabajados", "RUT Nombres y Apellidos Renta Imponible Seguro Social",
            "1 12.345.678-5 ALVAREZ PEREZ MARIA JOSE 35.933 0 323 0 0 JC 2 11 01/02/2026 04/02/2026",
            "2 12.345.678-5 ALVAREZ PEREZ MARIA JOSE 0 0 0 0 0 JC 0 4 07/02/2026 28/02/2026",
            "3 17.654.321-3 TORRES LOPEZ JUAN ANDRES 22.459 0 202 0 0 JC 1 11 01/02/2026 04/02/2026",
        ],
        [
            "DETALLE DE PAGO DE COTIZACIONES PREVISIONALES Y DEPOSITOS DE AHORRO VOLUNTARIO FONDO DE PENSIONES",
            "AFP UNO PREVIRED Periodo de Remuneraciones: 02/2026",
            "Identificacion del Trabajador Fondo de Pensiones Seguro Cesantia",
            "RUT Apellido Paterno, Materno, Nombres Remuneracion Imponible Cotizacion SIS",
            "12.345.678-5 ALVAREZ PEREZ MARIA JOSE 35.933 4.150 553 0 0 0 35.933 216 862 8 01/02/2026 04/02/2026",
            "17.654.321-3 TORRES LOPEZ JUAN ANDRES 22.459 2.372 346 0 0 0 22.459 135 539 8 01/02/2026 04/02/2026",
        ],
        [
            "PREVIRED ANEXOS DE DETALLE PLANILLA DE DECLARACION PAGO COTIZACIONES",
            "RUT 76.123.456-0 PERIODO DE REMUNERACION 02 2026",
            "ANEXOS: Detalle de Cotizaciones Movimiento del personal",
            "Dias Trabajados Entidad Previsional REMUNERACION IMPONIBLE PENSION O RENTA",
            "1 12.345.678-5 ALVAREZ PEREZ MARIA JOSE 2 AFP 35.933 2.515 11 01 02 2026 04 02 2026",
            "2 12.345.678-5 ALVAREZ PEREZ MARIA JOSE 0 AFP 0 0 4 07 02 2026 28 02 2026",
            "3 17.654.321-3 TORRES LOPEZ JUAN ANDRES 1 AFP 22.459 1.572 11 01 02 2026 04 02 2026",
        ],
        [
            "DETALLE DE COMPROBANTE PAGO DE COTIZACIONES PREVISIONALES",
            "Instituto de Seguridad Laboral ISL PREVIRED Periodo de Remuneraciones: 02/2026",
            "RUT Apellido Paterno Apellido Materno Nombres Remuneracion Cotizacion Movimiento Personal",
            "12.345.678-5 ALVAREZ PEREZ MARIA JOSE 35.933 334 6 01/02/2026 04/02/2026",
            "17.654.321-3 TORRES LOPEZ JUAN ANDRES 22.459 209 6 01/02/2026 04/02/2026",
        ],
    ]
    for i, lineas in enumerate(paginas):
        page = doc.new_page(width=780, height=840)
        for j, linea in enumerate(lineas):
            page.insert_text((30, 50 + 27 * j), linea, fontsize=9, fontname="helv")
    datos = doc.tobytes()
    doc.close()
    return datos


def test_periodo_es_del_contenido_no_de_nombre_archivo():
    assert _periodos("Periodo de Remuneraciones: 02/2026") == {"2026-02"}
    matriz, excel, revision, log = ejecutar_pipeline([
        (_pdf_sintetico(), "2026-MARZO_FIN_MES_EMPRESA_DEMO_LEYES_SOCIALES.pdf")
    ])
    assert len(matriz) == 2
    assert set(matriz["periodo_remuneracion"]) == {"2026-02"}
    assert set(matriz["estado_auditoria"]) == {"CONFORME"}
    assert sorted(matriz["dias_reales_pagados"].tolist()) == [1, 2]
    assert sum(matriz["renta_imponible_salud"]) == 58392
    assert sum(matriz["renta_imponible_afp"]) == 58392
    assert sum(matriz["renta_imponible_mutual"]) == 58392
    assert set(matriz["mutual_adherida"]) == {"ISL"}
    assert revision.empty
    assert log.iloc[0]["resultado"] == "PROCESADO"
    assert b"Trazabilidad_PDF" in excel or excel


def test_cotizacion_no_se_confunde_con_renta():
    afp = _parsear_fila("12.345.678-5 ALVAREZ PEREZ MARIA JOSE 35.933 4.150 553 0 0 0 35.933 216 862 8 01/02/2026 04/02/2026", "afp")
    assert afp["renta"] == 35933
    isl = _parsear_fila("12.345.678-5 ALVAREZ PEREZ MARIA JOSE 35.933 334 6 01/02/2026 04/02/2026", "mutual")
    assert isl["renta"] == 35933
    salud = _parsear_fila("1 12.345.678-5 ALVAREZ PEREZ MARIA JOSE 2 AFP 35.933 2.515 11 01 02 2026 04 02 2026", "salud")
    assert salud["renta"] == 35933 and salud["dias"] == 2


def test_falta_salud_es_revision_no_conforme():
    # Reproducción reducida: AFP e ISL sin detalle salud.
    doc = fitz.open()
    for text in (
        "PREVIRED\nNombre o Razon Social RUT\nEMPRESA DEMO SPA 76.123.456-0",
        "DETALLE DE PAGO DE COTIZACIONES PREVISIONALES Y DEPOSITOS DE AHORRO VOLUNTARIO FONDO DE PENSIONES\nAFP UNO PREVIRED Periodo de Remuneraciones: 02/2026\nIdentificacion del Trabajador Fondo de Pensiones\n12.345.678-5 ALVAREZ PEREZ MARIA JOSE 35.933 4.150 553 0 0 0 35.933 216 862 8 01/02/2026 04/02/2026",
    ):
        page = doc.new_page()
        page.insert_text((30, 50), text, fontsize=8)
    datos = doc.tobytes()
    doc.close()
    matriz, _, revision, _ = ejecutar_pipeline([(datos, "pago.pdf")])
    assert matriz.iloc[0]["estado_auditoria"] == "REVISAR_DATOS"
    assert "salud" in revision.iloc[0]["detalle_tecnico"].lower()


def test_totales_portadas_por_institucion_no_suman_cotizaciones():
    from previred_pdf import _totales_resumen
    textos = [
        "FORMULARIO DE COTIZACIONES PREVISIONALES SEGURO SOCIAL\n"
        "TOTAL REMUNERACION IMPONIBLE DECLARADA EN $ $58.392",
        "PLANILLA DE DECLARACION Y PAGO SIMULTANEO DE OBLIGACIONES PREVISIONALES\n"
        "TOTAL REMUNERACION IMPONIBLE DECLARADA EN $ 58.392",
        "COMPROBANTE DE PAGO DE COTIZACIONES PREVISIONALES\nAFP Provida\n"
        "Renta Imponible 35.933 35.933 NUMERO AFILIADOS INFORMADOS 1\n"
        "TOTAL A PAGAR FONDO DE PENSIONES AFP Provida 4.703",
        "COMPROBANTE DE PAGO DE COTIZACIONES PREVISIONALES\nAFP UNO\n"
        "Renta Imponible 22.459 22.459 NUMERO AFILIADOS INFORMADOS 1",
        "COMPROBANTE DE PAGO DE COTIZACIONES PREVISIONALES\n"
        "Instituto de Seguridad Laboral ISL\nTOTAL REMUNERACIONES 58.392\n"
        "TOTAL A PAGAR A LA MUTUAL 543",
    ]
    assert _totales_resumen(textos) == {
        "seguro_social": 58392, "salud": 58392, "afp": 58392, "mutual": 58392,
    }
