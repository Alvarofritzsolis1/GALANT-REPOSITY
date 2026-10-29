# Control Documental — Streamlit V13 (interfaz simple)

Aplicación para cargar **cotizaciones Previred** y **certificados F30-1** en secciones separadas, extraer datos y conciliar por RUT de empresa, período y trabajadores. La interfaz principal contiene dos cargas de archivos, un botón y resultados comprensibles. La página `Análisis avanzado` conserva los controles técnicos de la V11.

## Corregido en V13

- F30-1 escaneado F&B de septiembre de 2026 (archivo denominado octubre): la versión anterior recuperaba **2 de 5 RUT** y no identificaba bien al empleador. Ahora extrae **5 de 5**, confirma los RUT por módulo 11, identifica empresa, período, cantidad de remuneraciones y estado de pago **declarado**.
- El lector escaneado detecta líneas de tabla y aplica OCR **por columna de RUT y nombres**, sin pasar todo el certificado repetidamente. Solo se activa cuando la extracción inicial es incompleta.
- Admite RUT separados con `.` o `,` en texto OCR, con módulo 11 obligatorio.
- Recupera el RUT del empleador cuando aparece en línea distinta al nombre y separado de la sección del representante legal.
- La elección de `Certificados F30-1` orienta el enrutamiento de archivos escaneados de tipo desconocido; una clasificación digital concluyente tiene prioridad.
- `opencv-python-headless` se declara explícitamente en requisitos para que la lectura de tablas funcione en Streamlit Community Cloud.
- GitHub Actions instala Tesseract y ejecuta las pruebas automáticas, incluidas pruebas con documentos sintéticos escaneados.

## Cómo publicar en Streamlit Community Cloud

1. Descomprimir el ZIP y subir archivos **sin incluir PDF reales** a un repositorio privado de GitHub.
2. Seleccionar rama `main`, archivo principal **`app.py`** y Python **3.12**.
3. Permitir la instalación de `requirements.txt` y `packages.txt`.
4. Cargar PDF de F30-1 en la columna derecha; opcionalmente las cotizaciones del mismo RUT empleador y período en la izquierda; pulsar **Analizar documentos**.
5. Revisar resumen y, si aparecen, diferencias individuales. Descarga el Excel correspondiente.

### Para pruebas en computador

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
streamlit run app.py
```

En Linux necesitas Tesseract OCR instalado además de sus bibliotecas Python.

## Notas de interpretación

- **F30-1 no es una planilla Previred**. Contiene empresa, obra, período, número de certificado, declaración de cotizaciones pagadas, remuneraciones y, cuando se acompaña, nómina.
- **“Pagadas: Sí” es una declaración impresa**, no una consulta autenticada a la Dirección del Trabajo.
- El **mes del nombre del archivo** puede ser distinto al **período de remuneraciones** certificado; el sistema usa este último.
- **No se suman personas por coincidencia de nombres**; la clave de comparación es el RUT validado. Cuando falta nómina, no se inventan trabajadores.
- **Autenticidad DT:** el sitio [verificador.dirtrab.cl](https://verificador.dirtrab.cl/) requiere folio y clave; la aplicación brinda acceso, pero no afirma validación automática contra ese portal.
- La verificación de trabajadores en diferentes obras requiere revisar alcance; un RUT extra en la planilla de empresa no significa automáticamente incumplimiento.
- El alojamiento de Streamlit no garantiza que los archivos de sesión duren de manera permanente. Conserva los Excel generados y aplica medidas de privacidad a RUT y liquidaciones.


### V13 · Pagos parciales / múltiples folios (caso real F&B, septiembre 2026)
- Las portadas de AFP, Fonasa y Mutual se suman **por institución y folio**; un folio de $129.162 y 1 persona no se confronta con 5 trabajadores/$2.029.693.
- Reconoce detalles de Mutual encabezados «declaración y no pago» cuando registran posteriormente fecha de pago. No los descarta por el título.
- Valida también cada portada con su propio anexo, para detectar omisiones específicas de RUT o rentas.
- Separa cobertura de Seguro Social de cuadratura AFP/Salud/Mutual. Su ausencia puede requerir control legal específico; no demuestra incumplimiento ni exención.
- El comprobante puede contener declaraciones originales regularizadas: la fecha de pago se lee del documento pero no constituye verificación independiente de pago ante Previred.
