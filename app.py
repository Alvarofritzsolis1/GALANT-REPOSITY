"""Streamlit V13 — interfaz simple para F30-1 y cotizaciones.

Tres operaciones visibles: adjuntar, ejecutar y revisar diferencias.
Las opciones técnicas son secundarias; las cotizaciones y los F30-1
se clasifican por CONTENIDO y se verifican por empresa/RUT/período.
"""
from __future__ import annotations
import hashlib
import re
from io import BytesIO
import pandas as pd
import streamlit as st
from auditoria_laboral import ejecutar_pipeline, plantilla_topes_csv, COLUMNAS_SALIDA
from clasificador_documentos import enrutar_archivos
from f301 import analizar_f301, conciliar_f301, exportar_f301_excel

st.set_page_config(page_title='Control de contratistas', page_icon='📋', layout='wide')
VERSION = '13.0.0'
st.markdown('''
<style>
.block-container{max-width:1300px;padding-top:1.35rem;padding-bottom:3rem}
h1{font-size:2.1rem!important;letter-spacing:-.045em}
h2,h3{letter-spacing:-.035em}
[data-testid="stMetric"]{border:1px solid rgba(128,145,167,.30);border-radius:13px;padding:15px 18px}
[data-testid="stFileUploader"]{border-radius:12px}
[data-testid="stDataFrame"]{border-radius:12px;overflow:hidden}
.stButton>button[kind="primary"]{border-radius:10px;font-weight:700;min-height:46px}
[data-testid="stExpander"]{border-radius:12px}
.pill-note{font-size:.88rem;opacity:.78;padding-top:.2rem}
</style>
''',unsafe_allow_html=True)

st.title('Control documental de contratistas')
st.caption('Sube las cotizaciones y los certificados F30-1 por separado. El sistema reconoce trabajadores, períodos y diferencias.')

col_prev,col_f30=st.columns(2,gap='large')
with col_prev:
    st.markdown('#### 1. Cotizaciones previsionales')
    archivos_prev=st.file_uploader('Previred, AFP, Fonasa, Isapre y Mutual',type=['pdf','xls','xlsx','csv'],
                                   accept_multiple_files=True,key='cotizaciones',max_upload_size=25)
with col_f30:
    st.markdown('#### 2. Certificados F30-1')
    archivos_f30=st.file_uploader('Certificados de la Dirección del Trabajo',type=['pdf'],
                                  accept_multiple_files=True,key='f301',max_upload_size=25)

with st.expander('Opciones adicionales (normalmente no necesitas cambiarlas)',expanded=False):
    periodo=st.text_input('Período esperado (AAAA-MM, opcional)',placeholder='Ej. 2026-09').strip()
    modo=st.selectbox('Modo de lectura',['Automático','Rápido','OCR reforzado'],index=0)
    solo_obra=st.checkbox('Las cotizaciones corresponden exclusivamente a la obra del F30-1',value=False)
    asistir=st.file_uploader('Asistencia de obra (opcional)',type=['xlsx','xls','csv'],key='asistencia',max_upload_size=15)
    topes=st.file_uploader('CSV de topes imponibles (opcional)',type=['csv'],key='topes',max_upload_size=2)
    st.download_button('Plantilla de topes imponibles',plantilla_topes_csv(),file_name='topes_imponibles.csv',mime='text/csv')

archivos_cot,archivos_dt,avisos,rechazados=enrutar_archivos(
    [(f.getvalue(),f.name) for f in archivos_prev or []],
    [(f.getvalue(),f.name) for f in archivos_f30 or []],
)
if avisos:
    with st.expander(f'{len(avisos)} aviso(s) de clasificación automática'):
        for aviso in avisos:st.info(aviso)
for fn,msg in rechazados:st.error(f'{fn}: {msg}')

limite=(len(archivos_cot)+len(archivos_dt)>25 or
        sum(len(d) for d,_ in archivos_cot+archivos_dt)>80*1024*1024)
if limite:st.error('Se admiten hasta 25 documentos y 80 MB por análisis.')

def firma():
    h=hashlib.sha256()
    for d,n in archivos_cot+archivos_dt:h.update(n.encode());h.update(d)
    h.update(str((periodo,modo,solo_obra)).encode())
    if asistir:h.update(asistir.getvalue())
    if topes:h.update(topes.getvalue())
    return h.hexdigest()

