"""Text normalisation used for matching questions against names and terms.

Deliberately simple and deterministic: lower case, words of letters and
digits, snake_case split into words, and a light plural reduction ("orders" ->
"order", "categories" -> "category"). There is no stemming library, no
embedding and no fuzzy matching.
"""

import re
import unicodedata

_WORD_RE = re.compile(r"[a-z0-9]+")
_SPACE_RE = re.compile(r"\s+")

# Words that carry no meaning for schema matching.
STOPWORDS = frozenset(
    """a an and are as at be by did do does for from has have how i in is it its me my of on or our per show
    tell the their them this that to us was we were what when where which who whose why with give list get
    find display many much all any each every than then there these those over into about please can could
    would should will vs versus compare compared between top bottom last this next previous current total
    number count sum average avg mean month months week weeks day days year years quarter quarters today
    yesterday trend trends monthly weekly daily yearly quarterly annual by""".split()
)


def normalize_question(text: str) -> str:
    """Unicode NFKC, single spaces, trimmed. The meaning and casing are kept."""
    return _SPACE_RE.sub(" ", unicodedata.normalize("NFKC", text)).strip()


def singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("sses", "xes", "ches", "shes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def words(text: str | None) -> list[str]:
    """Lower-case words with snake_case split ("total_amount" -> "total", "amount")."""
    if not text:
        return []
    return _WORD_RE.findall(unicodedata.normalize("NFKC", text).lower().replace("_", " "))


def tokens(text: str | None, *, keep_stopwords: bool = False) -> set[str]:
    """Singular words, without stopwords unless asked."""
    return {singular(w) for w in words(text) if keep_stopwords or w not in STOPWORDS}


def phrase_in(phrase: str, text: str) -> bool:
    """Whether ``phrase`` occurs in ``text`` as whole words (case-insensitive, plural-tolerant)."""
    target = [singular(w) for w in words(phrase)]
    if not target:
        return False
    haystack = [singular(w) for w in words(text)]
    n = len(target)
    return any(haystack[i : i + n] == target for i in range(len(haystack) - n + 1))


def contains_expression(expression: str, question: str) -> bool:
    """Whether a time expression the model reported is really in the question (words in order)."""
    target = words(expression)
    haystack = words(question)
    n = len(target)
    return bool(target) and any(haystack[i : i + n] == target for i in range(len(haystack) - n + 1))
