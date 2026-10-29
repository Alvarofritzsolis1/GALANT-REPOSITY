"""Regresión documental reproducible, sin archivos privados dentro de GitHub.

Uso: python bateria_regresion.py --carpeta /ruta/a/pdfs --salida auditoria_lote.json
Opcional: --referencia base_validada.json para detectar pérdida de trabajadores.
La base de referencia debe estar APROBADA por una persona, no generada desde
las propias predicciones del sistema.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

import fitz
from auditoria_laboral import leer_archivo, limpiar_y_transformar


def revisar_pdf(path: Path) -> dict[str, Any]:
    inicio=time.perf_counter()
    contenido=path.read_bytes()
    sha=hashlib.sha256(contenido).hexdigest()
    with fitz.open(stream=contenido,filetype='pdf') as doc:
        n=len(doc)
        chars=[len(page.get_text().strip()) for page in doc]
    escaneadas=sum(c<70 for c in chars)
    clase='ESCANEADO' if escaneadas==n else 'DIGITAL' if escaneadas==0 else 'MIXTO'
    row={'archivo':path.name,'sha256':sha,'paginas':n,'tipo_pdf':clase,
         'estado':'SIN_PROCESAR','trabajadores':None,'periodos':[],
         'afp_total':None,'salud_total':None,'mutual_total':None,'dias_total':None,
         'errores':[],'segundos':None}
    try:
        # El modo rápido NO improvisa montos cuando el OCR genérico no
        # identifica celdas. El usuario puede reintentar cada archivo fallido
        # con el modo reforzado de Streamlit, sin detener los otros 19.
        crudo=leer_archivo(contenido,path.name,modo_ocr='rapido')
        df=limpiar_y_transformar(crudo,path.name)
        row['trabajadores']=len(df)
        row['periodos']=sorted(str(x) for x in df.periodo_remuneracion.dropna().unique() if str(x))
        for source,column in [('afp_total','renta_imponible_afp'),('salud_total','renta_imponible_salud'),('mutual_total','renta_imponible_mutual'),('dias_total','dias_reales_pagados')]:
            row[source]=int(df[column].sum()) if df[column].notna().any() else None
        conflictos=[]
        for obs in df['__conflicto_pdf'].dropna().unique() if '__conflicto_pdf' in df else []:
            for frag in str(obs).split('; '):
                if frag and frag not in conflictos:conflictos.append(frag)
        row['errores']=conflictos[:20]
        if any('Certificado individual PREVIRED' in s for s in conflictos):
            row['estado']='CERTIFICADO_SIN_DIAS'
        else:
            row['estado']='PROCESADO_CON_OBSERVACIONES' if conflictos else 'EXTRACCION_SIN_CONFLICTOS'
        # Solo los digitales con validación completa son candidatos a conformidad,
        # no se infiere cumplimiento laboral desde esta bandera técnica.
        if clase=='ESCANEADO' and row['estado']!='CERTIFICADO_SIN_DIAS':
            row['estado']='OCR_PARCIAL_REQUIERE_COTEJO'
            row['errores'].insert(0, 'OCR escaneado: registros provisionales; confirmar RUT, días y montos en imagen')
    except Exception as exc:
        row['estado']='PENDIENTE_EXTRACCION'
        row['errores']=[str(exc)[:420]]
    row['segundos']=round(time.perf_counter()-inicio,2)
    return row


def procesar(carpeta:Path, referencia:Path|None=None)->dict:
    archivos=sorted(carpeta.glob('*.pdf'))
    filas=[revisar_pdf(p) for p in archivos]
    resultados={'version':'7.0.0','archivos':filas,'resumen':{
        'total':len(filas),'digitales':sum(x['tipo_pdf']=='DIGITAL' for x in filas),
        'escaneados':sum(x['tipo_pdf']=='ESCANEADO' for x in filas),
        'sin_conflictos':sum(x['estado']=='EXTRACCION_SIN_CONFLICTOS' for x in filas),
        'con_observaciones':sum(x['estado']=='PROCESADO_CON_OBSERVACIONES' for x in filas),
        'certificados_parciales':sum(x['estado']=='CERTIFICADO_SIN_DIAS' for x in filas),
        'ocr_parcial':sum(x['estado']=='OCR_PARCIAL_REQUIERE_COTEJO' for x in filas),
        'pendientes':sum(x['estado']=='PENDIENTE_EXTRACCION' for x in filas)}}
    if referencia and referencia.exists():
        referencia_json=json.loads(referencia.read_text(encoding='utf8'))
        previos={x['sha256']:x for x in referencia_json.get('archivos',[])}
        regresiones=[]
        for item in filas:
            anterior=previos.get(item['sha256'])
            if not anterior:continue
            esperado=anterior.get('trabajadores')
            if esperado is not None and (item['trabajadores'] is None or item['trabajadores']<esperado):
                regresiones.append({'archivo':item['archivo'],'antes':esperado,'ahora':item['trabajadores'],
                                     'motivo':'Menos trabajadores o extracción fallida'})
        resultados['regresiones']=regresiones
    return resultados

if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--carpeta',type=Path,required=True)
    ap.add_argument('--salida',type=Path,default=Path('informe_regresion.json'))
    ap.add_argument('--referencia',type=Path)
    args=ap.parse_args()
    report=procesar(args.carpeta,args.referencia)
    args.salida.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report['resumen'],ensure_ascii=False,indent=2))
    if report.get('regresiones'):
        print('REGRESIONES DETECTADAS',len(report['regresiones']),file=sys.stderr)
        sys.exit(2)
