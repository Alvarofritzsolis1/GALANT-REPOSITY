"""Regresiones V10: la clasificación usa títulos, no la pestaña de subida.

Ejemplos sintéticos: ningún RUT real ni certificado de terceros en el repositorio.
"""
import fitz
from clasificador_documentos import clasificar_documento, enrutar_archivos
from f301 import extract_obra, pagos_remuneraciones, mes_en_nombre_archivo


def pdf_con_texto(texto):
    d=fitz.open()
    p=d.new_page()
    for pos,linea in enumerate(texto.splitlines()):
        p.insert_text((40,55+pos*20),linea,fontsize=10)
    b=d.tobytes()
    d.close()
    return b


def test_f301_cargado_erroneamente_en_cotizaciones():
    body='Certificado de Cumplimiento de Obligaciones Laborales y Previsionales\nInstitucion Recaudadora Previred\nVerifica este certificado en el sitio verificador.dirtrab.cl'
    pdf=pdf_con_texto(body)
    a,b,avisos,errores=enrutar_archivos([(pdf,'2026_JUNIO_EJEMPLO_F30.pdf')],[])
    assert not a and len(b)==1 and not errores
    assert any('enviado automáticamente a F301' in x for x in avisos)


def test_cotizaciones_no_se_confunden_con_f301_por_tener_previred():
    x=pdf_con_texto('FORMULARIO DE COTIZACIONES PREVISIONALES\nSeguro Social\nPREVIRED')
    a,b,avisos,err=enrutar_archivos([],[(x,'archivo_leyes_sociales.pdf')])
    assert len(a)==1 and not b and not err


def test_deduplicacion_de_misma_version_por_hash():
    x=pdf_con_texto('Certificado de Cumplimiento de Obligaciones Laborales y Previsionales')
    a,b,avisos,errores=enrutar_archivos([(x,'a.pdf')],[(x,'b.pdf')])
    assert len(b)==1 and any('duplicado' in msg for msg in avisos)


def test_f30_antecedentes_no_es_f301():
    x=pdf_con_texto('Certificado de Antecedentes Laborales y Previsionales')
    a,b,avisos,errores=enrutar_archivos([(x,'F30.pdf')],[])
    assert not a and not b and errores[0][1].startswith('F30 de antecedentes')


def test_mencion_previred_no_es_prueba_de_planilla():
    x=pdf_con_texto('CERTIFICADO DE CUMPLIMIENTO DE OBLIGACIONES LABORALES Y PREVISIONALES\nPREVIRED')
    assert clasificar_documento(x,'a.pdf').tipo=='F301'


def test_nombre_archivo_diferente_es_solo_metadato():
    assert mes_en_nombre_archivo('2026-JUNIO_FIN_MES_EJEMPLO_F30.pdf')=='2026-06'


def test_obras_multilinea_conservan_todos_los_terminos():
    t=('2 - Obra / Faena / Servicio\n'
       'Nombre Segun Contrato Civil Modo Participacion\n'
       'EDIFICIO CUMBRE LOMAS\n'
       '                     Contratista\n'
       'VERDES\n'
       'Tipo de Obra Empresa Principal\n')
    assert extract_obra(t)=='EDIFICIO CUMBRE LOMAS VERDES'


def test_estado_remuneraciones_no_confunde_monto():
    t='Pagadas No Pagadas Monto Informado\nRemuneraciones\n8 0 $916.299'
    assert pagos_remuneraciones(t)==(8,0)
    assert pagos_remuneraciones('Monto informado $500000')==(None,None)
