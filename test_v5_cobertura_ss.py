"""Regresión: un subtotal Seguro Social menor no contamina la matriz de bases.

Datos sintéticos; NO se almacenan PDF ni RUT reales de contratistas.
"""
from __future__ import annotations

from collections import defaultdict

import pandas as pd

from previred_pdf import _parsear_fila, _rut_ok
from auditoria_laboral import aplicar_motor_auditoria


def test_mutual_sin_fecha_inicio_acepta_fila():
    # La planilla puede mostrar solo fecha término. Antes se omitían filas.
    def dv_rut(cuerpo: str) -> str:
        t = sum(int(c) * (2 + i % 6) for i, c in enumerate(reversed(cuerpo)))
        v = 11 - t % 11
        return '0' if v == 11 else 'K' if v == 10 else str(v)

    rut = '12456378' + dv_rut('12456378')
    assert _rut_ok(rut)
    dato = _parsear_fila(f'{rut} PEREZ TORRES JUAN ALBERTO 184.518 6.421 1 10/08/2026', 'mutual')
    assert dato is not None and dato['renta'] == 184518


def test_base_ss_no_es_requisito_de_cuadratura_entre_salud_afp_mutual():
    # La cobertura SS se valida en hoja independiente, sin inventar exenciones.
    datos = pd.DataFrame([{
        'periodo_remuneracion': '2026-08',
        'contratista_nombre': 'EMPRESA EJEMPLO',
        'contratista_rut': '123456785',
        'trabajador_nombre': 'TRABAJADOR DE EJEMPLO',
        'trabajador_rut': '100000008',
        'dias_reales_pagados': 6,
        'renta_imponible_salud': 110711,
        'renta_imponible_afp': 110711,
        'renta_imponible_mutual': 110711,
        'mutual_adherida': 'ACHS',
        '__conflicto_pdf': '',
        '__campos_ausentes': '',
        '__faltantes_fila': '',
        '__valores_invalidos': '',
        '__clave_duplicada': False,
        '__rut_invalido': False,
    }])
    # La función verifica la cuadratura monetaria indicada: no declara cobertura SS.
    matriz, detalle = aplicar_motor_auditoria(datos, tolerancia_clp=0)
    assert len(matriz) == 1 and matriz.iloc[0]['estado_auditoria'] == 'CONFORME'
    assert detalle.empty


def test_parser_integrado_seguro_social_es_subconjunto(monkeypatch):
    """RUT 2 sin SS no produce conflicto monetario global ficticio."""
    import pdfplumber
    from previred_pdf import extraer_previred

    class Page:
        def __init__(self, texto):
            self.texto = texto
        def extract_text(self, **kwargs):
            return self.texto

    class FakePdf:
        def __init__(self, paginas):
            self.pages = [Page(t) for t in paginas]
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False

    # Dos RUT sintéticos válidos en la misma empresa. No contener datos personales reales.
    def make_rut(body):
        t = sum(int(c) * (2 + i % 6) for i, c in enumerate(reversed(body)))
        dv = 11 - t % 11
        return body + ('0' if dv == 11 else 'K' if dv == 10 else str(dv))

    r1, r2 = make_rut('12345678'), make_rut('23456789')
    empleador = make_rut('76543210')
    cab = f'Nombre o Razón Social RUT\nEMPRESA EJEMPLO {empleador}\n'
    periodo = 'Período de Remuneraciones: 08/2026\n'
    afp = ('DETALLE DE PAGO DE COTIZACIONES PREVISIONALES FONDO DE PENSIONES - AFP\n'
           + periodo + cab
           + f'{r1} PEREZ TORRES JUAN ALBERTO 100 10 0 0 0 0 100 0 0 0\n'
           + f'{r2} GOMEZ REYES CARLOS ANTONIO 200 20 0 0 0 0 200 0 0 0\n')
    ss = ('FORMULARIO DE COTIZACIONES PREVISIONALES – SEGURO SOCIAL PREVISIONAL\n'
          + periodo + 'Identificación del Trabajador, Jornada y Días Trabajados\n' + cab
          + f'1 {r1} PEREZ TORRES JUAN ALBERTO 100 0 10 0 0 JC 20 0\n')
    salud = ('PLANILLA DE DECLARACION Y PAGO SIMULTANEO DE OBLIGACIONES PREVISIONALES\n'
             + 'ANEXOS DE DETALLE: Días Trabajados, Renta Imponible, Cotizaciones\n'
             + periodo + cab
             + f'1 {r1} PEREZ TORRES JUAN ALBERTO 20 AFP 100 7 0\n'
             + f'2 {r2} GOMEZ REYES CARLOS ANTONIO 30 AFP 200 14 0\n')
    mutual = ('DETALLE DE COMPROBANTE PAGO DE COTIZACIONES PREVISIONALES\n'
              + 'Asociación Chilena de Seguridad ACHS\n' + periodo + cab
              + f'{r1} PEREZ TORRES JUAN ALBERTO 100 3 0\n'
              + f'{r2} GOMEZ REYES CARLOS ANTONIO 200 6 0\n')
    monkeypatch.setattr(pdfplumber, 'open', lambda _: FakePdf([afp, salud, ss, mutual]))
    salida = extraer_previred(b'%PDF-sintetico', 'ejemplo-previred.pdf')
    assert len(salida) == 2
    assert not any(salida['__conflicto_pdf'])
    que_falta = salida.loc[salida['trabajador_rut'].eq(r2), '__cobertura_seguro_social'].iloc[0]
    assert que_falta == 'SIN_REGISTRO_SS'
    assert salida['renta_imponible_afp'].sum() == 300
    assert salida['renta_imponible_salud'].sum() == 300
