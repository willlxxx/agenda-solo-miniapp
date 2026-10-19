"""Capa de servicios: reglas de escritura que comparten varios routers.

Aquí vive lo que necesita base de datos y no pertenece a una URL concreta. Los `domain/` siguen
sendo lógica pura sin sesión; los routers solo validan la petición y traducen estas excepciones
a códigos HTTP.
"""
