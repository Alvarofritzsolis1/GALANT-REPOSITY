"""Lectura segura de F30-1: separa CERTIFICADOS, no mezcla páginas solo por archivo.

Sin red/automatización del portal DT. 'Pagadas: Sí' es declaración del PDF;
autenticidad únicamente cuando un revisor registra el resultado oficial.
"""
from __future__ import annotations
from io import BytesIO
import hashlib
import re
import unicodedata
import pandas as pd
import fitz
from collections import Counter

MESES={'enero':1,'febrero':2,'marzo':3,'abril':4,'mayo':5,'junio':6,'julio':7,
       'agosto':8,'septiembre':9,'setiembre':9,'octubre':10,'noviembre':11,'diciembre':12}
RUT_RE=re.compile(r'(?<!\d)(\d{1,2}[.\s]?\d{3}[.\s]?\d{3}[\-\s]?[0-9kK])(?!\d)')
FOLIO_RE=re.compile(r'\b(2000)\s*[/|\\]\s*(20\d{2})\s*[/|\\]\s*(\d{7,9})\b')
FOLIO_ALT=re.compile(r'\b2000\s*[/|\\]\s*(20\d{2})\s*[/|\\]\s*(\d{7,9})\b')


def sin_acentos(s):
    return ''.join(c for c in unicodedata.normalize('NFKD',str(s)) if not unicodedata.combining(c))


def rut_normalizado(s):
    t=re.sub(r'[^0-9Kk]','',str(s)).upper()
    if len(t)<8 or len(t)>9 or not t[:-1].isdigit():return ''
    x=sum(int(d)*(2+i%6) for i,d in enumerate(reversed(t[:-1])))
    val=11-x%11
    dv='0' if val==11 else 'K' if val==10 else str(val)
    return t if t[-1]==dv else ''


def todos_rut(texto):
    return [(m.group(0),rut_normalizado(m.group(0))) for m in RUT_RE.finditer(texto)]


def folio_en_pagina(texto):
    f=FOLIO_RE.search(texto)
    return f'{f.group(1)}/{f.group(2)}/{f.group(3)}' if f else ''


def leer_pagina(page, permitir_ocr=True):
    t=page.get_text('text',sort=True)
    if len(t.strip())>180:return t,'DIGITAL'
    if not permitir_ocr:return t,'SIN_TEXTO'
    import pytesseract
    from PIL import Image
    pix=page.get_pixmap(matrix=fitz.Matrix(1.7,1.7),alpha=False)
    im=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
    s=pytesseract.image_to_string(im,lang='eng',config='--psm 3',timeout=8)
    return s,'OCR'


def pagina_es_resumen(texto):
    s=sin_acentos(texto).lower()
    return ('3.1' in s or 'periodo :' in s or 'periodo:' in s) and (
        'remuneraciones' in s or 'cotizaciones previsionales' in s or
        'monto informado' in s or '3 - periodos' in s or '3-periodos' in s)


def pagina_es_nomina(texto):
    s=sin_acentos(texto).lower()
    return ('trabajadores declarados' in s and 'certificac' in s) or ('nombre trabajador' in s and 'total trabajadores' in s)


def obtener_periodo(texto):
    t=sin_acentos(texto)
    m=re.search(r'3\s*\.\s*1\s*[-–]?\s*Periodo\s*[:;]?\s*([A-Za-z]+)\s+(20\d{2})',t,re.I)
    if not m:m=re.search(r'Detalle\s+por\s+Periodo\s*\n\s*[|\s]*([A-Za-z]+)\s+(20\d{2})',t,re.I)
    if m and m.group(1).lower() in MESES:return f'{m.group(2)}-{MESES[m.group(1).lower()]:02d}'
    return ''


def monto_remuneraciones(t):
    """Captura el monto de REMUNERACIONES, nunca el total de AFP/ISL.

    Acepta errores OCR leves en la etiqueta ("Remuneractones") pero no
    en la cifra. Las lecturas truncadas se cotejan en otro recorte.
    """
    s=sin_acentos(t)
    # Preferir fila con número de pagados / no pagados delante del monto.
    m=re.search(r'(?:\bR?emuner\w{4,15}\b)\s*\n?\s*(?:[\d|]\s+)?\d{1,4}\s+[0Oo]\s+\$\s*([\d.,]+)',s,re.I)
    if not m:
        m=re.search(r'\bR?emuner\w{4,15}\b[\s\S]{0,85}?\$\s*([\d.,]+)',s,re.I)
    if not m:
        # Si el OCR deformó 'Remuneraciones', todavía existe la cabecera
        # única 'Monto Informado' sobre la primera cifra del bloque.
        m=re.search(r'Monto\s+Informado[\s\S]{0,100}?\$\s*([\d.,]+)',s,re.I)
    if not m:return None
    try:return int(re.sub(r'\D','',m.group(1)))
    except ValueError:return None


