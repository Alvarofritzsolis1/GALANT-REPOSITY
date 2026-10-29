"""Extractor conservador para comprobantes Previred multipágina.

- Lee detalle de Seguro Social (días), Fonasa/Isapre (días y base salud),
  AFP (base AFP) e ISL/Mutual (base de accidentes).
- Una persona puede figurar en múltiples líneas de movimiento y varias instituciones.
  Consolida por RUT *dentro de cada fuente* y luego cruza fuentes, nunca suma
  la misma renta de distintas instituciones.
- PDF sin texto: OCR (Tesseract) y la misma validación estricta. Si OCR no reconoce
  una fuente o las cifras, el dato queda faltante y NO se aprueba automáticamente.
- Rechaza RUT con dígito verificador inválido antes de consolidar.
"""
from __future__ import annotations

import io
import re
import unicodedata
from collections import defaultdict
from typing import Any, Callable

import pandas as pd

RUT = r"\d{1,2}\.?(?:\d{3})\.?(?:\d{3})\s*-?\s*[0-9Kk]"
RUT_RE = re.compile(r"(?<!\d)(" + RUT + r")(?!\w)")
# Nombre admite tildes, guiones, apóstrofos; evita interpretar montos como nombre.
ROW_RE = re.compile(
    r"^\s*(?:\d{1,3}\s+)?(?P<rut>" + RUT + r")\s+"
    r"(?P<nombre>[A-ZÁÉÍÓÚÜÑa-záéíóúüñ'\- .]+?)\s+"
    r"(?P<numeros>\d[\d.,]*(?:\s+.+)?)\s*$"
)
DATE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")
MONEY = re.compile(r"^\d{1,3}(?:\.\d{3})*$|^\d+$")
FIELDS = ("dias_reales_pagados", "renta_imponible_salud", "renta_imponible_afp", "renta_imponible_mutual")


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s).lower() if not unicodedata.combining(c))


def _rut_clean(s: str) -> str:
    return re.sub(r"[^0-9K]", "", s.upper())


def _rut_ok(s: str) -> bool:
    v = _rut_clean(s)
    if len(v) < 2 or len(v) > 9 or not v[:-1].isdigit():
        return False
    total = sum(int(c) * (2 + i % 6) for i, c in enumerate(reversed(v[:-1])))
    n = 11 - total % 11
    return v[-1] == ("0" if n == 11 else "K" if n == 10 else str(n))


def _peso(s: str) -> int | None:
    s = s.strip().replace(" ", "")
    return int(s.replace(".", "")) if MONEY.fullmatch(s) else None


def _periodos(texto: str) -> set[str]:
    encontrados = set()
    for m in re.finditer(r"periodo\s+de\s+remuneraci(?:ones|on)\s*:?\s*(\d{1,2})\s*[/\-]\s*(20\d{2})", _fold(texto)):
        mes, year = int(m.group(1)), int(m.group(2))
        if 1 <= mes <= 12:
            encontrados.add(f"{year}-{mes:02d}")
    for m in re.finditer(r"periodo\s+de\s+remuneraci(?:ones|on)\s*:?\s*(\d{1,2})\s+(20\d{2})", _fold(texto)):
        mes, year = int(m.group(1)), int(m.group(2))
        if 1 <= mes <= 12:
            encontrados.add(f"{year}-{mes:02d}")
    return encontrados


