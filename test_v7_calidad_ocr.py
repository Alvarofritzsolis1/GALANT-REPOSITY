import pandas as pd
from diagnostico_ocr import construir_diagnostico, categorizar


def test_no_duplicar_misma_advertencia_en_ocho_trabajadores():
    detail=pd.DataFrame([
      {'archivo_origen':'buffalo.pdf','trabajador_rut':'11111111','trabajador_nombre':'A',
       'motivo_revision':'Conflicto entre tablas del PDF: p.6, fila 5: lecturas alternativas de renta [4933, 35933]; cotejar; OCR de imagen: cotejar con comprobante original antes de aprobar'},
      {'archivo_origen':'buffalo.pdf','trabajador_rut':'22222222','trabajador_nombre':'B',
       'motivo_revision':'Conflicto entre tablas del PDF: OCR de imagen: cotejar con comprobante original antes de aprobar'},
    ])
    log=pd.DataFrame([{'archivo':'buffalo.pdf','control_calidad':'OCR_REQUIERE_COTEJO','incidencias_ocr_sin_rut':''}])
    r=construir_diagnostico(detail,log)
    assert len(r[r['categoria'].eq('OCR · monto')])==1
    assert len(r[r['categoria'].eq('Control visual')])==1
    assert r[r['categoria'].eq('OCR · monto')]['trabajador_rut'].iloc[0]=='11111111'


def test_pendiente_sin_identidad_se_conserva_en_archivo():
    log=pd.DataFrame([{'archivo':'x.pdf','control_calidad':'OCR_REQUIERE_COTEJO',
                       'incidencias_ocr_sin_rut':'p.6, fila 5: RUT no reconocido | p.6, fila 8: base imponible no reconocida'}])
    r=construir_diagnostico(pd.DataFrame(),log)
    assert len(r[r['alcance'].eq('DOCUMENTO / FILA SIN RUT')])==2
    assert 'Control visual' in set(r.categoria)


def test_prioridad_ocr_vs_control_visual():
    assert categorizar('p.8, fila 2: lecturas alternativas [40, 400]')[1]=='ALTA'
    assert categorizar('OCR de imagen: cotejar')[1]=='INFORMATIVA'


def test_recuperacion_rut_ss_solo_cuando_es_unico_y_monto_exacto():
    from previred_scan_fast import _resolver_rut_ss_solo_con_evidencia
    # Ejemplo sintético, sin datos reales de trabajadores.
    datos={'123456785':{'salud':[(6,0,125000,7)]}, '986543214':{'salud':[(6,1,330000,20)]}}
    assert _resolver_rut_ss_solo_con_evidencia('12.345.678-6',125000,datos)=='123456785'
    assert not _resolver_rut_ss_solo_con_evidencia('12.345.678-6',99000,datos)
    assert not _resolver_rut_ss_solo_con_evidencia('12.345.678-6',0,datos)
    assert not _resolver_rut_ss_solo_con_evidencia('12.345.678-7',125000,
            {'123456785':datos['123456785'], '123456786':{'salud':[(6,1,125000,7)]}})


def test_dias_acumulados_solo_si_filas_completas():
    from previred_scan_fast import _sumar_dias_si_completos
    assert _sumar_dias_si_completos([(8,0,0,0),(8,1,161700,9)]) == 9
    assert _sumar_dias_si_completos([(8,0,0,None),(8,1,161700,9)]) is None
    assert _sumar_dias_si_completos([]) is None


