"""Extracción conservadora de certificados INDIVIDUALES de pago PREVIRED.

Un certificado no es el anexo de días de remuneración. Se extraen solo hechos
observables: RUT, empleador, mes, base repetida explícitamente en tabla.
Las cifras son provisionales ante OCR; jamás se infieren días desde la base.
"""
from __future__ import annotations
import io
import re
from collections import Counter
from typing import Optional

import fitz
import pandas as pd
from PIL import Image
from previred_pdf import _rut_ok, _rut_clean, RUT

MESES = {
    "ENERO":1,"FEBRERO":2,"MARZO":3,"ABRIL":4,"MAYO":5,"JUNIO":6,
    "JULIO":7,"AGOSTO":8,"SEPTIEMBRE":9,"SETIEMBRE":9,"OCTUBRE":10,
    "NOVIEMBRE":11,"DICIEMBRE":12,
}


def _leer_ocr(pagina: fitz.Page) -> str:
    import pytesseract
    pix=pagina.get_pixmap(matrix=fitz.Matrix(1.65,1.65), colorspace=fitz.csGRAY, alpha=False)
    im=Image.frombytes("L", (pix.width,pix.height),pix.samples)
    return pytesseract.image_to_string(im,lang="spa+eng",config="--psm 11")


def extraer_certificado_previred(contenido: bytes, nombre: str) -> Optional[pd.DataFrame]:
    with fitz.open(stream=contenido,filetype="pdf") as doc:
        if not doc.page_count or doc.page_count > 80:
            return None
        primera = doc[0].get_text("text")
        if len(primera.strip())<80:
            primera=_leer_ocr(doc[0])
        if not re.search(r"certificado\s+de\s+pagos?\s+de\s+cotizaciones\s+previsionales",primera,re.I):
            return None
        textos=[primera]
        for pagina in list(doc)[1:]:
            txt=pagina.get_text("text")
            textos.append(txt if len(txt.strip())>80 else _leer_ocr(pagina))

    datos=[]
    for n,tx in enumerate(textos,1):
        txt=' '.join(tx.split()).replace(',', '.')
        # El certificado identifica a su titular; esta fuente no reporta días.
        m=re.search(r"(?:Sr\.?\s*\(?a\)?\s*|Sra\.?\s*)\s*(.{7,105}?),?\s+Rut\s*:?\s*("+RUT+r")",txt,re.I)
        if not m:
            # Nunca convertir el RUT del empleador en el del trabajador.
            continue
        trabajador=m.group(1).strip(" .,")
        rut=_rut_clean(m.group(2))
        if not _rut_ok(rut):continue
        empleador=re.search(r"por su empleador\s+(.{5,100}?)[,.]\s*Rut\s*:?\s*("+RUT+r")",txt,re.I)
        razon=empleador.group(1).strip() if empleador else ''
        rut_emp=_rut_clean(empleador.group(2)) if empleador else ''
        if not _rut_ok(rut_emp):rut_emp=''
        periodos=[]
        for palabra,mes in MESES.items():
            if re.search(r"\b"+palabra+r"\s+(20\d\d)\b",txt,re.I):
                a=int(re.search(r"\b"+palabra+r"\s+(20\d\d)\b",txt,re.I).group(1))
                periodos.append(f"{a}-{mes:02d}")
        periodo=Counter(periodos).most_common(1)[0][0] if periodos else ''
        # Extrae únicamente bases en columna REMUNERACIÓN IMPONIBLE, nunca
        # total a pagar. Exige que el mismo valor aparezca al menos dos veces.
        bases=[]
        for raw in re.findall(r"\$\s*([1-9]\d{0,2}[.,]\d{3})\b",txt):
            num=int(raw.replace('.','').replace(',',''))
            if num>=1000:bases.append(num)
        comunes=Counter(bases).most_common()
        base=comunes[0][0] if comunes and comunes[0][1]>=2 else None
        inst=txt.upper()
        mutual=('IST' if 'INSTITUTO DE SEGURIDAD DEL TRABAJO' in inst else
                'ISL' if 'INSTITUTO DE SEGURIDAD LABORAL' in inst else
                'ACHS' if 'ASOCIACION CHILENA DE SEGURIDAD' in inst else
                'Mutual de Seguridad' if 'MUTUAL DE SEGURIDAD' in inst else '')
        obs=["Certificado individual PREVIRED: no informa días realmente pagados; requiere anexo de detalle o asistencia",
             "OCR de certificado: montos y RUT deben cotejarse con el PDF original"]
        if base is None:obs.append("Base imponible sin 2 lecturas coincidentes: no se asigna valor")
        if not rut_emp:obs.append("RUT del empleador no confirmado por OCR")
        if not mutual:obs.append("Mutual no identificada en certificado")
        datos.append({
            'periodo_remuneracion':periodo,
            'contratista_nombre':razon.upper(), 'contratista_rut':rut_emp,
            'trabajador_nombre':trabajador.upper(),'trabajador_rut':rut,
            'dias_reales_pagados':None,
            'renta_imponible_salud':base if 'FONASA' in inst or 'ISAPRE' in inst else None,
            'renta_imponible_afp':base if 'COTIZACION OBLIGATORIA' in inst or 'AFP ' in inst else None,
            'renta_imponible_mutual':base if mutual else None,
            'mutual_adherida':mutual,
            '__hoja_origen':f'p.{n}','__conflicto_pdf':'; '.join(obs),
            '__cobertura_seguro_social':('PRESENTE_SS' if 'SEGURO SOCIAL' in inst else 'SIN_COMPROBANTE_SS'),
            '__renta_seguro_social':base if 'SEGURO SOCIAL' in inst else None,
        })
    if not datos:
        raise ValueError(f"{nombre}: certificado PREVIRED detectado, pero el OCR no verificó RUT de titulares; revisión manual")
    # Un documento puede incluir reimpresiones de certificados idénticos.
    # Se identifican por RUT+período, nunca se SUMAN montos de dos certificados.
    registros={}
    for fila in datos:
        clave=(fila['trabajador_rut'],fila['periodo_remuneracion'])
        if clave not in registros:
            registros[clave]=fila
        else:
            previo=registros[clave]
            previo['__hoja_origen']+=', '+fila['__hoja_origen']
            previo['__conflicto_pdf']+='; Titular repetido en otra página; se conserva una sola persona (verificar que sea duplicado)'
    ruts_empresa={x['contratista_rut'] for x in registros.values() if _rut_ok(x['contratista_rut'])}
    if len(ruts_empresa)==1:
        rut_confirmado=next(iter(ruts_empresa))
        for fila in registros.values():
            if not fila['contratista_rut']:
                fila['contratista_rut']=rut_confirmado
                fila['__conflicto_pdf']+='; RUT empleador recuperado de otra página del mismo documento'
    return pd.DataFrame(registros.values())
