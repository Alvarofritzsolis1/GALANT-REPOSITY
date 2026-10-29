"""Memoria de correcciones validada por humanos y clasificador opcional de plantillas.

Nunca aprende valores monetarios a partir de sus propias predicciones.
Una corrección aplica SOLO al SHA256 exacto del PDF, RUT y período indicados.
La memoria se descarga/importa como JSON: Streamlit Cloud NO garantiza persistencia.
"""
from __future__ import annotations
import hashlib
import json
import re
from datetime import datetime,timezone
from typing import Callable

CAMPOS_EDITABLES={'dias_reales_pagados','renta_imponible_salud','renta_imponible_afp','renta_imponible_mutual','mutual_adherida'}
TIPOS_PAGINA=('afp','salud','isapre','mutual','seguro_social','otro')


def sha256_pdf(pdf:bytes)->str:
    return hashlib.sha256(pdf).hexdigest()


def nueva_memoria()->dict:
    return {'version':1,'correcciones':[],'plantillas':[],'verificaciones_ocr':[]}


def cargar_memoria(data:bytes|None)->dict:
    if not data:return nueva_memoria()
    obj=json.loads(data.decode('utf-8-sig'))
    if not isinstance(obj,dict) or obj.get('version')!=1 or not isinstance(obj.get('correcciones'),list) or not isinstance(obj.get('plantillas'),list):
        raise ValueError('Memoria JSON inválida: se espera version=1, correcciones y plantillas como listas.')
    obj.setdefault('verificaciones_ocr',[])
    if not isinstance(obj['verificaciones_ocr'],list):raise ValueError('Memoria JSON inválida: verificaciones_ocr debe ser lista.')
    if len(obj['correcciones'])>2000 or len(obj['plantillas'])>2000 or len(obj['verificaciones_ocr'])>2000:
        raise ValueError('La memoria supera el tamaño permitido.')
    return obj


def guardar_memoria(obj:dict)->bytes:
    return json.dumps(obj,ensure_ascii=False,indent=2).encode('utf-8')


def registrar_correccion(memoria:dict,archivo_sha:str,rut:str,periodo:str,campo:str,valor_anterior:str|int,valor_nuevo:str|int,confirmacion:bool)->None:
    if not confirmacion:raise ValueError('Una corrección debe ser confirmada expresamente por quien revisa.')
    if not re.fullmatch('[0-9a-f]{64}',archivo_sha):raise ValueError('SHA256 inválido')
    if campo not in CAMPOS_EDITABLES:raise ValueError('Campo de corrección no permitido')
    from auditoria_laboral import clave_rut,rut_chileno_valido
    rut=clave_rut(rut)
    if not rut_chileno_valido(rut):raise ValueError('RUT de corrección inválido')
    if not re.fullmatch(r'20\d\d-(0[1-9]|1[0-2])',periodo):raise ValueError('Período inválido')
    if campo.startswith('renta_') or campo=='dias_reales_pagados':
        try:
            valor_nuevo=int(valor_nuevo)
            valor_anterior=int(valor_anterior)
        except (TypeError,ValueError):
            # Un campo originalmente ausente se expresa con vacío.
            if str(valor_anterior).strip() in ('','None','nan'):
                valor_anterior=None
                valor_nuevo=int(valor_nuevo)
            else:raise ValueError('El valor numérico debe ser un entero')
        if valor_nuevo<0 or (campo=='dias_reales_pagados' and valor_nuevo>31):raise ValueError('Corrección fuera de rango')
    else:
        valor_nuevo=str(valor_nuevo).strip()[:80]
        valor_anterior=str(valor_anterior).strip()
        if not valor_nuevo:raise ValueError('Mutual corregida vacía')
    key=(archivo_sha,rut,periodo,campo)
    memoria['correcciones']=[x for x in memoria['correcciones'] if (x.get('sha256'),x.get('trabajador_rut'),x.get('periodo'),x.get('campo'))!=key]
    memoria['correcciones'].append({'sha256':archivo_sha,'trabajador_rut':rut,'periodo':periodo,'campo':campo,
                                    'valor_anterior':valor_anterior,'valor_nuevo':valor_nuevo,
                                    'confirmado':True,'fecha_utc':datetime.now(timezone.utc).isoformat()})


def aplicar_correcciones(df,memoria:dict,archivo_sha:str):
    """Revisiones exactas de documento; NO se generalizan a otra nómina."""
    import pandas as pd
    out=df.copy()
    eventos=[]
    if '__revision_manual' not in out:out['__revision_manual']=''
    for x in memoria.get('correcciones',[]):
        if x.get('sha256')!=archivo_sha or x.get('confirmado') is not True or x.get('campo') not in CAMPOS_EDITABLES:continue
        campo=x['campo']
        for i,row in out.iterrows():
            from auditoria_laboral import clave_rut,normalizar_periodo
            if clave_rut(row.get('trabajador_rut',''))!=x.get('trabajador_rut') or normalizar_periodo(row.get('periodo_remuneracion',''))!=x.get('periodo'):continue
            actual=row.get(campo)
            previo=x.get('valor_anterior')
            if str(actual).strip()!=str(previo).strip():
                eventos.append(f"No aplicada corrección {campo} del RUT {x['trabajador_rut']}: valor original cambió")
                continue
            out.at[i,campo]=x['valor_nuevo']
            out.at[i,'__revision_manual']=str(out.at[i,'__revision_manual'])+f'; {campo}: validado por usuario'
            eventos.append(f"Corrección validada aplicada a {campo} del RUT {x['trabajador_rut']}")
    return out,eventos