def pagos_remuneraciones(t):
    """Extrae cantidades de remuneraciones pagadas/no pagadas del bloque 3.

    No es lo mismo monto remuneraciones que sueldo individual ni renta imponible.
    Devuelve (None,None) cuando no puede confirmar los dos números.
    """
    z=sin_acentos(t)
    m=re.search(r'Pagadas\s+No Pagadas\s+Monto Informado\s*\n\s*Remuneraciones\s*\n\s*(\d{1,5})\s+(\d{1,5})\s+\$',z,re.I)
    if m:return (int(m.group(1)),int(m.group(2)))
    m=re.search(r'Remuneraciones\s*\n\s*(\d{1,5})\s+(\d{1,5})\s+\$',z,re.I)
    if m:return (int(m.group(1)),int(m.group(2)))
    # OCR puede deformar la palabra 'Remuneraciones' pero mantener la fila
    # numérica Pagadas/No pagadas/Monto. Preferir solo una coincidencia.
    candidato=re.findall(r'(?m)^\s*(\d{1,4})\s+([0Oo])\s+\$\s*[\d.,]+\s*$',z)
    if len(candidato)==1:return int(candidato[0][0]),0
    return (None,None)


def mes_en_nombre_archivo(nombre):
    """Metadato informativo; jamás se usa para reemplazar el período F30-1."""
    x=sin_acentos(nombre).lower()
    m=re.search(r'(?<!\d)(20\d{2})[_ -]('+'|'.join(MESES)+r')(?:[_ .-]|$)',x)
    if m:return f'{m.group(1)}-{MESES[m.group(2)]:02d}'
    return ''


def cantidad_vigentes(t):
    s=sin_acentos(t)
    # Layout horizontal de scans, con una o dos líneas.
    m=re.search(r'Trabajadores\s+Vigentes\s+Despedidos\s*\n\s*(\d{1,4})\s+[0Oo]\b',s,re.I)
    if m:return int(m.group(1))
    m=re.search(r'Vigentes\s+Despedidos\s*\n[^\n]{0,35}\n\s*(\d{1,4})\s+[0Oo]\b',s,re.I)
    if m:return int(m.group(1))
    m=re.search(r'Vigentes\s+Despedidos\s*\n\s*(\d{1,4})\s+[0Oo]\b',s,re.I)
    if m:return int(m.group(1))
    # Las muestras digitalizadas tienen orientación vertical:
    # Trabajadores / Vigentes / 2 / Despedidos / 0
    m=re.search(r'Trabajadores\s*\n\s*Vigentes\s*\n\s*(\d{1,4})',s,re.I)
    if m:return int(m.group(1))
    # OCR escaneados: el encabezado 'Trabajadores ...' puede deformarse.
    # Utilizar dos controles independientes: Nº de remuneraciones pagadas,
    # si se reconoce; los valores no confirmados permanecen None.
    m=re.search(r'Remuneraciones\s*\n\s*(\d{1,4})\s+0\s+\$\s*[\d.,]+',s,re.I)
    if m:return int(m.group(1))
    m=re.search(r'Remuneraciones[\s\S]{0,65}?\n\s*(\d{1,3})\s+(?:0|\)|°)\s+\$\s*[\d.,]+',s,re.I)
    if m:return int(m.group(1))
    # Encabezado 'Trabajadores igetitee ...' + dos líneas
    m=re.search(r'(?m)^Trabajadores[^\n]*\n\s*(\d{1,3})\s+(?:0|i\)|°|o)\s*$',s,re.I)
    if m:return int(m.group(1))
    return None


def pagadas_desde_resumen(t):
    s=sin_acentos(t).lower()
    start=s.find('cotizaciones previsionales')
    if start<0:return 'NO_LEIBLE'
    frag=s[start:start+115]
    # En el diseño DT el orden es 'Pagadas' y 'Adjunta Nómina' con valores 'Si No'
    # No confundir con remuneraciones 'Pagadas'.
    if re.search(r'\bsi\s+no\b',frag):return 'SI_DECLARADO'
    if re.search(r'\bno\s+si\b',frag):return 'NO_DECLARADO'
    if re.search(r'pagadas\s*\n\s*si\b',frag):return 'SI_DECLARADO'
    if re.search(r'pagadas\s*\n\s*no\b',frag):return 'NO_DECLARADO'
    return 'NO_LEIBLE'


