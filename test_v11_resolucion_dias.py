"""Pruebas sintéticas: jamás reemplazar días SS sin evidencia independiente."""
import numpy as np
from previred_scan_fast import Plantilla, _reconciliar_dias_ss


def test_resolver_dias_ocr_por_relectura_o_dos_anclas(monkeypatch):
    import previred_scan_fast as m
    # Matriz artificial de huellas: 6 y 8 deben ser visualmente distintos.
    seis = np.zeros((35,25),dtype=bool); seis[8:23,8:19]=1
    ocho = np.zeros((35,25),dtype=bool); ocho[3:31,3:23]=1
    once = np.zeros((35,25),dtype=bool); once[10:20,3:20]=1
    formas={0:seis,1:seis,2:seis,3:seis,4:ocho,5:once}
    monkeypatch.setattr(m,'_firma_celda_dias',lambda p,f:formas[f])
    monkeypatch.setattr(m,'_ocr_num_con_escala',lambda p,c,f,escala:11 if f==5 and escala in (4.0,5.0) else None)
    datos={}
    for n,dia_salud,dia_ss in ((0,6,6),(1,6,8),(2,6,6),(3,6,6),(4,8,8),(5,11,1)):
        datos[str(n)]={'salud':[(6,n,100000,dia_salud)],'seguro_social':[(10,n,100000,dia_ss)]}
    falso=Plantilla('seguro_social',10,None,[],[])
    cambios=_reconciliar_dias_ss(datos,[falso])
    assert datos['1']['seguro_social'][0][3]==6
    assert datos['5']['seguro_social'][0][3]==11
    assert datos['4']['seguro_social'][0][3]==8
    assert set(cambios)=={'1','5'}


def test_no_alterar_dias_ss_con_multiples_movimientos(monkeypatch):
    import previred_scan_fast as m
    monkeypatch.setattr(m,'_ocr_num_con_escala',lambda *args:6)
    datos={'a':{'salud':[(6,0,100000,6)],'seguro_social':[(10,0,0,0),(10,1,100000,3)]}}
    p=Plantilla('seguro_social',10,None,[],[])
    cambios=_reconciliar_dias_ss(datos,[p])
    assert cambios=={}
    assert [r[3] for r in datos['a']['seguro_social']]==[0,3]
