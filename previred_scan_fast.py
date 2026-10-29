"""OCR rápido y verificable para PDFs PREVIRED ESCANEADOS con tablas rectangulares.

Diseño: identificar retículas sin OCR -> extraer únicamente columnas RUT, días,
base imponible y nombre de detalle -> OCR por lotes -> validar DV y cuadratura.
No OCR a portadas, firmas, descripciones, cotizaciones pagadas ni totales sueltos.
Se acepta el resultado solo si el PDF coincide con los perfiles geométricos
reconocidos; los datos dudosos quedan en revisión y no se inventan.
"""
from __future__ import annotations

import io
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from PIL import Image, ImageOps

try:
    from PIL import ImageFilter
except Exception:  # pragma: no cover
    ImageFilter = None

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None

from previred_pdf import _rut_clean, _rut_ok

# Cantidad de límites verticales de los cuatro formularios PREVIRED escaneados.
# No corresponde a cantidad de trabajadores ni a porcentaje de cotizaciones.
RETICULAS = {10: "mutual", 14: "seguro_social", 16: "afp", 17: "salud"}
RUT_EN_TEXTO = re.compile(r"\d{1,2}[. ]?\d{3}[. ]?\d{3}[- ]?[0-9K]", re.I)
NUMERO = re.compile(r"\d{1,3}(?:\.\d{3})+|\d+", re.I)


def _preprocesar_imagen_ocr(imagen: Image.Image, escala: float = 2.2) -> Image.Image:
    """Mejora la legibilidad para OCR en escaneados, fotos y PDFs con columnas.

    - aumenta resolución
    - mejora contraste y nitidez
    - corrige orientación y borde por inspección visual
    - aplica binarización adaptativa cuando OpenCV está disponible
    """
    img = imagen.convert("L")
    img = ImageOps.autocontrast(img)
    if ImageFilter is not None:
        img = img.filter(ImageFilter.SHARPEN)
    nuevo = (max(1, int(img.width * escala)), max(1, int(img.height * escala)))
    img = img.resize(nuevo, Image.Resampling.BICUBIC)
    if cv2 is not None:
        arr = np.asarray(img)
        gray = arr.astype(np.uint8)
        if gray.ndim == 2:
            g = gray
        else:
            g = cv2.cvtColor(gray, cv2.COLOR_RGB2GRAY)
        # Binarización adaptativa para que las letras se separen del fondo y la retícula.
        bw = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 12)
        # invertir para texto negro sobre fondo claro (más legible para Tesseract)
        bw = cv2.bitwise_not(bw)
        img = Image.fromarray(bw)
        return ImageOps.autocontrast(img)
    # Fallback conservador: binarización simple para entornos sin cv2.
    threshold = 180
    return img.point(lambda p: 255 if p > threshold else 0)


def _ocr_imagen_robusta(imagen: Image.Image, lang: str = "spa+eng") -> str:
    """OCR robusto usando varios modos de segmentación y el texto más completo."""
    try:
        import pytesseract
    except Exception:
        return ""
    configuraciones = [
        "--oem 1 --psm 6 -c preserve_interword_spaces=1",
        "--oem 1 --psm 11 -c preserve_interword_spaces=1",
        "--oem 1 --psm 3 -c preserve_interword_spaces=1",
    ]
    textos = []
    for cfg in configuraciones:
        txt = pytesseract.image_to_string(imagen, lang=lang, config=cfg, timeout=12)
        if txt and txt.strip():
            textos.append(txt)
    if not textos:
        return ""
    return max(textos, key=lambda t: len(re.sub(r"\s+", "", t)))


def _rotar_y_ocr_optimo(imagen: Image.Image) -> tuple[Image.Image, str]:
    """Prueba rotaciones y conserva la lectura más completa y útil para fotos o páginas giradas."""
    mejor_txt = ""
    mejor_score = -1
    mejor = imagen
    for angulo in (0, 90, 180, 270):
        rot = imagen.rotate(angulo, expand=True)
        txt = _ocr_imagen_robusta(rot)
        score = len(re.sub(r"\s+", "", txt or ""))
        if score > mejor_score:
            mejor = rot
            mejor_txt = txt
            mejor_score = score
    return mejor, mejor_txt


@dataclass
class Plantilla:
    tipo: str
    numero_pagina: int
    foto: Image.Image
    limites_x: list[int]
    filas_y: list[tuple[int, int]]


def _grupos(ids: np.ndarray) -> list[list[int]]:
    grupos: list[list[int]] = []
    for item in ids:
        v = int(item)
        if not grupos or v > grupos[-1][-1] + 1:
            grupos.append([v])
        else:
            grupos[-1].append(v)
    return grupos


