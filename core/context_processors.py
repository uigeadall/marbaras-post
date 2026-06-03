from django.conf import settings

from .content import INTEGRATIONS, LANGS, TRANSLATIONS


def get_lang(request):
    """Resolve the active language: ?lang= wins, else session, else 'bg'."""
    lang = (request.GET.get("lang") or "").lower()
    if lang in LANGS:
        request.session["lang"] = lang
        return lang
    return request.session.get("lang", "en")


def brand_and_i18n(request):
    lang = get_lang(request)
    other = "en" if lang == "bg" else "bg"
    return {
        "BRAND": settings.BRAND,
        "lang": lang,
        "other_lang": other,
        "t": TRANSLATIONS[lang],
        "INTEGRATIONS": INTEGRATIONS,
    }
