# Django
from django.db import models

# Libs
from app.models.base_model import BaseModel


class TrendingTag(BaseModel):
    """
    Tendencias precomputadas para el autocomplete del buscador.

    Una fila por NOMBRE de tag (no por hilo) con su score = suma del
    momentum_score de los hilos activos que lo llevan. La REESCRIBE el
    cron `recompute_momentum` cada corrida (tabla pequeña, ~cientos de
    filas). El endpoint /search/suggest/ solo LEE de aquí — input vacío
    → top-N por score; al escribir → prefix sobre name_norm ordenado por
    score. Cero cálculo de tendencias por request.
    """

    # Display ("dns") y clave de prefijo (lowercase, sin acentos) — espejo
    # de Tag.name / Tag.name_norm para que sugerencia = búsqueda.
    name = models.CharField(max_length=250)
    name_norm = models.CharField(max_length=250, db_index=True)

    # Suma del momentum de los hilos con este tag. Indexado DESC: el top-N
    # y el prefijo ordenado por tendencia recorren el índice sin sort.
    score = models.FloatField(default=0, db_index=True)

    # Nº de hilos con el tag (lo muestra el dropdown como contexto).
    thread_count = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(fields=["-score"], name="trendingtag_score_desc_idx"),
        ]

    def __str__(self) -> str:
        return f"#{self.name} ({self.score:.2f})"
