"""Detecção de idioma restrita às línguas do acervo."""

from functools import lru_cache

from lingua import Language, LanguageDetectorBuilder

_CODES = {
    Language.PORTUGUESE: "pt",
    Language.SPANISH: "es",
    Language.FRENCH: "fr",
    Language.ENGLISH: "en",
    Language.GERMAN: "de",
}


@lru_cache
def _detector():
    return LanguageDetectorBuilder.from_languages(*_CODES).with_preloaded_language_models().build()


def detect(text: str) -> str | None:
    sample = text[:2000]
    if len(sample.strip()) < 40:
        return None
    lang = _detector().detect_language_of(sample)
    return _CODES.get(lang)


def detect_many(texts: list[str]) -> list[str | None]:
    samples = [t[:2000] for t in texts]
    langs = _detector().detect_languages_in_parallel_of(samples)
    return [
        _CODES.get(lang) if len(s.strip()) >= 40 else None for lang, s in zip(langs, samples, strict=True)
    ]