def registrar_verificacion_ocr(memoria: dict, archivo_sha: str, rut: str, periodo: str,
                               valores: dict, revisor: str, confirmado: bool) -> None:
    """Validación HUMANA y acotada: no se propaga a otros documentos ni RUT.

    Una confirmación afirma que el revisor cotejó RUT, días, tres bases y
    mutual contra el documento original. Es una validación de DATOS, no legal.
    """
    from auditoria_laboral import clave_rut, rut_chileno_valido
    if not confirmado:raise ValueError('Confirma haber cotejado todos los campos con el documento original.')
    if not re.fullmatch(r'[0-9a-f]{64}',archivo_sha):raise ValueError('SHA256 inválido.')
    rut=clave_rut(rut)
    if not rut_chileno_valido(rut):raise ValueError('RUT inválido.')
    if not re.fullmatch(r'20\d{2}-(?:0[1-9]|1[0-2])',str(periodo)):raise ValueError('Período inválido.')
    revisor=' '.join(str(revisor).split())[:90]
    if len(revisor)<5:raise ValueError('Identifica al revisor responsable (mínimo 5 caracteres).')
    requeridos=['dias_reales_pagados','renta_imponible_salud','renta_imponible_afp','renta_imponible_mutual','mutual_adherida']
    if not all(k in valores for k in requeridos):raise ValueError('Debes revisar las tres bases, días y mutual.')
    instantanea={k:int(valores[k]) for k in requeridos[:4]}
    instantanea['mutual_adherida']=str(valores['mutual_adherida']).strip()
    if not 0<instantanea['dias_reales_pagados']<=31 or min(instantanea[k] for k in requeridos[1:4])<=0:
        raise ValueError('No se pueden aprobar datos incompletos o días fuera de rango.')
    if instantanea['mutual_adherida'].upper() in ('','NO IDENTIFICADA'):
        raise ValueError('La mutual debe estar identificada.')
    ident=(archivo_sha,rut,periodo)
    memoria.setdefault('verificaciones_ocr',[])
    memoria['verificaciones_ocr']=[x for x in memoria['verificaciones_ocr']
        if (x.get('sha256'),x.get('rut'),x.get('periodo'))!=ident]
    memoria['verificaciones_ocr'].append({'sha256':archivo_sha,'rut':rut,'periodo':periodo,
           'valores':instantanea,'revisor':revisor,'confirmado':True,
           'fecha_utc':datetime.now(timezone.utc).isoformat()})


def aplicar_verificaciones_ocr(df,memoria:dict,archivo_sha:str):
    """Despeja SOLO avisos OCR confirmados, conservando evidencia y controles duros.

    Nunca despeja errores de bases, datos faltantes, documento incompleto,
    ni diferencias de días pendientes. Las verificaciones requieren hash,
    RUT, período y valores exactos, lo que evita reutilización por accidente.
    """
    from auditoria_laboral import clave_rut, normalizar_periodo, rut_chileno_valido
    out=df.copy()
    out['__ocr_verificacion_humana']=''
    eventos=[]
    pendientes_globales=out['__ocr_incidencias_documento'].astype(str).str.strip().ne('').any() if '__ocr_incidencias_documento' in out else False
    for x in memoria.get('verificaciones_ocr',[]):
        if x.get('sha256')!=archivo_sha or x.get('confirmado') is not True:continue
        for i,row in out.iterrows():
            if clave_rut(row.get('trabajador_rut',''))!=x.get('rut') or normalizar_periodo(row.get('periodo_remuneracion',''))!=x.get('periodo'):continue
            if row.get('__ocr_origen','')!='ESCANEADO':continue
            if not rut_chileno_valido(row['trabajador_rut']):continue
            if pendientes_globales:
                eventos.append('Validación no aplicada: hay pendientes OCR globales sin identificar')
                continue
            # No aceptar un dato cuyo estado real no se puede cerrar aunque
            # alguien marque la casilla: debe intervenir un revisor de calidad.
            aviso=str(row.get('__conflicto_pdf','') or '')
            if any(k in aviso for k in ('Falta detalle','Descuadre de bases','Días leídos por OCR difieren','Días de Salud ilegibles')):
                eventos.append('Validación no aplicada: hay discrepancia material o dato faltante')
                continue
            igual=all(str(row.get(k))==str(v) for k,v in x.get('valores',{}).items())
            if not igual:
                eventos.append('Validación OCR no aplicada: cambiaron los valores extraídos')
                continue
            s,a,m=(int(row[k]) for k in ('renta_imponible_salud','renta_imponible_afp','renta_imponible_mutual'))
            if not (s==a==m and s>0 and 0<int(row['dias_reales_pagados'])<=31):
                eventos.append('Validación no aplicada: las tres bases no coinciden')
                continue
            # Solo los avisos de incertidumbre OCR se resuelven explícitamente.
            out.at[i,'__conflicto_pdf']=''
            out.at[i,'__ocr_verificacion_humana']=x['revisor']+' · '+x.get('fecha_utc','')
            eventos.append('Verificación humana del OCR confirmada para un RUT')
    return out,eventos


