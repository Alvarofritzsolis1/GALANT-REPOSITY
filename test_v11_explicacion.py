"""Regresiones mínimas de la V11, sin datos de trabajadores reales."""
import pandas as pd
from explicaciones_revision import explicar_observacion
from auditoria_laboral import limpiar_y_transformar, aplicar_motor_auditoria
from test_auditoria_laboral import fila_base


def test_explicacion_diferencia_ocr_no_asegura_incumplimiento():
    info=explicar_observacion('Extracción OCR: p.6, fila 2: lecturas alternativas de renta [4933, 35933]; cotejar')
    assert 'no significa que' in info['motivo_revision'].lower()
    assert info['tipo_revision']=='LECTURA DE MONTO'
    assert '4933' in info['detalle_tecnico']


def test_explicacion_diferencia_real_compara_tres_fuentes():
    info=explicar_observacion('Descuadre de bases entre instituciones', {
        'renta_imponible_afp':100000,'renta_imponible_salud':90000,'renta_imponible_mutual':100000})
    assert '90.000' in info['motivo_revision']
    assert info['prioridad_revision']=='ALERTA'


def test_error_de_formato_no_se_oculta_tras_explicacion_clara():
    registro=fila_base();registro['Días Trabajados']='30,5'
    datos=limpiar_y_transformar(pd.DataFrame([registro]),'datos.xlsx')
    matriz,detalle=aplicar_motor_auditoria(datos)
    assert matriz.iloc[0]['estado_auditoria']=='REVISAR_DATOS'
    assert 'enteros' in detalle.iloc[0]['motivo_revision'].lower()
    assert 'fraccion' in detalle.iloc[0]['motivo_revision'].lower()
    assert '30,5' not in detalle.iloc[0]['motivo_revision']
    assert 'enteros' in detalle.iloc[0]['detalle_tecnico'].lower()


def test_origen_ocr_solo_no_es_motivo_revision():
    registro=fila_base()
    datos=pd.DataFrame([registro])
    datos['__ocr_origen']='ESCANEADO'
    datos['__ocr_conciliacion']='OCR_CONCILIADO'
    datos['__conflicto_pdf']=''
    matriz,detalle=aplicar_motor_auditoria(limpiar_y_transformar(datos,'escaneado.pdf'),tolerancia_clp=0)
    assert matriz.iloc[0]['estado_auditoria']=='CONFORME'
    assert detalle.empty


def test_diferencia_real_escaneada_no_se_aprueba_por_igualdad_parcial():
    registro=fila_base()
    datos=pd.DataFrame([registro])
    datos['__ocr_origen']='ESCANEADO'
    datos['__conflicto_pdf']='Días no conciliados: Salud 6, Seguro Social 8; revisar páginas de detalle'
    matriz,detalle=aplicar_motor_auditoria(limpiar_y_transformar(datos,'escaneado.pdf'),tolerancia_clp=0)
    assert matriz.iloc[0]['estado_auditoria']=='REVISAR_DATOS'
    assert 'puede confundir' in detalle.iloc[0]['motivo_revision'].lower()
