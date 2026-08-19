"""Phonetic spelling → Kokoro phonemes, for the lexicon pronunciation builder.

The lexicon's exact-pronunciation escape hatch is misaki's ``[word](/phonemes/)``
link form, but nobody types IPA. This module lets the user type how a word
*sounds* — ``swee``, ``mucks``, ``kroh no tron`` — and derives the phonemes
deterministically with English phonics rules: longest-match grapheme teams
(``ee``, ``ay``, ``ch``, ``igh`` …), the silent-final-e lengthening rule, and
letter-name defaults for the rest. It is intentionally rule-based rather than a
trained model: no new dependency (espeak-ng stays banned from the closure), no
network, and the same input always builds the same phonemes — the user tunes by
ear with the audition button, so predictability beats cleverness.

The output alphabet is fixed to symbols measured against the real misaki G2P
and present in the pinned Kokoro tokenizer vocabulary (2026-08-18 probe):
misaki compresses the diphthongs to single capitals (``A``=eɪ, ``I``=aɪ,
``O``=oʊ, ``W``=aʊ, ``Y``=ɔɪ) and writes r-colored "er" as ``ɜɹ``.
"""

from __future__ import annotations

# Every character this module may emit. All are present in the Kokoro
# tokenizer vocabulary and produced by the real misaki G2P; the unit tests
# assert the rule tables stay inside this set.
PHONEME_ALPHABET = frozenset("AIOWYbdfhijklmnpstuvwzæðŋɑɔəɛɜɡɪɹʃʊʌʒʤʧθˈ")

VOWEL_PHONEMES = frozenset("AIOWYiuæɑɔəɛɜɪʊʌ")

# Grapheme teams, longest-match first within each starting position. The
# mappings follow dictionary respelling conventions ("oh" says O, "ow" says W,
# "ee" says i), because the input is someone spelling a sound, not English
# orthography — ambiguity is resolved toward how respellings read aloud.
_TEAMS: tuple[tuple[str, str], ...] = (
    ("eigh", "A"),
    ("tch", "ʧ"),
    ("dge", "ʤ"),
    ("igh", "I"),
    ("air", "ɛɹ"),
    ("ear", "ɪɹ"),
    ("ar", "ɑɹ"),
    ("or", "ɔɹ"),
    ("er", "ɜɹ"),
    ("ir", "ɜɹ"),
    ("ur", "ɜɹ"),
    ("oy", "Y"),
    ("oi", "Y"),
    ("ow", "W"),
    ("ou", "W"),
    ("aw", "ɔ"),
    ("au", "ɔ"),
    ("oo", "u"),
    ("uu", "ʊ"),
    ("ee", "i"),
    ("ea", "i"),
    ("ai", "A"),
    ("ay", "A"),
    ("ey", "A"),
    ("oa", "O"),
    ("oe", "O"),
    ("oh", "O"),
    ("ie", "I"),
    ("ah", "ɑ"),
    ("eh", "ɛ"),
    ("ih", "ɪ"),
    ("uh", "ə"),
    ("ew", "u"),
    ("ue", "u"),
    ("ch", "ʧ"),
    ("sh", "ʃ"),
    ("zh", "ʒ"),
    ("th", "θ"),
    ("dh", "ð"),
    ("ng", "ŋ"),
    ("ph", "f"),
    ("wh", "w"),
    ("ck", "k"),
    ("qu", "kw"),
)

_SHORT_VOWELS = {"a": "æ", "e": "ɛ", "i": "ɪ", "o": "ɑ", "u": "ʌ"}
_LONG_VOWELS = {"a": "A", "e": "i", "i": "I", "o": "O", "u": "u"}

_CONSONANTS = {
    "b": "b", "d": "d", "f": "f", "g": "ɡ", "h": "h", "j": "ʤ", "k": "k",
    "l": "l", "m": "m", "n": "n", "p": "p", "q": "k", "r": "ɹ", "s": "s",
    "t": "t", "v": "v", "w": "w", "x": "ks", "z": "z",
}

_LETTERS = "abcdefghijklmnopqrstuvwxyz"


def phonetic_to_phonemes(piece: str) -> str | None:
    """One spelled-as-it-sounds token → misaki phonemes, or None if unmappable.

    Deterministic and total over lowercase letters; anything else (digits,
    apostrophes) is a signal the input is not a phonetic spelling, and the
    caller reports "could not derive" rather than guessing.
    """
    text = piece.strip().casefold()
    if not text or any(ch not in _LETTERS for ch in text):
        return None
    # Silent final e: a "swede"-shaped spelling lengthens its vowel. Applied
    # before scanning so the team rules never see the dropped e.
    long_at = -1
    if (
        len(text) >= 3
        and text.endswith("e")
        and text[-2] in _CONSONANTS
        and text[-3] in _SHORT_VOWELS
    ):
        long_at = len(text) - 3
        text = text[:-1]
    out: list[str] = []
    position = 0
    while position < len(text):
        for team, phoneme in _TEAMS:
            if text.startswith(team, position):
                # A team that swallows the lengthened vowel supersedes the rule.
                out.append(phoneme)
                position += len(team)
                break
        else:
            ch = text[position]
            if ch in _SHORT_VOWELS:
                table = _LONG_VOWELS if position == long_at else _SHORT_VOWELS
                out.append(table[ch])
            elif ch == "c":
                nxt = text[position + 1] if position + 1 < len(text) else ""
                out.append("s" if nxt in "eiy" else "k")
            elif ch == "y":
                # Onset y is the glide ("yes"); elsewhere it is a vowel sound
                # ("swy" → swI would be wrong more often than "sw-ee", so the
                # final y reads as "ee" the way respellings use it).
                is_onset = position == 0 or text[position - 1] in _CONSONANTS
                nxt_is_vowel = (
                    position + 1 < len(text) and text[position + 1] in _SHORT_VOWELS
                )
                if is_onset and nxt_is_vowel:
                    out.append("j")
                elif position == len(text) - 1:
                    out.append("i")
                else:
                    out.append("ɪ")
            elif ch in _CONSONANTS:
                out.append(_CONSONANTS[ch])
            else:
                return None
            position += 1
    # Doubled consonants spell one sound ("mucks" is not "muck-ks").
    collapsed: list[str] = []
    for phoneme in out:
        if collapsed and collapsed[-1] == phoneme and phoneme not in VOWEL_PHONEMES:
            continue
        collapsed.append(phoneme)
    phonemes = "".join(collapsed)
    # Primary stress on the first vowel; Kokoro speaks unstressed input flatly.
    for index, ch in enumerate(phonemes):
        if ch in VOWEL_PHONEMES:
            phonemes = phonemes[:index] + "ˈ" + phonemes[index:]
            break
    return phonemes or None