def _lineas_horizontales(gris: np.ndarray) -> list[int]:
    h, w = gris.shape
    x1, x2 = round(.062*w), round(.936*w)
    cuenta = (gris[:, x1:x2] < 158).sum(axis=1)
    idx = np.flatnonzero((cuenta > (x2-x1)*.76) & (np.arange(h) < .69*h) & (np.arange(h) > .12*h))
    return [int(np.median(g)) for g in _grupos(idx)]


def _runs_repetidos(ys: list[int], h: int) -> list[int]:
    # Elegir el paso más frecuente de las filas, no confundir una fila de
    # TOTAL (18-25 px) con movimientos (13-16 px en esta plantilla).
    if len(ys) < 3:
        return []
    runs = []
    for paso in range(max(8,int(h*.010)),min(22,int(h*.021)) + 1):
        for i in range(len(ys)-1):
            r=[ys[i]]
            for j in range(i+1,len(ys)):
                if abs((ys[j]-ys[j-1])-paso) <= 1.7:
                    r.append(ys[j])
                else:
                    break
            if len(r)>=3:
                runs.append(r)
    return max(runs,key=lambda g:(len(g),-g[0]),default=[])


def _lineas_verticales(gris: np.ndarray, run: list[int], incluir_cabecera: bool = False) -> list[int]:
    h,w=gris.shape
    if not run:
        return []
    paso=int(np.median(np.diff(run)))
    y1=run[0]-paso+2 if incluir_cabecera else run[0]+2
    y2=min(h,run[-1]-1)
    if y2-y1<20:
        return []
    # Diferenciar borde continuo de letras que aparecen en la misma columna:
    # un borde vertical recorre TODAS las filas consecutivamente.
    mascara=gris[y1:y2,:]<180
    actual=np.zeros(w,dtype=np.int32)
    maxima=np.zeros(w,dtype=np.int32)
    for linea in mascara:
        actual=np.where(linea,actual+1,0)
        maxima=np.maximum(maxima,actual)
    indices=np.flatnonzero((maxima>(y2-y1)*.65)&(np.arange(w)>.018*w)&(np.arange(w)<.975*w))
    return [int(np.median(g)) for g in _grupos(indices)]


def _preparar(imagen: Image.Image, pagina: int) -> Plantilla|None:
    g=np.array(imagen.convert('L'))
    h,w=g.shape
    if w < h:
        return None
    run = _runs_repetidos(_lineas_horizontales(g),h)
    if not run:
        return None
    verticales = _lineas_verticales(g,run)
    tipo = RETICULAS.get(len(verticales))
    if not tipo:
        verticales = _lineas_verticales(g,run,incluir_cabecera=True)
        tipo = RETICULAS.get(len(verticales))
    if not tipo:
        return None
    step = int(round(np.median(np.diff(run))))
    if not 10 <= step <= h*.04:
        return None
    if tipo=='afp':
        # La primera fila AFP se encuentra ANTES de la primera regla larga,
        # porque la cabecera fusionada no termina en línea horizontal completa.
        run=[run[0]-step]+run
    elif tipo=='seguro_social':
        # En este formulario, la última fila precede a un total fusionado;
        # su separador puede no atravesar todas las columnas.
        run=run + [run[-1]+step]
    filas=[(a,b) for a,b in zip(run[:-1],run[1:]) if b-a>=8]
    if len(filas)>250:
        return None
    return Plantilla(tipo,pagina,imagen,verticales,filas)


def _extraer_plantillas(contenido: bytes) -> list[Plantilla]:
    import fitz
    doc=fitz.open(stream=contenido,filetype='pdf')
    if doc.page_count>120:
        raise ValueError('El PDF supera el límite de 120 páginas de OCR en línea; divídelo para proteger la memoria.')
    perfiles=[]
    for i,page in enumerate(doc):
        if i >= 6 and not perfiles:
            return []
        if i % 2 == 0 and i > 0:
            continue
        if len(page.get_text().strip()) > 120:
            return []
        pix=page.get_pixmap(matrix=fitz.Matrix(1.8,1.8),colorspace=fitz.csGRAY,alpha=False)
        im=Image.frombytes('L',(pix.width,pix.height),pix.samples)
        im = _preprocesar_imagen_ocr(im, escala=2.0)
        candidatas=[im]
        if im.height>im.width:
            candidatas=[im.transpose(Image.Transpose.ROTATE_270),im.transpose(Image.Transpose.ROTATE_90)]
        opciones=[]
        for candidata in candidatas:
            perfil=_preparar(candidata,i+1)
            if perfil is not None:
                opciones.append(perfil)
                continue
            for angulo in (-0.5,0.5,-1.0,1.0):
                perfil=_preparar(candidata.rotate(angulo,fillcolor=255),i+1)
                if perfil is not None:
                    opciones.append(perfil)
                    break
        hallado=min(opciones,key=lambda x:x.filas_y[0][0]/x.foto.height) if opciones else None
        if hallado:
            perfiles.append(hallado)
    return perfiles


