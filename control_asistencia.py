"""Contraste opcional con asistencia mensual, sin atribuir incumplimientos automáticamente.

Datos requeridos: rut_trabajador, dias_asistencia. Las diferencias son señales de
revisión, no un dictamen: licencias, turnos y ausencias justificadas pueden explicar
que los días asistidos y remunerados sean distintos.
"""
from __future__ import annotations
import io
import re
import pandas as pd


def leer_asistencia(contenido:bytes, nombre:str)->pd.DataFrame:
    from auditoria_laboral import clave_texto, clave_rut, rut_chileno_valido, convertir_monto
    ext=nombre.lower().rsplit('.',1)[-1]
    try:
        if ext=='csv':
            for enc in ('utf-8-sig','latin1'):
                try:
                    df=pd.read_csv(io.BytesIO(contenido),sep=None,engine='python',encoding=enc,dtype=str)
                    break
                except UnicodeDecodeError:
                    continue
        elif ext in ('xlsx','xls'):
            df=pd.read_excel(io.BytesIO(contenido),dtype=str)
        else:raise ValueError('Adjunta Excel o CSV.')
    except Exception as e:raise ValueError(f'No fue posible leer el control de asistencia: {e}') from e
    rut_col=next((c for c in df if clave_texto(c) in ('rut trabajador','trabajador rut','rut','run')),None)
    dia_col=next((c for c in df if clave_texto(c) in ('dias asistencia','dias asistidos','dias registrados','dias en obra','dias trabajados','dias efectivos')),None)
    if rut_col is None or dia_col is None:
        raise ValueError('El control de asistencia debe contener las columnas rut_trabajador y dias_asistencia.')
    valores=[]
    for i,row in df.iterrows():
        r=clave_rut(row[rut_col]); bruto=str(row[dia_col]).strip()
        if not r and (bruto in ('','nan','None')):continue
        if not rut_chileno_valido(r):raise ValueError(f'Asistencia: RUT inválido en fila {i+2}.')
        if not re.fullmatch(r'\d{1,2}',bruto) or int(bruto)>31:
            raise ValueError(f'Asistencia: días inválidos en fila {i+2}.')
        valores.append({'trabajador_rut':r,'dias_asistencia':int(bruto)})
    if not valores:raise ValueError('Control de asistencia sin registros.')
    out=pd.DataFrame(valores)
    if out.trabajador_rut.duplicated().any():
        raise ValueError('Asistencia: RUT repetido. Entrega una fila consolidada por trabajador y período; no se sumarán días arbitrariamente.')
    return out


def comparar_asistencia(matriz:pd.DataFrame, asistencia:pd.DataFrame)->pd.DataFrame:
    compara=matriz[['periodo_remuneracion','trabajador_rut','trabajador_nombre','dias_reales_pagados']].copy()
    compara=compara.merge(asistencia,how='outer',on='trabajador_rut',indicator=True)
    def razon(r):
        if r['_merge']=='left_only':return 'SIN_ASISTENCIA_REGISTRADA'
        if r['_merge']=='right_only':return 'RUT_NO_EN_COTIZACIONES'
        if int(r['dias_reales_pagados'])!=int(r['dias_asistencia']):return 'DIFERENCIA_A_VERIFICAR'
        return 'COINCIDE'
    compara['resultado_comparacion']=compara.apply(razon,axis=1)
    return compara.drop(columns=['_merge'])