def test_aprobacion_ocr_exige_folio_exactamente_mismos_datos_y_revisor():
    import pandas as pd
    from auditoria_laboral import limpiar_y_transformar, aplicar_motor_auditoria
    from test_auditoria_laboral import fila_base
    from aprendizaje_revisiones import nueva_memoria, registrar_verificacion_ocr, aplicar_verificaciones_ocr
    crudo=pd.DataFrame([fila_base()])
    crudo['__ocr_origen']='ESCANEADO'
    crudo['__conflicto_pdf']='p.2, fila 2: lecturas alternativas de renta [900000, 1000000]; cotejar; OCR de imagen: cotejar'
    crudo['__ocr_incidencias_documento']=''
    limpio=limpiar_y_transformar(crudo,'prueba_2026-08.pdf')
    matriz,detalle=aplicar_motor_auditoria(limpio)
    assert matriz.estado_auditoria.iloc[0]=='REVISAR_DATOS'
    memoria=nueva_memoria()
    row=limpio.iloc[0]
    valores={c:row[c] for c in ('dias_reales_pagados','renta_imponible_salud','renta_imponible_afp','renta_imponible_mutual','mutual_adherida')}
    registrar_verificacion_ocr(memoria,'a'*64,row['trabajador_rut'],row['periodo_remuneracion'],valores,'Auditora Prueba',True)
    other,_=aplicar_verificaciones_ocr(limpio,memoria,'b'*64)
    assert other['__conflicto_pdf'].iloc[0]
    vals,evt=aplicar_verificaciones_ocr(limpio,memoria,'a'*64)
    assert not vals['__conflicto_pdf'].iloc[0] and vals['__ocr_verificacion_humana'].iloc[0]
    matriz_final,_=aplicar_motor_auditoria(vals)
    assert matriz_final.estado_auditoria.iloc[0]=='CONFORME'


def test_validacion_no_borra_datos_faltantes_ni_pendientes_globales():
    import pandas as pd
    from auditoria_laboral import limpiar_y_transformar
    from test_auditoria_laboral import fila_base
    from aprendizaje_revisiones import nueva_memoria,registrar_verificacion_ocr,aplicar_verificaciones_ocr
    df=pd.DataFrame([fila_base()]);df['__ocr_origen']='ESCANEADO'
    df['__conflicto_pdf']='Falta detalle Mutual; OCR de imagen: revisar'
    df['__ocr_incidencias_documento']='p.2, fila 4: RUT no identificado'
    limpio=limpiar_y_transformar(df,'a.pdf')
    mem=nueva_memoria();r=limpio.iloc[0]
    v={c:r[c] for c in ('dias_reales_pagados','renta_imponible_salud','renta_imponible_afp','renta_imponible_mutual','mutual_adherida')}
    registrar_verificacion_ocr(mem,'a'*64,r['trabajador_rut'],r['periodo_remuneracion'],v,'Revisor Autorizado',True)
    result,events=aplicar_verificaciones_ocr(limpio,mem,'a'*64)
    assert 'Falta detalle' in result['__conflicto_pdf'].iloc[0]
    assert result['__ocr_verificacion_humana'].iloc[0]==''


def test_validacion_de_una_fila_no_ignora_pendientes_de_otras_filas():
    from aprendizaje_revisiones import nueva_memoria, registrar_verificacion_ocr, aplicar_verificaciones_ocr
    import pandas as pd
    from auditoria_laboral import limpiar_y_transformar
    from test_auditoria_laboral import fila_base
    base=fila_base()
    segundo=dict(base);segundo['Nombre Trabajador']='Otro Trabajador';segundo['RUT Trabajador']='17.654.321-3'
    raw=pd.DataFrame([base,segundo]);raw['__ocr_origen']='ESCANEADO'
    raw['__conflicto_pdf']='OCR de imagen: cotejar'
    raw['__ocr_incidencias_documento']=['p.6, fila 9: ilegible','']
    limpio=limpiar_y_transformar(raw,'lote.pdf')
    m=nueva_memoria()
    i=1;r=limpio.iloc[i]
    v={k:r[k] for k in ('dias_reales_pagados','renta_imponible_salud','renta_imponible_afp','renta_imponible_mutual','mutual_adherida')}
    from auditoria_laboral import rut_chileno_valido
    # El segundo RUT ficticio podría no validar; usar primero, que sí valida.
    r=limpio.iloc[0]
    v={k:r[k] for k in ('dias_reales_pagados','renta_imponible_salud','renta_imponible_afp','renta_imponible_mutual','mutual_adherida')}
    registrar_verificacion_ocr(m,'a'*64,r.trabajador_rut,r.periodo_remuneracion,v,'Auditora Prueba',True)
    out,events=aplicar_verificaciones_ocr(limpio,m,'a'*64)
    assert out['__conflicto_pdf'].iloc[0]=='OCR de imagen: cotejar'