def _roi(plantilla:Plantilla, columna:str, fila:int) -> Image.Image|None:
    x=plantilla.limites_x
    y1,y2=plantilla.filas_y[fila]
    tipo=plantilla.tipo
    cols={
        'salud': {'rut':(1,2),'nombre':(2,5),'dias':(5,6),'renta':(7,8)},
        'seguro_social': {'rut':(1,2),'nombre':(2,3),'dias':(9,10),'renta':(3,4)},
        'mutual': {'rut':(0,1),'nombre':(1,4),'renta':(4,5)},
        'afp': {'rut':(0,1),'nombre':(1,2),'renta':(2,3)},
    }
    if columna not in cols[tipo]:
        return None
    left,right=cols[tipo][columna]
    if right>=len(x):
        return None
    a,b=x[left]+2,x[right]-2
    if b-a<8 or y2-y1<8:
        return None
    return plantilla.foto.crop((a,y1+2,b,y2-1))


def _montar_celdas(plantilla:Plantilla, columnas:list[str]) -> tuple[Image.Image,list[tuple[int,int]]]:
    """Concatenar solo datos esenciales: una llamada a Tesseract por página.

    El texto está posicionado en columnas sintéticas con coordenadas conocidas.
    Se elimina la retícula y se triplica la resolución de cada recorte pequeño.
    """
    amplitudes={'rut':240,'dias':96,'renta':192,'nombre':680}
    offset=0
    posiciones=[]
    for col in columnas:
        posiciones.append((offset,offset+amplitudes[col]));offset+=amplitudes[col]+24
    imagen=Image.new('L',(offset+18,len(plantilla.filas_y)*56+8),255)
    for fila in range(len(plantilla.filas_y)):
        for col,(left,right) in zip(columnas,posiciones):
            celda=_roi(plantilla,col,fila)
            if celda is None:
                continue
            maxw=right-left-12
            escala=3.0 if col!='nombre' else 2.4
            ww=min(int(celda.width*escala),maxw)
            hh=min(int(celda.height*escala),44)
            if ww<3 or hh<3:
                continue
            celda=ImageOps.autocontrast(celda.resize((ww,hh),Image.Resampling.BICUBIC))
            celda=celda.point(lambda v: 0 if v < 166 else 255)
            imagen.paste(celda,(left+6,fila*56+6))
    return imagen,posiciones


