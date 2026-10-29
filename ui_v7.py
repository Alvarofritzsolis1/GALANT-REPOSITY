"""Interfaz de auditoría: diagnósticos interpretables, sin duplicar incidencias."""
from __future__ import annotations
import pandas as pd
from diagnostico_ocr import construir_diagnostico


ESTADOS = ['CONFORME', 'REVISAR_DATOS', 'ALERTA_DIAS_CERO', 'ALERTA_DESCUADRE_RENTAS']


def resumen_metricas(matriz: pd.DataFrame, log: pd.DataFrame, diagnostico: pd.DataFrame | None = None) -> dict:
    if matriz is None or matriz.empty:
        return {'registros':0,'bases_coincidentes':0,'conformes':0,'revisar':0,'alertas':0,
                'incidencias_ocr':0,'ocr_celdas':0,'pdf_pendientes':0}
    s=matriz
    datos_afp=s['renta_imponible_afp'].gt(0) & s['renta_imponible_salud'].gt(0)
    bases=s['renta_imponible_afp'].eq(s['renta_imponible_salud']) & datos_afp
    tiene_mutual=s['renta_imponible_mutual'].gt(0) & s['mutual_adherida'].ne('NO IDENTIFICADA')
    bases &= (~tiene_mutual | s['renta_imponible_afp'].eq(s['renta_imponible_mutual']))
    diag=diagnostico if diagnostico is not None else pd.DataFrame()
    ocr_celdas=int(diag['prioridad'].eq('ALTA').sum()) if 'prioridad' in diag.columns else 0
    return {
        'registros':int(len(s)), 'bases_coincidentes':int(bases.sum()),
        'conformes':int(s.estado_auditoria.eq('CONFORME').sum()),
        'revisar':int(s.estado_auditoria.eq('REVISAR_DATOS').sum()),
        'alertas':int(s.estado_auditoria.astype(str).str.startswith('ALERTA').sum()),
        'incidencias_ocr':int(len(diag)), 'ocr_celdas':ocr_celdas,
        'pdf_pendientes':int(log['resultado'].eq('ERROR').sum()) if log is not None and 'resultado' in log else 0,
    }