if st.button('Analizar documentos',type='primary',disabled=(not archivos_cot and not archivos_dt) or limite,use_container_width=True):
    if periodo and not re.fullmatch(r'20\d{2}-(?:0[1-9]|1[0-2])',periodo):
        st.error('El período esperado debe tener formato AAAA-MM (ejemplo: 2026-09).')
    else:
        progress=st.progress(0,text='Analizando documentos...')
        try:
            if archivos_cot:
                matriz,excel,detalle,log=ejecutar_pipeline(
                    archivos_cot,topes_csv=topes.getvalue() if topes else None,
                    tolerancia_clp=0,incluir_mutual=True,periodo_esperado=periodo,
                    modo_ocr={'Automático':'automatico','Rápido':'rapido','OCR reforzado':'reforzado'}[modo],
                    asistencia=(asistir.getvalue(),asistir.name) if asistir else None,
                    progreso=lambda i,n,nombre:progress.progress(min(.8,i/n*.8),text=f'Cotizaciones: {i}/{n}'),
                )
            else:
                matriz=pd.DataFrame(columns=COLUMNAS_SALIDA)
                excel=None;detalle=pd.DataFrame();log=pd.DataFrame()
            progress.progress(.85,text='Leyendo certificados F30-1...')
            if archivos_dt:
                certs,nomina,incidencias=analizar_f301(archivos_dt)
                hallazgos,resumen=conciliar_f301(certs,nomina,matriz,solo_obra)
            else:
                certs=pd.DataFrame();nomina=pd.DataFrame();incidencias=pd.DataFrame()
                hallazgos=pd.DataFrame();resumen=pd.DataFrame()
            st.session_state['resultado_v13']={
                'firma':firma(),'matriz':matriz,'excel':excel,'detalle':detalle,'log':log,
                'certs':certs,'nomina':nomina,'incidencias':incidencias,
                'hallazgos':hallazgos,'resumen':resumen,
            }
            progress.progress(1.,text='Listo')
        except Exception as exc:
            st.error(f'No se pudo completar el análisis: {type(exc).__name__}: {exc}')
        finally:
            progress.empty()

resultado=st.session_state.get('resultado_v13')
if resultado and resultado['firma']!=firma():
    st.info('Los archivos o las opciones cambiaron. Pulsa «Analizar documentos» para actualizar los resultados.')
