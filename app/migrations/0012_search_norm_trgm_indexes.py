# Búsqueda indexable — diagnóstico medido con EXPLAIN (ANALYZE, BUFFERS):
#
#   * El índice GIN de 0011 (sobre `text` crudo) NUNCA se usó
#     (pg_stat_user_indexes.idx_scan = 0): la búsqueda matchea con
#     `text__unaccent__icontains`, que genera UPPER(UNACCENT(text)) LIKE …,
#     y el planner no puede casar esa expresión con un índice sobre la
#     columna sin funciones. Resultado: Seq Scan de toda la tabla en cada
#     COUNT (~41 ms con 55k hilos) y en cada búsqueda de término raro
#     (~74 ms) — por request.
#
# Fix (misma convención que Tag.name_norm y Thread.geohash4 — columna
# derivada + normalización en Python con strip_accents, idéntica en
# escritura y en query):
#
#   1. Thread.text_norm = strip_accents(text).lower(), derivado SIEMPRE en
#      save(); backfill aquí para el contenido existente.
#   2. GIN pg_trgm sobre text_norm → `text_norm__contains` (LIKE '%…%')
#      usa el índice. Se elimina el índice muerto de 0011 (solo costaba
#      en cada INSERT/UPDATE sin servir ninguna query).
#   3. GIN pg_trgm sobre Tag.name_norm → la pestaña de tags deja de
#      escanear las 24k filas por request (el btree solo cubre prefijos).
#
# El backfill usa la MISMA función Python (strip_accents) que el save() y
# que la normalización del query en la vista: ambos lados del LIKE quedan
# normalizados por el mismo código — sin depender de que unaccent() de
# Postgres coincida con la normalización NFKD de Python.

from django.contrib.postgres.indexes import GinIndex
from django.db import migrations, models

from app.utils.text import strip_accents

BATCH = 2000


def backfill_text_norm(apps, schema_editor):
    Thread = apps.get_model("app", "Thread")
    threads = []
    for thread in Thread.objects.only("id", "text").iterator(chunk_size=BATCH):
        thread.text_norm = strip_accents(thread.text or "").lower()
        threads.append(thread)
        if len(threads) >= BATCH:
            Thread.objects.bulk_update(threads, ["text_norm"], batch_size=BATCH)
            threads = []
    if threads:
        Thread.objects.bulk_update(threads, ["text_norm"], batch_size=BATCH)


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0011_thread_text_trigram_idx'),
    ]

    operations = [
        migrations.AddField(
            model_name='thread',
            name='text_norm',
            field=models.TextField(
                blank=True,
                default='',
                editable=False,
                help_text='Derived: lowercase, accent-stripped text (search index).',
            ),
        ),
        # Backfill ANTES de crear el índice: construir el GIN una vez sobre
        # los datos ya pobrados es más barato (Raspberry) que mantenerlo
        # fila a fila durante el backfill.
        migrations.RunPython(backfill_text_norm, migrations.RunPython.noop),
        migrations.RemoveIndex(
            model_name='thread',
            name='thread_text_trgm_idx',
        ),
        migrations.AddIndex(
            model_name='thread',
            index=GinIndex(
                name='thread_text_norm_trgm_idx',
                fields=['text_norm'],
                opclasses=['gin_trgm_ops'],
            ),
        ),
        migrations.AddIndex(
            model_name='tag',
            index=GinIndex(
                name='tag_name_norm_trgm_idx',
                fields=['name_norm'],
                opclasses=['gin_trgm_ops'],
            ),
        ),
    ]
