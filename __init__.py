# -*- coding: utf-8 -*-

def classFactory(iface):
    from .cad_to_vector_converter import CadToVectorConverter
    return CadToVectorConverter(iface)