def _tipo_pagina(t: str) -> str:
    """Detecta detalles por el encabezado, incluso pagos fuera de plazo.

    Un comprobante puede comenzar con «declaración y no pago» y contener
    después una constancia de pago. Esa redacción NO cambia el tipo de tabla.
    """
    k = _fold(t)
    head = k[:1500]
    if ("detalle de comprobante" in head and "cotizaciones previsionales" in head
            and any(x in head for x in ("instituto de seguridad laboral", "mutual de seguridad",
                                      "asociacion chilena de seguridad", "achs", "instituto de seguridad del trabajo"))):
        return "mutual"
    if "detalle de pago de cotizaciones previsionales a isapre" in head:
        return "isapre"
    if "anexos de detalle" in k and "cotizaciones" in k and ("dias" in k or "trabajados" in k):
        return "salud"
    if "identificacion del trabajador" in k and "seguro social" in head and "dias" in k and "jornada" in k:
        return "seguro_social"
    if "detalle de pago de cotizaciones previsionales" in head and "fondo de pensiones" in k and ("afp" in k or "sis" in k):
        return "afp"
    return ""


def _mutual_name(texto: str) -> str:
    k = _fold(texto[:1500])
    if "instituto de seguridad laboral" in k or re.search(r"\bisl\b", k):
        return "ISL"
    if "asociacion chilena de seguridad" in k or re.search(r"\bachs\b", k):
        return "ACHS"
    if "instituto de seguridad del trabajo" in k or re.search(r"\bist\b", k):
        return "IST"
    if "mutual de seguridad" in k:
        return "Mutual de Seguridad"
    return ""


def _empleador(textos: list[str]) -> tuple[str, str]:
    # Preferir páginas resumen con encabezado formal de empleador; nunca leer
    # accidentalmente el RUT del representante legal o del primer trabajador.
    for texto in textos:
        lineas = texto.splitlines()
        for i, line in enumerate(lineas):
            k = _fold(line.strip())
            if "nombre o razon social" in k and "rut" in k:
                for fila in lineas[i + 1:i + 4]:
                    m = RUT_RE.search(fila)
                    if m:
                        prefijo = fila[:m.start()].strip()
                        # Comprobantes ISL incluyen correo entre nombre y RUT.
                        prefijo = re.sub(r"\b\S+@\S+\b", "", prefijo).strip()
                        if len(prefijo) >= 5 and not any(x.isdigit() for x in prefijo):
                            return prefijo.upper(), _rut_clean(m.group(1))
            if k.startswith("razon social o nombre:"):
                nombre = line.split(":", 1)[1].strip()
                for siguiente in lineas[i + 1:i + 4]:
                    mr = re.search(r"\br\.?u\.?t\.?:?\s*(" + RUT + r")", siguiente, re.I)
                    if mr and len(nombre) >= 5:
                        return nombre.upper(), _rut_clean(mr.group(1))
    return "", ""


