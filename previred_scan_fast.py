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

from previred_pdf import _rut_clean, _rut_ok

# Cantidad de límites verticales de los cuatro formularios PREVIRED escaneados.
# No corresponde a cantidad de trabajadores ni a porcentaje de cotizaciones.
RETICULAS = {10: "mutual", 14: "seguro_social", 16: "afp", 17: "salud"}
RUT_EN_TEXTO = re.compile(r"\d{1,2}[. ]?\d{3}[. ]?\d{3}[- ]?[0-9K]", re.I)
NUMERO = re.compile(r"\d{1,3}(?:\.\d{3})+|\d+", re.I)


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
        # Preflight geométrico: si ni siquiera las primeras 3 páginas de
        # detalle (p.2/4/6) contienen una plantilla reconocible, no gastar
        # CPU rotando todas las demás páginas. El modo reforzado queda
        # disponible para diseños heterogéneos.
        if i >= 6 and not perfiles:
            return []
        if i % 2 == 0 and i > 0:
            # Resumen/portada: los importes individuales están en el detalle.
            continue
        # Páginas digitales deben pasar al lector de texto especializado; el OCR es el fallback.
        if len(page.get_text().strip()) > 120:
            return []
        # Render a ~1.8x; suficiente para texto escaneado sin agotar CPU.
        pix=page.get_pixmap(matrix=fitz.Matrix(1.8,1.8),colorspace=fitz.csGRAY,alpha=False)
        im=Image.frombytes('L',(pix.width,pix.height),pix.samples)
        # Si el PDF está guardado de lado se identifican las reglas tras girarlo.
        candidatas=[im]
        if im.height>im.width:
            candidatas=[im.transpose(Image.Transpose.ROTATE_270),im.transpose(Image.Transpose.ROTATE_90)]
        opciones=[]
        for candidata in candidatas:
            # Vía rápida: documentos rectos no necesitan OCR ni rotación adicional.
            perfil=_preparar(candidata,i+1)
            if perfil is not None:
                opciones.append(perfil)
                continue
            # Vía de recuperación: escaneos de fotocopiadora suelen llegar
            # girados ~0.5°; una retícula inclinada no supera el detector de líneas.
            # Solo intentar compensar el sesgo si la detección normal falló.
            for angulo in (-0.5,0.5,-1.0,1.0):
                perfil=_preparar(candidata.rotate(angulo,fillcolor=255),i+1)
                if perfil is not None:
                    opciones.append(perfil)
                    break
        # La tabla de detalle Previred aparece en la mitad SUPERIOR al visualizar
        # la página correctamente. Evita elegir una orientación boca abajo.
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
            # Para RUT, renta, días, el objetivo es tamaño de letra ~23-30 px.
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
    # Texto solo: una llamada a Tesseract por página. PSM 6 evita el modo de
    # detección global de documentos que es lento para tablas de muchas filas.
    df=pytesseract.image_to_data(
        imagen,lang='eng',config='--oem 1 --psm 6',
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
    df=pytesseract.image_to_data(imagen,lang='eng',config='--oem 1 --psm 6 -c tessedit_char_whitelist=0123456789.-K',output_type=pytesseract.Output.DATAFRAME)
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
    # Alejar bordes de celdas y ampliar el texto pequeño.
    c=ImageOps.autocontrast(c.resize((c.width*4,c.height*4),Image.Resampling.BICUBIC))
    conf='--oem 1 --psm 7'
    if solo_digitos:
        conf+=' -c tessedit_char_whitelist=0123456789.-K'
    return pytesseract.image_to_string(c,lang='eng',config=conf).strip()


def _nombre_salud(plantilla: Plantilla, fila: int) -> str:
    """Lee el nombre sin las dos divisiones de celda que OCR confunde con I/L.

    En Fonasa el nombre ocupa apellido paterno, materno y nombres. La línea
    vertical entre ellas generaba LELIAS/TNEIRA aun con PDF legible.
    """
    import pytesseract
    celda=_roi(plantilla,'nombre',fila)
    if celda is None:return ''
    arr=np.array(celda.convert('L'))
    if plantilla.tipo=='salud' and len(plantilla.limites_x)>5:
        borde_izq=plantilla.limites_x[2]+2
        for idx in (3,4):
            xc=plantilla.limites_x[idx]-borde_izq
            arr[:,max(0,xc-2):min(arr.shape[1],xc+3)]=255
    celda=Image.fromarray(arr)
    celda=ImageOps.autocontrast(celda.resize((celda.width*3,celda.height*3),Image.Resampling.BICUBIC))
    txt=pytesseract.image_to_string(celda,lang='eng',config='--oem 1 --psm 7')
    txt=re.sub(r'[^A-ZÁÉÍÓÚÑÜa-záéíóúñü ]',' ',txt)
    return ' '.join(txt.upper().split())


def _rut(texto: str) -> str:
    v=_rut_clean(texto)
    if _rut_ok(v):
        return v
    # Confusiones ÓPTICAS comunes (no completar un RUT parcialmente ilegible):
    # solo probar sustituciones de caracteres no válidos; el dígito verificador
    # debe validar, sin cambiar ningún dígito ya reconocido por OCR.
    visible = str(texto).upper().translate(str.maketrans({'B':'8','O':'0','I':'1','L':'1','S':'5'}))
    candidato = _rut_clean(visible)
    if candidato != v and _rut_ok(candidato):
        return candidato
    # OCR puede insertar barras, corchetes y espacios; nunca alterar dígitos
    # arbitrariamente para forzar un DV. El cruce posterior hará la revisión.
    return ''


def _sumar_dias_si_completos(filas: list[tuple]) -> int | None:
    """Una fila sin días legibles hace desconocido el total, NO cero días."""
    if not filas or any(len(f)<4 or f[3] is None for f in filas):
        return None
    return sum(int(f[3]) for f in filas)


def _resolver_rut_ss_solo_con_evidencia(rut_ocr: str, renta_ss: int | None, datos: dict) -> str:
    """Recuperación estricta: una sustitución OCR + monto único observado en Salud.

    Nunca inferir por salario solamente: el RUT se debe parecer casi por completo
    a uno ya verificado por DV, y la base de la misma celda SS debe coincidir.
    No aplicar si faltan datos o hay dos trabajadores candidatos.
    """
    if renta_ss is None or renta_ss <= 0:
        return ''
    crudo = _rut_clean(str(rut_ocr).upper())
    if len(crudo) < 8 or _rut_ok(crudo):
        return ''
    candidatos=[]
    for valido, fuentes in datos.items():
        if len(valido)!=len(crudo) or sum(x!=y for x,y in zip(valido,crudo)) != 1:
            continue
        filas_salud=fuentes.get('salud',[])
        if filas_salud and sum(x[2] for x in filas_salud)==renta_ss:
            candidatos.append(valido)
    return candidatos[0] if len(candidatos)==1 else ''


def _renta(texto: str) -> int|None:
    if str(texto).lstrip().startswith('-'):
        return None  # signo negativo no es renta positiva; no eliminar el signo y aceptar 49 por error.
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
    recorte=ImageOps.autocontrast(recorte.resize((int(recorte.width*escala),int(recorte.height*escala)),Image.Resampling.BICUBIC))
    txt=pytesseract.image_to_string(recorte,lang='eng',config='--oem 1 --psm 7 -c tessedit_char_whitelist=0123456789.')
    return _renta(txt)


def _seleccionar_valor(candidatos: list[int|None], esperado: int|None) -> tuple[int|None,bool]:
    """Nunca usar el valor de otra institución como sustituto de un OCR ausente.

    Solo elegir un importe que *aparece* en esta misma celda. Si hay lecturas
    contradictorias, el cruce permite decidir entre candidatos observados,
    dejando constancia de revisión.
    """
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
        # Bajo el rótulo 'ANEXOS DE DETALLE': razón social y RUT del empleador.
        imagen=uno.foto.crop((int(.12*w),int(.17*h),int(.53*w),int(.27*h)))
        texto=pytesseract.image_to_string(imagen.resize((imagen.width*2,imagen.height*2)),lang='eng',config='--psm 6')
        lineas=[l.strip() for l in texto.splitlines() if len(l.strip())>3]
        nombre=next((re.sub(r'^\s*(?:RE\s+)?','',l,flags=re.I).strip(' ._-') for l in lineas if len(l)>12 and not RUT_EN_TEXTO.search(l) and len(l.split())>=2), '')
        rut_match=RUT_EN_TEXTO.search(texto) or re.search(r'\d{1,2}[.]?\d{3}[.]?[\dBOSIl]{3}\s*[-]\s*[0-9K]',texto,re.I)
        empleador_rut=_rut(rut_match.group()) if rut_match else ''
        if not empleador_rut:
            cr=uno.foto.crop((int(.13*w),int(.19*h),int(.54*w),int(.27*h)))
            extra=pytesseract.image_to_string(cr.resize((cr.width*2,cr.height*2)),lang='eng',config='--psm 6')
            rut_match=RUT_EN_TEXTO.search(extra) or re.search(r'\d{1,2}[.]?\d{3}[.]?[\dBOSIl]{3}\s*[-]\s*[0-9K]',extra,re.I)
            empleador_rut=_rut(rut_match.group()) if rut_match else ''
    else:
        nombre='';empleador_rut=''
    # El período legal pertenece a remuneraciones, NO al mes del nombre del PDF.
    # Leer encabezado de página de detalle mutual/AFP/Seguro Social.
    cab=next((p for p in plantillas if p.tipo in ('mutual','afp','seguro_social')),uno)
    w,h=cab.foto.size
    crop=cab.foto.crop((int(.52*w),int(.05*h),int(.72*w),int(.10*h)))
    ts=pytesseract.image_to_string(crop.resize((crop.width*2,crop.height*2)),lang='eng',config='--psm 6')
    m=re.search(r'(0?[1-9]|1[0-2])\s*[/.-]\s*(20\d{2})',ts)
    if m:
        periodo=f'{m.group(2)}-{int(m.group(1)):02d}'
    elif salud:
        cr=salud.foto.crop((int(.8*w),int(.19*h),int(.95*w),int(.24*h)))
        ts=pytesseract.image_to_string(cr.resize((cr.width*2,cr.height*2)),lang='eng',config='--psm 6')
        m=re.search(r'(0?[1-9]|1[0-2])\s+20(\d{2})',ts)
        periodo=f'20{m.group(2)}-{int(m.group(1)):02d}' if m else ''
    else: periodo=''
    if not periodo:
        pag_afp=next((p for p in plantillas if p.tipo=='afp'), None)
        if pag_afp is not None:
            aw,ah=pag_afp.foto.size
            period_crop=pag_afp.foto.crop((int(.54*aw),int(.05*ah),int(.79*aw),int(.12*ah)))
            t=pytesseract.image_to_string(period_crop.resize((period_crop.width*2,period_crop.height*2)),lang='eng',config='--psm 6')
            mm=re.search(r'(?<!\d)(0?[1-9]|1[0-2])\s*[/.-]\s*(20\d{2})',t)
            if mm:
                periodo=f'{mm.group(2)}-{int(mm.group(1)):02d}'
    mutual = ''
    pagina_mutual = next((p for p in plantillas if p.tipo == 'mutual'), None)
    if pagina_mutual is not None:
        from previred_pdf import _mutual_name
        mw,mh=pagina_mutual.foto.size
        cabecera=pagina_mutual.foto.crop((int(.01*mw),int(.06*mh),int(.5*mw),int(.23*mh)))
        texto_m=pytesseract.image_to_string(cabecera.resize((cabecera.width*2,cabecera.height*2)),lang='eng',config='--psm 6')
        mutual=_mutual_name(texto_m)
    # Si no puede leerse el nombre, dejar vacío para que el auditor solicite revisión.
    # El OCR puede adjuntar parte del rótulo de cabecera al nombre de empresa.
    nombre=re.sub(r'^.*?(?:NOMBRE|JMBRE|BRE)\s+', '', nombre, flags=re.I)
    nombre=re.sub(r'^\\L\s+O\s+','',nombre,flags=re.I).strip(' ._-')
    return nombre.upper(), empleador_rut, periodo, mutual




def _firma_celda_dias(plantilla: Plantilla, fila: int):
    """Huella visual normalizada de una celda de días, sin OCR ni otros datos.

    Sólo sirve como SEGUNDA EVIDENCIA para un valor leído en otras filas del
    MISMO formulario. No interpreta un número a partir de la imagen aislada.
    """
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
    """Resuelve un error OCR de días sólo con evidencia independiente.

    Para evitar falsos arreglos no toca trabajadores con múltiples movimientos
    del Seguro Social. Si el valor está en otra lectura de ESA MISMA celda,
    lo puede confirmar Salud. Cuando Tesseract no recupera el dígito, exige
    semejanza visual con al menos dos filas ancla del mismo comprobante.
    """
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
        # El OCR debe haber leído por lo menos una base coherente en SS.
        renta_salud = sum(x[2] for x in filas_salud)
        if renta != renta_salud:
            continue
        lecturas = [_ocr_num_con_escala(plantilla, 'dias', fila, esc) for esc in (4.0, 5.0)]
        if dias_esperados in lecturas and (dias_esperados not in (0, 1) or lecturas.count(dias_esperados) == 2):
            filas_ss[0] = (pagina, fila, renta, dias_esperados)
            resueltos[rut] = f'Días SS {dias_ss}→{dias_esperados}: relectura de celda p.{pagina} y Salud'
            continue
        # Segundo camino: comparar tinta de esa celda contra 2 o más celdas
        # ANCLA con días ya coincidentes entre SS y Salud del mismo PDF.
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
    # V7: conservar el problema en el RUT que realmente produjo la lectura.
    # Un OCR fallido SIN RUT confirmado es un pendiente DEL ARCHIVO, nunca
    # una alerta ficticia para todos los trabajadores de ese comprobante.
    invalidas: list[str] = []  # solo incidencias globales / sin identificación
    incidentes_por_rut: dict[str, list[str]] = defaultdict(list)
    def advertir(msg: str, rut: str = "") -> None:
        (incidentes_por_rut[rut] if rut else invalidas).append(msg)

    salud_por_posicion: list[tuple[str,int|None,int|None]] = []
    # Primero Fonasa: su RUT y días sirven como control independiente de otras
    # instituciones, sin imponer su base como la base de AFP o Mutual.
    orden=sorted(perfiles,key=lambda p:0 if p.tipo=='salud' else 1)
    for p in orden:
        lote=_ocr_columna_digitos(p)
        nfilas=len(p.filas_y)
        # Una fuente con igual orden y número de filas puede utilizarse para
        # reconocer un RUT ilegible, pero solo si tres RUT válidos anclan el orden.
        puede_alinear=False
        if p.tipo in ('mutual','seguro_social') and len(salud_por_posicion)==nfilas:
            anclas=[(j,_rut(v.get('rut',''))) for j,v in enumerate(lote) if _rut(v.get('rut',''))]
            correctas=sum(1 for j,r in anclas if r==salud_por_posicion[j][0])
            puede_alinear=(correctas>=3 and correctas==len(anclas))
        for j,v in enumerate(lote):
            # La cabecera o el total no son trabajadores: evita OCR adicional.
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
            # En salud se puede corroborar por RUT; en las otras instituciones,
            # el esperado sirve únicamente para resolver discrepancias entre
            # montos *leídos realmente* en el propio recorte de esa fuente.
            esperado=None
            if p.tipo!='salud':
                val_salud=datos.get(rut,{}).get('salud',[])
                if val_salud:esperado=sum(row[2] for row in val_salud)
            cifra_lote=_renta(v.get('renta',''))
            # Una cifra coincidente en el OCR y en la fuente independiente
            # NO necesita otra invocación por celda; ahorro clave en nóminas grandes.
            if esperado is not None and cifra_lote==esperado:
                candidatos=[cifra_lote]
            else:
                cifra_2x=_ocr_num_con_escala(p,'renta',j,2.0)
                candidatos=[cifra_lote,cifra_2x]
                if (cifra_lote is None or cifra_2x is None or cifra_lote!=cifra_2x) and (esperado is None or esperado not in candidatos):
                    candidatos.append(_ocr_num_con_escala(p,'renta',j,3.0))
            renta,contradiccion=_seleccionar_valor(candidatos,esperado)
            # V7: RUT de Seguro Social mal leído (1 dígito) con monto idéntico
            # observado en un único RUT de Salud. Nunca solo por monto/posición.
            if not rut and p.tipo=='seguro_social':
                corregido=_resolver_rut_ss_solo_con_evidencia(v.get('rut',''),renta,datos)
                if corregido:
                    rut=corregido
                    advertir(f'p.{p.numero_pagina}, fila {j+1}: RUT identificado por coincidencia OCR de un dígito y base igual en Salud; confirmar visualmente',rut)
            # Cuando una celda muestra 0 en una pasada y 9 en otra,
            # ninguna justifica sumar $9. Preferir 0 *solo si fue leído*
            # en la misma celda y la supuesta renta es inferior a $100.
            # Mantener aviso explícito, NO confirmar sin revisión visual.
            if p.tipo == 'salud' and renta is not None and 0 < renta < 100 and 0 in candidatos:
                renta=0
                contradiccion=True
                advertir(f'p.{p.numero_pagina}, fila {j+1}: base pequeña ambigua; se seleccionó 0 observado en OCR', rut)
            if contradiccion and any(c is not None for c in candidatos):
                advertir(f'p.{p.numero_pagina}, fila {j+1}: lecturas alternativas de renta {sorted(set(n for n in candidatos if n is not None))}; cotejar', rut)
            dias=None
            if p.tipo=='salud':
                dias=_ocr_num_con_escala(p,'dias',j,2.0)
                # Una celda de 9/6 píxeles puede requerir 5x en vez de 2x.
                if dias is None:
                    dias=_ocr_num_con_escala(p,'dias',j,5.0)
                # Para 11 días, a veces el OCR reconoce únicamente '1'.
                # Un segundo recorte independiente verifica los dos caracteres.
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
                # Las tablas muy pequeñas pueden confundir 6 con 5/8 y 11 con
                # 41; cotejar contra salud si se reconoce alternativamente.
                if dias is None or dias>31:
                    dias=_renta(v.get('dias',''))
                if dias is not None and dias>31:
                    dias=None
                # NO comparar cada movimiento de SS con el total mensual de
                # Salud. Un trabajador puede tener 8 filas con 0 días y otra
                # con 9 días: la comparación corresponde al ACUMULADO por RUT.
                # Reintentar celdas solo si su OCR es ilegible o >31.
            if p.tipo=='salud':
                salud_por_posicion.append((rut,renta,dias))
            if not rut:
                if re.search(r'\d{5,}',v.get('rut','')):
                    advertir(f'p.{p.numero_pagina}, fila {j+1}: RUT no reconocido')
                continue
            if p.tipo=='salud' and renta is not None and 0 < renta < 100 and dias in (0,None):
                advertir(f'p.{p.numero_pagina}, fila {j+1}: renta mínima ${renta} con 0 días, OCR ambiguo; no se sumó sin respaldo visual', rut)
                # No interpretamos un carácter suelto (9/49) como $9/$49 de
                # remuneración. No declarar CONFORME: se registra observación.
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
    # El OCR de RUT pequeño produce ocasionalmente un segundo RUT que incluso
    # supera el DV por coincidencia. No presentar como trabajador una línea
    # con solo $0 ni un RUT aislado sin respaldo de salud/otra institución.
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
    # Reconciliación selectiva después de agrupar movimientos por RUT.
    # No confunde los cero-días legítimos con errores OCR.
    dias_resueltos = _reconciliar_dias_ss(datos, perfiles)
    # Comprobar coherencia de todas las fuentes, incluidos registros omitidos.
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
        # Las alternativas OCR no se convierten en infracción cuando el valor
        # elegido aparece realmente en la celda Y concuerda con las tres fuentes.
        # Conservarlas como evidencia técnica informativa, no como motivo de alerta.
        informativas=[]
        for aviso in avisos:
            baja = aviso.lower()
            if bases_ok and ('lecturas alternativas de renta' in baja or 'rut alineado con salud' in baja
                              or 'rut identificado por coincidencia ocr' in baja):
                informativas.append(aviso+' [resuelto por conciliación entre fuentes]')
            else:
                obs.append(aviso)
        if rut in dias_resueltos:
            informativas.append(dias_resueltos[rut])
        if not nombres.get(rut):obs.append('No se pudo confirmar el nombre en el anexo Salud')
        if dia_s is None and dia_ss is None:
            obs.append('Ninguna fuente permitió confirmar los días remunerados')
        # Las pérdidas de RUT y los descuadres de los totales AFP/Salud/Mutual
        # comprometen la integridad del archivo completo. Nunca dar por
        # conforme un subtotal que podría haber omitido una fila.
        problemas_globales = [x for x in invalidas if any(k in x for k in (
            'RUT OCR sin dos fuentes', 'Total AFP diferente', 'Total Mutual diferente',
            'No coinciden los recuentos', 'No coinciden los RUT',
            'Falta información verificada'))]
        if problemas_globales:
            obs.append('No se comprobó la integridad de todas las filas del PDF: '+ ' | '.join(problemas_globales))
        # Sólo exigir control visual si hay una duda real: el origen escaneado
        # NO equivale automáticamente a un error de cotizaciones.
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
    # Archivar problemas de filas sin RUT en un solo campo de documento.
    # No se borran: se ven en diagnóstico y Log_Procesamiento.
    if salida:
        salida[0]['__ocr_incidencias_documento'] = ' | '.join(dict.fromkeys(invalidas))
    return pd.DataFrame(salida)
