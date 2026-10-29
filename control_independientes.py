"""V8 · Evidencia independiente separada de la planilla del empleador.

No deduce propiedad ni cobertura legal solamente a partir de un RUT coincidente.
No suma las rentas de la empresa con las personales. Usa texto digital PyMuPDF;
los escaneos no se aprueban sin verificar su lectura.
"""
from __future__ import annotations
from collections import defaultdict
from typing import Any, Sequence
import re
import unicodedata
import pandas as pd

from previred_pdf import RUT_RE, _rut_clean, _rut_ok

MESES = {"enero":1,"febrero":2,"marzo":3,"abril":4,"mayo":5,"junio":6,
         "julio":7,"agosto":8,"septiembre":9,"setiembre":9,"octubre":10,
         "noviembre":11,"diciembre":12}
CAMPOS = ["periodo_remuneracion", "persona_nombre", "persona_rut", "empresa_nombre",
          "empresa_rut", "tipo_relacion_identificada", "mutual_independiente_entidad",
          "independiente_mutual_base", "independiente_mutual_dias", "independiente_mutual_pagado",
          "independiente_afp_base", "independiente_salud_base", "empresa_afp_base",
          "empresa_salud_base", "empresa_mutual_base", "empresa_dias_declarados",
          "estado_control_independiente", "requiere_revision", "explicacion", "archivos_respaldo", "paginas_respaldo"]