def _parsear_fila(linea: str, tipo: str) -> dict[str, Any] | None:
    """Lee filas Previred CON O SIN movimiento fechado.

    En un movimiento código 0 (sin movimiento) la tabla omite ambas fechas.
    La ausencia de fechas NO significa que falten días o renta: ese era el error
    que descartaba a los trabajadores sin novedades en agosto de 2026.
    Los índices de bases y días se fijan por tipo de planilla, no por su posición
    relativa al final de la línea; se verifican formato, DV del RUT y columnas.
    """
    m = ROW_RE.match(linea)
    if not m:
        return None
    rut = _rut_clean(m.group("rut"))
    if not _rut_ok(rut):
        return None
    nombre = " ".join(m.group("nombre").upper().split())
    datos = m.group("numeros").split()
    # En Previred las fechas son opcionales cuando el código movimiento es 0.
    # Si están, deben estar completas: jamás usar una fecha como base monetaria.
    # En Previred un movimiento de contratación/retiro puede traer SOLO fecha
    # de inicio; también hay casos con dos fechas y código 0 sin fechas.
    # La antigua exigencia de DOS fechas descartaba trabajadores válidos.
    if datos and DATE.fullmatch(datos[-1]):
        datos = datos[:-2] if len(datos) >= 2 and DATE.fullmatch(datos[-2]) else datos[:-1]
    if any(DATE.fullmatch(x) for x in datos):
        return None

    renta: int | None = None
    dias: int | None = None
    if tipo == "seguro_social":
        # <renta> <renta anterior> <seguro> <rentabilidad> <sis> JC/JP <dias> <mov>
        jornada = next((i for i, tok in enumerate(datos) if tok.upper() in ("JC", "JP")), -1)
        if jornada >= 5 and len(datos) == jornada + 3 and all(_peso(x) is not None for x in datos[:5]):
            renta = _peso(datos[0]); dias = _peso(datos[jornada + 1])
        elif len(datos) == 3 and all(_peso(x) is not None for x in datos):
            # Un detalle puede venir cortado al final del bloque imprimible:
            # conservar SOLO la renta explícita, nunca fabricar días desde el total.
            renta = _peso(datos[0]); dias = None
        else:
            return None
    elif tipo == "salud":
        # <dias> <AFP/IPS/...> <renta imponible> <cotizacion> <movimiento>
        if len(datos) not in (5, 8, 11) or datos[1].upper() not in ("AFP", "IPS", "INP", "SSS", "DIPRECA", "CAPRECA"):
            return None
        if not re.fullmatch(r"\d{1,2}", datos[0]) or _peso(datos[3]) is None or (len(datos) > 5 and not all(x.isdigit() for x in datos[5:])):
            return None
        dias = _peso(datos[0]); renta = _peso(datos[2])
    elif tipo == "isapre":
        # <renta> <cotización 7%> <ley 18566> <adicional> <otros>
        # <cotizacion pagar> <cotizacion pactada> UF <mov>.
        # No hay columna de días en el detalle Isapre; obtener de Seguro Social.
        if len(datos) < 8 or datos[-2].upper() != "UF":
            return None
        if not all(_peso(v) is not None for v in datos[:6]):
            # valores como 3,260 UF no son renta imponible
            return None
        renta = _peso(datos[0])
    elif tipo == "afp":
        # El primer valor es remuneración imponible, y el último código de movimiento.
        # Los otros valores son cotizaciones y seguro de cesantía, no rentas AFP.
        if len(datos) < 9 or not all(_peso(x) is not None for x in datos):
            return None
        # AFP en algunos comprobantes omite el código movimiento cuando no
        # hay novedades. No confundir la última cotización con ese código.
        renta = _peso(datos[0])
    elif tipo == "mutual":
        # <remuneracion> <cotizacion> <movimiento> [fechas opcionales].
        if len(datos) != 3 or not all(_peso(x) is not None for x in datos):
            return None
        renta = _peso(datos[0])
    else:
        return None
    if renta is None or (dias is None and tipo == "salud"):
        return None
    if dias is not None and not 0 <= dias <= 31:
        return None
    if renta < 0:
        return None
    # Un código de movimiento puede no estar impreso en AFP. Para Seguro
    # Social truncado no está disponible; recuperar la renta con advertencia.
    if tipo == "salud" and not re.fullmatch(r"\d{1,2}", datos[4]):
        return None
    if tipo == "mutual" and not re.fullmatch(r"\d{1,2}", datos[-1]):
        return None
    if tipo == "seguro_social" and len(datos) != 3 and not re.fullmatch(r"\d{1,2}", datos[-1]):
        return None
    return {"rut": rut, "nombre": nombre, "renta": renta, "dias": dias,
            "parcial": tipo == "seguro_social" and dias is None}


