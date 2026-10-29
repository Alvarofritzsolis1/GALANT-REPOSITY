"""Presentación de certificados F30-1 + conciliación. No simula una consulta DT."""
from __future__ import annotations
from datetime import datetime,timezone
import pandas as pd
import streamlit as st
from f301 import exportar_f301_excel

URL_DT='https://verificador.dirtrab.cl/'


def mostrar_f301(reporte):
    if not reporte:return
    certs=reporte['certificados'].copy()
    nomina=reporte['nomina']; hallazgos=reporte['hallazgos']; resumen=reporte['resumen'];errores=reporte['incidencias']
    st.markdown('---')
    st.subheader('03 / F30-1 · certificados y conciliación')
    st.caption('La indicación “Pagadas: Sí” procede del certificado presentado; no equivale a autenticidad validada en el portal DT.')
    if certs.empty:
        st.warning('No se pudieron reconocer certificados. Revisa incidencias y adjunta PDF legible.')
        return
    v=st.session_state.setdefault('f301_verificaciones',{})
    t1,t2,t3,t4=st.tabs(['Resumen F30-1','Conciliación RUT','Verificador oficial DT','Errores de extracción'])
    with t1:
        a,b,c,d=st.columns(4)
        a.metric('Certificados distintos',len(certs))
        b.metric('Con nómina legible',int(certs['nomina_disponible'].sum()))
        c.metric('Pagadas: Sí (declarado)',int((certs['cotizaciones_previsionales']=='SI_DECLARADO').sum()))
        d.metric('Pendientes de portal DT',sum(v.get((x['sha256_documento'],x['certificado']),{}).get('estado')!='REVISADO_MANUALMENTE_EN_PORTAL' for _,x in certs.iterrows()))
        visible=certs.drop(columns=['sha256_documento','clave'],errors='ignore')
        st.dataframe(visible,use_container_width=True,hide_index=True,height=min(540,110+len(visible)*35))
        st.markdown('**Controles del certificado — resultado del PDF, no del portal DT**')
        for _,c in certs.iterrows():
            with st.expander(f"{c['certificado']} · {c['contratista_nombre'] or c['archivo']}",expanded=len(certs)==1):
                cp,cn,ct=st.columns(3)
                cp.metric('Vigentes', c.get('trabajadores_vigentes') if pd.notna(c.get('trabajadores_vigentes')) else 'No leído')
                cn.metric('RUT en nómina',int(c.get('trabajadores_en_nomina') or 0))
                ct.metric('Período certificado',c.get('periodo_remuneracion') or 'Sin identificar')
                if c.get('cotizaciones_previsionales')=='SI_DECLARADO':
                    st.success('Cotizaciones previsionales: el PDF declara “Pagadas: Sí”.')
                elif c.get('cotizaciones_previsionales')=='NO_DECLARADO':
                    st.error('Cotizaciones previsionales: el PDF declara “Pagadas: No”.')
                else:
                    st.warning('Cotizaciones previsionales: estado no legible.')
                rp=c.get('remuneraciones_pagadas')
                rn=c.get('remuneraciones_no_pagadas')
                if pd.notna(rp) and pd.notna(rn):
                    st.write(f"Remuneraciones declaradas pagadas: **{int(rp)}**; no pagadas: **{int(rn)}**. "
                             f"Monto informado: **${int(c['monto_remuneraciones_informado']):,}**".replace(',', '.'))
                else:
                    st.warning('No se pudo extraer el detalle de remuneraciones pagadas/no pagadas.')
                if bool(c.get('mes_archivo_distinto_periodo',False)):
                    st.warning(f"El nombre del PDF indica {c['mes_nombre_archivo']}, pero el certificado "
                               f"acredita {c['periodo_remuneracion']}. Para cruzar se usa el período del documento.")
                if c.get('trabajadores_en_nomina') and pd.notna(c.get('trabajadores_vigentes')):
                    if int(c['trabajadores_en_nomina'])==int(c['trabajadores_vigentes']):
                        st.success('Coincide cantidad de RUT extraídos con vigentes del F30-1.')
                    else:
                        st.warning('La cantidad de RUT no cuadra con vigentes; revisar nómina y OCR.')
                st.info('Autenticidad ante la DT: **pendiente de verificar en el portal oficial**. '
                        'Los valores recuperados del PDF no son una consulta a la DT.')

        st.info('Si el F30-1 dice “Adjunta Nómina: No”, no es posible identificar faltantes por trabajador. '
                'Una nómina de trabajadores declarados puede venir en una página adicional del mismo certificado.')
    with t2:
        if resumen.empty:
            st.info('No hay una conciliación disponible. Carga comprobantes Previred para obtener comparaciones por RUT y período.')
        else:
            st.dataframe(resumen,use_container_width=True,hide_index=True)
        if not hallazgos.empty:
            st.markdown('**Diferencias por RUT**')
            st.dataframe(hallazgos,use_container_width=True,hide_index=True)
        else:
            st.caption('Sin diferencias individuales detectadas en las fuentes comparables. Los certificados sin nómina mantienen limitaciones de cobertura.')
        if not nomina.empty:
            with st.expander('Ver nóminas individuales extraídas de los F30-1'):
                st.dataframe(nomina,use_container_width=True,hide_index=True)
    with t3:
        st.write('El portal oficial requiere **N.º de certificado** y **clave**. La aplicación extrae ambos; el resultado oficial debe comprobarse en la página.')
        st.link_button('Abrir verificador de la Dirección del Trabajo',URL_DT)
        st.caption('Esta aplicación no puede confirmar automáticamente una respuesta del formulario oficial. La verificación requiere ingresar folio y clave en la web de la DT y comparar el resultado.')
        ids=list(range(len(certs)))
        idx=st.selectbox('Certificado a comprobar',ids,format_func=lambda i:f"{certs.iloc[i]['certificado']} · {certs.iloc[i]['contratista_nombre'] or certs.iloc[i]['archivo'][:45]}",key='f301_selector')
        fila=certs.iloc[idx];cert=str(fila['certificado']);key=(fila['sha256_documento'],cert)
        st.code(f'N.º certificado: {cert}\nClave detectada: {fila["clave"] or "NO RECONOCIDA - revisar PDF"}')
        clave_ingresada=st.text_input('Clave exacta del PDF (corrige OCR si es necesario)',value=fila['clave'],key=f'clave_{cert}',type='password')
        st.caption('Coteja la respuesta oficial: empresa/RUT, período, obra y número de certificado. No copies las claves en un repositorio público.')
        if (key in v):
            st.info(f"Registro de revisión: {v[key]['estado']} · {v[key]['fecha']} · {v[key]['revisor']}")
        nombre=st.text_input('Identificación del revisor',key='revisor_f301')
        resultado=st.radio('Resultado que viste EN EL PORTAL DT',['Verificado: coincide con el PDF','Rechazado / datos diferentes','No pude verificarlo'],key='resultado_f301')
        confirm=st.checkbox('Confirmo que consulté el sitio de la DT y comparé sus datos con este certificado',key='check_verificacion_f301')
        if st.button('Guardar resultado de verificación',key='guardar_verificacion_f301'):
            if not confirm or not nombre.strip() or not clave_ingresada.strip() or cert.startswith('SIN_FOLIO_'):
                st.error('Se requiere clave y folio legibles, nombre del revisor y confirmación explícita de la consulta al portal.')
            else:
                estado='REVISADO_MANUALMENTE_EN_PORTAL' if resultado.startswith('Verificado') else 'RECHAZADO' if resultado.startswith('Rechazado') else 'NO_VERIFICADO'
                v[key]={'estado':estado,'revisor':nombre.strip()[:80],'fecha':datetime.now(timezone.utc).isoformat(timespec='seconds'),'certificado':cert,'sha256_documento':fila['sha256_documento']}
                st.success('Resultado manual registrado en esta sesión, sin afirmar validación automática.')
        if v:
            import json
            st.download_button('Descargar registro de verificaciones (.json)',json.dumps(list(v.values()),ensure_ascii=False,indent=2),
                               file_name='verificaciones_f301_revision_manual.json',mime='application/json')
    with t4:
        if errores.empty:st.success('Sin incidencias de lectura registradas.')
        else:st.dataframe(errores,use_container_width=True,hide_index=True)
        st.caption('Las observaciones de OCR no son infracciones laborales y no deben declararse como deuda sin cotejo.')

    export_certs=certs.copy()
    for i,r in export_certs.iterrows():
        row=v.get((r['sha256_documento'],r['certificado']),{})
        export_certs.loc[i,'verificacion_dt']=row.get('estado','PENDIENTE_PORTAL_DT')
    excel=exportar_f301_excel(export_certs,nomina,hallazgos,resumen,errores)
    st.download_button('Descargar auditoría F30-1 y conciliación (Excel)',excel,file_name='Auditoria_F301_Conciliacion.xlsx',
                      mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',type='primary')
