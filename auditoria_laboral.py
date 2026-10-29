"""Motor de procesamiento y auditoría laboral para Streamlit.

El módulo NO calcula porcentajes de cotización ni inventa topes legales. Lee los
valores declarados en archivos cargados por el usuario y aplica reglas explícitas.
Los archivos se procesan en memoria; no se guardan en disco por este módulo.
"""
from __future__ import annotations

import io
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

import pandas as pd

try:
    import pdfplumber
except ImportError:  # el programa funciona con Excel/CSV aunque falte pdfplumber
    pdfplumber = None

COLUMNAS_SALIDA = [
    "periodo_remuneracion",
    "contratista_nombre",
    "contratista_rut",
    "trabajador_nombre",
    "trabajador_rut",
    "dias_reales_pagados",
    "renta_imponible_salud",
    "renta_imponible_afp",
    "renta_imponible_mutual",
    "mutual_adherida",
    "estado_auditoria",
]

COLUMNAS_NUMERICAS = [
    "dias_reales_pagados",
    "renta_imponible_salud",
    "renta_imponible_afp",
    "renta_imponible_mutual",
]
COLUMNAS_RENTAS = [
    "renta_imponible_salud",
    "renta_imponible_afp",
    "renta_imponible_mutual",
]

ALIAS_COLUMNAS: dict[str, list[str]] = {
    "periodo_remuneracion": [
        "periodo_remuneracion", "periodo remuneracion", "periodo de remuneraciones",
        "mes remuneracion", "mes de remuneracion", "mes remuneraciones", "periodo pago",
        "mes ano", "fecha periodo", "periodo",
    ],
    "contratista_nombre": [
        "contratista_nombre", "nombre contratista", "razon social contratista",
        "razon social empleador", "razon social empresa", "razon social", "nombre empresa",
        "empresa contratista", "empresa", "empleador", "nombre empleador",
    ],
    "contratista_rut": [
        "contratista_rut", "rut contratista", "rut del contratista", "rut empleador",
        "rut del empleador", "rut empresa", "rut de empresa", "rut empleadora",
    ],
    "trabajador_nombre": [
        "trabajador_nombre", "nombre trabajador", "nombre del trabajador", "trabajador nombre",
        "nombre completo trabajador", "nombre completo", "nombres y apellidos",
        "apellidos y nombres", "nombre y apellidos", "trabajador",
    ],
    "trabajador_rut": [
        "trabajador_rut", "rut trabajador", "rut del trabajador", "run trabajador",
        "rut afiliado", "rut beneficiario", "run", "rut", "r u t",
    ],
    "dias_reales_pagados": [
        "dias_reales_pagados", "dias reales pagados", "dias efectivamente pagados",
        "dias efectivamente liquidados", "dias liquidados", "dias trabajados en el mes",
        "dias trabajados", "dias pagados", "dias declarados planilla", "dias declarados",
        "dias remunerados",
    ],
    "renta_imponible_salud": [
        "renta_imponible_salud", "renta imponible salud", "base imponible salud",
        "imponible salud", "renta salud", "renta imponible fonasa", "base imponible fonasa",
        "renta imponible isapre", "base imponible isapre", "imponible isapre fonasa",
    ],
    "renta_imponible_afp": [
        "renta_imponible_afp", "renta imponible afp", "base imponible afp", "imponible afp",
        "renta afp", "remuneracion imponible afp", "sueldo imponible afp",
    ],
    "renta_imponible_mutual": [
        "renta_imponible_mutual", "renta imponible mutual", "base imponible mutual",
        "imponible mutual", "renta mutual", "remuneracion imponible mutual",
        "renta imponible isl", "base imponible isl", "remuneracion imponible accidente",
        "renta imponible ley 16 744", "base imponible ley 16744",
    ],
    "mutual_adherida": [
        "mutual_adherida", "mutual adherida", "organismo administrador", "entidad mutual",
        "mutualidad", "institucion accidente", "mutual", "organismo administrador ley 16744",
    ],
}

# Datos mínimos para emitir un estado definitivo. Se exige identificar la mutual porque
# forma parte del resultado de control solicitado, aunque la base Mutual pueda excluirse del cuadre.
CAMPOS_CRITICOS_BASE = {
    "periodo_remuneracion", "contratista_nombre", "contratista_rut", "trabajador_nombre",
    "trabajador_rut", "dias_reales_pagados", "renta_imponible_salud", "renta_imponible_afp",
    "mutual_adherida",
}

MISSING_TOKENS = {"", "na", "n a", "nan", "nat", "none", "null", "s i", "sin informacion"}


def clave_texto(valor: Any) -> str:
    """Normaliza texto para comparar columnas y valores, sin tildes ni signos."""
    if valor is None:
        return ""
    texto = str(valor).strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def es_vacio(valor: Any) -> bool:
    if valor is None:
        return True
    try:
        if pd.isna(valor):
            return True
    except (TypeError, ValueError):
        pass
    return clave_texto(valor) in MISSING_TOKENS or str(valor).strip() in {"-", "—", "--"}


def clave_rut(valor: Any) -> str:
    """RUT sin puntos, guion ni espacios, conservando K mayúscula."""
    if es_vacio(valor):
        return ""
    texto = str(valor).strip().upper()
    texto = re.sub(r"\.0$", "", texto)
    return re.sub(r"[^0-9K]", "", texto)


def rut_chileno_valido(valor: Any) -> bool:
    """Valida formato y dígito verificador chileno; no sustituye la revisión documental."""
    rut = clave_rut(valor)
    if len(rut) < 2 or len(rut) > 9:
        return False
    cuerpo, dv = rut[:-1], rut[-1]
    if not cuerpo.isdigit():
        return False
    suma = 0
    factor = 2
    for digito in reversed(cuerpo):
        suma += int(digito) * factor
        factor = 2 if factor == 7 else factor + 1
    resto = 11 - (suma % 11)
    esperado = "0" if resto == 11 else "K" if resto == 10 else str(resto)
    return dv == esperado


def normalizar_periodo(valor: Any) -> str:
    """Normaliza período a YYYY-MM. Nunca lo reemplaza automáticamente por el mes actual."""
    if es_vacio(valor):
        return ""
    if isinstance(valor, (pd.Timestamp, datetime, date)):
        return valor.strftime("%Y-%m")

    original = str(valor).strip()
    # Formato YYYY-MM, YYYY/MM, YYYY-MM-DD o YYYY/MM/DD.
    m = re.search(r"(?<!\d)(20\d{2}|19\d{2})[-/.](\d{1,2})(?:[-/.](\d{1,2}))?(?!\d)", original)
    if m:
        anio, mes = int(m.group(1)), int(m.group(2))
        if 1 <= mes <= 12:
            return f"{anio:04d}-{mes:02d}"

    # Formato MM/YYYY, MM-YYYY o MM.YYYY.
    m = re.search(r"(?<!\d)(\d{1,2})[-/.](20\d{2}|19\d{2})(?!\d)", original)
    if m:
        mes, anio = int(m.group(1)), int(m.group(2))
        if 1 <= mes <= 12:
            return f"{anio:04d}-{mes:02d}"

    # Mes escrito y año.
    mes_nombres = {
        "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5,
        "junio": 6, "julio": 7, "agosto": 8, "septiembre": 9,
        "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
    }
    k = clave_texto(original)
    anio = re.search(r"\b(20\d{2}|19\d{2})\b", original)
    if anio:
        for nombre_mes, numero in mes_nombres.items():
            if nombre_mes in k:
                return f"{anio.group(1)}-{numero:02d}"

    # Fecha completa, por ejemplo 31/08/2026. Último intento.
    try:
        dt = pd.to_datetime(original, errors="raise", dayfirst=True)
        if not pd.isna(dt):
            return dt.strftime("%Y-%m")
    except (ValueError, TypeError, OverflowError):
        pass
    return ""


def inferir_periodo_nombre_archivo(nombre: str) -> str:
    """Solo usa patrones explícitos YYYY-MM o MM-YYYY presentes en el nombre."""
    base = Path(nombre).stem
    patrones = [
        (r"(?<!\d)(20\d{2}|19\d{2})[-_.](0?[1-9]|1[0-2])(?!\d)", True),
        (r"(?<!\d)(0?[1-9]|1[0-2])[-_.](20\d{2}|19\d{2})(?!\d)", False),
    ]
    for patron, year_first in patrones:
        m = re.search(patron, base)
        if not m:
            continue
        if year_first:
            return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
        return f"{int(m.group(2)):04d}-{int(m.group(1)):02d}"
    return ""