def _resumenes_por_folio(textos: list[str]) -> list[dict[str, Any]]:
    """Identifica TODAS las portadas, separadas por institución y folio.

    No confundir el «resumen por folio» (p.ej. un afiliado $129.162)
    con el «total por institución» de un PDF que agrupa muchas planillas.
    """
    res: list[dict[str, Any]] = []
    vistos: set[tuple[str, str]] = set()
    for pagina, texto in enumerate(textos, start=1):
        k = _fold(texto)
        head = k[:650]
        if any(tag in head for tag in (
            "detalle de pago de cotizaciones", "detalle de comprobante",
            "anexos de detalle", "detalle de cotizaciones")):
            continue
        tipo = ""
        patron = ""
        if "formulario de cotizaciones previsionales" in head and "seguro social" in head:
            tipo = "seguro_social"
            patron = r"total remuneracion imponible declarada en \$\s*\$?\s*([\d.]+)"
        elif ("planilla de declaracion y pago" in head or
              "planilla de pago fuera de plazo" in head or
              "planilla de declaracion y no pago" in head):
            tipo = "salud"
            patron = r"total remuneracion imponible declarada en \$\s*\$?\s*([\d.]+)"
        elif ("comprobante de pago de cotizaciones previsionales" in head or
              "comprobante de declaracion y no pago de cotizaciones previsionales" in head or
              "comprobante de declaracion y no de pago de cotizaciones previsionales" in head):
            if any(x in k[:1600] for x in (
                "instituto de seguridad laboral", "mutual de seguridad",
                "asociacion chilena de seguridad", "instituto de seguridad del trabajo")):
                tipo = "mutual"
                patron = r"total remuneraciones\s+([\d.]+)"
            elif re.search(r"\bafp\b", k[:1600]):
                tipo = "afp"
                patron = r"renta imponible\s+([\d.]+)"
        if not tipo:
            continue
        match = re.search(patron, k)
        if not match:
            continue
        monto = _peso(match.group(1))
        if monto is None:
            continue
        folio = _folio_pagina(texto) or f"pagina-{pagina}"
        key = (tipo, folio)
        if key in vistos:
            # Copias de la misma portada no deben duplicar el total declarado.
            continue
        vistos.add(key)
        conteo = None
        if tipo == "seguro_social":
            cm = re.search(r"n.?\s*afiliados informados\s+(\d+)", k)
        elif tipo == "salud":
            cm = re.search(r"n.?\s*de afiliados\s+(\d+)", k)
        elif tipo == "afp":
            cm = re.search(r"numero afiliados informados\s+(\d+)", k)
        else:
            cm = re.search(r"n.?\s*de afiliados informados\s+(\d+)", k)
        if cm:
            conteo = int(cm.group(1))
        # Un título «declaración y no pago» no significa que siga impago si
        # también se acredita un pago posterior en la misma portada.
        pago_fecha = bool(re.search(r"fecha pago\s*:?\s*\d{1,2}/\d{1,2}/20\d{2}", k)
                          or re.search(r"pago electronico\s+\d{1,2}/\d{1,2}/20\d{2}", k))
        es_declaracion_sin_pago = "declaracion y no pago" in head or "declaracion y no de pago" in head
        res.append({"tipo": tipo, "folio": folio, "pagina": pagina, "renta": monto,
                    "afiliados": conteo, "pago_posterior": pago_fecha,
                    "encabezado_sin_pago": es_declaracion_sin_pago})
    return res


def _folio_pagina(texto: str) -> str:
    k = _fold(texto)
    m = re.search(r"numero de (?:folio|serie)\s*:\s*([0-9]{9,20})", k)
    return m.group(1) if m else ""


def _totales_resumen(textos: list[str]) -> dict[str, int]:
    """Suma todas las portadas válidas de una institución, no solo la última."""
    acumulados: dict[str, int] = defaultdict(int)
    for r in _resumenes_por_folio(textos):
        acumulados[r["tipo"]] += r["renta"]
    return dict(acumulados)


def _conteos_afiliados(textos: list[str]) -> dict[str, int]:
    """Suma afiliados por folio, sin contar múltiples movimientos como personas."""
    totales: dict[str, int] = defaultdict(int)
    for r in _resumenes_por_folio(textos):
        if r["afiliados"] is not None:
            totales[r["tipo"]] += r["afiliados"]
    return dict(totales)


def _letra_montaje(tipo: str) -> str:
    return {"salud": "renta_imponible_salud", "isapre": "renta_imponible_salud", "afp": "renta_imponible_afp", "mutual": "renta_imponible_mutual", "seguro_social": "renta_seguro_social"}[tipo]