elif resultado:
    matriz=resultado['matriz'];certs=resultado['certs'];nomina=resultado['nomina']
    st.divider()
    st.subheader('Resultado del análisis')
    r1,r2,r3,r4=st.columns(4)
    r1.metric('Trabajadores Previred',len(matriz))
    r2.metric('F30-1 identificados',len(certs))
    r3.metric('RUT F30-1 leídos',len(nomina))
    r4.metric('Trabajadores con observaciones',int(matriz['estado_auditoria'].ne('CONFORME').sum()) if 'estado_auditoria' in matriz.columns else 0)

    if not certs.empty:
        st.markdown('### Certificados F30-1')
        for _,c in certs.iterrows():
            with st.container(border=True):
                st.markdown(f"**{c.get('contratista_nombre') or 'Contratista sin identificar'}** · {c.get('periodo_remuneracion') or 'Período pendiente'}")
                x,y,z=st.columns(3)
                x.metric('Trabajadores informados',int(c['trabajadores_vigentes']) if pd.notna(c.get('trabajadores_vigentes')) else 'No leído')
                y.metric('RUT en nómina',int(c.get('trabajadores_en_nomina',0)))
                z.metric('Cotizaciones en el PDF','Pagadas: Sí' if c['cotizaciones_previsionales']=='SI_DECLARADO' else 'Revisar')
                if c.get('mes_archivo_distinto_periodo',False):
                    st.caption('El nombre del archivo indica otro mes; se utiliza el período escrito dentro del certificado.')
                if c.get('control_documental')=='REVISAR_EXTRACCION':
                    st.warning('La lectura quedó incompleta: '+str(c.get('observaciones') or 'revisar páginas del PDF'))
                elif int(c.get('trabajadores_en_nomina',0))>0:
                    st.success('Nómina identificada y cantidad cotejada con el certificado.')
                sub=nomina[nomina['certificado']==c['certificado']] if not nomina.empty else pd.DataFrame()
                if len(sub):
                    tabla=sub[['trabajador_nombre','trabajador_rut']].rename(columns={'trabajador_nombre':'Trabajador','trabajador_rut':'RUT (normalizado)'})
                    st.dataframe(tabla,use_container_width=True,hide_index=True)
                st.caption(f"Certificado: {c['certificado']} · Obra: {c.get('obra_faena') or 'No identificada'}")
                with st.expander('Comprobar autenticidad en Dirección del Trabajo'):
                    st.warning('Autenticidad pendiente: «Pagadas: Sí» proviene del PDF, no de una consulta al portal oficial.')
                    st.code(f"N.º: {c['certificado']}\nClave: {c.get('clave') or 'No reconocida'}")
                    st.link_button('Abrir verificador oficial DT','https://verificador.dirtrab.cl/')

    resumen=resultado['resumen']
    if not resumen.empty:
        st.markdown('### Cruce F30-1 ↔ Previred')
        for _,r in resumen.iterrows():
            state=r['resultado']
            caption=r['detalle']
            if state=='RUT_CONCILIADOS':st.success(f"{r['certificado']}: coinciden todos los RUT. {caption}")
            elif state in ('SIN_PREVIRED','SIN_PREVIRED_COINCIDENTE'):
                st.info(f"{r['certificado']}: falta cargar cotizaciones comparables del mismo empleador y período.")
            else:st.warning(f"{r['certificado']}: {caption}")
        if not resultado['hallazgos'].empty:
            st.dataframe(resultado['hallazgos'][['trabajador_nombre','trabajador_rut','tipo_hallazgo','detalle']]
                         .rename(columns={'trabajador_nombre':'Trabajador','trabajador_rut':'RUT','tipo_hallazgo':'Hallazgo','detalle':'Qué revisar'}),
                         use_container_width=True,hide_index=True)

    if not matriz.empty:
        st.markdown('### Cotizaciones previsionales')
        keys=['trabajador_nombre','trabajador_rut','periodo_remuneracion','dias_reales_pagados',
              'renta_imponible_salud','renta_imponible_afp','estado_auditoria']
        tabla=matriz[[k for k in keys if k in matriz.columns]].copy()
        tabla=tabla.rename(columns={'trabajador_nombre':'Trabajador','trabajador_rut':'RUT',
          'periodo_remuneracion':'Período','dias_reales_pagados':'Días','renta_imponible_salud':'Salud $',
          'renta_imponible_afp':'AFP $','estado_auditoria':'Estado'})
        st.dataframe(tabla,use_container_width=True,hide_index=True)
        if 'estado_auditoria' in matriz and matriz['estado_auditoria'].eq('CONFORME').all():
            st.success('Las bases de AFP, Salud y Mutual coinciden en los trabajadores identificados. Se consideraron todas las planillas y folios del período.')
        control_ss=matriz.attrs.get('control_seguro_social')
        if isinstance(control_ss,pd.DataFrame) and not control_ss.empty:
            faltan=int(control_ss['cobertura_seguro_social'].eq('SIN_REGISTRO_SS').sum())
            if faltan:
                st.info(f'Seguro Social: {faltan} trabajador(es) no aparece(n) en esa nómina. Verifica si corresponde cotizar o existe una excepción; esto no altera la cuadratura AFP, Salud y Mutual.')
        if 'comprobantes_con_pago_posterior' in resultado['log'].columns:
            cantidad=int(pd.to_numeric(resultado['log']['comprobantes_con_pago_posterior'],errors='coerce').fillna(0).sum())
            if cantidad:
                st.caption('Se identificaron declaraciones inicialmente sin pago, pero con fecha de pago posterior registrada en el mismo PDF. La fecha debe cotejarse con el comprobante oficial.')
        if 'estado_auditoria' in matriz and matriz['estado_auditoria'].ne('CONFORME').any():
            st.caption('«Revisar» no significa necesariamente cotización impaga. Se explica la diferencia por institución y folio en el detalle técnico.')

    a,b=st.columns(2)
    if resultado.get('excel'):
        a.download_button('Descargar auditoría Previred (Excel)',resultado['excel'],
            file_name='Auditoria_Cotizaciones.xlsx',mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',use_container_width=True)
    if not certs.empty:
        data=exportar_f301_excel(certs,nomina,resultado['hallazgos'],resumen,resultado['incidencias'])
        b.download_button('Descargar auditoría F30-1 (Excel)',data,
            file_name='Auditoria_F301.xlsx',mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',use_container_width=True)

    with st.expander('Ver detalles técnicos y observaciones originales',expanded=False):
        if not resultado['incidencias'].empty:st.dataframe(resultado['incidencias'],hide_index=True,use_container_width=True)
        if not resultado['detalle'].empty:st.dataframe(resultado['detalle'],hide_index=True,use_container_width=True)
        if not matriz.empty and isinstance(matriz.attrs.get('control_seguro_social'),pd.DataFrame):
            st.markdown('**Seguro Social — control independiente**')
            st.dataframe(matriz.attrs['control_seguro_social'],hide_index=True,use_container_width=True)
        if not resultado['log'].empty:st.dataframe(resultado['log'],hide_index=True,use_container_width=True)
        st.caption('Los datos provienen de los PDF adjuntos. La autenticidad de F30-1 solo se confirma en la DT.')

st.caption(f'Versión {VERSION} · El sistema no envía documentos a terceros ni almacena resultados en un servidor externo por su cuenta.')
