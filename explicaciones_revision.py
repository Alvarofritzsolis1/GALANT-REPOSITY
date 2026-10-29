"""Traducción controlada de alertas técnicas a instrucciones de revisión legibles.

No convierte errores OCR en incumplimientos laborales. Conserva la cadena
original en `detalle_tecnico` para que una revisión posterior sea reproducible.
"""
from __future__ import annotations
import re
from typing import Any


def _precio(n: Any) -> str:
    try:
        return '$' + f'{int(n):,}'.replace(',', '.')
    except (TypeError, ValueError):
        return 'no disponible'


def explicar_observacion(motivo: Any, fila: dict | None = None) -> dict[str,str]:
    original = str(motivo or '').strip()
    s = original.lower()
    r = fila or {}
    paginas = ', '.join(dict.fromkeys(re.findall(r'p\.(\d+)', original)))
    pagina_txt = ' (página'+ ('s' if ',' in paginas else '')+' '+paginas+')' if paginas else ''
    textos=[]
    acciones=[]
    grupo='DOCUMENTAL'
    nivel='REVISAR'

    if 'diferencia entre bases imponibles' in s or 'descuadre de bases entre instituciones' in s or 'bases imponibles extraídas son distintas' in s:
        grupo='DIFERENCIA DE RENTAS'; nivel='ALERTA'
        textos.append('Los importes de AFP, Salud y Mutual son distintos en el registro procesado: '
                      f"Salud {_precio(r.get('renta_imponible_salud'))}, AFP {_precio(r.get('renta_imponible_afp'))}, "
                      f"Mutual {_precio(r.get('renta_imponible_mutual'))}.")
        acciones.append('Compara las cifras con las páginas originales y revisa si corresponde una excepción legal o una rectificación.')
    if 'días no conciliados' in s or 'días leídos por ocr difieren' in s:
        grupo='LECTURA DE DÍAS'; nivel='REVISAR OCR'
        par = re.search(r'salud\s*(\d+)\s*[,/]\s*(?:seguro social)\s*(\d+)',original,re.I)
        dias = f' (Salud {par.group(1)}; Seguro Social {par.group(2)})' if par else ''
        textos.append('Los días se interpretaron de forma distinta entre Salud y Seguro Social'+dias+'. El OCR puede confundir 6/8 o 1/11; aún no es una diferencia documental confirmada.')
        acciones.append('Revisa la columna «Días trabajados» y los movimientos del RUT; contrasta la cifra impresa en ambas fuentes.')
    if 'lecturas alternativas' in s or 'base imponible no reconocida' in s or 'renta mínima' in s or 'base pequeña ambigua' in s:
        grupo='LECTURA DE MONTO'; nivel='REVISAR OCR'
        propuestas=re.findall(r'lecturas alternativas de renta\s*\[([^]]+)\]',original,re.I)
        texto_cifra='; alternativas OCR: '+', '.join(' / '.join(_precio(n.strip()) for n in group.split(',')) for group in propuestas[:2]) if propuestas else ''
        textos.append('La imagen no permitió reconocer un importe de manera inequívoca'+pagina_txt+texto_cifra+'. Esto no significa que la cotización esté mal pagada.')
        acciones.append('Confirma el importe en el recorte original y contrasta los totales de AFP, Salud y Mutual.')
    if 'rut alineado' in s or 'rut no reconocido' in s or 'nombre del trabajador no confirmado' in s:
        grupo='IDENTIDAD OCR';nivel='REVISAR OCR'
        textos.append('La lectura del RUT o nombre no fue suficientemente clara'+pagina_txt+'.')
        acciones.append('Contrasta el dígito verificador y el nombre del trabajador con la imagen original.')
    if 'faltan valores en la fila' in s or 'faltan columnas de origen' in s or 'falta detalle' in s or 'no se obtuvo base' in s:
        grupo='DATO NO EXTRAÍDO';nivel='REVISAR'
        textos.append('Una o más bases imponibles no pudieron extraerse de los archivos cargados; no necesariamente faltan pagos.')
        acciones.append('Revisa si el comprobante incluye las páginas de AFP, Salud y Mutual/ISL correspondientes al período.')
    if 'mutual adherida' in s or 'mutual no identificada' in s:
        grupo='MUTUAL NO IDENTIFICADA';nivel='REVISAR'
        textos.append('El documento procesado no permitió identificar el organismo administrador de accidentes del trabajo.')
        acciones.append('Adjunta el detalle de Mutual/ISL o verifica su nombre en el comprobante original.')
    if 'renta imponible positiva' in s and ('días' in s or 'dias' in s):
        grupo='DÍAS SIN BASE COHERENTE';nivel='ALERTA'
        textos.append('Se extrajo una renta imponible positiva, pero los días remunerados aparecen como cero.')
        acciones.append('Comprueba si hay licencia, ausencia, reliquidación o error de lectura de días.')
    if 'período declarado' in s and 'difiere' in s:
        grupo='PERÍODO';nivel='REVISAR'
        textos.append('El período de remuneraciones del PDF no coincide con el período esperado.')
        acciones.append('Compara el mes certificado con el mes de remuneraciones, no con el nombre del archivo ni la fecha de pago.')
    if 'los días deben ser enteros' in s or 'valores numéricos inválidos' in s:
        grupo='FORMATO INVÁLIDO'; nivel='REVISAR'
        if 'los días deben ser enteros' in s:
            textos.append('Los días deben ser enteros: la columna contiene un valor fraccionario o ilegible.')
            acciones.append('Corrige la fuente o confirma los días enteros en la liquidación original.')
        else:
            textos.append('Uno o más valores numéricos inválidos impiden comprobar correctamente la fila.')
            acciones.append('Verifica las cifras y el formato de las columnas del archivo original.')
    if 'supera el tope informado' in s:
        grupo='REVISIÓN DE TOPE';nivel='REVISAR'
        textos.append('Una renta imponible supera el tope informado para el período.')
        acciones.append('Comprueba el tope oficial aplicable y si la planilla declara una base anterior a su aplicación.')
    if 'diferencia_a_verificar' in s or 'control asistencia' in s:
        grupo='CONTRASTE ASISTENCIA';nivel='REVISAR'
        textos.append('Los días de asistencia y los días remunerados no son iguales; pueden existir licencias o movimientos que expliquen la diferencia.')
        acciones.append('Contrasta asistencia, contrato y licencias antes de concluir un incumplimiento.')
    if 'duplicado' in s:
        grupo='REGISTRO DUPLICADO';nivel='REVISAR'
        textos.append('El mismo trabajador aparece más de una vez para el mismo empleador y período, y los importes no se sumaron automáticamente.')
        acciones.append('Confirma si son movimientos del mismo mes o comprobantes duplicados antes de consolidar.')
    if not textos:
        if 'ocr' in s or 'escanead' in s:
            grupo='CONTROL OCR';nivel='REVISAR OCR'
            textos.append('Se reconocieron datos de un PDF escaneado, pero no hay evidencia suficiente para confirmar toda la fila.')
            acciones.append('Comprueba el RUT, días y rentas en el documento original.')
        else:
            textos.append('Este registro necesita una comprobación documental adicional; consulta el detalle técnico para identificar los campos.')
            acciones.append('Revisa el comprobante original y la trazabilidad por página antes de resolver.')
    # Evita paneles con párrafos repetidos por cada incidente OCR.
    return {
        'tipo_revision':grupo,
        'prioridad_revision':nivel,
        'motivo_revision':' '.join(dict.fromkeys(textos[:2])),
        'accion_recomendada':' '.join(dict.fromkeys(acciones[:2])),
        'detalle_tecnico':original,
    }