def extraer_previred(
    contenido: bytes,
    nombre: str,
    ocr_pagina: Callable[[bytes, int, str], bytes] | None = None,
    clasificador: Callable[[str], str] | None = None,
) -> pd.DataFrame | None:
    """Retorna tabla estandarizada o None si no identifica formato Previred.

    Si una fuente esperada no queda completa, conserva registro con campos vacíos
    y observación. El motor de auditoría lo califica como REVISAR_DATOS.
    """
    import pdfplumber
    try:
        with pdfplumber.open(io.BytesIO(contenido)) as pdf:
            textos = [(pagina.extract_text(x_tolerance=2, y_tolerance=3) or "") for pagina in pdf.pages]
    except Exception as exc:
        raise ValueError(f"{nombre}: no fue posible leer el PDF: {exc}") from exc

    pagina_ocr: set[int] = set()
    if ocr_pagina:
        for i, txt in enumerate(textos):
            if len(txt.strip()) < 70:
                try:
                    pdf_ocr = ocr_pagina(contenido, i, nombre)
                    with pdfplumber.open(io.BytesIO(pdf_ocr)) as convertido:
                        reemplazo = convertido.pages[0].extract_text(x_tolerance=2, y_tolerance=3) or ""
                    if len(reemplazo) > len(txt):
                        textos[i] = reemplazo
                        pagina_ocr.add(i)
                except Exception:
                    # El OCR podría fallar una página de portada. Las fuentes ausentes
                    # se informan como revisión; nunca se inventan cifras.
                    pagina_ocr.add(i)

    # Aprendizaje supervisado: solo reclasificar páginas SIN patrón conocido;
    # jamás sobreescribir un parser determinista mediante predicciones.
    tipos = [(i, _tipo_pagina(txt) or (clasificador(txt) if clasificador else "")) for i, txt in enumerate(textos)]
    tipos = [(i, tipo) for i, tipo in tipos if tipo]
    origen_previred = any("previred" in _fold(t) for t in textos) or "previred" in _fold(nombre)
    if not tipos and not origen_previred:
        return None
    if not tipos:
        raise ValueError(
            f"{nombre}: se identificó un documento de Previred pero no se reconocieron "
            "páginas de detalle de trabajadores. Revisa la resolución y las tablas del PDF."
        )

    periodos = set().union(*(_periodos(txt) for txt in textos))
    if len(periodos) > 1:
        raise ValueError(f"{nombre}: el PDF tiene períodos de remuneración contradictorios: {sorted(periodos)}")
    periodo = next(iter(periodos), "")
    contratista, rut_empleador = _empleador(textos)
    mutual = next((_mutual_name(textos[i]) for i, t in tipos if t == "mutual" and _mutual_name(textos[i])), "")
    if not mutual:
        mutual = next((_mutual_name(txt) for txt in textos if _mutual_name(txt)), "")

    # Agrupar movimientos en cada institución, sin duplicar rentas AFP, Salud o Mutual.
    por_fuente: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    paginas_por_fuente: dict[str, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
    fuentes_encontradas = {tipo for _, tipo in tipos}
    filas_rechazadas: list[str] = []
    for index, tipo in tipos:
        texto = textos[index]
        for linea in texto.splitlines():
            if not RUT_RE.search(linea):
                continue
            dato = _parsear_fila(linea, tipo)
            if dato is None:
                # No alertar sobre cabeceras con RUT, solo posibles filas de trabajador.
                if re.match(r"^\s*(?:\d{1,3}\s+)?" + RUT, linea) and any(c.isdigit() for c in linea.split()[-4:]):
                    filas_rechazadas.append(f"Página {index+1}, {tipo}: fila con formato inesperado o RUT inválido")
                continue
            dato["pagina"] = index + 1
            dato["folio"] = _folio_pagina(texto)
            por_fuente[tipo][dato["rut"]].append(dato)
            paginas_por_fuente[tipo][dato["rut"]].add(index + 1)

    # Isapre figura en un anexo separado de Fonasa y forma parte de SALUD.
    # Las bases se SUMAN por institución, nunca se interpreta su cotización como renta.
    # Cuadratura contra comprobantes resumen. Detecta omisiones silenciosas de
    # trabajadores o rentas, incluso cuando el OCR devolvió otras filas válidas.
    totales_resumen = _totales_resumen(textos)
    # El resumen Salud (Fonasa) incluye solo los afiliados a Fonasa.
    # Para Isapre se obtiene el total del detalle de Isapre, que tiene su propia
    # validación por suma. No es correcto exigir que 42 Fonasa = 44 AFP.
    alertas_totales: list[str] = []
    for tipo, monto_resumen in totales_resumen.items():
        monto_detalle = sum(x["renta"] for grupos in por_fuente[tipo].values() for x in grupos)
        if monto_detalle != monto_resumen:
            alertas_totales.append(
                f"Resumen {tipo}: renta ${monto_resumen:,} vs filas ${monto_detalle:,}"
            )

    # Comprobar personas informadas en cada resumen incluso si los importes
    # son $0. Sirve para detectar filas que desaparecen sin alterar totales.
    conteos_resumen=_conteos_afiliados(textos)
    for tipo,esperados in conteos_resumen.items():
        encontrados=len(por_fuente[tipo])
        if encontrados!=esperados:
            alertas_totales.append(f"{tipo}: portada informa {esperados} personas y detalle permite identificar {encontrados}")

    # Conciliación por folio: portadas de un afiliado NO se comparan contra
    # todo el PDF. Solo documentos del mismo folio e institución.
    # Cuando el folio no se reconoce, conserva el control global, sin fabricar
    # una correspondencia entre portadas y filas de instituciones diferentes.
    portadas_por_folio = _resumenes_por_folio(textos)
    importes_por_folio: dict[tuple[str, str], int] = defaultdict(int)
    personas_por_folio: dict[tuple[str, str], set[str]] = defaultdict(set)
    for tipo, grupos in por_fuente.items():
        for rut, filas in grupos.items():
            for fila in filas:
                folio = fila.get("folio", "")
                if folio:
                    importes_por_folio[(tipo, folio)] += fila["renta"]
                    personas_por_folio[(tipo, folio)].add(rut)
    for p in portadas_por_folio:
        tipo, folio = p["tipo"], p["folio"]
        if folio.startswith("pagina-") or (tipo, folio) not in importes_por_folio:
            continue
        total_det = importes_por_folio[(tipo, folio)]
        if p["renta"] != total_det:
            alertas_totales.append(
                f"{tipo.upper()} folio {folio} (p.{p['pagina']}): portada ${p['renta']:,}; detalle ${total_det:,}"
            )
        if p["afiliados"] is not None and p["afiliados"] != len(personas_por_folio[(tipo, folio)]):
            alertas_totales.append(
                f"{tipo.upper()} folio {folio}: {p['afiliados']} afiliados en portada; "
                f"{len(personas_por_folio[(tipo, folio)])} RUT en detalle"
            )
    # Detectar el total combinado de SALUD (Fonasa + Isapre) por RUT.
    solapamientos = set(por_fuente["salud"]) & set(por_fuente["isapre"])
    if solapamientos:
        alertas_totales.append(f"RUT aparece tanto en FONASA como en ISAPRE ({len(solapamientos)} personas): revisar")
    total_salud_combinada = sum(row["renta"] for fuente in ("salud", "isapre") for grupo in por_fuente[fuente].values() for row in grupo)
    total_afp = sum(row["renta"] for grupo in por_fuente["afp"].values() for row in grupo)
    total_ss = sum(row["renta"] for grupo in por_fuente["seguro_social"].values() for row in grupo)
    if por_fuente["salud"] or por_fuente["isapre"]:
        if por_fuente["afp"] and total_salud_combinada != total_afp:
            alertas_totales.append(f"Salud global (FONASA+ISAPRE): ${total_salud_combinada:,} vs AFP ${total_afp:,}")
    # Seguro Social NO siempre cubre el mismo universo de personas que AFP:
    # pueden existir trabajadores exentos. Si faltan personas en una fuente,
    # NO distribuir un "descuadre" global a todos los trabajadores.
    # Las diferencias se contrastan por RUT y se informan en un control propio.
    # Si el universo es idéntico, una diferencia global sí es un conflicto.
    if (por_fuente["seguro_social"] and por_fuente["afp"]
            and set(por_fuente["seguro_social"]) == set(por_fuente["afp"])
            and total_ss != total_afp):
        alertas_totales.append(
            f"Seguro Social ${total_ss:,} vs AFP ${total_afp:,} (mismos RUT declarados)"
        )
    # Las portadas de declaración sin pago pueden estar regularizadas.
    # Fecha de pago en la misma portada es evidencia de pago reportado,
    # pero NO sustituye validación independiente de Previred.
    portadas_regularizadas = [r for r in portadas_por_folio
                             if r["encabezado_sin_pago"] and r["pago_posterior"]]
    ruts = set().union(*(set(fuente.keys()) for fuente in por_fuente.values()))
    if not ruts:
        raise ValueError(
            f"{nombre}: encontré secciones Previred, pero no pude extraer filas de trabajadores confiables. "
            "Comprueba la orientación/calidad del PDF y que contenga detalle por trabajador."
        )

    resultados = []
    for rut in sorted(ruts):
        fuentes_trabajador = {tipo: por_fuente[tipo].get(rut, []) for tipo in ("seguro_social", "salud", "isapre", "afp", "mutual")}
        # Colocar Isapre en el canal de SALUD, sin exigir Fonasa para ese trabajador.
        if fuentes_trabajador["salud"] and fuentes_trabajador["isapre"]:
            pass  # solapamiento ya advertido globalmente
        elif fuentes_trabajador["isapre"]:
            fuentes_trabajador["salud"] = fuentes_trabajador["isapre"]
            fuentes_trabajador["isapre"] = []
        obs: list[str] = []
        nombres = {x["nombre"] for grupo in fuentes_trabajador.values() for x in grupo}
        if len(nombres) > 1:
            # Distintos formatos de nombres pueden diferir en orden o espaciado: comprobar,
            # pero no usarlo para agregar personas distintas, esa clave es el RUT validado.
            if {" ".join(sorted(n.split())) for n in nombres}.__len__() > 1:
                obs.append("Los nombres difieren entre instituciones; verificar")
        nombre_trab = next((grupo[0]["nombre"] for grupo in (fuentes_trabajador["salud"], fuentes_trabajador["seguro_social"], fuentes_trabajador["afp"], fuentes_trabajador["mutual"]) if grupo), "")
        valores: dict[str, int | None] = {}
        paginas: set[int] = set()
        for tipo, grupo in fuentes_trabajador.items():
            if not grupo:
                if tipo not in ("seguro_social", "isapre"):
                    obs.append(f"Falta detalle individual en {tipo.upper()}")
                continue
            paginas.update(x["pagina"] for x in grupo)
            if any(x.get("parcial") for x in grupo):
                obs.append(f"{tipo}: fila truncada en el PDF; base recuperada pero días no verificables desde esta fuente")
            # Dos movimientos distintos pueden tener 0 y montos; detectamos repetición
            # exacta solo si los importes no son cero y podrían inflar la renta.
            for monto in {x["renta"] for x in grupo if x["renta"] > 0}:
                if sum(x["renta"] == monto for x in grupo) > 1:
                    obs.append(f"{tipo}: varias líneas con la misma renta positiva, revisar posible duplicado")
            clave = _letra_montaje(tipo)
            valores[clave] = sum(x["renta"] for x in grupo)
            if tipo in ("salud", "seguro_social") and any(x["dias"] is not None for x in grupo):
                valores["dias_" + tipo] = sum(x["dias"] for x in grupo if x["dias"] is not None)
        # Fuente primaria de días: ANEXO FONASA/ISAPRE. SS como control cruzado
        # o respaldo excepcional si no está la fuente de salud (con advertencia).
        dias_salud = valores.get("dias_salud")
        dias_ss = valores.get("dias_seguro_social")
        if dias_salud is not None and dias_ss is not None and dias_salud != dias_ss:
            obs.append(f"Días Salud ({dias_salud}) distintos de Seguro Social ({dias_ss})")
        dias = dias_salud if dias_salud is not None else dias_ss
        if dias_salud is None and dias_ss is not None:
            if por_fuente["isapre"].get(rut):
                # El detalle de Isapre no contiene columna Días Trabajados.
                # Seguro Social es aquí la fuente explícita, no un error de extracción.
                pass
            else:
                obs.append("Días tomados del Seguro Social, sin confirmación en anexo Salud")
        # Cobertura de Seguro Social separada de la cuadratura AFP/Salud/Mutual:
        # "SIN_REGISTRO_SS" no es una diferencia de renta ni confirma exención.
        # Se emite un control específico para que RRHH compruebe si debe cotizar.
        if fuentes_trabajador["seguro_social"]:
            cobertura_ss = "PRESENTE_SS"
        elif por_fuente["seguro_social"]:
            cobertura_ss = "SIN_REGISTRO_SS"
        else:
            cobertura_ss = "SIN_COMPROBANTE_SS"
        renta_salud = valores.get("renta_imponible_salud")
        renta_ss = valores.get("renta_seguro_social")
        if renta_salud is not None and renta_ss is not None and renta_salud != renta_ss:
            obs.append(f"Renta Salud ({renta_salud}) no coincide con Renta Seguro Social ({renta_ss})")
        if not periodo:
            obs.append("Período de remuneraciones no encontrado en el contenido del PDF")
        if not contratista or not rut_empleador:
            obs.append("Nombre/RUT del empleador no encontrado en cabecera")
        if not mutual:
            obs.append("No hay comprobante de Mutual/ISL/ACHS/IST en este PDF; la CCAF no es la mutual")
        if alertas_totales:
            obs.append("Conciliación de resúmenes: " + "; ".join(alertas_totales))
        if filas_rechazadas:
            # Un RUT mal reconocido por OCR puede generar trabajador faltante. Evita aprobaciones
            # silenciosas si hay alguna fila de trabajadores descartada en el archivo.
            obs.append("Existen filas no interpretadas: " + "; ".join(filas_rechazadas[:3]))
        if pagina_ocr:
            obs.append("Se aplicó OCR; verificar cifras de las páginas escaneadas antes de aprobar")
        resultados.append({
            "periodo_remuneracion": periodo,
            "contratista_nombre": contratista,
            "contratista_rut": rut_empleador,
            "trabajador_nombre": nombre_trab,
            "trabajador_rut": rut,
            "dias_reales_pagados": dias,
            "renta_imponible_salud": renta_salud,
            "renta_imponible_afp": valores.get("renta_imponible_afp"),
            "renta_imponible_mutual": valores.get("renta_imponible_mutual"),
            "mutual_adherida": mutual,
            "__hoja_origen": ", ".join("p." + str(p) for p in sorted(paginas)),
            "__conflicto_pdf": "; ".join(obs),
            "__cobertura_seguro_social": cobertura_ss,
            "__renta_seguro_social": renta_ss,
            "__comprobantes_regularizados": len(portadas_regularizadas),
        })

    # Verificar totales de fuente si se han leído las filas; no confundir resumen
    # del monto A PAGAR con la RENTA IMPONIBLE (la cotización es un concepto diferente).
    return pd.DataFrame(resultados)