def _fold(text: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKD', text.lower()) if not unicodedata.combining(c))


def _num(text: str) -> int | None:
    text = text.strip()
    return int(text.replace('.','')) if re.fullmatch(r'\d{1,3}(?:\.\d{3})*|\d+',text) else None


def _valor(texto: str, patron: str) -> int | None:
    m=re.search(patron+r'\s*\n\s*\$?\s*(\d[\d.]*)\b',_fold(texto),flags=re.I|re.M)
    return _num(m.group(1)) if m else None


def _periodo(texto: str) -> str:
    k=_fold(texto)
    m=re.search(r'periodo\s+de\s+remuneraci(?:on|ones)\s*:?\s*(0?[1-9]|1[0-2])\s*[/\-]\s*(20\d{2})',k)
    if m: return f'{m.group(2)}-{int(m.group(1)):02d}'
    m=re.search(r'periodo\s*(?:\n|\s)+('+'|'.join(MESES)+r')\s+(20\d{2})',k)
    if m: return f'{m.group(2)}-{MESES[m.group(1)]:02d}'
    m=re.search(r'\bremuneracion\s*\n\s*(0?[1-9]|1[0-2])\s*\n\s*(20\d{2})\b',k)
    if m: return f'{m.group(2)}-{int(m.group(1)):02d}'
    return ''


def _rut_principal(texto: str) -> str:
    texto=_fold(texto)
    rut=RUT_RE.search(texto)
    return _rut_clean(rut.group(1)) if rut and _rut_ok(rut.group(1)) else ''


def _nombre_persona(texto: str, tipo: str) -> str:
    if tipo=='salud_ind':
        m=re.search(r'Raz[oó]n Social o Nombre:\s*([^\n]+)',texto,re.I)
        return m.group(1).strip().upper() if m else ''
    if tipo=='mutual_ind':
        m=re.search(r'Nombre\s*\nEmail\s*\nRUT\s*\nTel[eé]fono\s*\n([^\n]+)',texto,re.I)
        return m.group(1).strip().upper() if m else ''
    m=re.search(r'Nombre o Raz[oó]n Social\s*\nRUT(?:[^\n]*)?\n(?:C[oó]d\. Actividad Econ[oó]mica\s*\n)?([^\n]+)',texto,re.I)
    return m.group(1).strip().upper() if m else ''


def _tipo_independiente(texto: str) -> str:
    k=_fold(texto)
    if 'identificacion del trabajador independiente' in k and ('instituto de seguridad laboral' in k or 'mutual de seguridad' in k or 'asociacion chilena de seguridad' in k or 'instituto de seguridad del trabajo' in k):
        return 'mutual_ind'
    if 'identificacion del empleador: empleador de trabajador independiente' in k:
        if 'afp ' in k and 'fondo de pensiones' in k: return 'afp_ind'
        if 'seguro social' in k: return 'ss_ind'
    if re.search(r'trabajador independiente\s*\n\s*x\b',k) and 'planilla de declaracion y pago simultaneo' in k:
        return 'salud_ind'
    return ''


def _perfil_empleador(paginas: list[str]) -> dict[str,str] | None:
    for txt in paginas:
        k=_fold(txt)
        if 'identificacion del empleador: empresa' not in k: continue
        # El primer RUT de esta carátula corresponde al empleador, NO al representante.
        ruts=[_rut_clean(m.group(1)) for m in RUT_RE.finditer(txt[:1800]) if _rut_ok(m.group(1))]
        if not ruts: continue
        empresa_rut=ruts[0]
        # Ubicar el RUT posterior al encabezado "RUT Representante Legal".
        indice=k.find('rut representante legal')
        if indice<0: indice=k.find('r.u.t. repr. legal')
        if indice<0: continue
        tramo=txt[indice:indice+700]
        otros=[_rut_clean(m.group(1)) for m in RUT_RE.finditer(tramo) if _rut_ok(m.group(1))]
        rep=next((r for r in otros if r != empresa_rut),'')
        if not rep:continue
        # Buscar nombre de empresa por cabecera; en PDFs su orden puede variar.
        nombre=''
        m=re.search(r'Nombre o Raz[oó]n Social\s*\nRUT\s*\n([^\n]+)',txt,re.I)
        if m: nombre=m.group(1).strip()
        if not nombre:
            m=re.search(r'Raz[oó]n Social o Nombre:\s*([^\n]+)',txt,re.I)
            if m:nombre=m.group(1).strip()
        return {'empresa_rut':empresa_rut,'empresa_nombre':nombre.upper(),'representante_rut':rep}
    return None


def extraer_evidencia(archivos: Sequence[tuple[bytes,str]]) -> tuple[list[dict[str,Any]],list[dict[str,str]],set[str]]:
    import fitz
    docs=[]; perfiles=[]; solo_ind=set()
    for contenido,nombre in archivos:
        if not nombre.lower().endswith('.pdf'):continue
        try:
            with fitz.open(stream=contenido,filetype='pdf') as doc:
                paginas=[p.get_text('text',sort=False) for p in doc]
        except Exception:continue
        perfil=_perfil_empleador(paginas)
        if perfil:
            perfiles.append({**perfil,'archivo':nombre})
        tipos=[_tipo_independiente(t) for t in paginas]
        tipos_presentes={x for x in tipos if x}
        if not tipos_presentes:continue
        # No confundir la sola frase "trabajador independiente" en los campos de opción
        # de un empleador general con una planilla independiente real.
        if tipos_presentes == {'mutual_ind'} and len(paginas)==1:
            solo_ind.add(nombre)
        elif 'salud_ind' in tipos_presentes and 'afp_ind' in tipos_presentes and not perfil:
            solo_ind.add(nombre)
        fallback_periodo=next((p for t in paginas if (p:=_periodo(t))), '')
        fallback_rut='';fallback_nombre=''
        for i,(txt,tipo) in enumerate(zip(paginas,tipos)):
            if not tipo:continue
            rut=_rut_principal(txt)
            persona=_nombre_persona(txt,tipo)
            if not rut and fallback_rut: rut=fallback_rut
            if not persona and fallback_nombre:persona=fallback_nombre
            if rut: fallback_rut=rut
            if persona:fallback_nombre=persona
            per=_periodo(txt) or fallback_periodo
            registro={'archivo':nombre,'pagina':i+1,'tipo':tipo,'rut':rut,'nombre':persona,'periodo':per,
                      'base':None,'dias':None,'pagado':None,'entidad':''}
            if tipo=='mutual_ind':
                registro['base']=_valor(txt,r'total renta imponible')
                registro['dias']=_valor(txt,r'dias trabajados')
                registro['pagado']=_valor(txt,r'total a pagar a la mutual')
                k=_fold(txt)
                registro['entidad']=('ISL' if 'instituto de seguridad laboral' in k else
                    'ACHS' if 'asociacion chilena de seguridad' in k else
                    'IST' if 'instituto de seguridad del trabajo' in k else 'Mutual de Seguridad')
            elif tipo=='salud_ind':
                registro['base']=_valor(txt,r'total remuneracion imponible declarada en\s*\$')
                registro['entidad']='FONASA'
            elif tipo=='afp_ind':
                registro['base']=_valor(txt,r'^renta imponible')
                registro['entidad']='AFP'
            elif tipo=='ss_ind':
                registro['base']=_valor(txt,r'total remuneracion imponible declarada en\s*\$')
                registro['entidad']='SEGURO SOCIAL'
            if registro['rut'] and _rut_ok(registro['rut']) and per:
                docs.append(registro)
    return docs,perfiles,solo_ind


def construir_control(archivos: Sequence[tuple[bytes,str]], matriz:pd.DataFrame|None=None,
                      datos_preextraidos:tuple|None=None) -> pd.DataFrame:
    docs,perfiles,_=datos_preextraidos or extraer_evidencia(archivos)
    agrupado=defaultdict(lambda:defaultdict(list))
    for d in docs:
        agrupado[(d['rut'],d['periodo'])][d['tipo']].append(d)
    # Generar revisión para el dueño/representante que también figura en planilla de empresa.
    if matriz is not None and not matriz.empty:
        for prof in perfiles:
            r=prof['representante_rut']
            sub=matriz[(matriz['trabajador_rut'].astype(str)==r)&
                       (matriz['contratista_rut'].astype(str)==prof['empresa_rut'])]
            for _,row in sub.iterrows():
                agrupado.setdefault((r,str(row['periodo_remuneracion'])),defaultdict(list))
    filas=[]
    for (rut,periodo),grupo in sorted(agrupado.items()):
        perfiles_persona=[p for p in perfiles if p['representante_rut']==rut]
        perfil=perfiles_persona[0] if perfiles_persona else {}
        nombre=next((d['nombre'] for g in grupo.values() for d in g if d.get('nombre')),'')
        def valores(tipo,campo):
            return {d[campo] for d in grupo.get(tipo,[]) if d.get(campo) is not None}
        conflictos=[]
        for tipo in ('mutual_ind','afp_ind','salud_ind'):
            if len(valores(tipo,'base'))>1: conflictos.append(f'{tipo}: bases incompatibles entre comprobantes')
        def uno(tipo,campo):
            v=valores(tipo,campo)
            return next(iter(v)) if len(v)==1 else None
        mut=uno('mutual_ind','base');dias=uno('mutual_ind','dias');pago=uno('mutual_ind','pagado')
        afp=uno('afp_ind','base');sal=uno('salud_ind','base')
        mut_entidades={d['entidad'] for d in grupo.get('mutual_ind',[]) if d.get('entidad')}
        if len(mut_entidades)>1:conflictos.append('Mutuales distintas en comprobantes independientes')
        empresa_row=None
        if perfil and matriz is not None and not matriz.empty:
            coinciden=matriz[(matriz['trabajador_rut'].astype(str)==rut)&
                (matriz['periodo_remuneracion'].astype(str)==periodo)&
                (matriz['contratista_rut'].astype(str)==perfil['empresa_rut'])]
            if len(coinciden)==1:empresa_row=coinciden.iloc[0]
            elif len(coinciden)>1:conflictos.append('Registros de empresa duplicados; no se suman')
        empresa_afp=int(empresa_row['renta_imponible_afp']) if empresa_row is not None else None
        empresa_sal=int(empresa_row['renta_imponible_salud']) if empresa_row is not None else None
        empresa_mut=int(empresa_row['renta_imponible_mutual']) if empresa_row is not None else None
        empresa_dias=int(empresa_row['dias_reales_pagados']) if empresa_row is not None else None
        razon=[]
        if conflictos:
            estado='CONFLICTO_EVIDENCIA_INDEPENDIENTE';razon+=conflictos
        elif mut is not None and dias and dias>0 and afp is not None and sal is not None and mut==afp==sal:
            estado='BASES_INDEPENDIENTES_CONCILIADAS';razon.append('AFP, Salud y Mutual se acreditan como independiente con bases coincidentes; no sustituye verificación de afiliación, vigencia ni propiedad')
        elif mut is not None and dias and dias>0 and empresa_row is not None:
            estado='MUTUAL_INDEPENDIENTE_CON_PAGO_EN_EMPRESA';razon.append('Mutual personal independiente acreditada. AFP/Salud de la EIRL son otra modalidad y no se equiparan automáticamente a las cotizaciones del independiente')
        elif mut is not None and dias and dias>0:
            estado='MUTUAL_INDEPENDIENTE_ACREDITADA';razon.append('Pago independiente de Mutual acreditado; faltan fuentes personales de pensión/salud en los documentos analizados')
        elif afp is not None or sal is not None:
            estado='FALTA_MUTUAL_INDEPENDIENTE';razon.append('Se adjuntó AFP o Salud personal, pero no pago independiente de Mutual en el conjunto de archivos')
        else:
            estado='REPRESENTANTE_EN_PLANILLA_EMPRESA';razon.append('El representante legal aparece como trabajador de la empresa; comprobar participación, administración y modalidad jurídica antes de dictaminar')
        if empresa_row is not None:
            if estado=='MUTUAL_INDEPENDIENTE_CON_PAGO_EN_EMPRESA' and empresa_mut is not None:
                razon.append('El RUT también registra Mutual en planilla de la EIRL. Si se confirma participación mayoritaria y administración, verificar la modalidad con ISL/SUSESO; la cobertura personal no se sustituye con esa planilla')
            nombre_empresa_trab=str(empresa_row.get('trabajador_nombre','')).strip()
            if nombre_empresa_trab and nombre and sorted(_fold(nombre_empresa_trab).split())!=sorted(_fold(nombre).split()):
                razon.append('El nombre presenta diferencias entre comprobante personal y planilla de empresa; los RUT coinciden. Cotejar grafía')
                nombre=nombre_empresa_trab
            razon.append('La renta y días de la EIRL se conservan aparte y NUNCA se suman a los de su pago personal')
        docs_grupo=[d for g in grupo.values() for d in g]
        if empresa_row is not None:razon.append('Ser representante legal no acredita por sí solo propiedad mayoritaria ni subordinación laboral')
        requiere=estado!='BASES_INDEPENDIENTES_CONCILIADAS' or bool(perfil)
        filas.append({'periodo_remuneracion':periodo,'persona_nombre':nombre or (str(empresa_row['trabajador_nombre']) if empresa_row is not None else ''),
            'persona_rut':rut,'empresa_nombre':perfil.get('empresa_nombre',''),'empresa_rut':perfil.get('empresa_rut',''),
            'tipo_relacion_identificada':'REPRESENTANTE_LEGAL_EN_EMPRESA' if perfil else 'PERSONA_INDEPENDIENTE',
            'mutual_independiente_entidad':next(iter(mut_entidades)) if len(mut_entidades)==1 else '',
            'independiente_mutual_base':mut,'independiente_mutual_dias':dias,'independiente_mutual_pagado':pago,
            'independiente_afp_base':afp,'independiente_salud_base':sal,'empresa_afp_base':empresa_afp,
            'empresa_salud_base':empresa_sal,'empresa_mutual_base':empresa_mut,'empresa_dias_declarados':empresa_dias,
            'estado_control_independiente':estado,'requiere_revision':requiere,'explicacion':' | '.join(razon),
            'archivos_respaldo':' | '.join(sorted(set([d['archivo'] for d in docs_grupo]+([perfil['archivo']] if perfil else [])))),
            'paginas_respaldo':' | '.join(sorted(set(f"{d['archivo']}:p{d['pagina']}" for d in docs_grupo)))})
    return pd.DataFrame(filas,columns=CAMPOS)


def crear_filas_personales(docs:list[dict], solamente_archivos:set[str]) -> dict[str,pd.DataFrame]:
    """Construye una matriz individual SOLO donde AFP, Salud y Mutual personal están en el PDF.

    Un comprobante aislado ISL sin AFP/Salud no crea una fila incompleta en la
    matriz de trabajadores de la empresa; aparece en Control_Independientes.
    """
    grupos=defaultdict(lambda:defaultdict(list))
    for d in docs:
        if d['archivo'] in solamente_archivos: grupos[(d['archivo'],d['rut'],d['periodo'])][d['tipo']].append(d)
    result={}
    for (archivo,rut,periodo),g in grupos.items():
        if not {'mutual_ind','afp_ind','salud_ind'}<=set(g):continue
        def base(t):
            vals={d['base'] for d in g[t] if d['base'] is not None}
            return next(iter(vals)) if len(vals)==1 else None
        mut=base('mutual_ind');afp=base('afp_ind');sal=base('salud_ind')
        dias={d['dias'] for d in g['mutual_ind'] if d['dias'] is not None}
        ds=next(iter(dias)) if len(dias)==1 else None
        entidades={d['entidad'] for d in g['mutual_ind']}
        if any(v is None for v in (mut,afp,sal,ds)) or len(entidades)!=1:continue
        nombre=next((d['nombre'] for t in ('salud_ind','afp_ind','mutual_ind') for d in g[t] if d['nombre']), '')
        result[archivo]=pd.DataFrame([{'periodo_remuneracion':periodo,'contratista_nombre':nombre,
            'contratista_rut':rut,'trabajador_nombre':nombre,'trabajador_rut':rut,
            'dias_reales_pagados':ds,'renta_imponible_salud':sal,'renta_imponible_afp':afp,
            'renta_imponible_mutual':mut,'mutual_adherida':next(iter(entidades)),
            '__hoja_origen':', '.join(f"p.{d['pagina']}" for arr in g.values() for d in arr),
            '__naturaleza_cotizacion':'INDEPENDIENTE_PERSONA'}])
    return result
