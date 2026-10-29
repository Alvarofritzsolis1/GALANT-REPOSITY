"""Streamlit — Auditoría laboral V11: OCR conciliado, F30-1 y motivos explicativos.

Uso de IA: clasificador opcional de cabeceras, nunca cifras ni dictamen normativo.
"""
from __future__ import annotations
import hashlib
import re
import time
import streamlit as st
from auditoria_laboral import COLUMNAS_SALIDA, ejecutar_pipeline, plantilla_topes_csv
from clasificador_documentos import enrutar_archivos
from aprendizaje_revisiones import (
    cargar_memoria, guardar_memoria, nueva_memoria, registrar_correccion,
    registrar_plantilla, registrar_verificacion_ocr, sha256_pdf, TIPOS_PAGINA,
)

st.set_page_config(page_title="Auditoría Laboral | Control Previred",page_icon="🛡️",layout="wide")
APP_VERSION = "12.0.0"
# Estilos fijos de la propia aplicación; nunca se inserta contenido subido como HTML.
st.markdown("""
<style>
.block-container{padding-top:1.55rem;padding-bottom:2.2rem;max-width:1560px}
h1,h2,h3 {letter-spacing:-.02em}
[data-testid="stMetric"]{background:#131f2e;border:1px solid #2b394b;border-radius:17px;padding:19px 22px;min-height:110px}
[data-testid="stMetricLabel"]{font-size:0.83rem;letter-spacing:.01em;color:#a2afc1}
[data-testid="stMetricValue"]{font-size:2rem;font-weight:760;letter-spacing:-.03em}
[data-testid="stFileUploader"]{border-radius:16px}
[data-testid="stAlert"]{border-radius:13px}
.stTabs [data-baseweb="tab-list"]{gap:9px;background:#111b29;border:1px solid #26374b;border-radius:13px;padding:6px}
.stTabs [data-baseweb="tab"]{border-radius:9px;padding:9px 15px}
.stTabs [aria-selected="true"]{background:#26344b;color:#fff}
.stButton>button[kind="primary"],.stDownloadButton>button[kind="primary"]{border-radius:11px;font-weight:700;min-height:43px}
[data-testid="stExpander"]{border:1px solid #293748;border-radius:12px}
.hero-audit{background:linear-gradient(116deg,#19263a 0%,#111a2a 68%,#291b2a 100%);border:1px solid #334258;border-radius:21px;padding:25px 30px;margin-bottom:17px}
.hero-tag{color:#f99498;font-size:.72rem;font-weight:800;letter-spacing:.16em;margin-bottom:6px}
.hero-title{font-size:2.08rem;font-weight:790;color:#f4f6fc;letter-spacing:-.04em;line-height:1.1}
.hero-sub{color:#b6c2d5;font-size:.94rem;margin-top:9px;max-width:820px}
.hero-version{display:inline-block;background:#3a2635;color:#ffb0b3;font-size:.74rem;padding:5px 10px;border-radius:30px;margin-top:13px}
@media(max-width:700px){.hero-audit{padding:18px}.hero-title{font-size:1.56rem}.block-container{padding-top:.8rem}}
</style>
<div class="hero-audit">
  <div class="hero-tag">CONTROL DOCUMENTAL / PREVIRED + DIRECCIÓN DEL TRABAJO</div>
  <div class="hero-title">Auditoría de contratistas</div>
  <div class="hero-sub">Dos módulos independientes: comprobantes Previred y certificados F30-1 de la DT. Conciliación por RUT y período, con control de alcance de obra y verificación oficial asistida.</div>
  <div class="hero-version">Versión 12.0.0 · Herramientas avanzadas</div>
</div>
""",unsafe_allow_html=True)