def render_dashboard(reporte: dict, archivos: list) -> None:
    import streamlit as st
    from auditoria_laboral import COLUMNAS_SALIDA
    m=reporte['matriz'];d=reporte['detalle'];log=reporte['log']
    diag=construir_diagnostico(d,log)
    met=resumen_metricas(m,log,diag)
    st.markdown('### Panel de resultados')
    st.caption(f"Análisis completado en {reporte['tiempo']:.1f} s. La conformidad se refiere exclusivamente a las comprobaciones de bases y días documentados; no certifica pago legal completo.")
    a,b,c,e=st.columns(4)
    a.metric('Registros identificados',met['registros'])
    b.metric('Bases compatibles',f"{met['bases_coincidentes']}/{met['registros']}",help='Coincidencia de cifras extraídas; no equivale a revisión OCR concluida.')
    c.metric('Alertas documentales',met['alertas'])
    e.metric('Pendientes de revisión',met['revisar'])
    if met['ocr_celdas']:
        st.warning(f"{met['ocr_celdas']} incidencias OCR de prioridad alta para revisar. Se indican por RUT o por página del documento, sin repetirse en todos los trabajadores.")
    if met['pdf_pendientes']:
        st.error(f"{met['pdf_pendientes']} archivo(s) no se pudieron extraer de forma fiable. Son pendientes técnicos, no incumplimientos acreditados.")

    tab_res,tab_matriz,tab_ocr,tab_archivos=st.tabs([
        'Resumen ejecutivo', 'Trabajadores', 'Revisión OCR', 'Documentos y trazabilidad'
    ])
    with tab_res:
        s1,s2=st.columns([1.3,1])
        with s1:
            st.markdown('#### Resultado de la lectura')
            st.info('**Una diferencia de OCR no es una diferencia previsional.** Solo una cifra verificada contra la fuente puede considerarse conflicto documental. Los movimientos laborales y eventuales exenciones también pueden explicar diferencias entre instituciones.')
            cantidad_pdf=sum(x.name.lower().endswith('.pdf') for x in archivos)
            st.caption(f'{cantidad_pdf} PDF cargados · {met["conformes"]} registros conformes bajo las reglas configuradas · {met["revisar"]} pendientes de documentación o verificación.')
            if 'casos_sin_registro_seguro_social' in log.columns and int(log['casos_sin_registro_seguro_social'].fillna(0).sum())>0:
                st.warning('Hay trabajadores que aparecen en AFP pero no en Seguro Social. Su situación se registra aparte y no constituye por sí sola una deuda ni una exención confirmada.')
            if m['mutual_adherida'].eq('NO IDENTIFICADA').any():
                st.warning('Faltan comprobantes de Mutual/ISL para algunos registros. La Caja de Compensación no es el organismo administrador de accidentes.')
        with s2:
            st.markdown('#### Distribución de estados')
            conteos=m['estado_auditoria'].value_counts().rename_axis('Estado').reset_index(name='Registros')
            st.dataframe(conteos,hide_index=True,use_container_width=True)
            st.caption('La validación OCR pendiente aparece dentro de REVISAR_DATOS hasta confirmación visual.')
        st.markdown('#### Archivos con incidencias')
        muestra=log[['archivo','resultado','control_calidad','filas_detectadas']].copy()
        st.dataframe(muestra,hide_index=True,use_container_width=True,height=min(420,86+36*len(muestra)))

    with tab_matriz:
        st.markdown('#### Matriz de auditoría')
        filtro=st.segmented_control('Filtrar por resultado',options=['Todos','Conformes','Revisar','Alertas'],default='Todos')
        vista=m.copy()
        if filtro=='Conformes':vista=vista[vista.estado_auditoria.eq('CONFORME')]
        elif filtro=='Revisar':vista=vista[vista.estado_auditoria.eq('REVISAR_DATOS')]
        elif filtro=='Alertas':vista=vista[vista.estado_auditoria.str.startswith('ALERTA')]
        st.dataframe(vista[COLUMNAS_SALIDA],use_container_width=True,hide_index=True,height=430,
            column_config={
              'trabajador_nombre':st.column_config.TextColumn('Trabajador',width='medium'),
              'dias_reales_pagados':st.column_config.NumberColumn('Días remunerados',format='%d'),
              'renta_imponible_salud':st.column_config.NumberColumn('Salud',format='$%d'),
              'renta_imponible_afp':st.column_config.NumberColumn('AFP',format='$%d'),
              'renta_imponible_mutual':st.column_config.NumberColumn('Mutual',format='$%d'),
              'estado_auditoria':st.column_config.TextColumn('Control de bases',width='medium'),
            })
        st.caption('Para registros escaneados, las cifras son provisionales mientras no se comprueben las celdas dudosas.')
        if d is not None and not d.empty:
            with st.expander(f'Detalle documental completo ({len(d)} registros)',expanded=False):
                st.dataframe(d,hide_index=True,use_container_width=True)

    with tab_ocr:
        st.markdown('#### Incidencias para revisar, sin duplicaciones')
        st.caption('Cada fila identifica una lectura dudosa o un problema general sin RUT. La nota de control visual se muestra una sola vez por archivo.')
        if diag.empty:
            st.success('No se identificaron incidencias OCR específicas en los documentos procesados.')
        else:
            archivos_diag=['Todos']+sorted(diag.archivo.unique().tolist())
            f=st.selectbox('Documento a revisar',archivos_diag,key='v7_ocr_filter')
            subset=diag if f=='Todos' else diag[diag.archivo.eq(f)]
            st.dataframe(subset[['prioridad','alcance','trabajador_nombre','trabajador_rut','pagina','categoria','hallazgo','accion']],
                hide_index=True,use_container_width=True,height=min(470,max(170,85+36*len(subset))))
            st.download_button('Exportar incidencias a CSV',diag.to_csv(index=False).encode('utf-8-sig'),
                file_name='diagnostico_ocr.csv',mime='text/csv',key='v7_export_diagnostico')
        with st.expander('Ver página del PDF para comprobar los datos',expanded=False):
            archivos_pdf={a.name:a for a in archivos if a.name.lower().endswith('.pdf')}
            if archivos_pdf:
                elegido=st.selectbox('Archivo PDF',list(archivos_pdf),key='v7_pdf_preview')
                import fitz
                doc=fitz.open(stream=archivos_pdf[elegido].getvalue(),filetype='pdf')
                try:
                    pag=st.number_input('Número de página',min_value=1,max_value=len(doc),value=min(2,len(doc)),key='v7_page_preview')
                    if st.button('Mostrar página',key='v7_render_button'):
                        page=doc[int(pag)-1]
                        # Preview solo a petición; no OCR ni almacenamiento compartido.
                        pix=page.get_pixmap(matrix=fitz.Matrix(1.6,1.6),alpha=False)
                        st.image(pix.tobytes('png'),caption=f'{elegido} · página {pag}',use_container_width=True)
                finally:
                    doc.close()
            else: st.info('Carga un PDF para comprobar las celdas.')

    with tab_archivos:
        st.markdown('#### Documentos procesados')
        st.dataframe(log,use_container_width=True,hide_index=True)
        st.info('Los problemas del OCR sin un RUT reconocible aparecen en la columna incidencias_ocr_sin_rut y en la hoja Diagnostico_OCR, con alcance DOCUMENTO / FILA SIN RUT.')

    st.download_button('Descargar auditoría Excel completa',data=reporte['excel'],
        file_name='Auditoria_Contratistas_V7.xlsx',mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',type='primary',key='v7_download')
