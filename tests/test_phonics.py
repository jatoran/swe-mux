"""The phonetic-spelling → phoneme builder (Settings lexicon ✨ button)."""

from __future__ import annotations

from swe_mux.phonics import PHONEME_ALPHABET, VOWEL_PHONEMES, phonetic_to_phonemes

# The character set of the pinned Kokoro tokenizer vocabulary (hash-verified
# model, dumped 2026-08-18). The builder must never emit a character the model
# cannot token-ize — such characters are silently dropped at synthesis.
KOKORO_VOCAB_CHARS = set(
    ' !"$(),.:;?AIOQSTWYabcdefhijklmnopqrstuvwxyz'
    "æçðøŋœɐɑɒɔɕɖəɚɛɜɟɡɣɤɥɨɪɯɰɲɳɴɸɹɻɽɾʁʂʃʈʊʋʌʎʒʔʝʣʤʥʦʧʨʰʲˈˌː̃βθχᵊᵝᵻ"
    "—“”…→↓↗↘ꭧ"
)


def test_alphabet_is_speakable_by_the_pinned_model() -> None:
    assert PHONEME_ALPHABET <= KOKORO_VOCAB_CHARS
    assert VOWEL_PHONEMES <= PHONEME_ALPHABET


def test_builder_output_stays_inside_the_alphabet() -> None:
    for spelling in (
        "swee", "mucks", "kroh", "tron", "yes", "shine", "church", "thing",
        "quick", "sway", "boy", "cow", "book", "bird", "vault", "spaces",
        "swemux", "govspend", "chronotron", "phew", "judge",
    ):
        phonemes = phonetic_to_phonemes(spelling)
        assert phonemes is not None, spelling
        assert set(phonemes) <= PHONEME_ALPHABET, spelling


def test_measured_spellings_match_the_real_g2p_outputs() -> None:
    """Expectations mirror what misaki produces for the same real words, so a
    built pronunciation sounds like the dictionary one would have."""
    assert phonetic_to_phonemes("swee") == "swˈi"        # matches "swee-" words
    assert phonetic_to_phonemes("mucks") == "mˈʌks"      # misaki: mˈʌks
    assert phonetic_to_phonemes("sway") == "swˈA"        # misaki: swˈA
    assert phonetic_to_phonemes("swede") == "swˈid"      # misaki: swˈid
    assert phonetic_to_phonemes("yes") == "jˈɛs"         # misaki: jˈɛs
    assert phonetic_to_phonemes("church") == "ʧˈɜɹʧ"     # misaki: ʧˈɜɹʧ


def test_phonics_rules_cover_the_common_shapes() -> None:
    # teams and digraphs
    assert phonetic_to_phonemes("kroh") == "kɹˈO"
    assert phonetic_to_phonemes("thing") == "θˈɪŋ"
    assert phonetic_to_phonemes("quick") == "kwˈɪk"
    # silent final e lengthens the vowel
    assert phonetic_to_phonemes("like") == "lˈIk"
    assert phonetic_to_phonemes("shine") == "ʃˈIn"
    # doubled consonants spell one sound
    assert phonetic_to_phonemes("bill") == "bˈɪl"
    # c: soft before e/i/y, hard otherwise; final y reads "ee"
    assert phonetic_to_phonemes("city") == "sˈɪti"
    assert phonetic_to_phonemes("cat") == "kˈæt"
    # the word itself as its own phonetic spelling
    assert phonetic_to_phonemes("swemux") == "swˈɛmʌks"


def test_unmappable_input_returns_none_rather_than_guessing() -> None:
    assert phonetic_to_phonemes("") is None
    assert phonetic_to_phonemes("   ") is None
    assert phonetic_to_phonemes("123") is None
    assert phonetic_to_phonemes("it's") is None
    assert phonetic_to_phonemes("naïve") is None
