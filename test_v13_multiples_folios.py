"""Regresión: un PDF puede contener varios folios de la MISMA institución.

Los RUT y nombres de estas pruebas son datos sintéticos.
"""
import fitz
from previred_pdf import _tipo_pagina, _totales_resumen, _conteos_afiliados, _resumenes_por_folio
from auditoria_laboral import ejecutar_pipeline


def _synthetic_pdf(pages):
    doc=fitz.open()
    for lines in pages:
        p=doc.new_page(width=950,height=850)
        for i,line in enumerate(lines):
            p.insert_text((30,40+i*23),line,fontsize=8)
    b=doc.tobytes();doc.close()
    return b


def test_encabezado_declaracion_no_pago_es_tipo_mutual():
    pagina = ("DETALLE DE COMPROBANTE DE DECLARACION Y NO PAGO DE COTIZACIONES PREVISIONALES\n"
              "Asociación Chilena de Seguridad (ACHS)\n"
              "RUT Apellido Paterno Apellido Materno Nombres Remuneracion Cotizacion Movimiento")
    assert _tipo_pagina(pagina)=='mutual'


def test_sumar_varias_portadas_en_vez_de_una_sola():
    textos=[
      'COMPROBANTE DE DECLARACION Y NO DE PAGO DE COTIZACIONES PREVISIONALES\n'
      'Número de Folio: 2080202609211936\nAsociación Chilena de Seguridad (ACHS)\n'
      'TOTAL REMUNERACIONES 35.933\nFecha Pago:23/10/2026 10:42 AM',
      'COMPROBANTE DE PAGO DE COTIZACIONES PREVISIONALES\n'
      'Número de Folio: 2080202610012813\nAsociación Chilena de Seguridad (ACHS)\n'
      'TOTAL REMUNERACIONES 22.459\nN° de Afiliados Informados 1',
      'COMPROBANTE DE DECLARACION Y NO PAGO DE COTIZACIONES PREVISIONALES FONDO DE PENSIONES\n'
      'Número de Folio: 2008202609370421\nAFP Capital\nRenta Imponible 35.933\nNUMERO AFILIADOS INFORMADOS 1',
      'COMPROBANTE DE PAGO DE COTIZACIONES PREVISIONALES FONDO DE PENSIONES\n'
      'Número de Folio: 2008202610037694\nAFP Capital\nRenta Imponible 22.459\nNUMERO AFILIADOS INFORMADOS 1',
      'PLANILLA DE PAGO FUERA DE PLAZO DE OBLIGACIONES PREVISIONALES DECLARADAS\n'
      'NÚMERO DE SERIE: 2017260901035990\nTOTAL REMUNERACIÓN IMPONIBLE DECLARADA EN $ 35.933\nN° de Afiliados 1',
      'PLANILLA DE DECLARACION Y PAGO SIMULTANEO DE OBLIGACIONES PREVISIONALES\n'
      'NÚMERO DE SERIE: 2017261000090260\nTOTAL REMUNERACIÓN IMPONIBLE DECLARADA EN $ 22.459\nN° de Afiliados 1',
    ]
    assert _totales_resumen(textos)=={'mutual':58392,'afp':58392,'salud':58392}
    assert _conteos_afiliados(textos)['afp']==2
    assert _conteos_afiliados(textos)['salud']==2
    assert len(_resumenes_por_folio(textos))==6


