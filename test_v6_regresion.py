"""Pruebas sintéticas, sin RUT ni remuneraciones de los usuarios."""
import fitz
import pandas as pd
from previred_pdf import _tipo_pagina, _parsear_fila
from certificados_previred import extraer_certificado_previred


def test_anexo_salud_con_cabecera_fragmentada():
    pagina = ('PLANILLA DE DECLARACION Y PAGO SIMULTANEO DE OBLIGACIONES PREVISIONALES\n'
              'ANEXOS DE DETALLE ANEXOS: Detalle de Cotizaciones Movimiento del personal\n'
              'Días Trabajados P D E E N C I S M L I A P Ó R O N A N O D IB A R L E E')
    assert _tipo_pagina(pagina) == 'salud'


def test_salud_movimiento_fecha_incompleta():
    linea='1 12.345.678-6 PEREZ LOPEZ JUAN TEST 14 AFP 308.584 21.601 5 17 07 2025'
    # Si DV es inválido, no se convierte a trabajador.
    assert _parsear_fila(linea,'salud') is None


def test_afp_sin_codigo_ultimo():
    linea='12.345.678-5 PERSONA DE PRUEBA 601.075 67.741 9.016 0 0 0 601.075 0 18.032'
    r=_parsear_fila(linea,'afp')
    assert r and r['renta']==601075


def test_ss_truncado_no_inventa_dias():
    linea='5 12.345.678-5 PERSONA DE PRUEBA 548.180 0 4.933'
    r=_parsear_fila(linea,'seguro_social')
    assert r and r['renta']==548180 and r['dias'] is None and r['parcial']


def test_certificado_no_inventa_dias(monkeypatch):
    import certificados_previred as cp
    texto=('Certificado de Pagos de Cotizaciones Previsionales. '
           'cotizaciones previsionales del Sr.(a) ALFA BETA GAMMA, Rut: 12.345.678-5, '
           'por los períodos e instituciones de previsión que a continuación se indican, '
           'se encuentran pagadas por su empleador EMPRESA PRUEBA SPA, Rut: 12.345.678-5. '
           'CAPITAL (COTIZACION OBLIGATORIA) Agosto 2025 REM $28.000. '
           'FONASA (COTIZACION SALUD) Agosto 2025 REM $28.000. '
           'INSTITUTO DE SEGURIDAD DEL TRABAJO IST Agosto 2025 REM $28.000.')
    monkeypatch.setattr(cp,'_leer_ocr',lambda page:texto)
    doc=fitz.open(); doc.new_page(); data=doc.tobytes();doc.close()
    r=extraer_certificado_previred(data,'prueba.pdf')
    assert r is not None and len(r)==1 and pd.isna(r.iloc[0]['dias_reales_pagados'])
    assert r.iloc[0]['renta_imponible_salud']==28000
