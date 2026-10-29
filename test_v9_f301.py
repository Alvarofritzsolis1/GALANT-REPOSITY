"""Pruebas sin PDF reales ni datos personales de contratistas."""
import io
import fitz
import pandas as pd
from f301 import (rut_normalizado, extraer_nomina, conciliar_f301,
                  folio_en_pagina, pagina_es_nomina, cantidad_vigentes,
                  pagadas_desde_resumen, analizar_f301)

# Aseguran que separaciones de páginas y fuentes NO creen conformes ficticios.

def r(n):
    total=sum(int(x)*(2+i%6) for i,x in enumerate(reversed(str(n))))
    z=11-total%11
    return str(n)+('0' if z==11 else 'K' if z==10 else str(z))

R1=r('12345678');R2=r('87654321');E=r('76123456')

def test_rut_tolerante_no_falso():
    assert rut_normalizado('12.345.678-'+R1[-1])==R1
    assert rut_normalizado('12.345.678-X')==''

def test_folio():
    assert folio_en_pagina('N° Certificado: 2000/2026/12345678 Clave: ABC123')=='2000/2026/12345678'

def test_nomina_unica_sin_rut_de_representante():
    s=f'''Trabajadores declarados en la Certificación
    Detalle por Periodo
    Julio 2026
    N° RUT Nombre Trabajador
    1 {R1} Persona de Prueba Uno
    2 {R2} Persona de Prueba Dos
    Total Trabajadores 2'''
    a=extraer_nomina(s,'2026-07','2000/2026/11111111','demo.pdf',3)
    assert len(a)==2 and {x['trabajador_rut'] for x in a}=={R1,R2}

def test_no_inventar_pago():
    assert pagadas_desde_resumen('Cotizaciones Previsionales: No informadas')=='NO_LEIBLE'

def certs(with_roster=True):
    return pd.DataFrame([dict(certificado='2000/2026/11111111',contratista_nombre='EMPRESA DE PRUEBA',contratista_rut=E,
        periodo_remuneracion='2026-07',obra_faena='OBRA UNO',trabajadores_vigentes=2,
        verificacion_dt='PENDIENTE_PORTAL_DT')])

def cot(*rs):
    return pd.DataFrame([{'contratista_rut':E,'periodo_remuneracion':'2026-07',
                         'trabajador_rut':x,'trabajador_nombre':'Persona de prueba'} for x in rs])

def nom(*rs):
    return pd.DataFrame([{'certificado':'2000/2026/11111111','trabajador_rut':x,'trabajador_nombre':'Persona de prueba'} for x in rs])

def test_cuadre_exacto():
    dif,res=conciliar_f301(certs(),nom(R1,R2),cot(R1,R2))
    assert dif.empty and res.iloc[0]['resultado']=='RUT_CONCILIADOS'

def test_falta_en_previred_alerta():
    dif,res=conciliar_f301(certs(),nom(R1,R2),cot(R1))
    assert len(dif)==1 and dif.iloc[0]['trabajador_rut']==R2
    assert res.iloc[0]['resultado']=='ALERTA_F301_SIN_COTIZACION_RUT'

def test_extra_cotizaciones_otra_obra_no_es_incumplimiento():
    dif,res=conciliar_f301(certs(),nom(R1),cot(R1,R2))
    assert res.iloc[0]['resultado']=='REVISAR_ALCANCE_OBRA'
    assert dif.iloc[0]['tipo_hallazgo']=='REVISAR_OTRA_OBRA'
    dif,res=conciliar_f301(certs(),nom(R1),cot(R1,R2),True)
    assert res.iloc[0]['resultado']=='ALERTA_PREVIRED_FUERA_F301'

def test_sin_nomina_no_inventa_ruts():
    dif,res=conciliar_f301(certs(),pd.DataFrame(),cot(R1,R2))
    assert dif.empty and res.iloc[0]['resultado']=='SIN_NOMINA_F301'

def test_otra_empresa_otro_periodo_no_cruza():
    dif,res=conciliar_f301(certs(),nom(R1),pd.DataFrame([{
        'contratista_rut':E,'periodo_remuneracion':'2026-08','trabajador_rut':R1}]))
    assert res.iloc[0]['resultado']=='SIN_PREVIRED_COINCIDENTE'

def test_fuente_parcial_sin_resumen():
    d=fitz.open();page=d.new_page()
    page.insert_text((50,50),'N° Certificado: 2000/2026/11111111',fontsize=11)
    page.insert_text((50,75),'Trabajadores declarados en la Certificacion',fontsize=11)
    page.insert_text((50,100),'Detalle por Periodo',fontsize=11)
    page.insert_text((50,120),'Julio 2026',fontsize=11)
    page.insert_text((50,155),f'1 {R1} Persona de Prueba Uno',fontsize=11)
    doc=d.tobytes();d.close()
    cert,nom,err=analizar_f301([(doc,'prueba.pdf')])
    assert len(cert)==1 and len(nom)==1
    assert cert.iloc[0]['control_documental']=='REVISAR_EXTRACCION'
    assert cert.iloc[0]['verificacion_dt']=='PENDIENTE_PORTAL_DT'
