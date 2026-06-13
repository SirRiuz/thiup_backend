# Contributing

## Code comments must be written in English

All code comments — `#` line comments and docstrings (`"""..."""`) — **must
be written in English**. This keeps the codebase consistent and readable for
every contributor.

> **Scope:** this rule applies to **code comments and docstrings only**. It
> does **not** apply to:
> - User-facing strings / CLI `help=` text, log messages, and validation
>   messages — those are string literals, not comments.
> - Seed/dummy content and data (e.g. Spanish thread bodies in
>   `add_dummy_threads.py`, place names like `Girón`) — that is **content**.

### Heuristic check

A standard-library-only script (uses `tokenize` + `ast`) flags comments that
look like Spanish:

```bash
python scripts/check_comment_language.py            # scan app/, core/, honeypot/
python scripts/check_comment_language.py <file.py>  # scan specific files
```

Because it parses with `tokenize`/`ast`, it inspects **only** `#` comments
and module/class/function docstrings — never arbitrary string literals (so
Spanish content, `help=` text and SQL are never flagged). It flags a comment
when it contains `¿`, `¡`, `ñ`, or several Spanish function words (`el`,
`la`, `que`, `para`, `según`, ...).

**This is a heuristic, not a guarantee.** It reliably catches obvious
Spanish but can miss short or accent-free Spanish, and may occasionally flag
a false positive (e.g. a Spanish proper noun in a comment). It is a tripwire
to keep new Spanish comments out — not a perfect language detector. If you
hit a false positive, rephrase the comment in English.

### Enable the pre-commit hook

The check runs automatically on staged files via a shared git hook. Enable
it once per clone (no extra dependencies required):

```bash
git config core.hooksPath .githooks
```

The hook (`.githooks/pre-commit`) runs the script against staged `.py` files
and blocks the commit if a likely-Spanish comment is found.
