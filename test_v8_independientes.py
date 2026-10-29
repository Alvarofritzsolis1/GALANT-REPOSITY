"""Pruebas sin datos personales reales para el control de independientes V8."""
import io
import pandas as pd
import fitz
from control_independientes import (
    _tipo_independiente,_rut_ok,extraer_evidencia,crear_filas_personales,
    construir_control,_perfil_empleador
)

def rut(numero):
    total=sum(int(c)*(2+i%6) for i,c in enumerate(reversed(str(numero))))
    d=11-total%11
    return str(numero)+('0' if d==11 else 'K' if d==10 else str(d))

PERSONA='12123456'; EMPRESA='76123456'
P_RUT=rut(PERSONA); E_RUT=rut(EMPRESA)

def pdf(*paginas):
    doc=fitz.open()
    for t in paginas:
        page=doc.new_page(width=842,height=1300)
        page.insert_text((40,45),t,fontsize=10,fontname='helv',lineheight=1.25)
    b=doc.tobytes();doc.close()
    return b


def pagina_isl():
    return f'''COMPROBANTE DE PAGO DE COTIZACIONES PREVISIONALES
Instituto de Seguridad Laboral (ISL)
Identificación del Trabajador Independiente
Nombre
Email
RUT
Teléfono
Ana Demo Prueba
ana@example.org
{P_RUT}
999
TOTAL RENTA IMPONIBLE
553.553
TOTAL A PAGAR A LA MUTUAL
5.148
Remuneraciones
Período
Septiembre 2026
DÍAS TRABAJADOS
30
Pago Electrónico
08/10/2026'''

def pagina_afp():
    return f'''COMPROBANTE DE PAGO DE COTIZACIONES PREVISIONALES
AFP Demo
Identificación del Empleador: Empleador de Trabajador Independiente
Nombre o Razón Social
RUT
Cód. Actividad Económica
Ana Demo Prueba
{P_RUT}
123
Período de Remuneraciones: 09/2026
RESUMEN COTIZACIONES
FONDO DE PENSIONES
Renta Imponible
553.553
Cotización Obligatoria
63.382
Pago Electrónico'''

def pagina_salud():
    return f'''PLANILLA DE DECLARACION Y PAGO SIMULTANEO DE OBLIGACIONES PREVISIONALES
TIPO DE EMPLEADOR
Empleador General
Trabajador Independiente
X
SECCIÓN A: Identificación del Ente Pagador
Razón Social o Nombre: Ana Demo Prueba
R.U.T: {P_RUT}
TOTAL REMUNERACIÓN IMPONIBLE DECLARADA EN $
553.553
PERIODO DE REMUNERACIONES O GRATIFICACIÓN LEGAL
REMUNERACIÓN
09
2026
Pago Electrónico'''

def pagina_empresa():
    return f'''FORMULARIO DE COTIZACIONES PREVISIONALES - SEGURO SOCIAL PREVISIONAL
Identificación del Empleador: Empresa
Nombre o Razón Social
RUT
EMPRESA DE PRUEBA E.I.R.L.
{E_RUT}
Nombre Representante Legal
RUT Representante Legal
Ana Demo Prueba
{P_RUT}
Renta imponible de trabajadores
138.389
Período de Remuneraciones: 09/2026'''

def test_el_formulario_sin_x_no_se_considera_independiente():
    assert _tipo_independiente(pagina_empresa())==''
    assert _tipo_independiente(pagina_salud())=='salud_ind'
    assert _tipo_independiente(pagina_afp())=='afp_ind'
    assert _tipo_independiente(pagina_isl())=='mutual_ind'


def test_no_mezclar_empresa_y_mutual_independiente():
    documentos=[(pdf(pagina_isl()),'mutual.pdf'),(pdf(pagina_empresa()),'empresa.pdf')]
    evid,perfiles,solo=extraer_evidencia(documentos)
    assert len(evid)==1 and 'mutual.pdf' in solo
    assert perfiles[0]['representante_rut']==P_RUT
    matriz=pd.DataFrame([{'trabajador_rut':P_RUT,'contratista_rut':E_RUT,'periodo_remuneracion':'2026-09',
                           'trabajador_nombre':'ANA DEMO PRUEBA','renta_imponible_afp':138389,
                           'renta_imponible_salud':138389,'renta_imponible_mutual':138389,
                           'dias_reales_pagados':6}])
    control=construir_control(documentos,matriz,(evid,perfiles,solo))
    r=control.iloc[0]
    assert r['independiente_mutual_dias']==30 and r['empresa_dias_declarados']==6
    assert r['independiente_mutual_base']==553553 and r['empresa_afp_base']==138389
    assert r['estado_control_independiente']=='MUTUAL_INDEPENDIENTE_CON_PAGO_EN_EMPRESA'
    assert pd.isna(r['independiente_afp_base'])


def test_tres_bases_personales_iguales_son_conciliadas():
    docs=[(pdf(pagina_salud(),pagina_afp(),pagina_isl()),'pago_completo.pdf')]
    ev,pr,solo=extraer_evidencia(docs)
    assert len(ev)==3 and len(solo)==1
    fila=crear_filas_personales(ev,solo)['pago_completo.pdf'].iloc[0]
    assert all(fila[c]==553553 for c in ('renta_imponible_salud','renta_imponible_afp','renta_imponible_mutual'))
    control=construir_control(docs,datos_preextraidos=(ev,pr,solo))
    assert control.iloc[0]['estado_control_independiente']=='BASES_INDEPENDIENTES_CONCILIADAS'
    assert control.iloc[0]['requiere_revision']==False


def test_isl_solo_no_es_error_lectura_y_no_demuestra_afp_salud():
    docs=[(pdf(pagina_isl()),'solo_isl.pdf')]
    ev,pr,solo=extraer_evidencia(docs)
    assert len(ev)==1 and 'solo_isl.pdf' in solo
    assert not crear_filas_personales(ev,solo)
    c=construir_control(docs,datos_preextraidos=(ev,pr,solo)).iloc[0]
    assert c['estado_control_independiente']=='MUTUAL_INDEPENDIENTE_ACREDITADA'
    assert c['requiere_revision']==True
