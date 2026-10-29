"""Clasificación previa de PDF y enrutamiento sin contaminación de fuentes.

Prioridad: contenido verificable del PDF > nombre del archivo. Un texto que
simplemente menciona Previred NO convierte un F30-1 en planilla de cotizaciones.
No valida autenticidad ni paga cotizaciones.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import re
import unicodedata
from io import BytesIO
from pathlib import Path
import fitz


@dataclass(frozen=True)
class TipoDocumento:
    tipo: str  # F301, COTIZACIONES, F30_ANTECEDENTES, REVISAR_TIPO
    razon: str
    confianza: str
    paginas: int | None = None


def normalizar(txt: str) -> str:
    texto = ''.join(c for c in unicodedata.normalize('NFKD', str(txt)) if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', texto).upper()


def _es_f301(texto: str) -> bool:
    # El título jurídico prevalece sobre las menciones genéricas a Previred.
    return any(x in texto for x in (
        'CERTIFICADO DE CUMPLIMIENTO DE OBLIGACIONES LABORALES Y PREVISIONALES',
        'TRABAJADORES DECLARADOS EN LA CERTIFICACION',
        'VERIFICA ESTE CERTIFICADO EN EL SITIO VERIFICADOR.DIRTRAB.CL',
    ))


def _es_f30_distinto(texto: str) -> bool:
    return ('CERTIFICADO DE ANTECEDENTES LABORALES Y PREVISIONALES' in texto
            and 'CERTIFICADO DE CUMPLIMIENTO' not in texto)


def _es_cotizacion(texto: str) -> bool:
    claves = (
        'DETALLE DE PAGO DE COTIZACIONES PREVISIONALES',
        'FORMULARIO DE COTIZACIONES PREVISIONALES',
        'DETALLE DE COMPROBANTE PAGO DE COTIZACIONES PREVISIONALES',
        'PLANILLA DE DECLARACION Y PAGO SIMULTANEO DE OBLIGACIONES PREVISIONALES',
        'COMPROBANTE DE PAGO DE COTIZACIONES PREVISIONALES',
        'CERTIFICADO DE PAGOS DE COTIZACIONES PREVISIONALES',
    )
    return any(x in texto for x in claves)


def clasificar_documento(contenido: bytes, nombre: str, *, ocr_breve: bool = False) -> TipoDocumento:
    suffix = Path(nombre).suffix.lower()
    if suffix in ('.csv', '.xlsx', '.xls'):
        return TipoDocumento('COTIZACIONES', 'Hoja tabular, no certificado F30-1 PDF', 'ALTA')
    if suffix != '.pdf':
        return TipoDocumento('REVISAR_TIPO', 'Formato no admitido', 'BAJA')
    try:
        with fitz.open(stream=contenido, filetype='pdf') as doc:
            if not doc.page_count:
                return TipoDocumento('REVISAR_TIPO', 'PDF sin páginas', 'BAJA', 0)
            n=doc.page_count
            indices=sorted({0, min(1,n-1), n-1})
            # En PDFs multi-certificado, la primera y última página son muestras,
            # el lector F30-1 definitivo segmenta todos los folios.
            secciones=[doc[i].get_text('text',sort=True) for i in indices]
            texto=normalizar(' '.join(secciones))
            if _es_f301(texto):
                return TipoDocumento('F301', 'Título o verificador F30-1 confirmado en el PDF', 'ALTA', n)
            if _es_f30_distinto(texto):
                return TipoDocumento('F30_ANTECEDENTES', 'F30 de antecedentes (distinto de F30-1)', 'ALTA', n)
            if _es_cotizacion(texto):
                return TipoDocumento('COTIZACIONES', 'Planilla previsional identificada en PDF', 'ALTA', n)
            if ocr_breve and sum(len(s.strip()) for s in secciones)<200:
                # OCR selectivo de UNA página, no repetir OCR del archivo completo.
                import pytesseract
                from PIL import Image
                page=doc[0]
                pix=page.get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False)
                img=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
                lectura=normalizar(pytesseract.image_to_string(img,lang='eng',config='--psm 3',timeout=6))
                if _es_f301(lectura):
                    return TipoDocumento('F301', 'F30-1 identificado con OCR preliminar', 'MEDIA', n)
                if _es_cotizacion(lectura):
                    return TipoDocumento('COTIZACIONES', 'Planilla previsional identificada con OCR', 'MEDIA', n)
            # El nombre solamente aporta una SUGERENCIA, nunca evidencia de pago.
            x=normalizar(nombre)
            if re.search(r'(?:^|[^A-Z0-9])F30(?:[-_ ]?1)?(?:[^A-Z0-9]|$)',x):
                return TipoDocumento('F301', 'Posible F30-1 según nombre; verificar contenido mediante OCR', 'BAJA', n)
            return TipoDocumento('COTIZACIONES', 'Tipo no confirmado, enviada a revisión previsional', 'BAJA', n)
    except Exception as exc:
        return TipoDocumento('REVISAR_TIPO', f'PDF ilegible: {type(exc).__name__}', 'BAJA')


def enrutar_archivos(archivos_cotizaciones, archivos_f301):
    """Acepta [(bytes,filename)]; dedup SHA256, envía cada archivo al lector apropiado."""
    previred, f301, avisos, rechazados = [], [], [], []
    vistos=set()
    for origen,archivos in [('COTIZACIONES',archivos_cotizaciones),('F301',archivos_f301)]:
        for contenido,nombre in archivos:
            huella=hashlib.sha256(contenido).hexdigest()
            if huella in vistos:
                avisos.append(f'Archivo duplicado omitido: {nombre}')
                continue
            vistos.add(huella)
            tipo=clasificar_documento(contenido,nombre)
            # Si un PDF escaneado no permite saber su clase y se subió al
            # apartado F30-1, respetar la elección del usuario con cautela.
            # Nunca sobrescribir una clasificación concluyente de planilla.
            if (origen=='F301' and tipo.tipo=='COTIZACIONES'
                    and tipo.confianza=='BAJA'):
                tipo=TipoDocumento('F301',
                    'Documento no identificado por texto; se intentará como F30-1 según la sección elegida',
                    'BAJA',tipo.paginas)
            if tipo.tipo=='F301':
                f301.append((contenido,nombre))
            elif tipo.tipo=='COTIZACIONES':
                previred.append((contenido,nombre))
            else:
                rechazados.append((nombre,tipo.razon))
            if tipo.tipo != origen:
                avisos.append(f'{nombre}: enviado automáticamente a {tipo.tipo} (origen: {origen}). {tipo.razon}')
            if tipo.confianza=='BAJA' and tipo.tipo in ('F301','COTIZACIONES'):
                avisos.append(f'{nombre}: clasificación provisional. Se verificará durante la extracción.')
    return previred,f301,avisos,rechazados