def test_conciliacion_en_pdf_con_varios_folios():
    lines=[
      ["COMPROBANTE DE PREVIRED", "Identificacion del Empleador: Empresa",
       "Nombre o Razon Social RUT", "EMPRESA DEMO SPA 76.123.456-0"],
      ["COMPROBANTE DE DECLARACION Y NO DE PAGO DE COTIZACIONES PREVISIONALES",
       "Número de Folio: 2080202609211936", "Asociacion Chilena de Seguridad ACHS",
       "TOTAL REMUNERACIONES 35.933", "Fecha Pago:23/10/2026 10:42 AM"],
      ["DETALLE DE COMPROBANTE DE DECLARACION Y NO PAGO DE COTIZACIONES PREVISIONALES",
       "Asociacion Chilena de Seguridad (ACHS) Periodo de Remuneraciones: 02/2026",
       "Número de Folio: 2080202609211936",
       "RUT Apellido Paterno Materno Nombres Remuneracion Cotizacion Movimiento",
       "12.345.678-5 ALVAREZ PEREZ MARIA JOSE 35.933 334 6 01/02/2026 04/02/2026"],
      ["COMPROBANTE DE PAGO DE COTIZACIONES PREVISIONALES",
       "Número de Folio: 2080202610012813", "Asociacion Chilena de Seguridad ACHS",
       "TOTAL REMUNERACIONES 22.459"],
      ["DETALLE DE COMPROBANTE PAGO DE COTIZACIONES PREVISIONALES",
       "Asociacion Chilena de Seguridad (ACHS) Periodo de Remuneraciones: 02/2026",
       "Número de Folio: 2080202610012813",
       "RUT Apellido Paterno Materno Nombres Remuneracion Cotizacion Movimiento",
       "17.654.321-3 TORRES LOPEZ JUAN ANDRES 22.459 209 6 01/02/2026 04/02/2026"],
      ["COMPROBANTE DE DECLARACION Y NO PAGO DE COTIZACIONES PREVISIONALES FONDO DE PENSIONES",
       "Número de Folio: 2008202609370421", "AFP Capital",
       "Renta Imponible 35.933", "NUMERO AFILIADOS INFORMADOS 1"],
      ["DETALLE DE PAGO DE COTIZACIONES PREVISIONALES FONDO DE PENSIONES",
       "AFP UNO Periodo de Remuneraciones: 02/2026 Numero de Folio: 2008202609370421",
       "Identificacion del Trabajador Fondo de Pensiones Seguro Cesantia",
       "12.345.678-5 ALVAREZ PEREZ MARIA JOSE 35.933 4.150 553 0 0 0 35.933 216 862 8 01/02/2026 04/02/2026"],
      ["COMPROBANTE DE PAGO DE COTIZACIONES PREVISIONALES FONDO DE PENSIONES",
       "Número de Folio: 2008202610037694", "AFP Capital",
       "Renta Imponible 22.459", "NUMERO AFILIADOS INFORMADOS 1"],
      ["DETALLE DE PAGO DE COTIZACIONES PREVISIONALES FONDO DE PENSIONES",
       "AFP UNO Periodo de Remuneraciones: 02/2026 Numero de Folio: 2008202610037694",
       "Identificacion del Trabajador Fondo de Pensiones Seguro Cesantia",
       "17.654.321-3 TORRES LOPEZ JUAN ANDRES 22.459 2.372 346 0 0 0 22.459 135 539 8 01/02/2026 04/02/2026"],
      ["PLANILLA DE PAGO FUERA DE PLAZO DE OBLIGACIONES PREVISIONALES DECLARADAS",
       "NÚMERO DE SERIE: 2017260901035990",
       "TOTAL REMUNERACIÓN IMPONIBLE DECLARADA EN $ 35.933", "N° de Afiliados 1"],
      ["ANEXOS DE DETALLE", "RUT 76.123.456-0 PERIODO DE REMUNERACION 02 2026",
       "Numero de Serie: 2017260901035990",
       "ANEXOS: Detalle de Cotizaciones Movimiento del personal",
       "Dias Trabajados Entidad Previsional REMUNERACION IMPONIBLE PENSION O RENTA",
       "1 12.345.678-5 ALVAREZ PEREZ MARIA JOSE 2 AFP 35.933 2.515 11 01 02 2026 04 02 2026"],
      ["PLANILLA DE DECLARACION Y PAGO SIMULTANEO DE OBLIGACIONES PREVISIONALES",
       "NÚMERO DE SERIE: 2017261000090260",
       "TOTAL REMUNERACIÓN IMPONIBLE DECLARADA EN $ 22.459", "N° de Afiliados 1"],
      ["ANEXOS DE DETALLE", "RUT 76.123.456-0 PERIODO DE REMUNERACION 02 2026",
       "Numero de Serie: 2017261000090260",
       "ANEXOS: Detalle de Cotizaciones Movimiento del personal",
       "Dias Trabajados Entidad Previsional REMUNERACION IMPONIBLE PENSION O RENTA",
       "2 17.654.321-3 TORRES LOPEZ JUAN ANDRES 1 AFP 22.459 1.572 11 01 02 2026 04 02 2026"],
    ]
    m, excel, rev, log = ejecutar_pipeline([(_synthetic_pdf(lines),'cotizaciones.pdf')])
    assert len(m)==2
    assert set(m.estado_auditoria)=={'CONFORME'}
    assert m.renta_imponible_afp.sum()==58392
    assert m.renta_imponible_mutual.sum()==58392
    assert m.renta_imponible_salud.sum()==58392
    assert rev.empty
    assert log.iloc[0]['resultado']=='PROCESADO'
