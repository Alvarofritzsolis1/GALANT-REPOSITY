"""Hallazgos de extracción, separados de dictámenes documentales.

Un problema de celda OCR afecta a UN RUT o a un archivo, nunca a todos los
trabajadores. La salida es apta para Excel y panel Streamlit; no almacena datos.
"""
from __future__ import annotations

import re
from typing import Any
import pandas as pd

COLUMNAS = ['archivo', 'alcance', 'trabajador_rut', 'trabajador_nombre', 'pagina', 'categoria', 'prioridad', 'hallazgo', 'accion']


def _partir(texto: Any) -> list[str]:
    if pd.isna(texto) if not isinstance(texto, (list, tuple)) else False:
        return []
    return [p.strip() for p in re.split(r'\s*;\s*(?=p\.\d+,\s*fila|OCR de imagen|Días|Falta|Nombre|Descuadre|Total|No coinciden|Verificar)', str(texto)) if p.strip()]


def categorizar(texto: str) -> tuple[str, str, str]:
    s = texto.lower()
    if 'lecturas alternativas' in s or 'base imponible no reconocida' in s or 'renta mínima' in s or 'base pequeña' in s:
        return 'OCR · monto', 'ALTA', 'Comprobar el importe en la página indicada; no elegir por coincidencia solamente.'
    if 'rut alineado' in s or 'rut no reconocido' in s or 'nombre del trabajador' in s:
        return 'OCR · identidad', 'ALTA', 'Verificar el RUT y el nombre contra la fila original.'
    if 'día' in s or 'dias' in s:
        return 'OCR · días', 'ALTA', 'Comparar días y códigos de movimiento en ambas tablas; revisar el OCR.'
    if 'descuadre' in s or 'total' in s or 'no coinciden' in s:
        return 'Conciliación', 'ALTA', 'Comprobar totales de fuentes y registros omitidos antes de concluir.'
    if 'ocr de imagen' in s:
        return 'Control visual', 'INFORMATIVA', 'Revisar la muestra visual antes de dar el registro por validado.'
    return 'Extracción', 'MEDIA', 'Inspeccionar el comprobante y resolver el registro indicado.'


def construir_diagnostico(detalle: pd.DataFrame, log: pd.DataFrame) -> pd.DataFrame:
    rows=[]
    vistos=set()
    por_archivo={}
    if detalle is not None and not detalle.empty:
        for _, r in detalle.iterrows():
            archivo=str(r.get('archivo_origen','') or '')
            rut=str(r.get('trabajador_rut','') or '')
            nombre=str(r.get('trabajador_nombre','') or '')
            motivo=str(r.get('detalle_tecnico', r.get('motivo_revision','')) or '')
            if 'OCR' not in motivo and 'ocr' not in motivo and 'Días leídos por OCR' not in motivo:
                continue
            for p in _partir(motivo):
                # Las observaciones no-OCR ya constan en Detalle_Revision y no se repiten aquí.
                if not any(k in p.lower() for k in ('ocr','rut alineado','lecturas alternativas','renta mínima','base imponible no reconocida','días leídos','días de salud ilegibles','nombre del trabajador no confirmado')):
                    continue
                categoria, prioridad, accion = categorizar(p)
                if categoria == 'Control visual':
                    por_archivo.setdefault(archivo, p)
                    continue
                key=(archivo,rut,p)
                if key in vistos:continue
                vistos.add(key)
                pagina=(re.search(r'p\.(\d+)',p) or [None, ''])[1]
                rows.append({'archivo':archivo,'alcance':'TRABAJADOR','trabajador_rut':rut,
                             'trabajador_nombre':nombre,'pagina':pagina,'categoria':categoria,
                             'prioridad':prioridad,'hallazgo':p,'accion':accion})
    if log is not None and not log.empty:
        for _,r in log.iterrows():
            archivo=str(r.get('archivo','') or '')
            globales=str(r.get('incidencias_ocr_sin_rut','') or '')
            for p in [x.strip() for x in globales.split(' | ') if x.strip()]:
                key=(archivo,'',p)
                if key in vistos:continue
                vistos.add(key)
                categoria, prioridad, accion=categorizar(p)
                pagina=(re.search(r'p\.(\d+)',p) or [None,''])[1]
                rows.append({'archivo':archivo,'alcance':'DOCUMENTO / FILA SIN RUT','trabajador_rut':'',
                             'trabajador_nombre':'','pagina':pagina,'categoria':categoria,
                             'prioridad':prioridad,'hallazgo':p,'accion':accion})
            if str(r.get('control_calidad',''))=='OCR_REQUIERE_COTEJO':
                por_archivo.setdefault(archivo,'PDF escaneado: verificar lectura OCR antes de aprobar')
    # Solo una nota global por archivo; jamás una copia para cada trabajador.
    for archivo,p in por_archivo.items():
        rows.append({'archivo':archivo,'alcance':'DOCUMENTO','trabajador_rut':'',
                     'trabajador_nombre':'','pagina':'','categoria':'Control visual',
                     'prioridad':'INFORMATIVA','hallazgo':p,
                     'accion':'Revisar visualmente las celdas dudosas, priorizando las filas detalladas arriba.'})
    out=pd.DataFrame(rows,columns=COLUMNAS)
    if not out.empty:
        out=out.sort_values(by=['archivo','prioridad','pagina','trabajador_rut'],kind='stable').reset_index(drop=True)
    return out
