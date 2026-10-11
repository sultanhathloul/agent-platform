"""SA_NATIONAL_ID confirmation (ADR-0120). The recognizer in infra/presidio/ finds every
ten-digit number starting with 1 (national ID) or 2 (iqama); the presidio checker keeps
one only when its check digit is valid (the Luhn scheme, ISO/IEC 7812-1 Annex B, over all
ten digits) or a context word stands within the five tokens before it: هوية، الهوية
الوطنية، سجل مدني، إقامة، ID، iqama, national ID, with Arabic prefixes (و، ب، ل، ف، ال)
and possessive endings (هويته، إقامتي). Without either, the number is a reference, an
invoice or an order number and passes."""

import re

from app.text.arabic import normalize

CONTEXT_TOKENS = 5

_TOKEN = re.compile(r"\w+")
_PREFIX = r"(?:وبال|وال|بال|فال|لل|ال|و|ب|ل|ف)?"
_POSSESSIVE = r"(?:ه|ي|ك|ها|هم|كم|نا)?"
# One-token context words, on normalized tokens (ة → ه, alef forms → ا, case folded).
_WORD = re.compile(rf"^(?:{_PREFIX}(?:هوي|اقام)(?:ه|ت{_POSSESSIVE})|id|iqama)$")
# سجل مدني: two consecutive tokens (السجل المدني، سجله المدني).
_REGISTER = re.compile(rf"^{_PREFIX}سجل{_POSSESSIVE}$")
_CIVIL = re.compile(r"^(?:ال)?مدني$")


def luhn_valid(digits: str) -> bool:
    """Every second digit from the right, the check digit excluded, is doubled (minus 9
    above 9); the sum of all digits is a multiple of 10."""
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2:
            value = value * 2 - 9 if value > 4 else value * 2
        total += value
    return total % 10 == 0


def has_context(before: str) -> bool:
    """A context word ends within the last CONTEXT_TOKENS tokens of `before`."""
    tokens = [normalize(t) for t in _TOKEN.findall(before)]
    for index in range(max(0, len(tokens) - CONTEXT_TOKENS), len(tokens)):
        token = tokens[index]
        if _WORD.match(token):
            return True
        if index > 0 and _CIVIL.match(token) and _REGISTER.match(tokens[index - 1]):
            return True
    return False


def confirmed(text: str, start: int, end: int) -> bool:
    """Whether the candidate text[start:end] is kept as SA_NATIONAL_ID."""
    digits = "".join(c for c in text[start:end] if c.isdigit())
    return (len(digits) == 10 and luhn_valid(digits)) or has_context(text[:start])
