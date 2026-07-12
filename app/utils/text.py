# Python
import unicodedata


def strip_accents(text) -> str:
    """Removes accents/diacritics from a text (accented letters fold to their ASCII base).

    Used to normalize the search QUERY on the Python side: Django's
    __unaccent lookup only applies unaccent() to the DB FIELD, not to the
    query value. By normalizing both sides, an accented or unaccented search
    term (in any case, together with icontains) returns the same results.
    """
    return "".join(c for c in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(c))