def convertir_monto(valor: Any) -> int:
    """Convierte moneda CLP a entero. Vacíos se muestran como cero, pero quedan marcados aparte."""
    if es_vacio(valor):
        return 0
    if isinstance(valor, bool):
        return int(valor)
    if isinstance(valor, (int, float)):
        try:
            if pd.isna(valor):
                return 0
            return int(Decimal(str(valor)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        except (TypeError, ValueError, OverflowError):
            return 0

    texto = str(valor).strip().upper()
    negativo = texto.startswith("(") and texto.endswith(")")
    texto = texto.strip("() ").replace("$", "").replace("CLP", "").replace("UF", "")
    texto = re.sub(r"\s+", "", texto)
    texto = re.sub(r"[^0-9,.-]", "", texto)
    if not texto or texto in {".", ",", "-", "-."}:
        return 0
    if "-" in texto:
        negativo = True
        texto = texto.replace("-", "")

    tiene_punto, tiene_coma = "." in texto, "," in texto
    if tiene_punto and tiene_coma:
        # Chile: 1.234.567,89; también admite 1,234,567.89.
        decimal_sep = "," if texto.rfind(",") > texto.rfind(".") else "."
        miles_sep = "." if decimal_sep == "," else ","
        entero, decimal = texto.rsplit(decimal_sep, 1)
        entero = entero.replace(miles_sep, "")
        normalizado = entero + ("." + decimal if decimal.isdigit() and len(decimal) <= 2 else "")
    elif tiene_punto or tiene_coma:
        sep = "." if tiene_punto else ","
        partes = texto.split(sep)
        if len(partes) > 2 or len(partes[-1]) == 3:
            normalizado = "".join(partes)
        elif len(partes[-1]) in (1, 2):
            normalizado = partes[0] + "." + partes[-1]
        else:
            normalizado = "".join(partes)
    else:
        normalizado = texto

    try:
        numero = int(Decimal(normalizado).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        return -numero if negativo else numero
    except (InvalidOperation, ValueError):
        return 0


def monto_es_valido(valor: Any) -> bool:
    """Comprueba que un valor no vacío tenga formato numérico reconocible.

    Evita que textos como "error", "pendiente" o "1.2.3" acaben convertidos en cero
    y produzcan una falsa conformidad. Los vacíos se validan por otra regla de auditoría.
    """
    if es_vacio(valor):
        return True
    if isinstance(valor, bool):
        return False
    if isinstance(valor, (int, float)):
        try:
            return pd.notna(valor) and float(valor) == float(valor) and abs(float(valor)) != float("inf")
        except (TypeError, ValueError, OverflowError):
            return False

    texto = str(valor).strip().upper()
    if texto.startswith("(") != texto.endswith(")"):
        return False
    texto = texto.strip("() ").replace("$", "").replace("CLP", "").replace("UF", "")
    texto = re.sub(r"\s+", "", texto)
    if texto.startswith(("-", "+")):
        texto = texto[1:]
    patron = r"(?:\d+(?:[.,]\d{1,2})?|\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?)"
    return bool(texto and re.fullmatch(patron, texto))


def monto_es_entero(valor: Any) -> bool:
    """Valida días enteros aunque el origen use formatos como 30,0."""
    if es_vacio(valor):
        return True
    if isinstance(valor, bool):
        return False
    if isinstance(valor, (int, float)):
        try:
            numero = float(valor)
            return pd.notna(numero) and numero.is_integer()
        except (TypeError, ValueError, OverflowError):
            return False

    texto = str(valor).strip().upper()
    if texto.startswith("(") != texto.endswith(")"):
        return False
    texto = texto.strip("() ").replace("$", "").replace("CLP", "").replace("UF", "")
    texto = re.sub(r"\s+", "", texto)
    if texto.startswith(("-", "+")):
        texto = texto[1:]
    if "." in texto and "," in texto:
        separador_decimal = "," if texto.rfind(",") > texto.rfind(".") else "."
        decimal = texto.rsplit(separador_decimal, 1)[-1]
        if len(decimal) not in (1, 2):
            return True  # Se interpreta como separador de miles.
        return not any(digito != "0" for digito in decimal)
    if "." in texto or "," in texto:
        separador = "." if "." in texto else ","
        partes = texto.split(separador)
        if len(partes) > 2 or len(partes[-1]) == 3:
            return True  # Ej.: 1.234 significa 1234 CLP/días, no un decimal.
        if len(partes[-1]) in (1, 2):
            return not any(digito != "0" for digito in partes[-1])
    return True


def _header_score(fila: Iterable[Any]) -> int:
    celdas = [clave_texto(x) for x in fila if not es_vacio(x)]
    total = " | ".join(celdas)
    score = 0
    # Se premian términos representativos pero no se confía en una sola palabra.
    if any(x in total for x in ("rut", "run")):
        score += 2
    if "trabajador" in total or "afiliado" in total or "nombre completo" in total:
        score += 2
    if any(x in total for x in ("dias", "día", "dias trabajados")):
        score += 1
    if any(x in total for x in ("salud", "fonasa", "isapre")):
        score += 1
    if "afp" in total:
        score += 1
    if "imponible" in total or "renta" in total:
        score += 1
    if "periodo" in total or "remuneracion" in total:
        score += 1
    return score


def _aplicar_cabecera_automatica(crudo: pd.DataFrame, nombre: str) -> pd.DataFrame:
    if crudo.empty:
        raise ValueError(f"{nombre}: archivo o tabla sin datos.")
    limite = min(35, len(crudo))
    puntajes = [(_header_score(crudo.iloc[i].tolist()), i) for i in range(limite)]
    puntuacion, fila_header = max(puntajes, default=(0, 0))
    if puntuacion < 3:
        # Si la primera fila es una cabecera reconocible, no debemos perderla.
        fila_header = 0
        if _header_score(crudo.iloc[0].tolist()) < 3:
            raise ValueError(
                f"{nombre}: no pude detectar con confianza la fila de cabeceras. "
                "Verifica que el archivo tenga nombres de columnas y una fila por trabajador."
            )
    cabeceras: list[str] = []
    for i, valor in enumerate(crudo.iloc[fila_header].tolist()):
        if es_vacio(valor):
            cabeceras.append(f"columna_sin_nombre_{i+1}")
        else:
            cabeceras.append(str(valor).strip().replace("\n", " "))
    datos = crudo.iloc[fila_header + 1:].copy()
    datos.columns = cabeceras
    datos = datos.dropna(how="all").reset_index(drop=True)
    return datos


def _mapear_columnas(df: pd.DataFrame) -> tuple[pd.DataFrame, set[str]]:
    """Mapea sinónimos evitando que una misma columna alimente varios campos."""
    originales = list(df.columns)
    normalizados = {c: clave_texto(c) for c in originales}
    renombres: dict[Any, str] = {}
    usadas: set[Any] = set()
    faltantes: set[str] = set()

    for objetivo, aliases in ALIAS_COLUMNAS.items():
        alias_norm = [clave_texto(a) for a in aliases]
        # Primero, coincidencias exactas.
        candidatos = [c for c in originales if c not in usadas and normalizados[c] in alias_norm]
        if candidatos:
            # Preferir cabecera canónica en caso de repetidas.
            candidatos.sort(key=lambda c: (normalizados[c] != clave_texto(objetivo), -len(normalizados[c])))
            seleccionado = candidatos[0]
            renombres[seleccionado] = objetivo
            usadas.add(seleccionado)

    for objetivo, aliases in ALIAS_COLUMNAS.items():
        if objetivo in renombres.values():
            continue
        alias_norm = [clave_texto(a) for a in aliases if len(clave_texto(a)) >= 7]
        candidatos: list[tuple[int, Any]] = []
        for columna in originales:
            if columna in usadas:
                continue
            nombre = normalizados[columna]
            if not nombre:
                continue
            # El alias debe estar completo dentro de la cabecera para evitar confusiones
            # entre renta salud, AFP y mutual.
            for alias in alias_norm:
                if nombre == alias:
                    candidatos.append((1000 + len(alias), columna))
                elif f" {alias} " in f" {nombre} " or nombre.startswith(alias + " ") or nombre.endswith(" " + alias):
                    candidatos.append((100 + len(alias), columna))
        if candidatos:
            candidatos.sort(key=lambda x: x[0], reverse=True)
            seleccionado = candidatos[0][1]
            renombres[seleccionado] = objetivo
            usadas.add(seleccionado)
        else:
            faltantes.add(objetivo)

    salida = df.rename(columns=renombres).copy()
    salida = salida.loc[:, ~salida.columns.duplicated(keep="first")]
    for objetivo in ALIAS_COLUMNAS:
        if objetivo not in salida.columns:
            salida[objetivo] = pd.NA
    return salida, faltantes


def _tabla_es_de_trabajadores(tabla: pd.DataFrame) -> bool:
    mapeada, _ = _mapear_columnas(tabla)
    rut_col = mapeada["trabajador_rut"].apply(clave_rut)
    nombre_col = mapeada["trabajador_nombre"].astype(str).str.strip()
    tiene_ruts = rut_col.str.len().ge(7).any()
    tiene_nombre = nombre_col.ne("").any() and not nombre_col.str.lower().eq("nan").all()
    tiene_monto_o_dias = any(
        mapeada[c].apply(lambda x: not es_vacio(x)).any()
        for c in ("dias_reales_pagados", *COLUMNAS_RENTAS)
    )
    return bool(tiene_ruts and (tiene_nombre or tiene_monto_o_dias))


def _leer_excel_bytes(contenido: bytes, nombre: str) -> pd.DataFrame:
    hojas: list[pd.DataFrame] = []
    try:
        excel = pd.ExcelFile(io.BytesIO(contenido))
        for hoja in excel.sheet_names:
            try:
                crudo = pd.read_excel(io.BytesIO(contenido), sheet_name=hoja, header=None, dtype=object)
                tabla = _aplicar_cabecera_automatica(crudo, f"{nombre} / hoja {hoja}")
                if _tabla_es_de_trabajadores(tabla):
                    tabla["__hoja_origen"] = hoja
                    hojas.append(tabla)
            except Exception:
                # Puede haber una hoja de portada/resumen. Se intenta el resto.
                continue
    except ImportError as exc:
        raise ValueError("Falta un motor de Excel. Revise requirements.txt (openpyxl y xlrd).") from exc
    except Exception as exc:
        raise ValueError(f"No se pudo abrir el Excel: {exc}") from exc
    if not hojas:
        raise ValueError(
            f"{nombre}: no se encontró ninguna hoja con registros por trabajador. "
            "Verifica cabeceras, RUT y datos de días/rentas."
        )
    return pd.concat(hojas, ignore_index=True, sort=False)


def _leer_csv_bytes(contenido: bytes, nombre: str) -> pd.DataFrame:
    errores: list[str] = []
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            crudo = pd.read_csv(io.BytesIO(contenido), header=None, dtype=object,
                                sep=None, engine="python", encoding=encoding)
            tabla = _aplicar_cabecera_automatica(crudo, nombre)
            if _tabla_es_de_trabajadores(tabla):
                return tabla
            errores.append(f"codificación {encoding}: no parecen filas por trabajador")
        except Exception as exc:
            errores.append(f"codificación {encoding}: {exc}")
    raise ValueError(f"{nombre}: no se pudo interpretar el CSV. " + " | ".join(errores[-2:]))


def _extraer_contexto_pdf(texto: str) -> dict[str, str]:
    """Intenta extraer metadatos globales explícitos del documento PDF."""
    contexto: dict[str, str] = {}

    patrones_periodo = [
        r"Per[ií]odo\s+(?:de\s+)?(?:remuneraciones|remuneraci[oó]n|pago)\s*:?\s*(\d{1,2})\s*[/.-]\s*(\d{4})",
        r"Per[ií]odo[^\n\d]{0,35}(\d{1,2})\s+(\d{4})",
    ]
    for patron in patrones_periodo:
        m = re.search(patron, texto, re.IGNORECASE)
        if m:
            mes, anio = int(m.group(1)), int(m.group(2))
            if 1 <= mes <= 12:
                contexto["periodo_remuneracion"] = f"{anio:04d}-{mes:02d}"
                break

    m_nombre = re.search(
        r"(?:Raz[oó]n\s+Social\s+o\s+Nombre|Nombre\s+o\s+Raz[oó]n\s+Social)\s*:?\s*([^\n]+)",
        texto, re.IGNORECASE,
    )
    if m_nombre:
        nombre = re.sub(r"\s+", " ", m_nombre.group(1)).strip(" :\t")
        nombre = re.sub(r"\s+\S+@\S+", "", nombre).strip()
        nombre = re.split(r"\s+(?:R\.?U\.?T\.?|RUN)\s*:?", nombre, maxsplit=1, flags=re.IGNORECASE)[0].strip()
        if nombre:
            contexto["contratista_nombre"] = nombre

    m_rut = re.search(
        r"(?:R\.?U\.?T\.?|RUN)\s*(?:del\s+empleador)?\s*:?\s*"
        r"((?:\d{1,2}(?:\.\d{3}){2}|\d{7,8})\s*-\s*[\dkK])",
        texto, re.IGNORECASE,
    )
    if m_rut:
        contexto["contratista_rut"] = clave_rut(m_rut.group(1))

    texto_k = clave_texto(texto)
    if "asociacion chilena de seguridad" in texto_k or re.search(r"\bACHS\b", texto, re.IGNORECASE):
        contexto["mutual_adherida"] = "ACHS"
    elif "instituto de seguridad laboral" in texto_k or re.search(r"\bISL\b", texto, re.IGNORECASE):
        contexto["mutual_adherida"] = "ISL"
    elif "instituto de seguridad del trabajo" in texto_k or re.search(r"\bIST\b", texto, re.IGNORECASE):
        contexto["mutual_adherida"] = "IST"
    elif "mutual de seguridad" in texto_k or "c ch c" in texto_k or "cchc" in texto_k:
        contexto["mutual_adherida"] = "Mutual de Seguridad"
    return contexto


def _completar_contexto_pdf(tabla: pd.DataFrame, contexto: dict[str, str]) -> pd.DataFrame:
    """Completa campos globales solo cuando el documento los declara y no hay columna fuente."""
    mapeada, faltantes = _mapear_columnas(tabla)
    resultado = tabla.copy()
    for campo, valor in contexto.items():
        if campo in faltantes:
            resultado[campo] = valor
    return resultado


def _fila_pdf_parece_trabajador(fila: Iterable[Any]) -> bool:
    """Detecta RUTs con formato chileno dentro de una fila PDF sin cabecera."""
    texto = " ".join(str(x) for x in fila if x is not None)
    return bool(re.search(r"(?<!\d)\d{1,2}\.\d{3}\.\d{3}\s*-\s*[\dkK](?!\w)", texto))


def _fusionar_tablas_pdf(tablas: list[pd.DataFrame], contexto: dict[str, str]) -> pd.DataFrame:
    """Une secciones/tablas del mismo PDF por trabajador sin sumar filas repetidas.

    Las cantidades repetidas idénticas se conservan una sola vez. Si dos tablas entregan
    valores distintos para la misma base o días, se deja una marca de conflicto para revisión.
    Solo se fusionan filas cuando período, RUT del contratista y RUT del trabajador están
    identificados; de lo contrario quedan separadas y el motor las marcará para revisar.
    """
    filas_normalizadas: list[dict[str, Any]] = []
    campos = [
        "periodo_remuneracion", "contratista_nombre", "contratista_rut",
        "trabajador_nombre", "trabajador_rut", "dias_reales_pagados",
        "renta_imponible_salud", "renta_imponible_afp", "renta_imponible_mutual",
        "mutual_adherida",
    ]

    for tabla in tablas:
        normalizada, faltantes = _mapear_columnas(tabla)
        for campo, valor in contexto.items():
            if campo in normalizada.columns:
                normalizada[campo] = normalizada[campo].apply(lambda x: valor if es_vacio(x) else x)
        for _, fila in normalizada.iterrows():
            registro: dict[str, Any] = {}
            for campo in campos:
                valor = fila.get(campo, pd.NA)
                if campo == "periodo_remuneracion":
                    valor = normalizar_periodo(valor)
                elif campo in {"contratista_rut", "trabajador_rut"}:
                    valor = clave_rut(valor)
                elif campo in {"contratista_nombre", "trabajador_nombre"}:
                    valor = "" if es_vacio(valor) else re.sub(r"\s+", " ", str(valor).strip()).upper()
                elif campo == "mutual_adherida":
                    valor = normalizar_mutual(valor)
                elif campo in COLUMNAS_NUMERICAS:
                    valor = None if es_vacio(valor) else convertir_monto(valor)
                else:
                    valor = None if es_vacio(valor) else str(valor).strip()
                registro[campo] = valor
            registro["__hoja_origen"] = str(fila.get("__hoja_origen", ""))
            registro["__conflicto_pdf"] = ""
            filas_normalizadas.append(registro)

    if not filas_normalizadas:
        return pd.DataFrame(columns=campos + ["__hoja_origen", "__conflicto_pdf"])

    # Separar filas sin una clave completa para evitar fusionar personas/meses por suposiciones.
    grupos: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    filas_sin_clave: list[dict[str, Any]] = []
    for i, fila in enumerate(filas_normalizadas):
        periodo = str(fila.get("periodo_remuneracion") or "")
        rut_emp = str(fila.get("contratista_rut") or "")
        rut_trab = str(fila.get("trabajador_rut") or "")
        if periodo and rut_emp and rut_trab:
            grupos.setdefault((periodo, rut_emp, rut_trab), []).append(fila)
        else:
            fila["__conflicto_pdf"] = "No se pudo fusionar porque faltaba período o RUT del empleador/trabajador"
            filas_sin_clave.append(fila)

    resultados: list[dict[str, Any]] = []
    for clave, grupo in grupos.items():
        unido = dict(grupo[0])
        conflictos: list[str] = []
        for campo in campos:
            valores: list[Any] = []
            for fila in grupo:
                valor = fila.get(campo)
                if valor is None or es_vacio(valor):
                    continue
                if campo in COLUMNAS_NUMERICAS:
                    comparable = int(valor)
                elif campo in {"contratista_rut", "trabajador_rut"}:
                    comparable = clave_rut(valor)
                elif campo == "periodo_remuneracion":
                    comparable = normalizar_periodo(valor)
                else:
                    comparable = clave_texto(valor)
                if comparable not in [x[0] for x in valores]:
                    valores.append((comparable, valor))
            if len(valores) == 1:
                unido[campo] = valores[0][1]
            elif len(valores) > 1:
                conflictos.append(f"{campo}: valores distintos {', '.join(str(x[1]) for x in valores)}")
                # No escoger a ciegas un valor: dejar el primer valor para mostrarlo, pero marcar revisión.
                unido[campo] = valores[0][1]
        unido["__hoja_origen"] = "; ".join(dict.fromkeys(str(x.get("__hoja_origen", "")) for x in grupo if x.get("__hoja_origen")))
        unido["__conflicto_pdf"] = "; ".join(conflictos)
        resultados.append(unido)

    resultados.extend(filas_sin_clave)
    return pd.DataFrame(resultados, columns=campos + ["__hoja_origen", "__conflicto_pdf"])


def _ocr_pdf_pagina(contenido: bytes, indice_pagina: int, nombre: str) -> bytes:
    """Convierte una página escaneada a PDF con capa de texto OCR en español.

    Se utiliza PyMuPDF para renderizar; no requiere Poppler. Tesseract y el paquete
    de idioma español deben instalarse en el sistema (packages.txt en Streamlit Cloud).
    """
    try:
        import fitz
        import pytesseract
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise ValueError(
            "El PDF necesita OCR, pero faltan dependencias. Verifica requirements.txt "
            "(PyMuPDF, pytesseract, Pillow) y packages.txt (tesseract-ocr y tesseract-ocr-spa), "
            "y vuelve a desplegar la aplicación en Streamlit."
        ) from exc

    try:
        with fitz.open(stream=contenido, filetype="pdf") as doc:
            if indice_pagina < 0 or indice_pagina >= doc.page_count:
                raise ValueError("Número de página fuera del PDF.")
            # Límite de seguridad para no agotar memoria/CPU del alojamiento gratuito.
            if doc.page_count > 60:
                raise ValueError(
                    f"{nombre}: el PDF tiene {doc.page_count} páginas. Para proteger el servicio, "
                    "el OCR automático admite hasta 60 páginas por archivo. Divide el PDF y vuelve a cargarlo."
                )
            page = doc.load_page(indice_pagina)
            pix = page.get_pixmap(matrix=fitz.Matrix(3.0, 3.0), colorspace=fitz.csGRAY, alpha=False)
            imagen = Image.frombytes("L", (pix.width, pix.height), pix.samples)
            imagen = ImageOps.autocontrast(imagen)

        idiomas = set(pytesseract.get_languages(config=""))
        if "spa" in idiomas and "eng" in idiomas:
            idioma = "spa+eng"
        elif "spa" in idiomas:
            idioma = "spa"
        elif "eng" in idiomas:
            idioma = "eng"
        else:
            raise ValueError(
                "Tesseract está instalado, pero no tiene un modelo de idioma compatible. "
                "Comprueba packages.txt y que se haya instalado tesseract-ocr-spa."
            )

        pdf_ocr = pytesseract.image_to_pdf_or_hocr(
            imagen,
            extension="pdf",
            lang=idioma,
            config="--psm 4",
        )
        if not pdf_ocr:
            raise ValueError("Tesseract no devolvió una capa de texto OCR.")
        return pdf_ocr
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(
            f"{nombre}, página {indice_pagina + 1}: falló el OCR automático: {exc}. "
            "Prueba con un PDF menos borroso o exportado directamente desde el sistema de origen."
        ) from exc


def _tabla_pdf_validada(filas: list[list[Any]], etiqueta: str) -> Optional[pd.DataFrame]:
    """Devuelve una tabla solo si las cabeceras/datos parecen identificar trabajadores."""
    if not filas or len(filas) < 2:
        return None
    try:
        crudo = pd.DataFrame(filas)
        tabla = _aplicar_cabecera_automatica(crudo, etiqueta)
    except (ValueError, IndexError, TypeError):
        return None
    if _tabla_es_de_trabajadores(tabla):
        return tabla
    return None


def _extraer_tablas_pagina_pdf(pagina: Any, etiqueta: str, usar_ocr: bool = False) -> tuple[list[pd.DataFrame], list[str]]:
    """Prueba varios métodos conservadores de detección de tablas sin asignar columnas por posición."""
    tablas: list[pd.DataFrame] = []
    errores_cabecera: list[str] = []
    estrategias: list[Optional[dict[str, Any]]] = [None]
    if usar_ocr:
        # OCR conserva posiciones aproximadas, por eso se prueban también estrategias basadas en texto.
        estrategias.extend([
            {
                "vertical_strategy": "text",
                "horizontal_strategy": "text",
                "text_x_tolerance": 5,
                "text_y_tolerance": 5,
                "min_words_vertical": 2,
                "min_words_horizontal": 1,
            },
            {
                "vertical_strategy": "text",
                "horizontal_strategy": "text",
                "text_x_tolerance": 8,
                "text_y_tolerance": 8,
                "min_words_vertical": 1,
                "min_words_horizontal": 1,
            },
        ])

    for estrategia in estrategias:
        try:
            tablas_crudas = pagina.extract_tables(table_settings=estrategia) if estrategia else pagina.extract_tables()
        except Exception:
            continue
        for num_tabla, filas in enumerate(tablas_crudas or [], start=1):
            tabla = _tabla_pdf_validada(filas, f"{etiqueta} / tabla {num_tabla}")
            if tabla is not None:
                # Evita añadir la misma tabla cuando dos estrategias la detectan.
                firma = tuple(str(x) for x in tabla.columns) + tuple(
                    str(x) for x in tabla.head(3).astype(str).to_numpy().ravel()
                )
                if not any(
                    tuple(str(x) for x in existente.columns) + tuple(
                        str(x) for x in existente.head(3).astype(str).to_numpy().ravel()
                    ) == firma
                    for existente in tablas
                ):
                    tabla["__hoja_origen"] = etiqueta
                    tablas.append(tabla)
        if tablas:
            # La primera estrategia que consigue una tabla fiable tiene prioridad.
            break
    return tablas, errores_cabecera


def _leer_pdf_bytes(contenido: bytes, nombre: str) -> pd.DataFrame:
    """Extrae tablas PDF y aplica OCR automático cuando encuentra páginas escaneadas."""
    if pdfplumber is None:
        raise ValueError("Para procesar PDF, agregue pdfplumber a requirements.txt.")

    tablas_validas: list[pd.DataFrame] = []
    textos_pdf: list[str] = []
    paginas_ocr: list[int] = []  # índices base cero de páginas que requieren OCR
    paginas_con_tabla: set[int] = set()
    total_paginas = 0

    try:
        with pdfplumber.open(io.BytesIO(contenido)) as pdf:
            total_paginas = len(pdf.pages)
            for indice, pagina in enumerate(pdf.pages):
                texto = pagina.extract_text() or ""
                textos_pdf.append(texto)
                label = f"Página {indice + 1}"
                tablas_pagina, _ = _extraer_tablas_pagina_pdf(pagina, label, usar_ocr=False)
                if tablas_pagina:
                    paginas_con_tabla.add(indice)
                    tablas_validas.extend(tablas_pagina)
                else:
                    texto_norm = clave_texto(texto)
                    parece_tabla = any(
                        palabra in texto_norm
                        for palabra in ("trabajador", "cotizaciones", "imponible", "dias trabajados", "afiliado", "identificacion del trabajador")
                    )
                    # Páginas sin capa de texto o con términos de nómina se prueban con OCR.
                    if not texto.strip() or parece_tabla:
                        paginas_ocr.append(indice)
    except Exception as exc:
        raise ValueError(f"{nombre}: error leyendo PDF: {exc}") from exc

    # OCR solo sobre páginas candidatas; las páginas digitales que ya se pudieron leer se conservan.
    textos_ocr: list[str] = []
    paginas_ocr_sin_tabla: list[tuple[int, str]] = []
    if paginas_ocr:
        for indice in paginas_ocr:
            # Ahorra recursos si la página ya fue detectada como tabla válida.
            if indice in paginas_con_tabla:
                continue
            try:
                pdf_pagina_ocr = _ocr_pdf_pagina(contenido, indice, nombre)
                with pdfplumber.open(io.BytesIO(pdf_pagina_ocr)) as doc_ocr:
                    if not doc_ocr.pages:
                        continue
                    pagina_ocr = doc_ocr.pages[0]
                    texto_ocr = pagina_ocr.extract_text() or ""
                    textos_ocr.append(texto_ocr)
                    etiqueta = f"Página {indice + 1} (OCR)"
                    tablas_pagina, _ = _extraer_tablas_pagina_pdf(pagina_ocr, etiqueta, usar_ocr=True)
                    if tablas_pagina:
                        tablas_validas.extend(tablas_pagina)
                        paginas_con_tabla.add(indice)
                    elif texto_ocr.strip():
                        # No inventar estructura si el OCR leyó letras, pero no recuperó tabla.
                        texto_norm = clave_texto(texto_ocr)
                        tiene_indicios_nomina = any(
                            palabra in texto_norm
                            for palabra in ("trabajador", "cotizaciones", "imponible", "dias trabajados", "afiliado")
                        )
                        tiene_rut = bool(re.search(r"\b\d{1,2}\.\d{3}\.\d{3}\s*-\s*[\dkK]\b", texto_ocr))
                        if tiene_indicios_nomina and tiene_rut:
                            paginas_ocr_sin_tabla.append((indice + 1, texto_ocr[:300].replace("\n", " | ")))
            except ValueError:
                raise
            except Exception as exc:
                raise ValueError(
                    f"{nombre}: no se pudo aplicar OCR a la página {indice + 1}: {exc}. "
                    "Revisa la instalación de Tesseract en Streamlit Cloud."
                ) from exc

    # Si el OCR detectó una aparente nómina pero no pudo reconstruir una tabla, no devolver un parcial.
    if paginas_ocr_sin_tabla:
        paginas = ", ".join(str(n) for n, _ in paginas_ocr_sin_tabla[:8])
        raise ValueError(
            f"{nombre}: el OCR detectó texto de trabajadores en las páginas {paginas}, pero no logró "
            "reconstruir las columnas con suficiente seguridad. No se generó una auditoría parcial. "
            "Prueba descargando el PDF con mejor resolución o exportando el reporte original a Excel/CSV."
        )

    if tablas_validas:
        contexto = _extraer_contexto_pdf("\n".join(textos_pdf + textos_ocr))
        return _fusionar_tablas_pdf(tablas_validas, contexto)

    texto_original = "\n".join(textos_pdf).strip()
    texto_reconocido = "\n".join(textos_ocr).strip()
    if not texto_original and not texto_reconocido:
        raise ValueError(
            f"{nombre}: no se pudo reconocer texto en el PDF ni siquiera con OCR. "
            "Puede estar en blanco, protegido, borroso o con una calidad de imagen insuficiente."
        )
    if not texto_original and texto_reconocido:
        raise ValueError(
            f"{nombre}: se aplicó OCR automáticamente y se reconoció texto, pero no se pudieron identificar "
            "las columnas de trabajadores de forma fiable. Para evitar errores en días o imponibles, exporta "
            "la planilla original a Excel/CSV o solicita adaptar el extractor al formato exacto de este PDF."
        )
    raise ValueError(
        f"{nombre}: el PDF contiene texto, pero no se encontró una tabla de trabajadores fiable. "
        "Se probó OCR en las páginas con indicios de nómina. No se asignarán días ni rentas por posición aproximada; "
        "exporta el reporte a Excel/CSV si la extracción sigue fallando."
    )

def leer_archivo(contenido: bytes, nombre: str, modo_ocr: str = "automatico", clasificador=None) -> pd.DataFrame:
    """Lee un archivo cargado por Streamlit sin guardarlo en disco."""
    extension = Path(nombre).suffix.lower()
    if not contenido:
        raise ValueError(f"{nombre}: el archivo está vacío.")
    if extension in {".xlsx", ".xls"}:
        return _leer_excel_bytes(contenido, nombre)
    if extension == ".csv":
        return _leer_csv_bytes(contenido, nombre)
    if extension == ".pdf":
        # Una lectura digital ya contiene caracteres indexables. No pasarla a
        # Tesseract: conserva velocidad, precisión y trazabilidad del archivo.
        # En escaneos, el lector rápido identifica líneas de tablas y aplica OCR
        # solo a celdas útiles (RUT, días, renta, nombre), no a las 12 portadas.
        import fitz
        with fitz.open(stream=contenido, filetype="pdf") as probe:
            digital = any(len(probe[i].get_text().strip()) > 120 for i in range(min(2,probe.page_count)))
        if not digital:
            # Prioridad: certificados INDIVIDUALES (sin días) no son planillas de
            # detalle y NO deben ejecutarse por el extractor de tablas.
            from certificados_previred import extraer_certificado_previred
            certificado=extraer_certificado_previred(contenido, nombre)
            if certificado is not None:
                return certificado
        if not digital and modo_ocr != "reforzado":
            from previred_scan_fast import extraer_previred_escaneado_rapido
            escaneado = extraer_previred_escaneado_rapido(contenido, nombre)
            if escaneado is not None:
                return escaneado
            if modo_ocr == "rapido":
                raise ValueError("OCR rápido no pudo recuperar una tabla confiable. Usa modo automático o reforzado.")
        from previred_pdf import extraer_previred
        especializado = extraer_previred(contenido, nombre, ocr_pagina=_ocr_pdf_pagina, clasificador=clasificador)
        if especializado is not None:
            return especializado
        return _leer_pdf_bytes(contenido, nombre)
    raise ValueError(f"{nombre}: formato {extension or '(sin extensión)'} no admitido.")


def normalizar_mutual(valor: Any) -> str:
    if es_vacio(valor):
        return "NO IDENTIFICADA"
    texto = str(valor).strip()
    k = clave_texto(texto)
    if "achs" in k or "asociacion chilena de seguridad" in k:
        return "ACHS"
    if "instituto de seguridad laboral" in k or re.search(r"\bisl\b", k):
        return "ISL"
    if "instituto de seguridad del trabajo" in k or re.search(r"\bist\b", k):
        return "IST"
    if "mutual de seguridad" in k or "cchc" in k:
        return "Mutual de Seguridad"
    return re.sub(r"\s+", " ", texto).upper()


def limpiar_y_transformar(df_crudo: pd.DataFrame, nombre_archivo: str) -> pd.DataFrame:
    """Transforma cabeceras y datos manteniendo marcas internas para auditoría segura."""
    if df_crudo is None or df_crudo.empty:
        raise ValueError("El archivo no contiene filas de datos.")

    df, faltantes_columnas = _mapear_columnas(df_crudo)
    periodo_nombre = inferir_periodo_nombre_archivo(nombre_archivo)

    # Se guardan faltantes a nivel de celda ANTES de completar numéricos con cero.
    faltantes_fila: list[str] = []
    for idx in df.index:
        ausentes = []
        for columna in ALIAS_COLUMNAS:
            if columna in faltantes_columnas or columna not in df.columns:
                continue
            if es_vacio(df.at[idx, columna]):
                ausentes.append(columna)
        faltantes_fila.append("|".join(ausentes))
    df["__faltantes_fila"] = faltantes_fila
    df["__archivo_origen"] = nombre_archivo
    if "__conflicto_pdf" in df_crudo.columns:
        df["__conflicto_pdf"] = df_crudo["__conflicto_pdf"].reindex(df.index).fillna("")
    for campo_extraccion in ('__ocr_incidencias_documento', '__ocr_origen', '__ocr_conciliacion', '__ocr_observaciones_informativas', '__comprobantes_regularizados'):
        if campo_extraccion in df_crudo.columns:
            df[campo_extraccion] = df_crudo[campo_extraccion].reindex(df.index).fillna('')
    if '__ocr_incidencias_documento' not in df.columns:
        df['__ocr_incidencias_documento'] = ''
    if '__ocr_origen' not in df.columns:
        df['__ocr_origen'] = ''
    if "__hoja_origen" not in df.columns:
        df["__hoja_origen"] = ""

    # El período solo se completa desde el nombre si allí hay un patrón explícito.
    df["periodo_remuneracion"] = df["periodo_remuneracion"].apply(normalizar_periodo)
    if periodo_nombre:
        df.loc[df["periodo_remuneracion"].eq(""), "periodo_remuneracion"] = periodo_nombre
        # Al inferirse un período explícito desde el nombre del archivo, ya no es un campo ausente.
        faltantes_columnas.discard("periodo_remuneracion")
        def quitar_periodo_faltante(valor: Any, periodo: Any) -> str:
            partes = [p for p in str(valor or "").split("|") if p and not (p == "periodo_remuneracion" and periodo)]
            return "|".join(partes)
        df["__faltantes_fila"] = [
            quitar_periodo_faltante(a, p) for a, p in zip(df["__faltantes_fila"], df["periodo_remuneracion"])
        ]
    df["__campos_ausentes"] = "|".join(sorted(faltantes_columnas))

    for columna in ("contratista_nombre", "trabajador_nombre"):
        df[columna] = df[columna].apply(lambda x: "" if es_vacio(x) else re.sub(r"\s+", " ", str(x).strip()).upper())
    for columna in ("contratista_rut", "trabajador_rut"):
        df[columna] = df[columna].apply(clave_rut)
    df["mutual_adherida"] = df["mutual_adherida"].apply(normalizar_mutual)

    # Revisar formato antes de convertir: un texto inesperado no debe transformarse
    # silenciosamente en cero y terminar en un falso estado CONFORME.
    valores_invalidos_por_fila: list[str] = []
    for _, fila in df.iterrows():
        invalidos: list[str] = []
        for columna in COLUMNAS_NUMERICAS:
            valor = fila.get(columna, pd.NA)
            if not monto_es_valido(valor):
                invalidos.append(columna + " (formato no numérico)")
                continue
            if es_vacio(valor):
                continue
            numero = convertir_monto(valor)
            if numero < 0:
                invalidos.append(columna + " (valor negativo)")
            elif columna == "dias_reales_pagados" and not monto_es_entero(valor):
                invalidos.append(columna + " (los días deben ser enteros)")
            elif columna == "dias_reales_pagados" and numero > 31:
                invalidos.append(columna + " (más de 31 días en un período mensual)")
        valores_invalidos_por_fila.append("|".join(invalidos))
    df["__valores_invalidos"] = valores_invalidos_por_fila

    # No asignar mutual por defecto. Las columnas numéricas ausentes se exponen en cero,
    # pero las marcas internas impiden clasificarlas erróneamente como conformes.
    for columna in COLUMNAS_NUMERICAS:
        df[columna] = df[columna].apply(convertir_monto).astype("int64")

    # Eliminar filas vacías, totales/subtotales y cabeceras repetidas.
    df = df.dropna(how="all").copy()
    nombre_k = df["trabajador_nombre"].astype(str).map(clave_texto)
    rut_k = df["trabajador_rut"].astype(str).map(clave_rut)
    basura = nombre_k.str.match(r"^(total|subtotal|resumen|total general|pagina|página|acumulado)\b", na=False)
    header_repetido = nombre_k.str.contains(r"nombre trabajador|nombre del trabajador", na=False) | rut_k.eq("rut")
    df = df.loc[~(basura | header_repetido)].copy()

    # Quitar solo filas completamente vacías; conservar filas que requieren revisión.
    tiene_algo = (
        df["trabajador_nombre"].ne("") | df["trabajador_rut"].ne("")
        | df["dias_reales_pagados"].ne(0)
        | df[COLUMNAS_RENTAS].ne(0).any(axis=1)
        | df["periodo_remuneracion"].ne("")
    )
    df = df.loc[tiene_algo].copy()
    if df.empty:
        raise ValueError("No se identificaron registros de trabajadores luego de la limpieza.")

    df["__rut_invalido"] = [
        not rut_chileno_valido(c) or not rut_chileno_valido(t)
        for c, t in zip(df["contratista_rut"], df["trabajador_rut"])
    ]
    # No se suman registros repetidos de manera automática: podrían ser páginas duplicadas
    # o fuentes que se contradicen. Se marcan para revisión, evitando inflar días/rentas.
    keys_validas = df["periodo_remuneracion"].ne("") & df["contratista_rut"].ne("") & df["trabajador_rut"].ne("")
    df["__clave_duplicada"] = False
    df.loc[keys_validas, "__clave_duplicada"] = df.loc[keys_validas].duplicated(
        subset=["periodo_remuneracion", "contratista_rut", "trabajador_rut"], keep=False
    )
    return df.reset_index(drop=True)


def cargar_topes_csv(contenido: Optional[bytes], nombre: str = "topes.csv") -> dict[str, int]:
    """Carga topes CLP por período desde un CSV entregado por el usuario y verificado oficialmente."""
    if not contenido:
        return {}
    error_ultimo: Optional[Exception] = None
    tabla: Optional[pd.DataFrame] = None
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            tabla = pd.read_csv(io.BytesIO(contenido), dtype=object, sep=None, engine="python", encoding=enc)
            break
        except Exception as exc:
            error_ultimo = exc
    if tabla is None:
        raise ValueError(f"No se pudo leer {nombre}: {error_ultimo}")
    columnas = {clave_texto(c): c for c in tabla.columns}
    col_periodo = next((columnas[k] for k in ("periodo remuneracion", "periodo", "mes remuneracion") if k in columnas), None)
    col_tope = next((columnas[k] for k in (
        "tope imponible clp", "tope imponible salud afp clp", "tope salud afp clp", "tope clp"
    ) if k in columnas), None)
    if col_periodo is None or col_tope is None:
        raise ValueError(
            "El CSV debe tener columnas 'periodo_remuneracion' y 'tope_imponible_clp'."
        )
    topes: dict[str, int] = {}
    for nro_fila, fila in enumerate(tabla.to_dict("records"), start=2):
        periodo = normalizar_periodo(fila.get(col_periodo))
        valor_tope = fila.get(col_tope)
        if es_vacio(valor_tope) and es_vacio(fila.get(col_periodo)):
            continue
        if not periodo:
            raise ValueError(f"{nombre}, fila {nro_fila}: período vacío o inválido.")
        if not monto_es_valido(valor_tope):
            raise ValueError(f"{nombre}, fila {nro_fila}: el tope no es numérico ({valor_tope!r}).")
        tope = convertir_monto(valor_tope)
        if tope <= 0:
            raise ValueError(f"{nombre}, fila {nro_fila}: el tope debe ser mayor que cero.")
        if periodo in topes and topes[periodo] != tope:
            raise ValueError(
                f"{nombre}: hay topes distintos para el período {periodo}. "
                "Deja una sola fila válida por período."
            )
        topes[periodo] = tope
    if not topes:
        raise ValueError("El CSV de topes no contiene períodos y topes positivos válidos.")
    return topes


def aplicar_motor_auditoria(
    datos: pd.DataFrame,
    topes_por_periodo: Optional[dict[str, int]] = None,
    tolerancia_clp: int = 15,
    incluir_mutual: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Asigna estados, devuelve matriz estándar y tabla de detalle para revisión."""
    if tolerancia_clp < 0:
        raise ValueError("La tolerancia no puede ser negativa.")
    topes = topes_por_periodo or {}
    for periodo, monto in topes.items():
        if int(monto) <= 0:
            raise ValueError(f"El tope de {periodo} debe ser mayor que cero.")

    salida = datos.copy().reset_index(drop=True)
    # Revalidar duplicados después de unir todos los archivos, no solo dentro de cada archivo.
    keys_validas = (
        salida["periodo_remuneracion"].astype(str).str.strip().ne("")
        & salida["contratista_rut"].astype(str).str.strip().ne("")
        & salida["trabajador_rut"].astype(str).str.strip().ne("")
    )
    duplicados_globales = pd.Series(False, index=salida.index)
    if keys_validas.any():
        duplicados_globales.loc[keys_validas] = salida.loc[keys_validas].duplicated(
            subset=["periodo_remuneracion", "contratista_rut", "trabajador_rut"], keep=False
        )
    duplicados_previos = salida["__clave_duplicada"].fillna(False).astype(bool) if "__clave_duplicada" in salida.columns else False
    salida["__clave_duplicada"] = duplicados_previos | duplicados_globales

    estados: list[str] = []
    razones_lista: list[str] = []

    for _, fila in salida.iterrows():
        razones: list[str] = []
        campos_ausentes = set(filter(None, str(fila.get("__campos_ausentes", "")).split("|")))
        faltantes_fila = set(filter(None, str(fila.get("__faltantes_fila", "")).split("|")))
        criticos = set(CAMPOS_CRITICOS_BASE)
        if incluir_mutual:
            criticos.add("renta_imponible_mutual")
        faltan_columnas_criticas = campos_ausentes.intersection(criticos)
        faltan_celdas_criticas = faltantes_fila.intersection(criticos)

        if faltan_columnas_criticas:
            razones.append("Faltan columnas de origen: " + ", ".join(sorted(faltan_columnas_criticas)))
        if faltan_celdas_criticas:
            razones.append("Faltan valores en la fila: " + ", ".join(sorted(faltan_celdas_criticas)))
        if not str(fila.get("periodo_remuneracion", "")).strip():
            razones.append("No se pudo identificar el período de remuneración")
        if not str(fila.get("contratista_nombre", "")).strip():
            razones.append("Falta el nombre del contratista")
        if not str(fila.get("trabajador_nombre", "")).strip():
            razones.append("Falta el nombre del trabajador")
        if not str(fila.get("contratista_rut", "")).strip():
            razones.append("Falta el RUT del contratista")
        if not str(fila.get("trabajador_rut", "")).strip():
            razones.append("Falta el RUT del trabajador")
        if str(fila.get("mutual_adherida", "NO IDENTIFICADA")).strip().upper() == "NO IDENTIFICADA":
            razones.append("No se pudo identificar la mutual adherida desde el documento")
        conflicto_pdf = str(fila.get("__conflicto_pdf", "") or "").strip()
        if conflicto_pdf and conflicto_pdf.lower() not in {"nan", "none", "<na>"}:
            # Evitar llamar "conflicto" a incertidumbres del OCR.
            tecnico = conflicto_pdf.lower()
            es_lectura = any(x in tecnico for x in (
                'ocr', 'rut alineado', 'lecturas alternativas', 'días no conciliados',
                'días de salud ilegibles', 'renta mínima', 'base pequeña ambigua',
                'nombre del trabajador no confirmado', 'celdas ocr'))
            razones.append(("Extracción OCR pendiente de cotejo: " if es_lectura else
                           "Diferencia de fuentes o datos incompletos: ") + conflicto_pdf)
        if bool(fila.get("__rut_invalido", False)):
            razones.append("RUT del contratista o trabajador no supera la validación de dígito verificador")
        valores_invalidos = [x for x in str(fila.get("__valores_invalidos", "") or "").split("|") if x]
        if valores_invalidos:
            razones.append("Valores numéricos inválidos o negativos: " + ", ".join(valores_invalidos))
        if bool(fila.get("__clave_duplicada", False)):
            razones.append("Registro duplicado para el mismo período, contratista y trabajador; no se sumó automáticamente")

        dias = int(fila.get("dias_reales_pagados", 0) or 0)
        salud = int(fila.get("renta_imponible_salud", 0) or 0)
        afp = int(fila.get("renta_imponible_afp", 0) or 0)
        mutual = int(fila.get("renta_imponible_mutual", 0) or 0)
        rentas = [salud, afp, mutual] if incluir_mutual else [salud, afp]
        hay_datos_de_renta = any(v > 0 for v in rentas)
        periodo = str(fila.get("periodo_remuneracion", "")).strip()
        tope = topes.get(periodo)
        # Nunca declarar CONFORME solo porque varios importes se limitaron al mismo tope:
        # si el campo es "renta imponible" y supera el límite, se debe comprobar la fuente.
        supera_tope = bool(tope) and any(v > int(tope) + tolerancia_clp for v in rentas)
        if supera_tope:
            razones.append(
                f"Al menos una renta imponible declarada supera el tope informado de ${int(tope):,}; "
                "verificar si el archivo contiene la base previa a aplicar el tope o un dato incorrecto"
            )
        # Un RUT inválido, un duplicado o una base sobre el tope no borra otros hallazgos.
        correccion_manual=str(fila.get("__revision_manual", "") or "").strip()
        if correccion_manual:
            razones.append("Correcciones confirmadas manualmente: " + correccion_manual)
        problema_integridad = bool(razones)
        bases_a_comparar = [min(v, int(tope)) for v in rentas] if tope else rentas
        hay_descuadre = (max(bases_a_comparar) - min(bases_a_comparar)) > tolerancia_clp
        columnas_rentas_a_comparar = {"renta_imponible_salud", "renta_imponible_afp"}
        if incluir_mutual:
            columnas_rentas_a_comparar.add("renta_imponible_mutual")
        rentas_faltantes = campos_ausentes.intersection(columnas_rentas_a_comparar) | faltantes_fila.intersection(columnas_rentas_a_comparar)
        dias_faltantes = "dias_reales_pagados" in campos_ausentes or "dias_reales_pagados" in faltantes_fila

        if not dias_faltantes and dias <= 0 and hay_datos_de_renta:
            razones.append("Hay renta imponible positiva, pero los días reales pagados son cero")
        if not rentas_faltantes and hay_descuadre:
            razones.append(
                "Diferencia entre bases imponibles fuera de la tolerancia de "
                f"${tolerancia_clp:,}; valores comparados: Salud ${salud:,}, AFP ${afp:,}, Mutual ${mutual:,}"
            )
            if not tope:
                razones.append("No se proporcionó tope del período; no se aplicó excepción por tope imponible")

        # Primero se evita un dictamen definitivo cuando los datos de entrada son inválidos.
        if problema_integridad:
            estado = "REVISAR_DATOS"
        elif dias <= 0 and hay_datos_de_renta:
            estado = "ALERTA_DIAS_CERO"
        elif dias <= 0 and not hay_datos_de_renta:
            estado = "REVISAR_DATOS"
            razones.append("No hay días ni rentas positivas para confirmar el período")
        elif dias > 0 and not hay_datos_de_renta:
            estado = "REVISAR_DATOS"
            razones.append("Hay días pagados, pero todas las rentas imponibles son cero; verificar el antecedente")
        elif hay_descuadre:
            estado = "ALERTA_DESCUADRE_RENTAS"
        else:
            estado = "CONFORME"
        estados.append(estado)
        razones_lista.append("; ".join(razones) if razones else "Sin observaciones según las reglas configuradas")

    salida["estado_auditoria"] = estados
    salida["__detalle_auditoria"] = razones_lista
    matriz = salida.reindex(columns=COLUMNAS_SALIDA).copy()

    # Hoja aparte para explicar estados que no son conformes; no se agregan columnas a la matriz solicitada.
    mask = salida["estado_auditoria"].ne("CONFORME")
    cols_detalle = ["__archivo_origen", "__hoja_origen"] + [c for c in COLUMNAS_SALIDA if c != "estado_auditoria"] + ["estado_auditoria", "__detalle_auditoria"]
    detalle = salida.loc[mask, [c for c in cols_detalle if c in salida.columns]].copy()
    detalle = detalle.rename(columns={
        "__archivo_origen": "archivo_origen",
        "__hoja_origen": "hoja_o_pagina",
        "__detalle_auditoria": "motivo_revision",
    })
    # Mostrar explicaciones claras en UI y Excel, conservando el original
    # técnico para reproducir el diagnóstico.
    if not detalle.empty:
        from explicaciones_revision import explicar_observacion
        mensajes = [explicar_observacion(r['motivo_revision'],r.to_dict()) for _,r in detalle.iterrows()]
        for campo in ('tipo_revision','prioridad_revision','accion_recomendada','detalle_tecnico'):
            detalle[campo] = [m[campo] for m in mensajes]
        detalle['motivo_revision'] = [m['motivo_revision'] for m in mensajes]
    return matriz, detalle.reset_index(drop=True)


def _incidencia_adicional(matriz: pd.DataFrame, detalle: pd.DataFrame, rut: str, motivo: str) -> None:
    """Añade alerta comprobable y actualiza explicación legible y evidencia cruda."""
    from explicaciones_revision import explicar_observacion
    ix=matriz.index[matriz['trabajador_rut'].astype(str).eq(rut)].tolist()
    for i in ix:
        estado=str(matriz.at[i,'estado_auditoria'])
        if not estado.startswith('ALERTA'):
            matriz.at[i,'estado_auditoria']='REVISAR_DATOS'
        d_ix=detalle.index[detalle['trabajador_rut'].astype(str).eq(rut)].tolist()
        if d_ix:
            for j in d_ix:
                bruto=str(detalle.at[j,'detalle_tecnico']) if 'detalle_tecnico' in detalle else str(detalle.at[j,'motivo_revision'])
                frase=bruto+'; '+motivo
                claro=explicar_observacion(frase,detalle.loc[j].to_dict())
                for campo,valor in claro.items():
                    detalle.at[j,campo]=valor
                detalle.at[j,'estado_auditoria']=matriz.at[i,'estado_auditoria']
        else:
            drow={c: matriz.at[i,c] for c in matriz.columns if c in detalle.columns}
            drow.update(explicar_observacion(motivo,drow))
            for campo in drow:
                if campo not in detalle.columns:
                    detalle[campo] = ''
            detalle.loc[len(detalle)]=drow


def exportar_excel(
    matriz: pd.DataFrame,
    detalle_revision: pd.DataFrame,
    log_procesamiento: pd.DataFrame,
    trazabilidad_pdf: Optional[pd.DataFrame] = None,
    comparacion_asistencia: Optional[pd.DataFrame] = None,
    control_seguro_social: Optional[pd.DataFrame] = None,
    diagnostico_ocr: Optional[pd.DataFrame] = None,
    control_independientes: Optional[pd.DataFrame] = None,
) -> bytes:
    """Genera un Excel con matriz estándar, detalle de revisión y log de procesamiento."""
    import xlsxwriter  # dependecia declarada en requirements.txt
    buffer = io.BytesIO()
    with pd.ExcelWriter(
        buffer,
        engine="xlsxwriter",
        engine_kwargs={"options": {"strings_to_formulas": False, "strings_to_urls": False}},
    ) as writer:
        nombre_hoja = "Matriz_Auditoria"
        matriz.reindex(columns=COLUMNAS_SALIDA).to_excel(writer, sheet_name=nombre_hoja, startrow=3, index=False, header=False)
        wb = writer.book
        ws = writer.sheets[nombre_hoja]
        ws.hide_gridlines(2)
        titulo = wb.add_format({"font_name": "Calibri", "font_size": 14, "bold": True, "font_color": "#FFFFFF", "bg_color": "#990000", "valign": "vcenter"})
        subtitulo = wb.add_format({"font_name": "Calibri", "font_size": 10, "italic": True, "font_color": "#FFFFFF", "bg_color": "#1E293B", "valign": "vcenter"})
        cabecera = wb.add_format({"font_name": "Calibri", "bold": True, "font_color": "#FFFFFF", "bg_color": "#990000", "align": "center", "valign": "vcenter", "text_wrap": True, "border": 1})
        fmt_texto = wb.add_format({"font_name": "Calibri", "font_size": 10, "valign": "vcenter"})
        fmt_centro = wb.add_format({"font_name": "Calibri", "font_size": 10, "align": "center", "valign": "vcenter", "num_format": "@"})
        fmt_dias = wb.add_format({"font_name": "Calibri", "font_size": 10, "align": "center", "num_format": "#,##0"})
        fmt_moneda = wb.add_format({"font_name": "Calibri", "font_size": 10, "align": "right", "num_format": "$#,##0;[Red]-$#,##0"})
        fmt_total = wb.add_format({"font_name": "Calibri", "bold": True, "font_color": "#FFFFFF", "bg_color": "#1E293B", "num_format": "$#,##0"})
        fmt_total_dias = wb.add_format({"font_name": "Calibri", "bold": True, "font_color": "#FFFFFF", "bg_color": "#1E293B", "num_format": "#,##0"})
        fmt_ok = wb.add_format({"bg_color": "#E2EFDA", "font_color": "#1E4620"})
        fmt_alerta = wb.add_format({"bg_color": "#FCE4D6", "font_color": "#9C0006"})
        fmt_estado_ok = wb.add_format({"bg_color": "#C6EFCE", "font_color": "#006100", "bold": True, "align": "center"})
        fmt_estado_alerta = wb.add_format({"bg_color": "#FFC7CE", "font_color": "#9C0006", "bold": True, "align": "center"})
        fmt_revision = wb.add_format({"bg_color": "#FFF2CC", "font_color": "#7F6000", "bold": True, "align": "center"})

        ws.set_row(0, 28)
        ws.set_row(1, 20)
        ws.merge_range(0, 0, 0, len(COLUMNAS_SALIDA) - 1, "MATRIZ DE AUDITORÍA LABORAL DE SUBCONTRATISTAS", titulo)
        ws.merge_range(1, 0, 1, len(COLUMNAS_SALIDA) - 1, f"Generado: {datetime.now():%Y-%m-%d %H:%M} | No calcula tasas de cotización", subtitulo)
        ws.set_row(2, 32)
        for j, col in enumerate(COLUMNAS_SALIDA):
            ws.write(2, j, col, cabecera)
        n = len(matriz)
        inicio = 3
        fin = inicio + max(n, 1) - 1
        anchos = [18, 38, 17, 34, 17, 18, 23, 23, 25, 24, 27]
        for j, ancho in enumerate(anchos):
            fmt = fmt_centro if j in (0, 2, 4, 9, 10) else fmt_dias if j == 5 else fmt_moneda if j in (6, 7, 8) else fmt_texto
            ws.set_column(j, j, ancho, fmt)
        if n:
            # Se usa fórmula relativa a la primera fila de datos (Excel, fila 4).
            ws.conditional_format(inicio, 0, fin, len(COLUMNAS_SALIDA) - 1, {"type": "formula", "criteria": f'=$K{inicio + 1}="CONFORME"', "format": fmt_ok})
            ws.conditional_format(inicio, 0, fin, len(COLUMNAS_SALIDA) - 1, {"type": "formula", "criteria": f'=LEFT($K{inicio + 1},6)="ALERTA"', "format": fmt_alerta})
            ws.conditional_format(inicio, 10, fin, 10, {"type": "cell", "criteria": "equal to", "value": '"CONFORME"', "format": fmt_estado_ok})
            ws.conditional_format(inicio, 10, fin, 10, {"type": "text", "criteria": "containing", "value": "ALERTA", "format": fmt_estado_alerta})
            ws.conditional_format(inicio, 10, fin, 10, {"type": "cell", "criteria": "equal to", "value": '"REVISAR_DATOS"', "format": fmt_revision})
            fila_total = inicio + n
            ws.merge_range(fila_total, 0, fila_total, 4, "TOTALES CONSOLIDADOS", fmt_total)
            for col in (5, 6, 7, 8):
                letra = chr(ord("A") + col)
                form = fmt_total_dias if col == 5 else fmt_total
                ws.write_formula(fila_total, col, f"=SUM({letra}{inicio+1}:{letra}{fin+1})", form)
            ws.write_blank(fila_total, 9, None, fmt_total)
            ws.write_blank(fila_total, 10, None, fmt_total)
        else:
            ws.write(inicio, 0, "Sin registros procesados", fmt_texto)
        ws.autofilter(2, 0, max(2, fin), len(COLUMNAS_SALIDA) - 1)
        ws.freeze_panes(inicio, 0)

        if detalle_revision is not None and not detalle_revision.empty:
            detalle_revision.to_excel(writer, sheet_name="Detalle_Revision", index=False)
            wsd = writer.sheets["Detalle_Revision"]
            wsd.freeze_panes(1, 0)
            wsd.autofilter(0, 0, len(detalle_revision), max(len(detalle_revision.columns) - 1, 0))
            for idx, col in enumerate(detalle_revision.columns):
                wsd.set_column(idx, idx, min(max(len(str(col)) + 3, 16), 48))
            for j, col in enumerate(detalle_revision.columns):
                wsd.write(0, j, col, cabecera)
        else:
            pd.DataFrame([{"resultado": "Sin alertas ni registros que requieran revisión."}]).to_excel(writer, sheet_name="Detalle_Revision", index=False)
            writer.sheets["Detalle_Revision"].set_column(0, 0, 65)

        if trazabilidad_pdf is not None and not trazabilidad_pdf.empty:
            trazabilidad_pdf.to_excel(writer, sheet_name="Trazabilidad_PDF", index=False)
            wst = writer.sheets["Trazabilidad_PDF"]
            wst.freeze_panes(1, 0)
            wst.autofilter(0, 0, len(trazabilidad_pdf), len(trazabilidad_pdf.columns) - 1)
            for idx, col in enumerate(trazabilidad_pdf.columns):
                wst.set_column(idx, idx, min(max(len(str(col)) + 3, 18), 56))
                wst.write(0, idx, col, cabecera)

        # V7: incidencias OCR desduplicadas (por RUT o documento).
        if diagnostico_ocr is not None and not diagnostico_ocr.empty:
            diagnostico_ocr.to_excel(writer, sheet_name='Diagnostico_OCR', index=False)
            wo=writer.sheets['Diagnostico_OCR']
            wo.freeze_panes(1,0)
            wo.autofilter(0,0,len(diagnostico_ocr),len(diagnostico_ocr.columns)-1)
            wo.hide_gridlines(2)
            for j,c in enumerate(diagnostico_ocr.columns):
                wo.write(0,j,c,cabecera)
                wo.set_column(j,j, 75 if c in ('hallazgo','accion') else 42 if c=='archivo' else 22)
        else:
            pd.DataFrame([{'resultado':'Sin incidencias OCR identificadas'}]).to_excel(writer,sheet_name='Diagnostico_OCR',index=False)

        # Resumen ejecutivo simple para explicar por qué puede haber bases iguales
        # aun cuando falte la mutual. No contabilizar como $0 una fuente ausente.
        def _resumen_control():
            ausentes=int(matriz['mutual_adherida'].eq('NO IDENTIFICADA').sum()) if 'mutual_adherida' in matriz else 0
            return pd.DataFrame([
                {'control':'Trabajadores/registros identificados','resultado':int(len(matriz))},
                {'control':'Bases AFP=Salud (filas)','resultado':int((matriz['renta_imponible_salud']==matriz['renta_imponible_afp']).sum())},
                {'control':'Renta imponible Salud total (CLP)','resultado':int(matriz['renta_imponible_salud'].sum())},
                {'control':'Renta imponible AFP total (CLP)','resultado':int(matriz['renta_imponible_afp'].sum())},
                {'control':'Días declarados acumulados','resultado':int(matriz['dias_reales_pagados'].sum())},
                {'control':'Trabajadores sin mutual identificada','resultado':ausentes},
                {'control':'Trabajadores sin registro en Seguro Social','resultado':(
                    int(control_seguro_social['cobertura_seguro_social'].eq('SIN_REGISTRO_SS').sum())
                    if control_seguro_social is not None and not control_seguro_social.empty else 0
                )},
                {'control':'Fuente de renta Mutual','resultado':'PENDIENTE DE DOCUMENTO' if ausentes else 'Incluida en detalle'},
                {'control':'Alcance','resultado':'Control de datos extraídos, no certificación legal'},
            ])
        _resumen_control().to_excel(writer,sheet_name="Resumen_Control",index=False)
        wsr=writer.sheets['Resumen_Control']
        wsr.set_column(0,0,43);wsr.set_column(1,1,59)
        wsr.freeze_panes(1,0)
        wsr.write(0,0,'control',cabecera);wsr.write(0,1,'resultado',cabecera)

        # Separa el control de cobertura de Seguro Social del estado de bases.
        # Una persona puede no estar en Seguro Social por exención legal; NO se
        # declara exenta sin evidencia. La matriz CONFORME solo controla bases.
        if control_seguro_social is not None and not control_seguro_social.empty:
            control_seguro_social.to_excel(writer, sheet_name="Control_Seguro_Social", index=False)
            ws_ss = writer.sheets["Control_Seguro_Social"]
            ws_ss.hide_gridlines(2)
            ws_ss.freeze_panes(1, 0)
            ws_ss.autofilter(0, 0, len(control_seguro_social), len(control_seguro_social.columns) - 1)
            for j, columna in enumerate(control_seguro_social.columns):
                ws_ss.set_column(j, j, 23 if j not in (1, 6) else 39)
                ws_ss.write(0, j, columna, cabecera)
        if comparacion_asistencia is not None and not comparacion_asistencia.empty:
            comparacion_asistencia.to_excel(writer, sheet_name="Comparacion_Asistencia", index=False)
            wsa=writer.sheets["Comparacion_Asistencia"]
            wsa.freeze_panes(1,0)
            wsa.autofilter(0,0,len(comparacion_asistencia),len(comparacion_asistencia.columns)-1)
            for j,c in enumerate(comparacion_asistencia.columns):
                wsa.set_column(j,j, min(max(len(str(c))+3,18),35))
                wsa.write(0,j,c,cabecera)
        if control_independientes is not None and not control_independientes.empty:
            control_independientes.to_excel(writer, sheet_name="Control_Independientes",index=False)
            wsi=writer.sheets["Control_Independientes"]
            wsi.hide_gridlines(2);wsi.freeze_panes(1, 3)
            wsi.autofilter(0,0,len(control_independientes),len(control_independientes.columns)-1)
            for j,c in enumerate(control_independientes.columns):
                ancho=45 if c in ('explicacion','archivos_respaldo','paginas_respaldo') else 34 if c in ('estado_control_independiente','persona_nombre','empresa_nombre') else 24
                wsi.set_column(j,j,ancho)
                wsi.write(0,j,c,cabecera)
        (log_procesamiento if log_procesamiento is not None else pd.DataFrame()).to_excel(writer, sheet_name="Log_Procesamiento", index=False)
        wsl = writer.sheets["Log_Procesamiento"]
        wsl.freeze_panes(1, 0)
        if log_procesamiento is not None and len(log_procesamiento.columns):
            wsl.autofilter(0, 0, max(len(log_procesamiento), 1), len(log_procesamiento.columns) - 1)
            for idx, col in enumerate(log_procesamiento.columns):
                wsl.set_column(idx, idx, min(max(len(str(col)) + 3, 16), 70))
            for j, col in enumerate(log_procesamiento.columns):
                wsl.write(0, j, col, cabecera)
    return buffer.getvalue()


def ejecutar_pipeline(
    archivos: Sequence[tuple[bytes, str]],
    topes_csv: Optional[bytes] = None,
    nombre_topes: str = "topes.csv",
    tolerancia_clp: int = 0,
    incluir_mutual: bool = True,
    progreso: Optional[Callable[[int, int, str], None]] = None,
    periodo_esperado: str = "",
    modo_ocr: str = "automatico",
    asistencia: Optional[tuple[bytes, str]] = None,
    memoria_aprendizaje: Optional[dict] = None,
) -> tuple[pd.DataFrame, bytes, pd.DataFrame, pd.DataFrame]:
    """Procesa todos los archivos, continúa ante errores parciales y arma el Excel final."""
    if not archivos:
        raise ValueError("Carga al menos un archivo PDF, Excel o CSV.")
    topes = cargar_topes_csv(topes_csv, nombre_topes) if topes_csv else {}
    from aprendizaje_revisiones import sha256_pdf, aplicar_correcciones, clasificador_aprendido
    memoria = memoria_aprendizaje or {'version':1,'correcciones':[],'plantillas':[]}
    clasificador = clasificador_aprendido(memoria)
    from control_independientes import extraer_evidencia, crear_filas_personales, construir_control
    evidencia_independiente=extraer_evidencia(archivos)
    documentos_independientes, _, solo_independientes = evidencia_independiente
    filas_personales=crear_filas_personales(documentos_independientes,solo_independientes)
    filas_limpias: list[pd.DataFrame] = []
    log: list[dict[str, Any]] = []
    trazas: list[pd.DataFrame] = []

    for numero, (contenido, nombre) in enumerate(archivos, start=1):
        try:
            if nombre in solo_independientes and nombre not in filas_personales:
                # Un comprobante ISL personal aislado NO es planilla del empleador.
                # Se refleja como evidencia en Control_Independientes, no como error.
                log.append({
                    "archivo": nombre, "resultado": "EVIDENCIA_INDEPENDIENTE",
                    "control_calidad": "MUTUAL_INDEPENDIENTE_RECONOCIDA",
                    "filas_detectadas": 0, "campos_no_detectados": "",
                    "incidencias_ocr_sin_rut": "", "registros_ocr_verificados": 0,
                    "casos_sin_registro_seguro_social": 0,
                    "detalle": "Comprobante personal identificado. Revisar hoja Control_Independientes; no se suma a la planilla de la EIRL.",
                })
                continue
            crudo = filas_personales.get(nombre)
            if crudo is None:
                crudo = leer_archivo(contenido, nombre, modo_ocr=modo_ocr, clasificador=clasificador)
            limpio = limpiar_y_transformar(crudo, nombre)
            limpio, cambios = aplicar_correcciones(limpio, memoria, sha256_pdf(contenido))
            from aprendizaje_revisiones import aplicar_verificaciones_ocr
            limpio, verificaciones = aplicar_verificaciones_ocr(limpio, memoria, sha256_pdf(contenido))
            cambios += verificaciones
            # Las correcciones aprobadas no convierten automáticamente un PDF
            # incompleto en CONFORME: queda traza del revisor y de la fuente.
            filas_limpias.append(limpio)
            if nombre.lower().endswith(".pdf"):
                columnas_traza = {
                    "__archivo_origen": "archivo",
                    "periodo_remuneracion": "periodo_remuneracion",
                    "trabajador_nombre": "trabajador",
                    "trabajador_rut": "rut_trabajador",
                    "__hoja_origen": "paginas_de_respaldo",
                    "__conflicto_pdf": "advertencias_de_extraccion",
                    "__ocr_origen": "tipo_extraccion",
                    "__ocr_verificacion_humana": "verificado_por",
                    "__ocr_conciliacion": "calidad_lectura_ocr",
                    "__ocr_observaciones_informativas": "lecturas_reconciliadas_automaticamente",
                }
                for columna in columnas_traza:
                    if columna not in limpio.columns:
                        limpio[columna] = ""
                trazas.append(limpio[list(columnas_traza)].rename(columns=columnas_traza).copy())
            campos_ausentes = str(limpio["__campos_ausentes"].iloc[0]) if len(limpio) else ""
            incidencia_global = ' | '.join(
                str(x) for x in limpio.get('__ocr_incidencias_documento', pd.Series(dtype=str)).dropna().unique()
                if str(x).strip()
            )
            advertencias_archivo = ' '.join(
                str(x) for x in limpio.get('__conflicto_pdf', pd.Series(dtype=str)).dropna().unique()
            )
            ocr_total = int(limpio.get('__ocr_origen', pd.Series(dtype=str)).eq('ESCANEADO').sum())
            ocr_conciliados = int(limpio.get('__ocr_conciliacion',pd.Series(dtype=str)).eq('OCR_CONCILIADO').sum())
            diagnostico_archivo = (
                'OCR_CONCILIADO_AUTOMATICAMENTE'
                if ocr_total and ocr_total == ocr_conciliados and not incidencia_global
                else 'CERTIFICADO_PARCIAL_SIN_DIAS'
                if 'Certificado individual PREVIRED' in advertencias_archivo
                else 'OCR_REQUIERE_COTEJO'
                if limpio['__ocr_origen'].eq('ESCANEADO').any() or any(x in advertencias_archivo for x in ('Se aplicó OCR', 'Verificar extracción OCR', 'OCR escaneado'))
                else 'EXTRACCION_CON_OBSERVACIONES'
                if advertencias_archivo.strip()
                else 'EXTRACCION_SIN_ALERTAS_TECNICAS'
            )
            log.append({
                "archivo": nombre,
                "resultado": "PROCESADO",
                "control_calidad": diagnostico_archivo,
                "comprobantes_con_pago_posterior": int(
                    pd.to_numeric(limpio.get('__comprobantes_regularizados',pd.Series(dtype=float)),errors='coerce').fillna(0).max()
                ) if '__comprobantes_regularizados' in limpio.columns else 0,
                "filas_detectadas": len(limpio),
                "campos_no_detectados": campos_ausentes,
                "incidencias_ocr_sin_rut": incidencia_global,
                "registros_ocr_verificados": int(limpio['__ocr_verificacion_humana'].astype(str).str.len().gt(0).sum()),
                "registros_ocr_conciliados": ocr_conciliados,
                "registros_con_lecturas_ocr_resueltas": int(limpio.get('__ocr_observaciones_informativas',pd.Series(dtype=str)).astype(str).str.strip().ne('').sum()),
                "casos_sin_registro_seguro_social": int(
                    (limpio.get("__cobertura_seguro_social", pd.Series(dtype=str)) == "SIN_REGISTRO_SS").sum()
                ),
                "detalle": "Archivo leído. " + (
                    "; ".join(cambios) if cambios
                    else "Consultar Matriz_Auditoria y Control_Seguro_Social; Detalle_Revision muestra solo incidencias."
                ),
            })
        except Exception as exc:
            log.append({
                "archivo": nombre,
                "resultado": "ERROR",
                "control_calidad": "PENDIENTE_EXTRACCION",
                "filas_detectadas": 0,
                "campos_no_detectados": "",
                "incidencias_ocr_sin_rut": "",
                "registros_ocr_verificados": 0,
                "casos_sin_registro_seguro_social": 0,
                "detalle": str(exc),
            })
        finally:
            if progreso is not None:
                # La barra no altera resultados ni crea archivos; errores de UI
                # no interrumpen el procesamiento ya efectuado.
                try:
                    progreso(numero, len(archivos), nombre)
                except Exception:
                    pass

    if not filas_limpias and solo_independientes and len(solo_independientes)==len(archivos):
        from diagnostico_ocr import construir_diagnostico
        vacia=pd.DataFrame(columns=COLUMNAS_SALIDA)
        log_df=pd.DataFrame(log)
        control=construir_control(archivos, vacia, evidencia_independiente)
        reporte=exportar_excel(vacia,pd.DataFrame(),log_df,
            control_independientes=control,diagnostico_ocr=construir_diagnostico(pd.DataFrame(),log_df))
        vacia.attrs['control_independientes']=control
        return vacia,reporte,pd.DataFrame(),log_df
    if not filas_limpias:
        detalle_errores = " | ".join(f"{x['archivo']}: {x['detalle']}" for x in log if x["resultado"] == "ERROR")
        raise ValueError("No se pudo procesar ningún archivo válido. " + detalle_errores)

    unificado = pd.concat(filas_limpias, ignore_index=True, sort=False)
    matriz, detalle = aplicar_motor_auditoria(
        unificado,
        topes_por_periodo=topes,
        tolerancia_clp=tolerancia_clp,
        incluir_mutual=incluir_mutual,
    )
    log_df = pd.DataFrame(log)
    # Período esperado nunca sobrescribe el declarado en el PDF.
    if periodo_esperado:
        if not re.fullmatch(r"20\d{2}-(?:0[1-9]|1[0-2])", periodo_esperado):
            raise ValueError("El período esperado debe ser YYYY-MM.")
        for _, r in matriz.iterrows():
            if str(r['periodo_remuneracion']) != periodo_esperado:
                _incidencia_adicional(matriz, detalle, str(r['trabajador_rut']),
                    f"Período declarado {r['periodo_remuneracion']} difiere del esperado {periodo_esperado}")
    comparacion = None
    if asistencia is not None:
        from control_asistencia import leer_asistencia, comparar_asistencia
        datos_asistencia=leer_asistencia(*asistencia)
        comparacion=comparar_asistencia(matriz, datos_asistencia)
        for _,r in comparacion.iterrows():
            if r['resultado_comparacion']!='COINCIDE' and pd.notna(r.get('trabajador_rut')):
                _incidencia_adicional(matriz,detalle,str(r['trabajador_rut']),
                    f"Control asistencia: {r['resultado_comparacion']} (días en obra y remunerados pueden diferir justificadamente)")
    trazabilidad = pd.concat(trazas, ignore_index=True) if trazas else None
    control_ss = None
    if "__cobertura_seguro_social" in unificado.columns:
        # La cotización al Seguro Social tiene excepciones legales. La ausencia
        # de un RUT en esta sección no debe generar descuadres ficticios entre
        # las bases de AFP, Salud y Mutual, ni implica exención automática.
        columnas_ss = [
            "periodo_remuneracion", "contratista_nombre", "trabajador_nombre",
            "trabajador_rut", "renta_imponible_afp", "__renta_seguro_social",
            "__cobertura_seguro_social", "__archivo_origen", "__hoja_origen"
        ]
        control_ss = unificado.reindex(columns=columnas_ss).copy()
        control_ss["__cobertura_seguro_social"] = control_ss["__cobertura_seguro_social"].fillna("NO_INFORMADO")
        def diagnostico_ss(row):
            situacion = row["__cobertura_seguro_social"]
            if situacion == "SIN_REGISTRO_SS":
                return "VERIFICAR SI CORRESPONDE COTIZAR / ACREDITAR EXENCIÓN"
            if situacion == "SIN_COMPROBANTE_SS":
                return "NO SE ADJUNTÓ COMPROBANTE DE SEGURO SOCIAL"
            bruto = row["__renta_seguro_social"]
            if pd.isna(bruto):
                return "BASE DE SEGURO SOCIAL NO DISPONIBLE"
            if int(bruto) != int(row["renta_imponible_afp"]):
                return "BASE DE SEGURO SOCIAL DIFERENTE DE AFP: REVISAR"
            return "BASE COINCIDENTE EN LOS RUT INFORMADOS"
        control_ss["resultado_control_ss"] = control_ss.apply(diagnostico_ss, axis=1)
        control_ss = control_ss.rename(columns={
            "__renta_seguro_social": "renta_declarada_seguro_social",
            "__cobertura_seguro_social": "cobertura_seguro_social",
            "__archivo_origen": "archivo_origen",
            "__hoja_origen": "paginas_fuente",
        })
    from diagnostico_ocr import construir_diagnostico
    control_independientes = construir_control(archivos, matriz, evidencia_independiente)
    diagnostico_ocr = construir_diagnostico(detalle, log_df)
    excel = exportar_excel(matriz, detalle, log_df,
        trazabilidad_pdf=trazabilidad,
        comparacion_asistencia=comparacion,
        control_seguro_social=control_ss,
        diagnostico_ocr=diagnostico_ocr,
        control_independientes=control_independientes)
    matriz.attrs['control_independientes']=control_independientes
    matriz.attrs['control_seguro_social']=control_ss
    return matriz, excel, detalle, log_df


def plantilla_topes_csv() -> bytes:
    """Plantilla vacía. El usuario debe completar topes oficiales por período."""
    return b"periodo_remuneracion,tope_imponible_clp\n"
