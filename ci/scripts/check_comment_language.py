#!/usr/bin/env python3
"""Heuristic guard: flag code COMMENTS written in Spanish.

Convention: all code comments must be in English (see CONTRIBUTING.md).

This is a HEURISTIC, not a guarantee. It reliably catches obvious Spanish
(¿ ¡ ñ, or comments containing several Spanish function words such as
"el", "la", "que", "para", "según"...). It will NOT catch short Spanish
comments that use no function words, and may occasionally miss accent-free
Spanish. It is a tripwire to keep new Spanish comments out, not a perfect
language detector.

It ONLY inspects ``#`` comments and docstrings via the ``tokenize``/``ast``
modules — never arbitrary string literals (return values, ``help=`` text,
SQL, user-facing copy), so Spanish *content/data* is never flagged.

Usage:
    python scripts/check_comment_language.py [file.py ...]
    python scripts/check_comment_language.py        # scans app/, core/, honeypot/
Exit code 1 if any likely-Spanish comment is found.
"""

from __future__ import annotations

import ast
import io
import sys
import tokenize
import unicodedata
from pathlib import Path

# Spanish function words (accent-stripped, lowercase). A comment needs at
# least MIN_HITS distinct matches to be flagged.
SPANISH_WORDS = {
    "el",
    "la",
    "los",
    "las",
    "un",
    "una",
    "unos",
    "unas",
    "del",
    "al",
    "que",
    "con",
    "sin",
    "por",
    "para",
    "pero",
    "como",
    "mas",
    "segun",
    "este",
    "esta",
    "esto",
    "estos",
    "estas",
    "ese",
    "esa",
    "eso",
    "esos",
    "esas",
    "cada",
    "donde",
    "cuando",
    "porque",
    "tambien",
    "solo",
    "hace",
    "hacer",
    "son",
    "estan",
    "ser",
    "estar",
    "hay",
    "asi",
    "aqui",
    "alli",
    "sus",
    "mismo",
    "misma",
    "muy",
    "entre",
    "sobre",
    "hasta",
    "desde",
    "cual",
    "cuales",
    "cuantos",
    "funcion",
    "aunque",
    "cuyo",
    "siguiente",
    "tiene",
    "debe",
    "puede",
    "usuario",
    "pantalla",
    "campo",
    "hilo",
    "hilos",
    "cuenta",
    "encola",
    "ejecuta",
    "apto",
}
MIN_HITS = 2
STRONG = set("ñÑ¿¡")

SCAN_DIRS = ("app", "core", "honeypot")
# Directories to skip entirely. Empty by default — migrations are scanned too,
# since their comments must also be in English.
SKIP_PARTS: set = set()


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def is_spanish(text: str) -> bool:
    if any(ch in STRONG for ch in text):
        return True
    words = "".join(c if c.isalpha() else " " for c in _strip_accents(text).lower()).split()
    return len({w for w in words if w in SPANISH_WORDS}) >= MIN_HITS


def comments_and_docstrings(path: Path):
    """Yield (lineno, text) for every # comment and docstring in a .py file."""
    src = path.read_text(encoding="utf-8")

    # 1. Hash comments via tokenize.
    try:
        tokens = tokenize.generate_tokens(io.StringIO(src).readline)
        for tok in tokens:
            if tok.type == tokenize.COMMENT:
                yield tok.start[0], tok.string.lstrip("#").strip()
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass

    # 2. Docstrings via ast (module/class/function only — never plain strings).
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if isinstance(
            node,
            (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
        ):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                lineno = getattr(node, "lineno", 1)
                yield lineno, doc


def collect_files(args):
    if args:
        return [Path(a) for a in args if a.endswith(".py") and Path(a).exists()]
    files = []
    for d in SCAN_DIRS:
        base = Path(d)
        if base.is_dir():
            files.extend(base.rglob("*.py"))
    return files


def main() -> int:
    files = collect_files(sys.argv[1:])
    findings = []
    for path in files:
        if SKIP_PARTS & set(path.parts):
            continue
        try:
            for lineno, text in comments_and_docstrings(path):
                if is_spanish(text):
                    snippet = " ".join(text.split())[:80]
                    findings.append((path, lineno, snippet))
        except (OSError, UnicodeDecodeError):
            continue

    if findings:
        sys.stderr.write("\n✖ Likely Spanish comment(s) found. Comments must be in English.\n\n")
        for path, lineno, snippet in findings:
            sys.stderr.write(f"  {path}:{lineno}  # {snippet}\n")
        sys.stderr.write(
            f"\n{len(findings)} comment(s) flagged. This check is heuristic — "
            "if a flag is a false positive (e.g. a proper noun), rephrase the "
            "comment in English. See CONTRIBUTING.md.\n\n"
        )
        return 1

    sys.stdout.write("✓ No Spanish comments detected.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