with st.expander("Alcance, privacidad y confiabilidad",expanded=False):
    st.info(
        "El análisis separa el período de remuneraciones de la fecha de pago, identifica RUT, "
        "cruza distintas AFP y salud (FONASA + Isapres) y audita la integridad de las fuentes. "
        "CCAF Los Andes no se considera Mutual de accidentes. Si falta Mutual/ISL se reporta como pendiente, "
        "sin suponer incumplimiento. La comparación con asistencia es orientativa; licencias y otros movimientos "
        "pueden justificar diferencias."
    )
    st.warning("Contiene datos personales: publica la aplicación solo para usuarios autorizados. No incluyas PDF reales en GitHub. "
               "La memoria de aprendizaje solo se conserva durante esta sesión, salvo que la descargues y la vuelvas a subir; "
               "el hosting gratuito no garantiza almacenamiento local permanente.")

st.subheader("01 / Cargar documentos por tipo")
tab_cotizaciones,tab_f301=st.tabs(["Cotizaciones previsionales (Previred)","Certificados F30-1 (Dirección del Trabajo)"])
with tab_cotizaciones:
    archivos = st.file_uploader("Planillas de cotizaciones PDF, Excel o CSV",type=["pdf","xlsx","xls","csv"],
        accept_multiple_files=True,max_upload_size=25,key="cotizaciones_pdf",
        help="El sistema distingue automáticamente F30-1 de AFP, Fonasa, Mutual y Seguro Social, aunque el archivo se suba aquí.")
with tab_f301:
    archivos_f301 = st.file_uploader("Certificados de cumplimiento F30-1 (PDF)",type=["pdf"],
        accept_multiple_files=True,max_upload_size=25,key="certificados_f301",
        help="Sube los certificados de la Dirección del Trabajo. Un PDF puede contener varios certificados con folios diferentes.")
    st.caption("Se extraen RUT de contratista, período, obra, pago declarado, nómina si está adjunta y folio/claves de verificación.")
    st.link_button("Verificador oficial de certificados DT","https://verificador.dirtrab.cl/")
# Clasificar por CONTENIDO, no por el nombre ni la pestaña donde se cargó.
# Un F30-1 puede mencionar Previred; no debe entrar al extractor de AFP.
archivos_previred_ruta, archivos_f301_ruta, avisos_ruta, rechazados_ruta = enrutar_archivos(
    [(f.getvalue(),f.name) for f in archivos or []],
    [(f.getvalue(),f.name) for f in archivos_f301 or []],
)
if avisos_ruta:
    with st.expander(f"Clasificación automática: {len(avisos_ruta)} observaciones",expanded=True):
        for mensaje in avisos_ruta:st.info(mensaje)
if rechazados_ruta:
    for nombre,motivo in rechazados_ruta:
        st.error(f"{nombre}: {motivo}. Este formato no se procesó como cotizaciones.")
if archivos or archivos_f301:
    col_a,col_b = st.columns(2)
    col_a.metric('Comprobantes previsionales identificados',len(archivos_previred_ruta))
    col_b.metric('PDF F30-1 identificados',len(archivos_f301_ruta))
st.caption('Si subes un F30-1 en Cotizaciones, se dirigirá automáticamente al lector de certificados. '           'El F30 de antecedentes laborales es otro documento y no se confunde con F30-1.')

coincidencia_obra_exclusiva=st.checkbox("Las cotizaciones corresponden exclusivamente a la misma obra certificada",
    value=False,help="Por defecto las nóminas Previred pueden incluir varias obras. Si marcas esta opción, un trabajador extra de Previred frente al F30-1 se tratará como alerta de cobertura.")
subidos=list(archivos or [])+list(archivos_f301 or [])
limite=len(subidos)>25 or sum(x.size for x in subidos)>80*1024*1024
if limite:st.error("Máximo: 25 archivos y 80 MB en total, entre ambas pestañas.")

