"""Evaluación de concordancia de la clasificación asistida por IA (S8-E1).

Infraestructura de medición de CA-4 / RNF-05. Es adapter-agnostic: recibe el
adaptador por parámetro y nunca resuelve el proveedor de runtime, de modo que la
misma maquinaria sirve para el adaptador falso en CI y para el proveedor real en
un gate separado.

**Ejecutar esto con el adaptador falso no demuestra CA-4.** Valida que el dataset
carga, que los contratos encajan, que el parser se aplica y que las métricas se
calculan de forma reproducible. La capacidad del modelo solo puede medirse contra
el proveedor real (contrato S8-A, D19).
"""