def extract_rut_y_nombre(t):
    s=sin_acentos(t)
    # Cortar desde sección 1.1 y ANTES de representante; no tomar al representante como RUT de empresa.
    a=re.search(r'1\s*\.\s*1\s*[-.]?\s*Antecedentes\s+Empleador',s,re.I)
    b=re.search(r'1\s*\.\s*2\s*[-.]?\s*Empresa\s+Principal',s,re.I)
    area=s[a.end():b.start()] if a and b else s[:1800]
    m=re.search(r'Nombre\s+o\s+Raz\w+\s+Social\s+RUT\s*\n\s*([^\n]+)',area,re.I)
    if not m:
        m=re.search(r'Nombre\s+o\s+Raz\w+\s+Social\s*\n\s*([^\n]+)\n\s*RUT\s*\n\s*([^\n]+)',area,re.I)
    linea=(m.group(1) if m else '')
    bruto=RUT_RE.search(linea)
    rut=rut_normalizado(bruto.group(0)) if bruto else ''
    if not rut:
        # OCR suele insertar espacios dentro del RUT (77,359.81 1-8).
        # Normalizar solamente si el módulo 11 confirma el número.
        ab=re.search(r'(?<!\d)\d{1,2}[\d., ]{6,18}[-–][0-9Kk]\b',linea)
        if ab and rut_normalizado(ab.group(0)):
            bruto=ab;rut=rut_normalizado(ab.group(0))
    nombre=(linea[:bruto.start()] if bruto else linea).strip(' -_:.,')
    nombre=re.sub(r'\s+',' ',nombre).upper()
    if not rut and re.search(r'\d{2}[.,]\d',nombre):
        nombre=re.sub(r'\s+[0-9][0-9.,\s-]+$', '', nombre).strip()
    # Cuando el OCR separa nombre y RUT en distintas líneas, probar solo la
    # siguiente línea, nunca RUT del representante legal.
    if not rut and m and m.lastindex and m.lastindex>=2:
        cand=RUT_RE.search(m.group(2))
        if cand:rut=rut_normalizado(cand.group(0))
    return nombre,rut


def extract_empresa_principal(t):
    s=sin_acentos(t)
    a=re.search(r'1\s*\.\s*2\s*[-.]?\s*Empresa\s+Principal',s,re.I)
    b=re.search(r'2\s*[-–]\s*Obra',s,re.I)
    if not a:return ''
    frag=s[a.end():b.start() if b else a.end()+750]
    m=re.search(r'Nombre\s+o\s+Raz\w+\s+Social\s+RUT\s*\n\s*([^\n]+)',frag,re.I)
    if not m:return ''
    y=RUT_RE.search(m.group(1))
    return rut_normalizado(y.group(0)) if y else ''


def extract_obra(t):
    """Lee todo el nombre de obra incluso si está partido en dos líneas."""
    s=sin_acentos(t)
    a=re.search(r'2\s*[-–]\s*Obra[^\n]*',s,re.I)
    if not a:return ''
    frag=s[a.end():a.end()+1000]
    m=re.search(r'Nombre\s+Segun\s+Contrato\s+Civil\s+Modo\s+Participacion\s*\n([\s\S]{1,250}?)(?=\n\s*Tipo\s+de\s+Obra)',frag,re.I)
    if m:
        partes=[]
        for linea in m.group(1).splitlines():
            x=re.sub(r'\b(?:Contratista|Subcontratista)\b',' ',linea,flags=re.I)
            x=re.sub(r'\s+',' ',x).strip(' -_.')
            if x:partes.append(x)
        if partes:return ' '.join(partes).upper()
    m=re.search(r'Nombre\s+Segun\s+Contrato\s+Civil\s+Modo\s+Participacion\s*\n\s*([^\n]+)',frag,re.I)
    if m:
        return re.split(r'\s+(?:Contratista|Subcontratista)\b',m.group(1),flags=re.I)[0].strip(' -_.').upper()
    return ''


def extraer_nomina(t,periodo='',folio='',archivo='',pagina=1):
    if not pagina_es_nomina(t):return []
    # Solo procesar DESDE la leyenda Detalle por Periodo, no RUT del empleador.
    sn=sin_acentos(t)
    i=sn.lower().find('detalle por periodo')
    sub=t[max(i,0):]
    fin=sin_acentos(sub).lower().find('total trabajadores')
    if fin>=0:sub=sub[:fin]
    salida=[];vistos=set()
    for linea in sub.splitlines():
        bruto=RUT_RE.search(linea)
        if not bruto:continue
        rut=rut_normalizado(bruto.group(0))
        if not rut or rut in vistos:continue
        resto=linea[bruto.end():]
        nom=re.sub(r'[^\w\sÁÉÍÓÚÑáéíóúñ-]','',resto).strip()
        if len(nom)<5 or re.search(r'\b(?:Certificado|Clave|Pagina)\b',nom,re.I):continue
        salida.append({'certificado':folio,'periodo_remuneracion':periodo,'trabajador_rut':rut,
                       'trabajador_nombre':re.sub(r'\s+',' ',nom),'archivo':archivo,'pagina':pagina})
        vistos.add(rut)
    return salida


def _ocr_zona(page, coordenadas, psm=6):
    """Segunda lectura local: solo el recorte dudoso, en alta resolución."""
    import pytesseract
    from PIL import Image
    pix=page.get_pixmap(matrix=fitz.Matrix(3,3),clip=fitz.Rect(*coordenadas),alpha=False)
    im=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
    return pytesseract.image_to_string(im,config=f'--psm {psm}',lang='eng',timeout=8)


