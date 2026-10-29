"""Regresión V4: datos sintéticos, ninguno real de trabajadores."""
import io
import hashlib
import pandas as pd
import pytest
from previred_pdf import _parsear_fila,_tipo_pagina
from aprendizaje_revisiones import nueva_memoria,registrar_correccion,aplicar_correcciones,guardar_memoria,cargar_memoria,registrar_plantilla,clasificador_aprendido
from control_asistencia import leer_asistencia,comparar_asistencia
from auditoria_laboral import rut_chileno_valido

RUT='123456785'

def test_fecha_sola_no_descarta_renta_afp_salud_seguro_social():
    n='PRUEBA PEREZ ALVAREZ'
    a=_parsear_fila(f'12.345.678-5 {n} 1.344.115 143.551 0 0 0 0 1.344.115 8.065 32.259 7 10/08/2026','afp')
    b=_parsear_fila(f'41 12.345.678-5 {n} 30 AFP 1.344.115 37.635 8 10 08 2026','salud')
    c=_parsear_fila(f'42 12.345.678-5 {n} 1.344.115 0 9.678 12.097 23.925 JC 30 8 10/08/2026','seguro_social')
    assert (a['renta'],b['renta'],c['renta'])==(1344115,1344115,1344115)
    assert b['dias']==c['dias']==30

def test_isapre_es_salud_no_cotizacion_uf():
    cab='DETALLE DE PAGO DE COTIZACIONES PREVISIONALES A ISAPRE\nISAPRE Colmena'
    assert _tipo_pagina(cab)=='isapre'
    row=_parsear_fila('12.345.678-5 PEREZ GONZALEZ PERSONA 1.671.115 116.978 0 16.270 0 133.248 3,260 UF 0','isapre')
    assert row and row['renta']==1671115 and row['dias'] is None

def test_caja_de_compensacion_no_es_mutual():
    from previred_pdf import _mutual_name
    assert not _mutual_name('Caja de Compensación Los Andes, adherido a Mutual SI')

def test_asistencia_permite_diferencias_y_detecta_ausentes():
    csv=b'rut_trabajador,dias_asistencia\n12.345.678-5,28\n17.654.321-3,30\n'
    asistencia=leer_asistencia(csv,'asistencia.csv')
    matriz=pd.DataFrame([
        {'periodo_remuneracion':'2026-08','trabajador_rut':'123456785','trabajador_nombre':'A','dias_reales_pagados':30},
        {'periodo_remuneracion':'2026-08','trabajador_rut':'176543213','trabajador_nombre':'B','dias_reales_pagados':30},
    ])
    c=comparar_asistencia(matriz,asistencia)
    assert list(c.resultado_comparacion)==['DIFERENCIA_A_VERIFICAR','COINCIDE']

def test_correcciones_no_se_proyectan_a_otro_mes_y_json_es_portable():
    sha=hashlib.sha256(b'PDF-DEMO').hexdigest()
    memoria=nueva_memoria()
    registrar_correccion(memoria,sha,RUT,'2026-08','renta_imponible_salud',0,123456,True)
    copia=cargar_memoria(guardar_memoria(memoria))
    df=pd.DataFrame([{'trabajador_rut':RUT,'periodo_remuneracion':'2026-08','renta_imponible_salud':0}])
    corregido,eventos=aplicar_correcciones(df,copia,sha)
    assert corregido.renta_imponible_salud.iloc[0]==123456 and eventos
    sin_cambio,_=aplicar_correcciones(df,copia,hashlib.sha256(b'OTRO-PDF').hexdigest())
    assert sin_cambio.renta_imponible_salud.iloc[0]==0
    otro_mes=df.copy();otro_mes['periodo_remuneracion']='2026-09'
    sin_mes,_=aplicar_correcciones(otro_mes,copia,sha)
    assert sin_mes.renta_imponible_salud.iloc[0]==0

def test_correccion_requiere_confirmacion():
    with pytest.raises(ValueError,match='confirmada'):
        registrar_correccion(nueva_memoria(),hashlib.sha256(b'A').hexdigest(),RUT,'2026-08','dias_reales_pagados',0,30,False)

def test_clasificador_no_inventa_con_pocos_ejemplos():
    m=nueva_memoria()
    registrar_plantilla(m,'DETALLE DE PAGO DE COTIZACIONES PREVISIONALES FONDO DE PENSIONES','afp',True)
    modelo=clasificador_aprendido(m)
    assert modelo is not None
    assert modelo('DETALLE DE PAGO DE COTIZACIONES PREVISIONALES FONDO DE PENSIONES')=='afp'
    assert modelo('UNA TABLA DE MONTOS DESCONOCIDA')=='' 

def test_rut_ejemplo_valido():
    assert rut_chileno_valido(RUT)
