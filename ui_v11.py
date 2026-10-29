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
    independientes=reporte.get('control_independientes',pd.DataFrame())
    diag=construir_diagnostico(d,log)
    met=resumen_metricas(m,log,diag)
    st.markdown('### Panel de resultados')
    st.caption(f"Análisis completado en {reporte['tiempo']:.1f} s. La conformidad se refiere exclusivamente a las comprobaciones de bases y días documentados; no certifica pago legal completo.")
    a,b,c,e=st.columns(4)
    a.metric('Registros identificados',met['registros'])
    b.metric('Bases compatibles',f"{met['bases_coincidentes']}/{met['registros']}",help='Coincidencia de cifras extraídas; no equivale a revisión OCR concluida.')
    c.metric('Alertas documentales',met['alertas'])
    e.metric('Pendientes de revisión',met['revisar'])
    ocr_listos=int(log.get('control_calidad',pd.Series(dtype=str)).eq('OCR_CONCILIADO_AUTOMATICAMENTE').sum()) if log is not None else 0
    if ocr_listos:
        total_ok=int(log.get('registros_ocr_conciliados',pd.Series(dtype=float)).fillna(0).sum())
        notas=int(log.get('registros_con_lecturas_ocr_resueltas',pd.Series(dtype=float)).fillna(0).sum())
        st.success(f"{ocr_listos} PDF escaneado(s) y {total_ok} registros con RUT, días y bases conciliados entre instituciones.")
        if notas:
            st.caption(f"El sistema resolvió lecturas dudosas en {notas} registros al cotejar otras lecturas de las celdas y fuentes. Se guarda la evidencia técnica en Trazabilidad_PDF del Excel.")
        st.caption('Resultado automático de cuadratura documental, no certificación oficial de que todas las cotizaciones se encuentren pagadas.')
    if met['ocr_celdas']:
        st.warning(f"{met['ocr_celdas']} incidencias OCR de prioridad alta para revisar. Se indican por RUT o por página del documento, sin repetirse en todos los trabajadores.")
    if met['pdf_pendientes']:
        st.error(f"{met['pdf_pendientes']} archivo(s) no se pudieron extraer de forma fiable. Son pendientes técnicos, no incumplimientos acreditados.")

    tab_res,tab_matriz,tab_independientes,tab_ocr,tab_archivos=st.tabs([
        'Resumen ejecutivo', 'Trabajadores', 'Titulares e independientes', 'Revisión OCR', 'Documentos'
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
            st.caption('Un PDF escaneado NO es por sí solo un error. Solo quedan pendientes las celdas que no pasaron las verificaciones cruzadas.')
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
            st.markdown('#### ¿Por qué aparece en revisión?')
            st.caption('Cada observación distingue un error del lector OCR de una diferencia realmente extraída del comprobante.')
            cols_visibles=[x for x in ('trabajador_nombre','trabajador_rut','estado_auditoria',
                'tipo_revision','motivo_revision','accion_recomendada','hoja_o_pagina') if x in d.columns]
            st.dataframe(d[cols_visibles],hide_index=True,use_container_width=True,
                height=min(470,120+43*len(d)),column_config={
                    'trabajador_nombre':st.column_config.TextColumn('Trabajador',width='medium'),
                    'estado_auditoria':st.column_config.TextColumn('Resultado',width='small'),
                    'motivo_revision':st.column_config.TextColumn('Qué ocurrió',width='large'),
                    'accion_recomendada':st.column_config.TextColumn('Qué revisar',width='large'),
                    'tipo_revision':st.column_config.TextColumn('Tipo de observación',width='medium'),
                })
            with st.expander('Explicación individual: qué ocurrió y qué hacer',expanded=True):
                posiciones=list(d.index)
                seleccionado=st.selectbox('Selecciona un trabajador con observaciones',posiciones,
                    format_func=lambda k: str(d.loc[k].get('trabajador_nombre',''))+' · '+str(d.loc[k].get('trabajador_rut','')),
                    key='v11_detalle_selector')
                caso=d.loc[seleccionado]
                st.markdown('**¿Qué detectó el sistema?**')
                st.write(str(caso.get('motivo_revision','')))
                st.markdown('**¿Cómo resolverlo?**')
                st.info(str(caso.get('accion_recomendada','')))
                st.caption('Origen: '+str(caso.get('archivo_origen',''))+' · Página(s): '+str(caso.get('hoja_o_pagina','')))
            with st.expander('Mostrar trazabilidad técnica (para resolver incidencias)',expanded=False):
                st.dataframe(d,hide_index=True,use_container_width=True)
        else:
            st.success('No se detectaron diferencias documentales ni lecturas OCR pendientes en los registros mostrados.')
            st.caption('Las bases y días han pasado los controles configurados. Este resultado no certifica por sí mismo que todas las obligaciones laborales estén cumplidas.')

    with tab_independientes:
        st.markdown('#### Cotizaciones personales del titular y pagos de la empresa')
        st.caption('Una persona puede aparecer como independiente y en la EIRL: se revisa cada modalidad por separado, sin sumar rentas ni días de dos planillas.')
        if independientes is None or independientes.empty:
            st.info('No se identificaron pagos independientes ni representantes que figuren como trabajadores en las planillas cargadas.')
        else:
            c1,c2,c3=st.columns(3)
            c1.metric('Personas relacionadas',len(independientes))
            c2.metric('Mutual independiente documentada',int(independientes['independiente_mutual_base'].notna().sum()))
            c3.metric('Casos que requieren verificación',int(independientes['requiere_revision'].fillna(True).sum()))
            columnas=['periodo_remuneracion','persona_nombre','persona_rut','empresa_nombre',
                'mutual_independiente_entidad','independiente_mutual_dias','independiente_mutual_base','independiente_mutual_pagado','independiente_afp_base',
                'independiente_salud_base','empresa_dias_declarados','empresa_afp_base',
                'estado_control_independiente','requiere_revision']
            st.dataframe(independientes.reindex(columns=columnas),use_container_width=True,hide_index=True,height=380,
                column_config={
                    'persona_nombre':st.column_config.TextColumn('Persona',width='medium'),
                    'persona_rut':st.column_config.TextColumn('RUT personal'),
                    'empresa_nombre':st.column_config.TextColumn('Empresa vinculada',width='large'),
                    'independiente_mutual_dias':st.column_config.NumberColumn('Días mutual personal',format='%d'),
                    'empresa_dias_declarados':st.column_config.NumberColumn('Días planilla empresa',format='%d'),
                    'mutual_independiente_entidad':st.column_config.TextColumn('Mutual personal'),
                    'independiente_mutual_base':st.column_config.NumberColumn('Base mutual personal',format='$%d'),
                    'independiente_mutual_pagado':st.column_config.NumberColumn('Pago mutual personal',format='$%d'),
                    'independiente_salud_base':st.column_config.NumberColumn('Salud personal',format='$%d'),
                    'independiente_afp_base':st.column_config.NumberColumn('AFP personal',format='$%d'),
                    'empresa_afp_base':st.column_config.NumberColumn('AFP empresa',format='$%d'),
                    'estado_control_independiente':st.column_config.TextColumn('Diagnóstico',width='large'),
                })
            for _,caso in independientes.iterrows():
                estado=str(caso['estado_control_independiente'])
                titulo=f"{caso['persona_nombre']} · {caso['periodo_remuneracion']}"
                with st.expander(titulo,expanded=bool(caso['requiere_revision'])):
                    if estado=='BASES_INDEPENDIENTES_CONCILIADAS':
                        st.success('Los tres comprobantes personales coinciden en la base. Conformidad documental del pago independiente; no acredita por sí sola la condición legal de titular ni su cobertura.')
                    elif estado=='MUTUAL_INDEPENDIENTE_CON_PAGO_EN_EMPRESA':
                        st.warning('Mutual independiente por 30 días acreditada. AFP y Salud aparecen en la planilla de la EIRL: verificar modalidad previsional y situación jurídica antes de aprobar cobertura personal.')
                    else:
                        st.warning('Se necesita verificación documental o jurídica para determinar el alcance del pago independiente.')
                    st.write(str(caso['explicacion']))
                    st.caption('Archivos de respaldo: '+str(caso['archivos_respaldo']))
            st.download_button('Exportar control de independientes (CSV)',
                independientes.to_csv(index=False).encode('utf-8-sig'),
                file_name='Control_Independientes_V8.csv',mime='text/csv',key='v8_csv_ind')
        st.info('**Criterio:** ser representante legal por sí solo no prueba participación mayoritaria, administración ni existencia de un vínculo laboral dependiente. Según ISL/SUSESO, el independiente debe cotizar en planilla personal para el Seguro de la Ley 16.744; comprobar también AFP/Salud y vigencia de afiliación.')

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
