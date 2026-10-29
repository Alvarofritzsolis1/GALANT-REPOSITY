"""Pruebas sin información de trabajadores reales, aptas para GitHub público."""
from PIL import Image, ImageDraw
from previred_scan_fast import _preparar


def test_detectar_reticula_escaneada_afp():
    """El layout de tablas se identifica ANTES de OCR, aunque sean imágenes."""
    im=Image.new('L',(1516,1072),255)
    d=ImageDraw.Draw(im)
    xs=[94,166,454,540,612,684,756,828,901,973,1059,1131,1203,1275,1347,1419]
    for y in (175,193,219,232,245,258):
        d.line((94,y,1419,y),fill=0,width=2)
    for x in xs:
        d.line((x,175,x,258),fill=0,width=2)
    perfil=_preparar(im,4)
    assert perfil is not None
    assert perfil.tipo=='afp'
    assert perfil.numero_pagina==4
    assert len(perfil.filas_y)>=2


def test_documentos_no_tabulares_no_se_inventan():
    im=Image.new('L',(1516,1072),255)
    assert _preparar(im,1) is None
