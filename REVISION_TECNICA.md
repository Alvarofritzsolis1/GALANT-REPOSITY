# Revisión técnica V12 — F30-1 escaneado e interfaz

## Defecto reproducido

Caso de regresión: F30-1 escaneado de un empleador, con cinco trabajadores. Los PDF y datos identificadores originales no se incluyen en el repositorio.

**V11:** solo detectaba 2/5 RUT, RUT empresa vacío y generaba `REVISAR_EXTRACCION`. La causa fue lectura de la tabla completa con OCR general, donde los caracteres de la columna RUT y de nombres quedaban mezclados por las líneas de la tabla. También exigía nombre de empresa y RUT en la misma línea.

**V12:** segmenta la tabla geométricamente, recorta de forma independiente columnas de RUT y de nombres, ejecuta una lectura focalizada (OCR) y verifica 5 RUT mediante módulo 11. Recupera el RUT empresarial desde su campo específico, sin confundir al representante.

| Control | Resultado de prueba V12 |
|---|---|
| Empresa | Ejemplo de empresa (omitido) |
| RUT empresa normalizado | Omitido |
| Certificado | Omitido |
| Período de remuneración | 2026-09 |
| Cotizaciones declaradas | Pagadas: Sí |
| Remuneraciones pagadas / no pagadas | 5 / 0 |
| Monto informado | $2.029.693 |
| Trabajadores vigentes y RUT en nómina | 5 / 5 |
| Control documental de extracción | PAGO_DECLARADO_SI |
| Autenticidad frente al portal DT | PENDIENTE_PORTAL_DT |

**Precaución:** “PAGO_DECLARADO_SI” no equivale a autenticidad comprobada ante la DT ni certificación legal de todas las obligaciones.

## Pruebas de regresión

Se ejecutan pruebas funcionales con PDFs sintéticos y se preservan los módulos de F30-1, cotizaciones, control de independientes, OCR y revisión de datos anteriores.

Pruebas de muestreo local con archivos reales (no incluidos en el ZIP):

- Manuel Sánchez Garrido (digital): 2/2 RUT.
- MAQMAX (escaneado): 7/7 RUT.
- Buffalo (digital): 8/8 RUT.
- ER Construcciones (certificado incompleto): mantiene alerta por resumen ausente; no se aprueba artificialmente.
- F&B (escaneado): 5/5 RUT, sin observaciones de extracción.

## Límites

- El OCR de tablas no se garantiza para todos los formatos. Una estructura desconocida o un RUT sin módulo 11 válido se mantiene pendiente.
- Los nombres pueden necesitar confirmación visual si el escaneo es borroso; la coincidencia legal se basa en RUT.
- La interfaz no valida certificados contra una API oficial de la DT y no debe mostrar autenticidad confirmada por el simple hecho de leer un código.
- La prueba visual del frontend dentro de Streamlit Community Cloud requiere completar el despliegue.


## V13 — Error crítico de conciliación de folios
El extractor V12 solo reconocía algunas portadas: en documentos con cotizaciones agrupadas una portada de $129.162 se comparaba con el detalle de cinco RUT y $2.029.693. Además no reconocía el anexo ACHS con el título «declaración y no pago», aunque exhibía fecha de pago posterior. V13 corrige ambos casos y hace comparación por folio.