def limpiar_encabezado_para_ml(texto:str)->str:
    """Solo señales de diseño, nunca nombres, RUT, correos ni valores salariales.

    El archivo JSON de entrenamiento contiene etiquetas de diseño y no líneas
    completas de una nómina personal.
    """
    import unicodedata
    patrones={
        'afp', 'pension', 'fondo de pensiones', 'fonasa', 'isapre',
        'seguro social', 'mutual de seguridad', 'seguridad laboral',
        'caja de compensacion', 'anexos de detalle', 'detalle de cotizaciones',
        'declaracion y pago', 'seguro cesantia', 'dias trabajados',
        'remuneracion imponible', 'identificacion del trabajador',
        'folio', 'movimiento de personal', 'tipo jornada', 'rut',
        'salud', 'cotizacion', 'comprobante de pago',
    }
    tokens=[]
    for raw in texto.splitlines()[:28]:
        linea=unicodedata.normalize('NFKD',raw.lower())
        linea=''.join(c for c in linea if not unicodedata.combining(c))
        # descartar por completo líneas con datos identificables o montos,
        # aun si también incluyen una palabra de formulario.
        if re.search(r'\b\S+@\S+\b',linea) or re.search(r'\d{1,2}\.\d{3}\.\d{3}[-\s]?[0-9k]',linea):
            continue
        for clave in sorted(patrones,key=len,reverse=True):
            if clave in linea and clave not in tokens:
                tokens.append(clave)
    return ' | '.join(tokens)[:900]


def registrar_plantilla(memoria:dict,encabezado:str,tipo:str,confirmacion:bool)->None:
    if not confirmacion:raise ValueError('Confirma manualmente el tipo de página.')
    if tipo not in TIPOS_PAGINA:raise ValueError('Tipo de página no permitido')
    k=limpiar_encabezado_para_ml(encabezado)
    if len(k)<12:raise ValueError('Encabezado insuficiente para aprendizaje')
    h=hashlib.sha256(k.encode()).hexdigest()
    memoria['plantillas']=[p for p in memoria['plantillas'] if p.get('hash')!=h]
    memoria['plantillas'].append({'hash':h,'encabezado':k,'tipo':tipo,'confirmado':True})


def clasificador_aprendido(memoria:dict)->Callable[[str],str]|None:
    """Memoria inmediata exacta + scikit-learn si hay suficientes ejemplos.

    El aprendizaje de un diseño ya visto funciona desde la PRIMERA etiqueta
    confirmada. Para diseños NUEVOS, el modelo solo sugiere tipo si hubo
    al menos 12 ejemplos y la confianza estimada >= 95%.
    Nunca se usa para predecir importes o completar trabajadores.
    """
    plantillas=[x for x in memoria.get('plantillas',[]) if x.get('confirmado') and x.get('tipo') in TIPOS_PAGINA]
    if not plantillas:return None
    conocidos={x['hash']:x['tipo'] for x in plantillas}
    modelo_vec=None
    clasificador=None
    if len(plantillas)>=12 and len(set(p['tipo'] for p in plantillas))>=2:
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.linear_model import LogisticRegression
            modelo_vec=TfidfVectorizer(ngram_range=(1,2),max_features=4096,strip_accents='unicode')
            datos=modelo_vec.fit_transform([p['encabezado'] for p in plantillas])
            clasificador=LogisticRegression(max_iter=350,class_weight='balanced')
            clasificador.fit(datos,[p['tipo'] for p in plantillas])
        except (ImportError,ValueError):
            modelo_vec=None; clasificador=None
    def predecir(texto:str)->str:
        if not texto.strip():return ''
        limpio=limpiar_encabezado_para_ml(texto)
        h=hashlib.sha256(limpio.encode()).hexdigest()
        if h in conocidos:
            return conocidos[h] if conocidos[h]!='otro' else ''
        if clasificador is None:return ''
        probs=clasificador.predict_proba(modelo_vec.transform([limpio]))[0]
        import numpy as np
        i=int(np.argmax(probs))
        return str(clasificador.classes_[i]) if float(probs[i])>=.95 and clasificador.classes_[i]!='otro' else ''
    return predecir
