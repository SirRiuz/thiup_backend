# Índice GIN trigram sobre Thread.text para el autocomplete de CONTENIDO
# del buscador: `text__icontains` (ILIKE '%...%') usa este índice en vez
# de escanear toda la tabla — barato en la Raspberry. El buscador
# principal (text__unaccent__icontains) es MÁS permisivo, así que todo lo
# que sugiere el suggest (icontains) la búsqueda también lo encuentra.

from django.contrib.postgres.operations import TrigramExtension
from django.contrib.postgres.indexes import GinIndex
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0010_trendingtag'),
    ]

    operations = [
        TrigramExtension(),
        migrations.AddIndex(
            model_name='thread',
            index=GinIndex(
                name='thread_text_trgm_idx',
                fields=['text'],
                opclasses=['gin_trgm_ops'],
            ),
        ),
    ]
