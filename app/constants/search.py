# Límites de búsqueda — fuente ÚNICA (FE replica el 100 como cortesía,
# el BE lo valida como defensa real contra DoS por cómputo).

# Tope DURO de longitud del query. 100 sobra para cualquier búsqueda
# legítima; se valida ANTES de normalizar o tocar la BD, y nunca se hace
# eco del query en el error (privacidad / no filtrar payload).
MAX_QUERY_LENGTH = 100

# Mínimo de caracteres para que el autocomplete consulte (debajo → vacío,
# sin tocar la BD).
SUGGEST_MIN_CHARS = 2

# Search tab identifiers (the `type` query param) — also the keys of the
# `context.counts` dict the frontend tabs consume.
POSTS = "posts"
TAGS = "tags"
USERS = "users"
MEDIA = "media"
VALID_TYPES = (POSTS, TAGS, USERS, MEDIA)

# Days of the per-tag activity time series (frontend sparkline).
ACTIVITY_DAYS = 14

# Autocomplete suggestion caps (short lists, Google-style).
SUGGEST_TAGS_LIMIT = 5       # tags while typing (prefix, by trend score)
SUGGEST_THREADS_LIMIT = 4    # threads while typing (content, by momentum)
SUGGEST_TRENDING_LIMIT = 8   # trending tags for the empty input
SUGGEST_SNIPPET_RADIUS = 30  # characters on each side of the match