def _ocr_lote(plantilla:Plantilla,columnas:list[str]) -> tuple[list[dict[str,str]], list[str]]:
    import pytesseract
    imagen,posiciones=_montar_celdas(plantilla,columnas)
    imagen = _preprocesar_imagen_ocr(imagen, escala=1.6)
    # Texto solo: una llamada a Tesseract por página. PSM 6 evita el modo de
    # detección global de documentos que es lento para tablas de muchas filas.
    df=pytesseract.image_to_data(
        imagen,lang='spa+eng',config='--oem 1 --psm 6 -c preserve_interword_spaces=1',
        output_type=pytesseract.Output.DATAFRAME,
    )
    datos=[defaultdict(str) for _ in plantilla.filas_y]
    for _,p in df.iterrows():
        if pd.isna(p.get('text')) or float(p.get('conf',-1))<12:
            continue
        t=str(p['text']).strip()
        if not t:
            continue
        fila=int((float(p['top'])+float(p['height'])/2)//56)
        if not 0<=fila<len(datos):
            continue
        xc=float(p['left'])+float(p['width'])/2
        for col,(x1,x2) in zip(columnas,posiciones):
            if x1<=xc<x2:
                datos[fila][col]+=(' ' if datos[fila][col] else '')+t
                break
    return [dict(d) for d in datos],[]


def _ocr_columna_digitos(plantilla:Plantilla) -> list[dict[str,str]]:
    """Tesseract con diccionario de dígitos: más rápido y preciso que OCR general.

    Verificar RUT con DV, y bases entre instituciones, antes de usar la cifra.
    """
    import pytesseract
    cols=['rut']+(['dias'] if plantilla.tipo in ('salud','seguro_social') else [])+['renta']
    imagen,posiciones=_montar_celdas(plantilla,cols)
    imagen = _preprocesar_imagen_ocr(imagen, escala=1.8)
    df=pytesseract.image_to_data(imagen,lang='spa+eng',config='--oem 1 --psm 6 -c tessedit_char_whitelist=0123456789.-K -c preserve_interword_spaces=1',output_type=pytesseract.Output.DATAFRAME)
    datos=[defaultdict(str) for _ in plantilla.filas_y]
    for _,p in df.iterrows():
        if pd.isna(p.get('text')):
            continue
        t=str(p['text']).strip()
        if not t:
            continue
        fila=int((float(p['top'])+float(p['height'])/2)//56)
        if not 0<=fila<len(datos):
            continue
        xc=float(p['left'])+float(p['width'])/2
        for col,(a,b) in zip(cols,posiciones):
            if a<=xc<b:
                datos[fila][col]+=(' ' if datos[fila][col] else '')+t
                break
    return [dict(x) for x in datos]


def _ocr_celda(plantilla: Plantilla, columna: str, fila: int, solo_digitos: bool = True) -> str:
    """Reintento preciso SOLO si el lote OCR da datos dudosos."""
    import pytesseract
    c=_roi(plantilla,columna,fila)
    if c is None:
        return ''
    c = _preprocesar_imagen_ocr(c, escala=4.0)
    c = _rotar_y_ocr_optimo(c)[0]
    conf='--oem 1 --psm 7'
    if solo_digitos:
        conf+=' -c tessedit_char_whitelist=0123456789.-K'
    return pytesseract.image_to_string(c,lang='spa+eng',config=conf).strip()


def _nombre_salud(plantilla: Plantilla, fila: int) -> str:
    """Lee el nombre sin las dos divisiones de celda que OCR confunde con I/L."""
    import pytesseract
    celda=_roi(plantilla,'nombre',fila)
    if celda is None:return ''
    celda = _preprocesar_imagen_ocr(celda, escala=3.0)
    arr=np.array(celda.convert('L'))
    if plantilla.tipo=='salud' and len(plantilla.limites_x)>5:
        borde_izq=plantilla.limites_x[2]+2
        for idx in (3,4):
            xc=plantilla.limites_x[idx]-borde_izq
            arr[:,max(0,xc-2):min(arr.shape[1],xc+3)]=255
    celda=Image.fromarray(arr)
    txt=_ocr_imagen_robusta(celda, lang='spa+eng')
    txt=re.sub(r'[^A-ZÁÉÍÓÚÑÜa-záéíóúñü ]',' ',txt)
    return ' '.join(txt.upper().split())


def _rut(texto: str) -> str:
    v=_rut_clean(texto)
    if _rut_ok(v):
        return v
    visible = str(texto).upper().translate(str.maketrans({'B':'8','O':'0','I':'1','L':'1','S':'5'}))
    candidato = _rut_clean(visible)
    if candidato != v and _rut_ok(candidato):
        return candidato
    return ''


def _sumar_dias_si_completos(filas: list[tuple]) -> int | None:
    """Una fila sin días legibles hace desconocido el total, NO cero días."""
    if not filas or any(len(f)<4 or f[3] is None for f in filas):
        return None
    return sum(int(f[3]) for f in filas)


def _renta(texto: str) -> int|None:
    if str(texto).lstrip().startswith('-'):
        return None
    t=re.sub(r'[^\d.]','',texto)
    if not t:
        return None
    if re.fullmatch(r'\d{1,3}(?:\.\d{3})+|\d+',t):
        n=int(t.replace('.',''))
        return n if n>=0 else None
    return None


def _ocr_num_con_escala(plantilla: Plantilla, columna: str, fila: int, escala: float = 2.0) -> int|None:
    import pytesseract
    recorte=_roi(plantilla,columna,fila)
    if recorte is None:
        return None
    recorte = _preprocesar_imagen_ocr(recorte, escala=escala)
    txt=_ocr_imagen_robusta(recorte, lang='spa+eng')
    return _renta(txt)


def _seleccionar_valor(candidatos: list[int|None], esperado: int|None) -> tuple[int|None,bool]:
    """Nunca usar el valor de otra institución como sustituto de un OCR ausente."""
    valores=[v for v in candidatos if v is not None]
    if not valores:
        return None,True
    conteo={v:valores.count(v) for v in set(valores)}
    elegido=max(conteo,key=lambda n:(conteo[n],n==esperado))
    if esperado is not None and esperado in conteo and conteo[esperado]>=1:
        elegido=esperado
    return elegido,len(conteo)>1


def _contexto(documento: bytes, plantillas:list[Plantilla]) -> tuple[str,str,str,str]:
    """Lee solamente zonas de cabecera en lugar de las portadas completas."""
    import pytesseract
    salud=next((p for p in plantillas if p.tipo=='salud'),None)
    uno=salud or plantillas[0]
    w,h=uno.foto.size
    if salud:
        imagen=uno.foto.crop((int(.12*w),int(.17*h),int(.53*w),int(.27*h)))
        imagen = _preprocesar_imagen_ocr(imagen, escala=2.2)
        texto=_ocr_imagen_robusta(imagen, lang='spa+eng')
        lineas=[l.strip() for l in texto.splitlines() if len(l.strip())>3]
        nombre=next((re.sub(r'^\s*(?:RE\s+)?','',l,flags=re.I).strip(' ._-') for l in lineas if len(l)>12 and not RUT_EN_TEXTO.search(l) and len(l.split())>=2), '')
        rut_match=RUT_EN_TEXTO.search(texto) or re.search(r'\d{1,2}[.]?\d{3}[.]?[\dBOSIl]{3}\s*[-]\s*[0-9K]',texto,re.I)
        empleador_rut=_rut(rut_match.group()) if rut_match else ''
        if not empleador_rut:
            cr=uno.foto.crop((int(.13*w),int(.19*h),int(.54*w),int(.27*h)))
            cr = _preprocesar_imagen_ocr(cr, escala=2.2)
            extra=_ocr_imagen_robusta(cr, lang='spa+eng')
            rut_match=RUT_EN_TEXTO.search(extra) or re.search(r'\d{1,2}[.]?\d{3}[.]?[\dBOSIl]{3}\s*[-]\s*[0-9K]',extra,re.I)
            empleador_rut=_rut(rut_match.group()) if rut_match else ''
    else:
        nombre='';empleador_rut=''
    cab=next((p for p in plantillas if p.tipo in ('mutual','afp','seguro_social')),uno)
    w,h=cab.foto.size
    crop=cab.foto.crop((int(.52*w),int(.05*h),int(.72*w),int(.10*h)))
    crop = _preprocesar_imagen_ocr(crop, escala=2.5)
    ts=_ocr_imagen_robusta(crop, lang='spa+eng')
    m=re.search(r'(0?[1-9]|1[0-2])\s*[/.-]\s*(20\d{2})',ts)
    if m:
        periodo=f'{m.group(2)}-{int(m.group(1)):02d}'
    elif salud:
        cr=salud.foto.crop((int(.8*w),int(.19*h),int(.95*w),int(.24*h)))
        cr = _preprocesar_imagen_ocr(cr, escala=2.5)
        ts=_ocr_imagen_robusta(cr, lang='spa+eng')
        m=re.search(r'(0?[1-9]|1[0-2])\s+20(\d{2})',ts)
        periodo=f'20{m.group(2)}-{int(m.group(1)):02d}' if m else ''
    else: periodo=''
    if not periodo:
        pag_afp=next((p for p in plantillas if p.tipo=='afp'), None)
        if pag_afp is not None:
            aw,ah=pag_afp.foto.size
            period_crop=pag_afp.foto.crop((int(.54*aw),int(.05*ah),int(.79*aw),int(.12*ah)))
            period_crop = _preprocesar_imagen_ocr(period_crop, escala=2.5)
            t=_ocr_imagen_robusta(period_crop, lang='spa+eng')
            mm=re.search(r'(?<!\d)(0?[1-9]|1[0-2])\s*[/.-]\s*(20\d{2})',t)
            if mm:
                periodo=f'{mm.group(2)}-{int(mm.group(1)):02d}'
    mutual = ''
    pagina_mutual = next((p for p in plantillas if p.tipo == 'mutual'), None)
    if pagina_mutual is not None:
        from previred_pdf import _mutual_name
        mw,mh=pagina_mutual.foto.size
        cabecera=pagina_mutual.foto.crop((int(.01*mw),int(.06*mh),int(.5*mw),int(.23*mh)))
        cabecera = _preprocesar_imagen_ocr(cabecera, escala=2.4)
        texto_m=_ocr_imagen_robusta(cabecera, lang='spa+eng')
        mutual=_mutual_name(texto_m)
    nombre=re.sub(r'^.*?(?:NOMBRE|JMBRE|BRE)\s+', '', nombre, flags=re.I)
    nombre=re.sub(r'^\L\s+O\s+','',nombre,flags=re.I).strip(' ._-')
    return nombre.upper(), empleador_rut, periodo, mutual


def _firma_celda_dias(plantilla: Plantilla, fila: int):
    imagen = _roi(plantilla, 'dias', fila)
    if imagen is None:
        return None
    gris = np.array(ImageOps.autocontrast(imagen.resize((imagen.width * 6, imagen.height * 6), Image.Resampling.BICUBIC)))
    puntos = gris < 165
    ys, xs = np.where(puntos)
    if len(xs) < 5:
        return None
    contenido = Image.fromarray(np.uint8(puntos[ys.min():ys.max()+1, xs.min():xs.max()+1])*255)
    return np.array(contenido.resize((25,35), Image.Resampling.NEAREST)) > 127


def _reconciliar_dias_ss(datos: dict, perfiles: list[Plantilla]) -> dict[str, str]:
    pss = {p.numero_pagina: p for p in perfiles if p.tipo == 'seguro_social'}
    resueltos = {}
    anclas = []
    for rut, instituciones in datos.items():
        filas_salud = instituciones.get('salud', [])
        filas_ss = instituciones.get('seguro_social', [])
        if len(filas_ss) != 1 or not filas_salud:
            continue
        dia_salud = _sumar_dias_si_completos(filas_salud)
        dia_ss = _sumar_dias_si_completos(filas_ss)
        if dia_salud is None or dia_ss is None:
            continue
        pagina, fila, renta_ss, _ = filas_ss[0]
        plantilla = pss.get(pagina)
        if plantilla is None:
            continue
        if dia_salud == dia_ss and 0 <= dia_ss <= 31:
            firma = _firma_celda_dias(plantilla, fila)
            if firma is not None:
                anclas.append((pagina, dia_ss, firma))
    for rut, instituciones in datos.items():
        filas_salud = instituciones.get('salud', [])
        filas_ss = instituciones.get('seguro_social', [])
        if len(filas_ss) != 1 or not filas_salud:
            continue
        dias_esperados = _sumar_dias_si_completos(filas_salud)
        dias_ss = _sumar_dias_si_completos(filas_ss)
        if dias_esperados is None or dias_ss is None or dias_ss == dias_esperados:
            continue
        pagina, fila, renta, _ = filas_ss[0]
        plantilla = pss.get(pagina)
        if plantilla is None:
            continue
        renta_salud = sum(x[2] for x in filas_salud)
        if renta != renta_salud:
            continue
        lecturas = [_ocr_num_con_escala(plantilla, 'dias', fila, esc) for esc in (4.0, 5.0)]
        if dias_esperados in lecturas and (dias_esperados not in (0, 1) or lecturas.count(dias_esperados) == 2):
            filas_ss[0] = (pagina, fila, renta, dias_esperados)
            resueltos[rut] = f'Días SS {dias_ss}→{dias_esperados}: relectura de celda p.{pagina} y Salud'
            continue
        firma = _firma_celda_dias(plantilla, fila)
        if firma is None:
            continue
        distancias = []
        for pg, dia, ancla in anclas:
            if pg != pagina:
                continue
            distancias.append((float(np.mean(firma != ancla)),dia))
        mismo = sorted(dist for dist,valor in distancias if valor == dias_esperados)
        distinto = sorted(dist for dist,valor in distancias if valor != dias_esperados)
        if len(mismo)>=2 and mismo[0] < .12 and mismo[1] < .13 and (not distinto or mismo[0]+.025 < distinto[0]):
            filas_ss[0] = (pagina, fila, renta, dias_esperados)
            resueltos[rut] = f'Días SS {dias_ss}→{dias_esperados}: 2 celdas ancla corroboradas en p.{pagina}'
    return resueltos


def extraer_previred_escaneado_rapido(contenido:bytes,nombre:str) -> pd.DataFrame|None:
    """Extractor opcional para PREVIRED escaneado, no sustituye lectura digital.

    Devuelve None si el documento no coincide con el perfil de tablas; entonces
    entra el procesador estándar. Nunca emite 'CONFORME' sin respaldo total.
    """
    perfiles=_extraer_plantillas(contenido)
    tipos={p.tipo for p in perfiles}
    if not perfiles or len(tipos)<2 or 'salud' not in tipos:
        return None
    nombres_empleador,rut_empleador,periodo,mutual=_contexto(contenido,perfiles)
    datos:dict[str,dict[str,list[tuple[int,int,int|None,int|None]]]]=defaultdict(lambda:defaultdict(list))
    nombres: dict[str,str] = {}
    invalidas: list[str] = []
    incidentes_por_rut: dict[str, list[str]] = defaultdict(list)
    def advertir(msg: str, rut: str = "") -> None:
        (incidentes_por_rut[rut] if rut else invalidas).append(msg)

    salud_por_posicion: list[tuple[str,int|None,int|None]] = []
    orden=sorted(perfiles,key=lambda p:0 if p.tipo=='salud' else 1)
    for p in orden:
        lote=_ocr_columna_digitos(p)
        nfilas=len(p.filas_y)
        puede_alinear=False
        if p.tipo in ('mutual','seguro_social') and len(salud_por_posicion)==nfilas:
            anclas=[(j,_rut(v.get('rut',''))) for j,v in enumerate(lote) if _rut(v.get('rut',''))]
            correctas=sum(1 for j,r in anclas if r==salud_por_posicion[j][0])
            puede_alinear=(correctas>=3 and correctas==len(anclas))
        for j,v in enumerate(lote):
            if not v.get('rut') and not v.get('renta') and not v.get('dias'):
                if p.tipo=='salud':
                    salud_por_posicion.append(('',None,None))
                continue
            rut=_rut(v.get('rut',''))
            if not rut:
                rut=_rut(_ocr_celda(p,'rut',j))
            if not rut and puede_alinear:
                rut=salud_por_posicion[j][0]
                if rut:
                    advertir(f'p.{p.numero_pagina}, fila {j+1}: RUT alineado con Salud; cotejar visualmente', rut)
            esperado=None
            if p.tipo!='salud':
                val_salud=datos.get(rut,{}).get('salud',[])
                if val_salud:esperado=sum(row[2] for row in val_salud)
            cifra_lote=_renta(v.get('renta',''))
            if esperado is not None and cifra_lote==esperado:
                candidatos=[cifra_lote]
            else:
                cifra_2x=_ocr_num_con_escala(p,'renta',j,2.0)
                candidatos=[cifra_lote,cifra_2x]
                if (cifra_lote is None or cifra_2x is None or cifra_lote!=cifra_2x) and (esperado is None or esperado not in candidatos):
                    candidatos.append(_ocr_num_con_escala(p,'renta',j,3.0))
            renta,contradiccion=_seleccionar_valor(candidatos,esperado)
            if not rut and p.tipo=='seguro_social':
                pass
            if p.tipo == 'salud' and renta is not None and 0 < renta < 100 and 0 in candidatos:
                renta=0
                contradiccion=True
                advertir(f'p.{p.numero_pagina}, fila {j+1}: base pequeña ambigua; se seleccionó 0 observado en OCR', rut)
            if contradiccion and any(c is not None for c in candidatos):
                advertir(f'p.{p.numero_pagina}, fila {j+1}: lecturas alternativas de renta {sorted(set(n for n in candidatos if n is not None))}; cotejar', rut)
            dias=None
            if p.tipo=='salud':
                dias=_ocr_num_con_escala(p,'dias',j,2.0)
                if dias is None:
                    dias=_ocr_num_con_escala(p,'dias',j,5.0)
                if dias is None or dias==1:
                    alt=_ocr_num_con_escala(p,'dias',j,5.0)
                    if alt is not None and 0<=alt<=31:
                        dias=alt
                if dias is None:
                    dias=_renta(v.get('dias',''))
                if dias is None or dias>31:
                    advertir(f'p.{p.numero_pagina}, fila {j+1}: días Salud no verificados', rut)
                    dias=None
            elif p.tipo=='seguro_social':
                dias=_ocr_num_con_escala(p,'dias',j,1.5)
                if dias is None or dias>31:
                    dias=_renta(v.get('dias',''))
                if dias is not None and dias>31:
                    dias=None
            if p.tipo=='salud':
                salud_por_posicion.append((rut,renta,dias))
            if not rut:
                if re.search(r'\d{5,}',v.get('rut','')):
                    advertir(f'p.{p.numero_pagina}, fila {j+1}: RUT no reconocido')
                continue
            if renta is None:
                advertir(f'p.{p.numero_pagina}, fila {j+1}: base imponible no reconocida', rut)
                continue
            if dias is not None and not 0<=dias<=31:
                dias=None
            datos[rut][p.tipo].append((p.numero_pagina,j,renta,dias))
            if p.tipo=='salud':
                nombre=_nombre_salud(p,j)
                if nombre:nombres[rut]=nombre
    if not datos:
        raise ValueError('OCR escaneado detectó las tablas, pero no extrajo RUT y rentas verificables.')
    descartados=[]
    for rut, por_fuente_rut in list(datos.items()):
        fuentes_positivas={t for t, lineas in por_fuente_rut.items() if any(row[2]>0 for row in lineas)}
        if len(fuentes_positivas)<2:
            descartados.append(rut)
            del datos[rut]
    if descartados:
        invalidas.append(f'{len(descartados)} RUT OCR sin dos fuentes con renta positiva: excluidos de matriz; verificar')
    if not datos:
        raise ValueError('OCR detectó posibles RUT, pero ninguno tiene bases positivas respaldadas por dos instituciones.')
    dias_resueltos = _reconciliar_dias_ss(datos, perfiles)
    fuentes={t for t in tipos if t in ('salud','afp','mutual','seguro_social')}
    totales={t:sum(row[2] for por in datos.values() for row in por.get(t,[])) for t in fuentes}
    tam_fuentes={t:len({rut for rut,por in datos.items() if por.get(t)}) for t in fuentes}
    ruts_salud={rut for rut,fu in datos.items() if fu.get('salud')}
    ruts_consolidado=set(datos)
    if ruts_salud!=ruts_consolidado:
        invalidas.append('No coinciden los RUT entre Salud y las demás instituciones')
    if 'seguro_social' in totales and totales['seguro_social']!=totales['salud']:
        invalidas.append('Total Seguro Social diferente de Salud')
    if 'afp' in totales and totales['afp']!=totales['salud']:
        invalidas.append('Total AFP diferente de Salud')
    if 'mutual' in totales and totales['mutual']!=totales['salud']:
        invalidas.append('Total Mutual diferente de Salud')
    if any(v!=tam_fuentes.get('salud') for v in tam_fuentes.values()):
        invalidas.append('No coinciden los recuentos de trabajadores entre instituciones')
    if not nombres_empleador or not rut_empleador or not periodo:
        invalidas.append('Falta información verificada de empleador o período en cabecera OCR')
    salida=[]
    for rut,fuentes_rut in sorted(datos.items()):
        obs=[]
        for t in ('salud','afp','mutual'):
            if not fuentes_rut.get(t):obs.append(f'No se obtuvo base imponible de {t.upper()} para este trabajador')
        def base(tipo):
            vals=fuentes_rut.get(tipo,[])
            return sum(x[2] for x in vals) if vals else None
        dia_s=_sumar_dias_si_completos(fuentes_rut.get('salud',[]))
        dia_ss=_sumar_dias_si_completos(fuentes_rut.get('seguro_social',[]))
        if dia_s is not None and dia_ss is not None and dia_s!=dia_ss:
            obs.append(f'Días no conciliados: Salud {dia_s}, Seguro Social {dia_ss}; revisar páginas de detalle')
        if any(x[3] is None for x in fuentes_rut.get('salud',[])):
            obs.append('Los días de Salud no se pudieron reconocer con fiabilidad')
        rentas=[base('salud'),base('afp'),base('mutual')]
        bases_ok = len(rentas)==3 and all(x is not None and x>0 for x in rentas) and len(set(rentas))==1
        if len({x for x in rentas if x is not None})>1:
            obs.append('Las bases imponibles extraídas son distintas entre AFP, Salud y Mutual')
        paginas=sorted({r[0] for v in fuentes_rut.values() for r in v})
        avisos=list(dict.fromkeys(incidentes_por_rut.get(rut, [])))
        informativas=[]
        for aviso in avisos:
            baja = aviso.lower()
            if bases_ok and ('lecturas alternativas de renta' in baja or 'rut alineado con salud' in baja):
                informativas.append(aviso+' [resuelto por conciliación entre fuentes]')
            else:
                obs.append(aviso)
        if rut in dias_resueltos:
            informativas.append(dias_resueltos[rut])
        if not nombres.get(rut):obs.append('No se pudo confirmar el nombre en el anexo Salud')
        if dia_s is None and dia_ss is None:
            obs.append('Ninguna fuente permitió confirmar los días remunerados')
        problemas_globales = [x for x in invalidas if any(k in x for k in (
            'RUT OCR sin dos fuentes', 'Total AFP diferente', 'Total Mutual diferente',
            'No coinciden los recuentos', 'No coinciden los RUT',
            'Falta información verificada'))]
        if problemas_globales:
            obs.append('No se comprobó la integridad de todas las filas del PDF: '+ ' | '.join(problemas_globales))
        if obs:
            obs.append('Celdas OCR aún sin corroborar; cotejar el original')
        salida.append({
            '__ocr_incidencias_documento': '',
            '__ocr_origen': 'ESCANEADO',
            '__ocr_conciliacion': 'OCR_CONCILIADO' if not obs else 'OCR_PENDIENTE',
            '__ocr_observaciones_informativas': ' | '.join(informativas),
            'periodo_remuneracion':periodo,
            'contratista_nombre':nombres_empleador,
            'contratista_rut':rut_empleador,
            'trabajador_nombre':nombres.get(rut,''),
            'trabajador_rut':rut,
            'dias_reales_pagados':dia_s if dia_s is not None else dia_ss,
            'renta_imponible_salud':base('salud'),
            'renta_imponible_afp':base('afp'),
            'renta_imponible_mutual':base('mutual'),
            'mutual_adherida':mutual,
            '__hoja_origen':', '.join(f'p.{z}' for z in paginas),
            '__conflicto_pdf':'; '.join(dict.fromkeys(obs)),
        })
    if salida:
        salida[0]['__ocr_incidencias_documento'] = ' | '.join(dict.fromkeys(invalidas))
    return pd.DataFrame(salida)
