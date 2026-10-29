"""Pruebas sintéticas: certificados escaneados F30-1, sin datos de personas reales."""
import shutil
import fitz
import pytest
from f301 import _extract_nomina_columnas, _extraer_cabecera_ocr_simple, pagos_remuneraciones, rut_normalizado


def valid_rut(n):
    t=str(n)
    v=11-sum(int(d)*(2+i%6) for i,d in enumerate(reversed(t)))%11
    return t+('0' if v==11 else 'K' if v==10 else str(v))


def pretty(r):
    return f'{r[:-1][:-6]}.{r[:-1][-6:-3]}.{r[:-1][-3:]}-{r[-1]}'


def synthetic_scan():
    doc=fitz.open()
    page=doc.new_page(width=612,height=792)
    page.insert_text((100,210),'Detalle por Periodo',fontsize=13)
    page.insert_text((108,250),'Septiembre 2026',fontsize=10)
    ys=[235,252,269,286,303,320,337,354,371]
    for y in ys:page.draw_line((100,y),(511,y),color=(0,0,0),width=.5)
    for x in [100,138,510]:page.draw_line((x,235),(x,371),color=(0,0,0),width=.5)
    nombres=['Persona de Prueba Uno','Persona de Prueba Dos','Persona de Prueba Tres',
             'Persona de Prueba Cuatro','Persona de Prueba Cinco']
    ruts=[valid_rut(n) for n in (12345678,87654321,10456987,19234987,15678345)]
    for i,(r,nombre) in enumerate(zip(ruts,nombres)):
        y=281+17*i
        page.insert_text((145,y),pretty(r),fontsize=9)
        page.insert_text((236,y),nombre,fontsize=9)
    raster=page.get_pixmap(matrix=fitz.Matrix(2,2),alpha=False)
    scan=fitz.open()
    rendered=scan.new_page(width=612,height=792)
    rendered.insert_image(rendered.rect,stream=raster.tobytes('png'))
    return scan,ruts


@pytest.mark.skipif(not shutil.which('tesseract'),reason='Tesseract no instalado')
def test_ocr_tabla_separada_recupera_cinco():
    d,ruts=synthetic_scan()
    try:
        items=_extract_nomina_columnas(d[0],'2026-09','2000/2026/11111111','sintetico.pdf',1,5)
        assert len(items)==5, items
        assert {x['trabajador_rut'] for x in items}==set(ruts)
        assert len({x['trabajador_nombre'] for x in items})==5
    finally:d.close()


def test_empresa_escaneada_columnas_separadas():
    r=valid_rut(76123456)
    texto=f'''1.1 - Antecedentes Empleador (solicitante)
Nombre o Razin Social
EMPRESA DE PRUEBA SPA
Representante Legal
Persona de Prueba
RUT
{pretty(r)}
RUT Representante Legal
12.345.678-5
1.2 - Empresa Principal'''
    empresa,rut=_extraer_cabecera_ocr_simple(texto)
    assert (empresa,rut)==('EMPRESA DE PRUEBA SPA',r)


def test_remuneraciones_pendientes_no_inventadas():
    assert pagos_remuneraciones('Remuneraciones ilegibles')==(None,None)
    assert pagos_remuneraciones('Monto Informado\n5 0 $2.029.693')==(5,0)

def test_pdf_escaneado_sin_nombre_f30_respetar_seccion_de_carga():
    from clasificador_documentos import enrutar_archivos
    d=fitz.open()
    p=d.new_page()
    # No hay texto seleccionable: el usuario lo subió al apartado F30-1.
    data=d.tobytes();d.close()
    cot,docs,avisos,rechazados=enrutar_archivos([],[ (data,'archivo_escan.pdf') ])
    assert not cot and len(docs)==1 and not rechazados
    assert any('clasificación provisional' in x.lower() for x in avisos)
