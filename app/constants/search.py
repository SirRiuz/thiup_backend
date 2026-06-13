# Límites de búsqueda — fuente ÚNICA (FE replica el 100 como cortesía,
# el BE lo valida como defensa real contra DoS por cómputo).

# Tope DURO de longitud del query. 100 sobra para cualquier búsqueda
# legítima; se valida ANTES de normalizar o tocar la BD, y nunca se hace
# eco del query en el error (privacidad / no filtrar payload).
MAX_QUERY_LENGTH = 100

# Mínimo de caracteres para que el autocomplete consulte (debajo → vacío,
# sin tocar la BD).
SUGGEST_MIN_CHARS = 2