def _extraer_cabecera_ocr_simple(t):
    """Fallback para certificados escaneados con columnas PDF desordenadas.

    Busca el RUT validado de la EMPRESA en 1.1 y el nombre bajo su etiqueta.
    Nunca usa el RUT del representante legal para suplirlo.
    """
    s=sin_acentos(t)
    a=re.search(r'1\s*\.\s*1\s*[-.]?\s*Antecedentes\s+Empleador',s,re.I)
    b=re.search(r'1\s*\.\s*2\s*[-.]?\s*Empresa\s+Principal',s,re.I)
    frag=s[a.end():b.start()] if a and b else ''
    if not frag:return ('','')
    lines=[q.strip() for q in frag.splitlines() if q.strip()]
    nombre='';rut=''
    for j,line in enumerate(lines):
        if re.search(r'Nombre\s+o\s+Raz\w+\s+Social',line,re.I):
            # F30 escaneado: "Nombre o Razón Social" / nombre / "Representante Legal" ...
            for candidate in lines[j+1:j+4]:
                z=re.sub(r'\s+',' ',candidate)
                if re.search(r'^(RUT|Representante|Codigo|C[oó]digo)',z,re.I):break
                if len(z)>=7 and not re.fullmatch(r'[0-9 .Kk-]+',z):
                    nombre=z.upper().strip(' :-')
                    break
            break
    # Buscar RUT que sigue a su propia etiqueta, ANTES de "RUT Representante Legal".
    for j,line in enumerate(lines):
        if re.fullmatch(r'R\.?U\.?T\.?\s*:?|RUT\s*:',line,re.I):
            for candidate in lines[j+1:j+3]:
                m=RUT_RE.search(candidate)
                if m and rut_normalizado(m.group(0)):
                    rut=rut_normalizado(m.group(0));break
            if rut:break
    if not rut:
        # Puede quedar en la misma línea: empresa/RUT, pero nunca en la
        # subsección del representante.
        mo=re.search(r'RUT\s+Representante\s+Legal',frag,re.I)
        safe=frag[:mo.start()] if mo else frag
        ruts=[valid for _,valid in todos_rut(safe) if valid]
        if ruts:rut=ruts[0]
    return nombre,rut


