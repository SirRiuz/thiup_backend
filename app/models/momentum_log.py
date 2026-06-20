# Django
from django.db import models

# Libs
from app.models.base_model import BaseModel


class MomentumLog(BaseModel):
    """
    Log of each run of the momentum recount (For You).

    Written only by the management command `recompute_momentum` (run by the
    external scheduler) — the admin exposes it read-only, with no creation or
    editing (see MomentumLogAdmin). Used to audit that the job runs every
    10 min, how long it takes and whether it failed.
    """

    window_days = models.PositiveIntegerField(
        default=0,
        help_text="Ventana activa usada en la corrida (--days)."
    )

    processed_count = models.PositiveIntegerField(
        default=0,
        help_text="Posts raíz dentro de la ventana evaluados."
    )

    updated_count = models.PositiveIntegerField(
        default=0,
        help_text="Posts cuyos score/contadores cambiaron (bulk_update)."
    )

    duration_ms = models.PositiveIntegerField(
        default=0,
        help_text="Duración total de la corrida en milisegundos."
    )

    was_successful = models.BooleanField(
        default=True,
        help_text="False si la corrida lanzó una excepción."
    )

    error = models.TextField(
        blank=True,
        default="",
        help_text="Mensaje de la excepción cuando was_successful es False."
    )

    def __str__(self) -> (str):
        status = "ok" if self.was_successful else "ERROR"
        return (
            f"{self.create_at:%Y-%m-%d %H:%M} · {status} · "
            f"{self.processed_count} procesados / {self.updated_count} actualizados"
        )
