# Libs
from app.models.momentum_log import MomentumLog


class SystemMetrics(MomentumLog):
    """
    Admin-only PROXY model: exists solely so the metrics dashboard gets a
    natural entry in the admin index. No table and no fields of its own — the
    ModelAdmin fully overrides the changelist to render the dashboard (see
    SystemMetricsAdmin), so the parent model is never actually listed.
    """

    class Meta:
        proxy = True
        verbose_name = "System metrics"
        verbose_name_plural = "System metrics"
