@@
-    if not filas_limpias and solo_independientes and len(solo_independientes)==len(archivos):
-        from diagnostico_ocr import construir_diagnostico
-        vacia=pd.DataFrame(columns=COLUMNAS_SALIDA)
-        log_df=pd.DataFrame(log)
-        control=construir_control(archivos, vacia, evidencia_independiente)
-        reporte=exportar_excel(vacia,pd.DataFrame(),log_df,
-            control_independientes=control,diagnostico_ocr=construir_diagnostico(pd.DataFrame(),log_df))
-        vacia.attrs['control_independientes']=control
-        return vacia,reporte,pd.DataFrame(),log_df
-    if not filas_limpias:
-        detalle_errores = " | ".join(f"{x['archivo']}: {x['detalle']}" for x in log if x["resultado"] == "ERROR")
-        raise ValueError("No se pudo procesar ningún archivo válido. " + detalle_errores)
+    if not filas_limpias and solo_independientes and len(solo_independientes)==len(archivos):
+        from diagnostico_ocr import construir_diagnostico
+        vacia=pd.DataFrame(columns=COLUMNAS_SALIDA)
+        log_df=pd.DataFrame(log)
+        control=construir_control(archivos, vacia, evidencia_independiente)
+        reporte=exportar_excel(vacia,pd.DataFrame(),log_df,
+            control_independientes=control,diagnostico_ocr=construir_diagnostico(pd.DataFrame(),log_df))
+        vacia.attrs['control_independientes']=control
+        return vacia,reporte,pd.DataFrame(),log_df
+
+    # Si ningún archivo resultó legible, no se debe abortar la ejecución del
+    # proceso completo: en ese caso se genera un diagnóstico de archivo vacío,
+    # con el motivo exacto del OCR/PDF no validado, para que el usuario pueda
+    # revisar el documento y no pierda el análisis general del lote.
+    if not filas_limpias:
+        detalle_errores = " | ".join(f"{x['archivo']}: {x['detalle']}" for x in log if x["resultado"] == "ERROR")
+        vacia = pd.DataFrame(columns=COLUMNAS_SALIDA)
+        log_df = pd.DataFrame(log)
+        from diagnostico_ocr import construir_diagnostico
+        control_independientes = construir_control(archivos, vacia, evidencia_independiente)
+        reporte = exportar_excel(
+            vacia,
+            pd.DataFrame(),
+            log_df,
+            control_independientes=control_independientes,
+            diagnostico_ocr=construir_diagnostico(pd.DataFrame(), log_df),
+        )
+        vacia.attrs['control_independientes'] = control_independientes
+        vacia.attrs['error_procesamiento'] = detalle_errores or "No se pudo extraer ninguna fila válida de cotizaciones."
+        vacia.attrs['analisis'] = {
+            'estado': 'REVISAR_DATOS',
+            'motivo': detalle_errores or 'OCR/PDF no pudo verificar la estructura de la nómina.',
+            'cotizaciones_pagadas': None,
+        }
+        return vacia, reporte, pd.DataFrame(), log_df
***