# Django
from django.db import models
from django.contrib.postgres.indexes import GinIndex

# Libs
from app.models.thread import Thread
from app.models.base_model import BaseModel


class Tag(BaseModel):

    thread = models.ForeignKey(to=Thread, on_delete=models.CASCADE)
    # db_index: tag mode filters by name (exact match) and search groups by
    # name — the index speeds up both.
    name = models.CharField(max_length=250, db_index=True)
    # Normalized name (lowercase, no accents) for autocomplete:
    # name_norm__startswith uses Postgres's btree/LIKE index (Django creates
    # the varchar_pattern_ops automatically on an indexed CharField) — an
    # accent-insensitive prefix without functions (unaccent()) that would break the index.
    name_norm = models.CharField(max_length=250, db_index=True, default="")

    class Meta:
        indexes = [
            # GIN trigram sobre name_norm → la pestaña de tags de la
            # búsqueda (name_norm__contains, infijo) usa índice. El btree
            # de arriba solo cubre prefijos (autocomplete); el icontains
            # sobre `name` hacía Seq Scan de toda la tabla en cada request
            # (medido: ~7 ms × 2-3 ejecuciones por búsqueda).
            GinIndex(
                name="tag_name_norm_trgm_idx",
                fields=["name_norm"],
                opclasses=["gin_trgm_ops"],
            ),
        ]

    def __str__(self) -> (str):
        return f"#{self.name}"