def _extract_nomina_columnas(page,periodo,folio,archivo,numero_pagina,esperados=None):
    """OCR para F30-1 escaneado con tabla: detectar filas y leer columnas por separado.

    No rellena RUT por nombre ni corrige dígitos por intuición. Un RUT tiene
    que aprobar módulo 11. Evita aplicar OCR a toda la página repetidamente.
    """
    try:
        import cv2
        import numpy as np
        from PIL import Image
        import pytesseract
        scale=2
        pix=page.get_pixmap(matrix=fitz.Matrix(scale,scale),colorspace=fitz.csGRAY,alpha=False)
        gray=np.frombuffer(pix.samples,np.uint8).reshape((pix.height,pix.width))
        bw=cv2.adaptiveThreshold(gray,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                 cv2.THRESH_BINARY_INV,31,12)
        horizontal=cv2.morphologyEx(bw,cv2.MORPH_OPEN,
                        cv2.getStructuringElement(cv2.MORPH_RECT,(max(90,int(gray.shape[1]*0.16)),1)))
        contours,_=cv2.findContours(horizontal,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        lineas=[]
        for c in contours:
            x,y,w,h=cv2.boundingRect(c)
            if w<gray.shape[1]*.15 or y/scale<155 or y/scale>page.rect.height*.8:continue
            lineas.append((y/scale,x/scale,(x+w)/scale))
        grupos=[]
        for y,x1,x2 in sorted(lineas):
            if not grupos or abs(y-grupos[-1]['y'])>2.5:
                grupos.append({'y':y,'l':x1,'r':x2})
            else:
                grupos[-1]['l']=min(grupos[-1]['l'],x1)
                grupos[-1]['r']=max(grupos[-1]['r'],x2)
        # Encontrar una secuencia densa de líneas horizontales a distancias
        # similares; las primeras dos son cabecera/mes, seguidas de filas.
        runs=[];run=[]
        for item in grupos:
            if run and not 9<=item['y']-run[-1]['y']<=30:
                if len(run)>=5:runs.append(run)
                run=[]
            run.append(item)
        if len(run)>=5:runs.append(run)
        if not runs:return []
        runs.sort(key=lambda r:(abs(len(r)-int(esperados or 5)-4),-len(r)))
        lines=runs[0]
        if len(lines)<6:return []
        # El primer borde superior pertenece a la cabecera "Detalle por período";
        # la fila de título del mes y el encabezado anteceden a los trabajadores.
        starts=lines[2:-1]
        if esperados and len(starts)>=esperados+1:starts=starts[:esperados]
        if not starts:return []
        x_left=min(r['l'] for r in lines)
        x_right=max(r['r'] for r in lines)
        width=x_right-x_left
        if not 230<=width<=page.rect.width: return []
        y_top=starts[0]['y']-3.5
        y_bottom=lines[min(2+len(starts),len(lines)-1)]['y']+2
        if y_bottom-y_top<12:return []
        def texto_columna(x1,x2):
            r=fitz.Rect(x1,y_top,x2,y_bottom) & page.rect
            if r.is_empty:return ''
            pi=page.get_pixmap(matrix=fitz.Matrix(4,4),clip=r,alpha=False)
            img=Image.frombytes('RGB',(pi.width,pi.height),pi.samples)
            return pytesseract.image_to_string(img,lang='eng',config='--psm 6',timeout=12)
        cr=texto_columna(x_left+width*.095,x_left+width*.267)
        cn=texto_columna(x_left+width*.318,x_left+width*.80)
        ruts=[]
        for line in cr.splitlines():
            m=RUT_RE.search(line.replace(',','.'))
            if not m:continue
            rut=rut_normalizado(m.group(0))
            if rut:ruts.append(rut)
        nombres=[]
        for line in cn.splitlines():
            n=re.sub(r'[^\w\sÁÉÍÓÚÑáéíóúñ-]',' ',line).strip(' -_')
            n=re.sub(r'\s+',' ',n)
            if len(n)>=10 and len(n.split())>=2 and not re.search(r'^(Total|Nombre|Trabajador)',n,re.I):
                nombres.append(n)
        if len(ruts)!=len(nombres):return []
        if esperados is not None and len(ruts)!=esperados:return []
        if len(set(ruts))!=len(ruts):return []
        return [{'certificado':folio,'periodo_remuneracion':periodo,
                 'trabajador_rut':rut,'trabajador_nombre':nombre,
                 'archivo':archivo,'pagina':numero_pagina}
                for rut,nombre in zip(ruts,nombres)]
    except Exception:
        return []


def _corregir_folios(paginas):
    """Fusión conservadora de errores OCR. Nunca unir folios que tengan dos resúmenes distintos."""
    c=Counter(p['folio'] for p in paginas if p['folio'])
    evidencias=[p for p in paginas if pagina_es_resumen(p['texto']) and p['folio']]
    for p in paginas:
        x=p['folio']
        if not x:continue
        matches=[]
        for y in c:
            if x==y:continue
            ax=x.split('/');ay=y.split('/')
            if len(ax)!=3 or len(ay)!=3 or ax[0]!=ay[0]:continue
            # Misma secuencia de 8 dígitos, año mal leído; el período es referencia.
            if ax[2]==ay[2] and ax[1]!=ay[1]:
                # Favorecer año explícito de una página resumen, nunca inventarlo.
                correctos=[z for z in evidencias if z['folio']==y and obtener_periodo(z['texto']).startswith(ay[1])]
                if correctos:matches.append((y,100))
            elif ax[1]==ay[1] and len(ax[2])==len(ay[2]) and sum(a!=b for a,b in zip(ax[2],ay[2]))==1:
                # Un dígito distinto en un anexo AISLADO, y otro folio repetido
                # en al menos dos páginas; no tocar dos certificados completos.
                if c[x]==1 and c[y]>=2 and not pagina_es_resumen(p['texto']) and '1.1' not in p['texto']:
                    matches.append((y,10))
        if matches:
            p['folio']=sorted(matches,key=lambda z:-z[1])[0][0]
    return paginas


def analizar_f301(archivos:list[tuple[bytes,str]],progreso=None):
    """Retorna (certificados_df, nominas_df, incidencias_df); no consulta servidor DT."""
    certificados=[];nominas=[];alertas=[]
    for ix,(data,nombre) in enumerate(archivos,1):
        try:
            doc=fitz.open(stream=data,filetype='pdf')
            if len(doc)>100:raise ValueError('PDF de más de 100 páginas; dividir por certificados.')
            paginas=[]
            for i,p in enumerate(doc):
                t,origen=leer_pagina(p)
                fid=folio_en_pagina(t)
                # Rescatar folio y clave en el margen inferior de página resumen.
                if not fid and pagina_es_resumen(t) and origen=='OCR':
                    try:
                        extra=_ocr_zona(p,(18,635,590,788))
                        fid=folio_en_pagina(extra)
                        if fid:t+='\n'+extra
                    except Exception:pass
                resumen_ocr=''
                if pagina_es_resumen(t) and origen=='OCR':
                    try:resumen_ocr=_ocr_zona(p,(65,90,550,380))
                    except Exception:pass
                cabecera_ocr=''
                if ('1.1' in t or 'Antecedentes Empleador' in t) and origen=='OCR':
                    try:cabecera_ocr=_ocr_zona(p,(0,235,550,390))
                    except Exception:pass
                paginas.append({'pagina':i+1,'texto':t,'folio':fid,'origen':origen,'cabecera_ocr':cabecera_ocr,'resumen_ocr':resumen_ocr})
            doc.close()
            paginas=_corregir_folios(paginas)
            # Si las primeras páginas quedaron sin folio por OCR, y el mismo
            # documento lo identifica luego, enlazar SOLO el prefijo inicial.
            primero=next((p['folio'] for p in paginas if p['folio']), '')
            if primero:
                for p in paginas:
                    if p['folio']:break
                    p['folio']=primero
            # Segmentar por FOLIO, no por nombre de archivo; un PDF puede contener varios.
            grupos={};actual=None
            for pg in paginas:
                fid=pg['folio']
                if fid:actual=fid
                if not actual:
                    # Sin identificar folio, mantener grupo aislado y NO validar autenticidad.
                    actual=f'SIN_FOLIO_{nombre}_{pg["pagina"]}'
                if actual not in grupos:grupos[actual]=[]
                grupos[actual].append(pg)
            hash_file=hashlib.sha256(data).hexdigest()
            for fid,pags in grupos.items():
                t0=next((p['texto'] for p in pags if '1.1' in p['texto'] or 'Antecedentes Empleador' in p['texto']),'')
                sumas=[p['texto'] for p in pags if pagina_es_resumen(p['texto'])]
                ts='\n'.join((p.get('resumen_ocr') or p['texto']) for p in pags if pagina_es_resumen(p['texto']))
                periodo=obtener_periodo(ts) or next((obtener_periodo(p['texto']) for p in pags if obtener_periodo(p['texto'])),'')
                empresa,rut=extract_rut_y_nombre(t0) if t0 else ('','')
                if t0 and (not rut or not empresa):
                    ne,nr=_extraer_cabecera_ocr_simple(t0)
                    empresa=empresa or ne;rut=rut or nr
                # Re-OCR de la cabecera SOLO si el primer RUT no superó módulo 11.
                for pg in pags:
                    if not pg.get('cabecera_ocr'):continue
                    reforzado='1.1 - Antecedentes Empleador (solicitante)\n'+pg['cabecera_ocr']+'\n1.2 - Empresa Principal'
                    ne,nr=extract_rut_y_nombre(reforzado)
                    if nr and (not rut or not empresa):empresa=ne or empresa;rut=nr
                principal=extract_empresa_principal(t0) if t0 else ''
                obra=extract_obra(t0) if t0 else ''
                # Puede estar en página resumen cuando no aparece en portada por OCR.
                if not obra:
                    m=re.search(r'(?:Obra,?\s*Faena\s*o\s*Servicio)\s+([^\n]+)',sin_acentos(ts),re.I)
                    if m:obra=m.group(1).strip().upper()
                n=cantidad_vigentes(ts)
                monto=monto_remuneraciones(ts)
                remuneraciones_pagadas,remuneraciones_no_pagadas=pagos_remuneraciones("\n".join(sumas))
                pagado=pagadas_desde_resumen(ts)
                if pagado=='NO_LEIBLE':pagado=pagadas_desde_resumen('\n'.join(sumas))
                if monto is None:monto=monto_remuneraciones('\n'.join(sumas))
                if sumas and (monto is None or monto<10000) and any(p['origen']=='OCR' for p in pags):
                    for p in pags:
                        if not pagina_es_resumen(p['texto']) or p['origen']!='OCR':continue
                        try:
                            with fitz.open(stream=data,filetype='pdf') as dd:
                                alt=_ocr_zona(dd[p['pagina']-1],(100,150,560,380))
                            x=monto_remuneraciones(alt)
                            if x is not None:monto=x;break
                        except Exception:pass
                fecha_emision=''
                if t0:
                    mf=re.search(r'Fecha\s+Emisi\w*\s*[:;]\s*(\d{2}[-/.]\d{2}[-/.]\d{4})',sin_acentos(t0),re.I)
                    if mf:fecha_emision=mf.group(1)
                nominas_grupo=[]
                for p in pags:
                    if pagina_es_nomina(p['texto']):
                        candidatos=extraer_nomina(p['texto'],periodo,fid,nombre,p['pagina'])
                        # Columnas RUT y nombres separadas: lectura más precisa que OCR general.
                        if p['origen']=='OCR' and (n is None or len(candidatos)<n):
                            try:
                                with fitz.open(stream=data,filetype='pdf') as dd:
                                    mejores=_extract_nomina_columnas(dd[p['pagina']-1],periodo,fid,nombre,p['pagina'],n)
                                if len(mejores)>len(candidatos):candidatos=mejores
                            except Exception:pass
                        # Releer solo el recorte de nómina si existe riesgo
                        # de perder dígitos; no releer la totalidad del PDF.
                        if p['origen']=='OCR' and (n is None or len(candidatos)<n):
                            try:
                                with fitz.open(stream=data,filetype='pdf') as dd:
                                    for zona in [(0,245,580,630),(0,180,580,640)]:
                                        recorte=_ocr_zona(dd[p['pagina']-1],zona)
                                        mejores=extraer_nomina('Trabajadores declarados en la Certificacion\nDetalle por Periodo\n'+recorte,
                                            periodo,fid,nombre,p['pagina'])
                                        if len(mejores)>len(candidatos):candidatos=mejores
                                        if n is not None and len(candidatos)>=n:break
                            except Exception:pass
                        nominas_grupo+=candidatos
                # Evitar duplicados si el PDF incluye la misma nómina en varias hojas.
                nominas_grupo=list({w['trabajador_rut']:w for w in nominas_grupo}.values())
                ruts={w['trabajador_rut'] for w in nominas_grupo}
                warnings=[]
                if not sumas:warnings.append('No se adjuntó página de resumen para este número de certificado')
                if not t0:warnings.append('No se adjuntó portada de empresa para este número de certificado')
                if not rut:warnings.append('RUT de contratista no validado mediante módulo 11; revisar PDF')
                if not periodo:warnings.append('Período no identificado')
                if sumas and monto is None:warnings.append('Monto de remuneraciones no reconocido; cotejar PDF')
                if pagado=='NO_LEIBLE':warnings.append('Estado de cotizaciones no legible; cotejar sección 3')
                if not any(pagina_es_nomina(p['texto']) for p in pags):warnings.append('No hay nómina individual en este certificado; no es posible identificar faltantes por RUT')
                if n is not None and ruts and len(ruts)!=n:
                    warnings.append(f'Nómina leída {len(ruts)} de {n}; comprobar OCR y páginas')
                    # Una cantidad OCR incompatible con nómina NO es evidencia
                    # firme de menos trabajadores: quitar número hasta cotejo.
                    if n<len(ruts):n=None
                if fid.startswith('SIN_FOLIO_'):warnings.append('Folio no identificado; requiere validación')
                for w in nominas_grupo:nominas.append(w)
                # Clave que corresponde a este folio, exclusivamente extraída del resumen.
                clave=''
                for tx in sumas:
                    m=re.search(r'Clave\s*[:;]\s*([a-zA-Z0-9]{6,14})',tx,re.I)
                    if m:clave=m.group(1);break
                periodo_nombre=mes_en_nombre_archivo(nombre)
                diferencia_nombre=bool(periodo_nombre and periodo and periodo_nombre!=periodo)
                certificados.append({
                    'mes_nombre_archivo':periodo_nombre,
                    'mes_archivo_distinto_periodo':diferencia_nombre,
                    'remuneraciones_pagadas':remuneraciones_pagadas,
                    'remuneraciones_no_pagadas':remuneraciones_no_pagadas,
                    'archivo':nombre,'sha256_documento':hash_file,'certificado':fid,
                    'clave':clave,'fecha_emision':fecha_emision,'contratista_nombre':empresa,'contratista_rut':rut,
                    'empresa_principal_rut':principal,'obra_faena':obra,'periodo_remuneracion':periodo,
                    'trabajadores_vigentes':n,'cotizaciones_previsionales':pagado,
                    'monto_remuneraciones_informado':monto,
                    'trabajadores_en_nomina':len(ruts),'nomina_disponible':bool(ruts),
                    'paginas':', '.join(str(p['pagina']) for p in pags),
                    'tipo_extraccion':'OCR' if any(p['origen']=='OCR' for p in pags) else 'DIGITAL',
                    'verificacion_dt':'PENDIENTE_PORTAL_DT',
                    'control_documental':'REVISAR_EXTRACCION' if warnings and any(x.startswith(('No se adjuntó página','RUT de contratista','Período no','Nómina leída','Folio no')) for x in warnings)
                          else 'PAGO_DECLARADO_SI' if pagado=='SI_DECLARADO' else 'REVISAR_PAGO',
                    'observaciones':' | '.join(warnings),
                })
                for w in warnings:alertas.append({'archivo':nombre,'certificado':fid,'tipo':'EXTRACCION_O_ALCANCE','detalle':w})
        except Exception as exc:
            alertas.append({'archivo':nombre,'certificado':'','tipo':'ERROR_ARCHIVO','detalle':f'{type(exc).__name__}: {str(exc)[:350]}'})
        finally:
            if progreso:progreso(ix,len(archivos),nombre)
    return (pd.DataFrame(certificados),pd.DataFrame(nominas),pd.DataFrame(alertas))


def conciliar_f301(certificados:pd.DataFrame,nominas:pd.DataFrame,previred:pd.DataFrame,coincidencia_obra_exclusiva=False):
    """Comparar únicamente mismo contratista + período; RUT solo con nómina disponible.

    Empresa/mes idénticos no garantizan idéntico centro de costo (obra). La diferencia
    de cobertura global se etiqueta como 'REVISAR_ALCANCE', no deuda demostrada.
    """
    columnas=['certificado','contratista_nombre','periodo_remuneracion','trabajador_rut','trabajador_nombre',
              'presencia_f301','presencia_previred','tipo_hallazgo','detalle']
    if certificados.empty:return pd.DataFrame(columns=columnas),pd.DataFrame()
    if previred is None or previred.empty:
        return pd.DataFrame(columns=columnas),pd.DataFrame([{'certificado':c['certificado'],'resultado':'SIN_PREVIRED','detalle':'Cargar cotizaciones del mismo período para conciliar'} for _,c in certificados.iterrows()])
    filas=[];resumen=[]
    tmp=previred.copy()
    for c in ('contratista_rut','trabajador_rut','periodo_remuneracion'):
        if c not in tmp.columns:tmp[c]=''
        tmp[c]=tmp[c].fillna('').astype(str)
    tmp['empresa_validada']=tmp['contratista_rut'].map(rut_normalizado)
    tmp['rut_valido']=tmp['trabajador_rut'].map(rut_normalizado)
    for _,cert in certificados.iterrows():
        cr=cert['contratista_rut'];period=cert['periodo_remuneracion'];folio=cert['certificado']
        base=tmp[(tmp['empresa_validada']==cr)&(tmp['periodo_remuneracion']==period)] if cr and period else tmp.iloc[0:0]
        rtset={x for x in base['rut_valido'] if x}
        detail=nominas[nominas['certificado']==folio] if not nominas.empty else pd.DataFrame()
        fset={rut_normalizado(x) for x in detail.get('trabajador_rut',pd.Series(dtype=str)) if rut_normalizado(x)}
        if not cr or not period:
            resultado='REVISAR_IDENTIFICACION'
            descripcion='No se pudo validar RUT y período F30-1 para conciliar'
        elif not len(base):
            resultado='SIN_PREVIRED_COINCIDENTE'
            descripcion='No se encontraron cotizaciones con el mismo RUT empleador y período'
        elif not fset:
            resultado='SIN_NOMINA_F301'
            descripcion=('F30-1 no contiene nómina individual legible: imposible comparar RUT. '
                f'F30-1 informa {cert["trabajadores_vigentes"]} vigentes; PREVIRED {len(rtset)} trabajadores únicos (puede incluir otras obras).')
        else:
            faltan_f=fset-rtset; faltan_p=rtset-fset
            if faltan_f:
                resultado='ALERTA_F301_SIN_COTIZACION_RUT'
                descripcion=f'{len(faltan_f)} RUT presentes en F30-1 sin planilla Previred adjunta del período'
            elif faltan_p:
                resultado='ALERTA_PREVIRED_FUERA_F301' if coincidencia_obra_exclusiva else 'REVISAR_ALCANCE_OBRA'
                descripcion=f'{len(faltan_p)} RUT Previred no aparecen en nómina F30-1; podrían pertenecer a otra obra'
            else:
                resultado='RUT_CONCILIADOS'
                descripcion='Coinciden RUT de nómina F30-1 y Previred para este período'
            for rut in sorted(faltan_f):
                n=detail[detail['trabajador_rut'].map(rut_normalizado)==rut]
                filas.append({'certificado':folio,'contratista_nombre':cert['contratista_nombre'],
                    'periodo_remuneracion':period,'trabajador_rut':rut,'trabajador_nombre':n.iloc[0]['trabajador_nombre'] if len(n) else '',
                    'presencia_f301':'SI','presencia_previred':'NO','tipo_hallazgo':'ALERTA_RUT_F301_NO_PREVIRED',
                    'detalle':'Verificar otras planillas o faltante de pago antes de concluir incumplimiento'})
            for rut in sorted(faltan_p):
                n=base[base['rut_valido']==rut]
                filas.append({'certificado':folio,'contratista_nombre':cert['contratista_nombre'],
                    'periodo_remuneracion':period,'trabajador_rut':rut,'trabajador_nombre':n.iloc[0]['trabajador_nombre'] if len(n) else '',
                    'presencia_f301':'NO','presencia_previred':'SI',
                    'tipo_hallazgo':'ALERTA_RUT_PREVIRED_NO_F301' if coincidencia_obra_exclusiva else 'REVISAR_OTRA_OBRA',
                    'detalle':'Verificar si el trabajador pertenece a otra obra o si la nómina F30-1 está incompleta'})
        resumen.append({'certificado':folio,'contratista_nombre':cert['contratista_nombre'],'periodo_remuneracion':period,
            'obra_faena':cert['obra_faena'],'f301_vigentes':cert['trabajadores_vigentes'],
            'f301_rut_nomina':len(fset),'previred_rut':len(rtset),'resultado':resultado,'detalle':descripcion,
            'verificacion_dt':cert['verificacion_dt']})
    return pd.DataFrame(filas,columns=columnas),pd.DataFrame(resumen)


def exportar_f301_excel(certificados,nominas,conciliacion,resumen,incidencias):
    """Exportación en memoria; sin imprimir claves de DT en hojas compartidas."""
    import pandas as pd
    out=BytesIO()
    with pd.ExcelWriter(out,engine='xlsxwriter',engine_kwargs={'options':{'strings_to_formulas':False,'strings_to_urls':False}}) as writer:
        for sheet,df in [('F301_Certificados',certificados.drop(columns=['clave','sha256_documento'],errors='ignore')),
                         ('F301_Nomina',nominas),('Conciliacion_RUT',conciliacion),('Resumen_Cruce',resumen),('Incidencias',incidencias)]:
            (df if not df.empty else pd.DataFrame({'informacion':['Sin datos en este apartado']})).to_excel(writer,sheet_name=sheet,index=False)
            ws=writer.sheets[sheet];ws.freeze_panes(1,0)
            ws.set_row(0,29,writer.book.add_format({'bg_color':'#16243B','font_color':'white','bold':True,'text_wrap':True}))
            ws.autofilter(0,0,max(1,len(df)),max(0,len(df.columns)-1))
            ws.set_column(0,min(max(0,len(df.columns)-1),14),23)
            if 'detalle' in df.columns:ws.set_column(int(df.columns.get_loc('detalle')),int(df.columns.get_loc('detalle')),75)
            if 'observaciones' in df.columns:ws.set_column(int(df.columns.get_loc('observaciones')),int(df.columns.get_loc('observaciones')),90)
    return out.getvalue()