if archivos:
    with st.expander("Diagnóstico previo de archivos: tipo PDF y trabajo estimado", expanded=False):
        import fitz
        diagnostico=[]
        for original in archivos:
            extension=original.name.rsplit('.',1)[-1].lower()
            if extension!='pdf':
                diagnostico.append({'archivo':original.name,'páginas':'—',
                                    'tipo':'Excel/CSV','estrategia':'Lectura estructurada'})
                continue
            try:
                with fitz.open(stream=original.getvalue(),filetype='pdf') as pdf:
                    total=pdf.page_count
                    muestras=sorted(set([0,total//2,max(0,total-1)]))
                    cont=sum(len(pdf[j].get_text('text').strip())>=70 for j in muestras)
                modo='Digital' if cont==len(muestras) else 'Escaneado' if cont==0 else 'Mixto'
                diagnostico.append({'archivo':original.name,'páginas':total,'tipo':modo,
                    'estrategia':'Texto directo' if modo=='Digital' else 'OCR con validación de celdas'})
            except Exception:
                diagnostico.append({'archivo':original.name,'páginas':'—','tipo':'PDF no legible',
                                    'estrategia':'Revisar archivo'})
        st.dataframe(diagnostico,use_container_width=True,hide_index=True)
        st.caption('Diagnóstico preliminar por muestreo de páginas, no acreditación de pago. '
                   'Los certificados individuales pueden demostrar bases declaradas, pero no días trabajados. '
                   'Un OCR de baja confianza nunca debe aprobarse automáticamente.')

st.subheader("02 / Reglas de verificación")
col1,col2=st.columns(2)
with col1:
    periodo_modo=st.radio("Período de remuneración",["Detectar desde los documentos","Verificar contra un período esperado"],index=0)
    periodo_esperado=""
    if periodo_modo.startswith("Verificar"):
        periodo_esperado=st.text_input("Período esperado (AAAA-MM)",placeholder="2026-08",max_chars=7).strip()
with col2:
    modo_label=st.selectbox("Procesamiento",["Automático — recomendado","Rápido — priorizar velocidad","Reforzado — OCR alternativo completo (más lento)"],index=0,
        help="Automático intenta lectura digital, luego OCR geométrico por celdas y, si falla, OCR alternativo. Rápido termina si la lectura geométrica falla. Reforzado prueba directamente OCR general para escaneos: más lento y no necesariamente más preciso.")
    modo_ocr="rapido" if modo_label.startswith("Rápido") else "reforzado" if modo_label.startswith("Reforzado") else "automatico"

with st.expander("Comparar opcionalmente con asistencia de obra"):
    st.caption("Carga un Excel/CSV con columnas rut_trabajador y dias_asistencia, una fila por trabajador. "
               "Una diferencia se informa para revisión, no prueba automáticamente un incumplimiento.")
    asistencia_file=st.file_uploader("Control de asistencia mensual",type=["xlsx","xls","csv"],key="asistencia",max_upload_size=15)
    st.download_button("Descargar plantilla de asistencia",b"rut_trabajador,dias_asistencia\n",file_name="asistencia_plantilla.csv",mime="text/csv")

with st.expander("Topes imponibles legales (solo si los conoces)"):
    st.caption("Opcional. No se usan montos o tasas legales fijos en el código. El límite debe estar verificado para cada período y régimen.")
    st.download_button("Descargar plantilla de topes",plantilla_topes_csv(),file_name="topes_imponibles.csv",mime="text/csv")
    topes=st.file_uploader("CSV de topes, opcional",type=["csv"],key="topes",max_upload_size=2)

if 'memoria_validada' not in st.session_state:
    st.session_state['memoria_validada']=nueva_memoria()
with st.expander("03 / Memoria de revisión y aprendizaje",expanded=False):
    st.write("Se pueden guardar correcciones de un documento EXACTO y ejemplos de tipos de página. "
             "scikit-learn aprende **tipos de página**, nunca montos ni dictámenes laborales. "
             "Para encabezados ya enseñados puede actuar desde el primer ejemplo; "
             "para generalizar necesita suficientes ejemplos confirmados y alta confianza.")
    archivo_memoria=st.file_uploader("Importar memoria JSON de revisiones anteriores (opcional)",type=["json"],key="memoria",max_upload_size=2)
    if archivo_memoria:
        huella=hashlib.sha256(archivo_memoria.getvalue()).hexdigest()
        if st.session_state.get('memoria_importada_sha')!=huella:
            try:
                st.session_state['memoria_validada']=cargar_memoria(archivo_memoria.getvalue())
                st.session_state['memoria_importada_sha']=huella
                st.success("Memoria importada, sujeta a las validaciones exactas de RUT, documento y período.")
            except Exception as exc:st.error(str(exc))
    mem=st.session_state['memoria_validada']
    st.caption(f"Correcciones aprobadas: {len(mem['correcciones'])} | Ejemplos de páginas: {len(mem['plantillas'])}. "
               "Las páginas idénticas se reconocen desde el primer ejemplo; para generalizar se requieren al menos 12 ejemplos y 2 clases.")
    if archivos:
        st.markdown("**Enseñar el tipo de una página difícil**")
        candidatos=[f for f in archivos if f.name.lower().endswith('.pdf')]
        if candidatos:
            elegido=st.selectbox("Documento para etiquetar",candidatos,format_func=lambda f:f.name,key='pdf_aprendizaje')
            import fitz
            with fitz.open(stream=elegido.getvalue(),filetype='pdf') as doc:
                pag=st.number_input("Página (número físico del PDF)",min_value=1,max_value=len(doc),value=1,key='pagina_ml')
                encabezado=doc[int(pag)-1].get_text(sort=False)[:1600]
            if encabezado.strip():
                st.caption("Muestra del encabezado, limitada y depurada antes de entrenar:")
                from aprendizaje_revisiones import limpiar_encabezado_para_ml
                st.code(limpiar_encabezado_para_ml(encabezado)[:400])
                tipo=st.selectbox("Tipo de página confirmado",TIPOS_PAGINA,key='tipo_ml')
                confirmar_etiqueta=st.checkbox("Verifiqué visualmente el tipo de esta página",key='confirmar_ml')
                if st.button("Guardar ejemplo para el aprendizaje",key='guardar_ml'):
                    try:
                        registrar_plantilla(mem,encabezado,tipo,confirmar_etiqueta)
                        st.success("Ejemplo guardado en la memoria de sesión. Descarga el JSON para conservarlo.")
                    except Exception as exc:st.error(str(exc))
            else:st.caption("Esta página no tiene texto seleccionable. Para enseñar formatos escaneados, importa la cabecera OCR reconocida desde otra fuente.")
    st.download_button("Descargar memoria de aprendizaje (JSON)",data=guardar_memoria(mem),
        file_name="memoria_auditoria_validada.json",mime="application/json",key='descargar_memoria')

memoria=st.session_state['memoria_validada']

# No se utiliza caché global para evitar cruces de información de distintos usuarios.
def firma_entrada():
    h=hashlib.sha256()
    for f in list(archivos or [])+list(archivos_f301 or []):h.update(f.name.encode());h.update(f.getvalue())
    h.update(str(coincidencia_obra_exclusiva).encode())
    if asistencia_file:h.update(asistencia_file.getvalue())
    if topes:h.update(topes.getvalue())
    h.update(periodo_esperado.encode());h.update(modo_ocr.encode());h.update(guardar_memoria(memoria))
    return h.hexdigest()

if st.button("Ejecutar análisis y conciliación",type="primary",
             disabled=(not archivos_previred_ruta and not archivos_f301_ruta) or limite):
    st.session_state.pop('reporte',None)
    st.session_state.pop('f301_reporte',None)
    if periodo_modo.startswith("Verificar") and not re.fullmatch(r'20\d{2}-(?:0[1-9]|1[0-2])',periodo_esperado):
        st.error("Período esperado incorrecto. Escribe AAAA-MM (por ejemplo, 2026-08).")
    else:
        inicio=time.perf_counter();prog=st.progress(0,text="Comprobando archivos...")
        try:
            def progreso(i,n,nombre):prog.progress(i/n,text=f"{i}/{n}: {nombre[:65]}")
            import pandas as pd
            from auditoria_laboral import COLUMNAS_SALIDA
            if archivos_previred_ruta:
                matriz,excel,detalle,log=ejecutar_pipeline(
                    archivos_previred_ruta,
                    topes_csv=topes.getvalue() if topes else None,
                    tolerancia_clp=0,incluir_mutual=True,
                    periodo_esperado=periodo_esperado,modo_ocr=modo_ocr,
                    asistencia=(asistencia_file.getvalue(),asistencia_file.name) if asistencia_file else None,
                    memoria_aprendizaje=memoria,progreso=progreso,
                )
                st.session_state['reporte']={'firma':firma_entrada(),'matriz':matriz,'excel':excel,'detalle':detalle,
                    'log':log,'tiempo':time.perf_counter()-inicio,
                    'control_independientes':matriz.attrs.get('control_independientes')}
            else:
                matriz=pd.DataFrame(columns=COLUMNAS_SALIDA)
            if archivos_f301_ruta:
                from f301 import analizar_f301,conciliar_f301
                certificados,nomina,incidencias=analizar_f301(archivos_f301_ruta)
                hallazgos,resumen=conciliar_f301(certificados,nomina,matriz,coincidencia_obra_exclusiva)
                if periodo_esperado and not certificados.empty:
                    fuera=certificados['periodo_remuneracion'].ne(periodo_esperado)
                    for _,cf in certificados.loc[fuera].iterrows():
                        incidencias=pd.concat([incidencias,pd.DataFrame([{
                            'archivo':cf['archivo'],'certificado':cf['certificado'],
                            'tipo':'ALERTA_PERIODO',
                            'detalle':f"Período certificado {cf['periodo_remuneracion']} diferente del esperado {periodo_esperado}"}])],ignore_index=True)
                st.session_state['f301_reporte']={'firma':firma_entrada(),'certificados':certificados,
                    'nomina':nomina,'incidencias':incidencias,'hallazgos':hallazgos,'resumen':resumen}
            if not archivos and not archivos_f301:st.info('Sube al menos un documento.')
        except Exception as exc:st.error(f"No se pudo completar la auditoría: {exc}")
        finally:prog.empty()

reporte=st.session_state.get('reporte')
if reporte:
    if reporte['firma'] != firma_entrada():
        st.info('Cambió la selección de documentos, el período, el procesamiento o la memoria. Vuelve a ejecutar la auditoría.')
    else:
        from ui_v11 import render_dashboard
        render_dashboard(reporte,[f for f in (archivos or []) if f.name in {n for _,n in archivos_previred_ruta}])
        m=reporte['matriz']
        with st.expander('04 / Confirmar datos OCR cotejados visualmente',expanded=False):
            st.caption('Esta acción requiere revisar el RUT, días, bases de Salud/AFP/Mutual y mutualidad en las páginas originales. '
                'La conformidad resultante es únicamente documental y se limita a este PDF y período.')
            registros_ocr=reporte['detalle']
            registros_ocr=registros_ocr[
                registros_ocr['motivo_revision'].astype(str).str.contains('OCR de imagen',case=False,regex=False)
            ] if not registros_ocr.empty else registros_ocr
            if registros_ocr.empty:
                st.info('No hay registros OCR pendientes aptos para verificación individual.')
            else:
                idx=st.selectbox('Registro escaneado pendiente',list(registros_ocr.index),
                    format_func=lambda i: f"{registros_ocr.loc[i,'trabajador_nombre']} · {registros_ocr.loc[i,'trabajador_rut']} · {registros_ocr.loc[i,'periodo_remuneracion']}",key='valid_ocr_rut')
                r=registros_ocr.loc[idx]
                st.dataframe([{
                    'Días':int(r['dias_reales_pagados']),
                    'Salud ($)':int(r['renta_imponible_salud']),
                    'AFP ($)':int(r['renta_imponible_afp']),
                    'Mutual ($)':int(r['renta_imponible_mutual']),
                    'Mutualidad':r['mutual_adherida']
                }],hide_index=True,use_container_width=True)
                st.caption(f"Archivo: {r['archivo_origen']} | Páginas: {r['hoja_o_pagina']}. Ver página original en la pestaña Revisión OCR.")
                revisor=st.text_input('Nombre o identificación del revisor autorizado',key='valid_ocr_revisor')
                visto=st.checkbox('Confirmo que contrasté estos cinco campos con el comprobante original y que corresponden al RUT seleccionado',key='valid_ocr_confirmo')
                if st.button('Registrar verificación humana',key='valid_ocr_guardar'):
                    fuente=next((f for f in (archivos or []) if f.name==r['archivo_origen']),None)
                    if fuente is None:
                        st.error('No se encuentra el PDF correspondiente al registro seleccionado.')
                    else:
                        try:
                            valores={k:r[k] for k in ('dias_reales_pagados','renta_imponible_salud',
                                'renta_imponible_afp','renta_imponible_mutual','mutual_adherida')}
                            registrar_verificacion_ocr(memoria,sha256_pdf(fuente.getvalue()),str(r['trabajador_rut']),
                                str(r['periodo_remuneracion']),valores,revisor,visto)
                            st.success('Verificación guardada en la memoria. Descarga el JSON y vuelve a ejecutar para aplicarla.')
                        except Exception as exc:st.error(str(exc))
                st.download_button('Guardar memoria de verificaciones',data=guardar_memoria(memoria),
                    file_name='verificaciones_ocr_aprobadas.json',mime='application/json',key='valid_ocr_json')
        with st.expander('05 / Corregir campos a partir de evidencia',expanded=False):
            st.caption('Solo para datos verificados contra el documento original o un respaldo válido. '
                       'La corrección se vincula al SHA256 del PDF: nunca se copiará el sueldo de un período a otro.')
            if len(m):
                selected=st.selectbox('Trabajador para corregir',list(range(len(m))),
                    format_func=lambda i:f"{m.iloc[i]['trabajador_nombre']} · {m.iloc[i]['trabajador_rut']} · {m.iloc[i]['periodo_remuneracion']}")
                fcol=st.selectbox('Campo verificado',['dias_reales_pagados','renta_imponible_salud','renta_imponible_afp',
                                                       'renta_imponible_mutual','mutual_adherida'])
                origen=m.iloc[selected]
                valor_anterior=origen[fcol]
                nuevo=st.text_input('Valor correcto verificado',value=str(valor_anterior),key=f'correction_{selected}_{fcol}')
                confirmado=st.checkbox('Revisé el PDF o respaldo y confirmo el valor',key='confirmar_correccion')
                nombre_fuente=st.selectbox('Documento al que pertenece la corrección',list(archivos),format_func=lambda f:f.name,key='fuente_correccion')
                if st.button('Guardar corrección confirmada'):
                    try:
                        registrar_correccion(memoria,sha256_pdf(nombre_fuente.getvalue()),str(origen['trabajador_rut']),
                            str(origen['periodo_remuneracion']),fcol,valor_anterior,nuevo,confirmado)
                        st.success('Corrección registrada en memoria. Descarga el JSON y vuelve a ejecutar la auditoría para aplicarla.')
                    except Exception as exc:st.error(str(exc))
                st.download_button('Guardar JSON con correcciones',data=guardar_memoria(memoria),
                    file_name='memoria_auditoria_validada.json',mime='application/json',key='memoria_corregida')

reporte_f301=st.session_state.get('f301_reporte')
if reporte_f301:
    if reporte_f301['firma']!=firma_entrada():
        st.info('Los archivos o las opciones cambiaron. Ejecuta de nuevo la conciliación F30-1.')
    else:
        from ui_f301 import mostrar_f301
        mostrar_f301(reporte_f301)
