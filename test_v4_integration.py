"""Pruebas de integración de V4 sobre documentos PDF sintéticos."""
from auditoria_laboral import ejecutar_pipeline
from test_previred_pdf import _pdf_sintetico


def test_periodo_esperado_ajeno_no_sobrescribe_periodo_pdf():
    matriz,excel,detalle,log=ejecutar_pipeline(
        [(_pdf_sintetico(), '2026-MARZO_DEMO.pdf')],periodo_esperado='2026-03',
    )
    assert all(matriz.periodo_remuneracion=='2026-02')
    assert all(matriz.estado_auditoria=='REVISAR_DATOS')
    assert 'difiere del esperado 2026-03' in ' '.join(detalle.detalle_tecnico)


def test_asistencia_discrepante_queda_en_revision_y_hoja_excel():
    asist=b'rut_trabajador,dias_asistencia\n12.345.678-5,1\n17.654.321-3,1\n'
    matriz,excel,detalle,log=ejecutar_pipeline(
        [(_pdf_sintetico(),'mes.pdf')], asistencia=(asist,'asistencia.csv'),
    )
    reg=matriz[matriz.trabajador_rut=='123456785'].iloc[0]
    assert int(reg.dias_reales_pagados)==2
    assert reg.estado_auditoria=='REVISAR_DATOS'
    assert 'DIFERENCIA_A_VERIFICAR' in ' '.join(detalle.detalle_tecnico)
    import zipfile,io
    with zipfile.ZipFile(io.BytesIO(excel)) as zipf:
        assert 'Comparacion_Asistencia' in zipf.read('xl/workbook.xml').decode()


def test_modo_ocr_rapido_no_afecta_texto_digital():
    matriz,_,_,_=ejecutar_pipeline([(_pdf_sintetico(),'mes.pdf')],modo_ocr='rapido')
    assert len(matriz)==2
