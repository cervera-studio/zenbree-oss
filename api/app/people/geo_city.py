"""Small, dependency-free city identity helpers shared across domains."""

from __future__ import annotations

import unicodedata

ITALIAN_CITY_EXONYMS: dict[str, str] = {
    'milan': 'milano',
    'turin': 'torino',
    'rome': 'roma',
    'florence': 'firenze',
    'naples': 'napoli',
    'venice': 'venezia',
    'genoa': 'genova',
    'padua': 'padova',
    'mantua': 'mantova',
    'syracuse': 'siracusa',
    'trent': 'trento',
}

US_STATE_NAMES: frozenset[str] = frozenset(
    {
        'alabama',
        'alaska',
        'arizona',
        'arkansas',
        'california',
        'colorado',
        'connecticut',
        'delaware',
        'florida',
        'georgia',
        'hawaii',
        'idaho',
        'illinois',
        'indiana',
        'iowa',
        'kansas',
        'kentucky',
        'louisiana',
        'maine',
        'maryland',
        'massachusetts',
        'michigan',
        'minnesota',
        'mississippi',
        'missouri',
        'montana',
        'nebraska',
        'nevada',
        'new hampshire',
        'new jersey',
        'new mexico',
        'new york',
        'north carolina',
        'north dakota',
        'ohio',
        'oklahoma',
        'oregon',
        'pennsylvania',
        'rhode island',
        'south carolina',
        'south dakota',
        'tennessee',
        'texas',
        'utah',
        'vermont',
        'virginia',
        'washington',
        'west virginia',
        'wisconsin',
        'wyoming',
    }
)

NON_ITALIAN_COUNTRY_NAMES: frozenset[str] = frozenset(
    {
        'united states',
        'usa',
        'u.s.',
        'u.s.a.',
        'united states of america',
        'united kingdom',
        'uk',
        'great britain',
        'england',
        'scotland',
        'wales',
        'northern ireland',
        'ireland',
        'austria',
        'osterreich',
        'australia',
        'belgium',
        'belgique',
        'belgie',
        'brazil',
        'brasil',
        'bulgaria',
        'canada',
        'czech republic',
        'czechia',
        'cesko',
        'denmark',
        'danmark',
        'finland',
        'suomi',
        'france',
        'germany',
        'deutschland',
        'greece',
        'hellas',
        'hungary',
        'magyarorszag',
        'india',
        'latvia',
        'latvija',
        'lithuania',
        'lietuva',
        'mexico',
        'netherlands',
        'holland',
        'nederland',
        'new zealand',
        'norway',
        'norge',
        'poland',
        'polska',
        'portugal',
        'romania',
        'serbia',
        'srbija',
        'singapore',
        'south africa',
        'spain',
        'espana',
        'sweden',
        'sverige',
        'switzerland',
        'schweiz',
        'suisse',
        'svizzera',
    }
)

_ITALY_NAMES = frozenset({'it', 'ita', 'italy', 'italia'})
_ITALIAN_REGION_NAMES = frozenset(
    {
        'abruzzo',
        'aosta valley',
        'apulia',
        'basilicata',
        'calabria',
        'campania',
        'emilia-romagna',
        'friuli-venezia giulia',
        'latium',
        'lazio',
        'liguria',
        'lombardia',
        'lombardy',
        'marche',
        'molise',
        'piemonte',
        'piedmont',
        'puglia',
        'sardegna',
        'sardinia',
        'sicilia',
        'sicily',
        'south tyrol',
        'toscana',
        'trentino',
        'trentino-alto adige',
        'trentino-south tyrol',
        'tuscany',
        'umbria',
        "valle d'aosta",
        'veneto',
    }
)


def _fold_place(value: str) -> str:
    folded = unicodedata.normalize('NFKD', value)
    folded = ''.join(ch for ch in folded if not unicodedata.combining(ch))
    return ' '.join(folded.casefold().replace('’', "'").split())


def location_match_parts(location: str, *, bare_hint: bool = False) -> tuple[str, ...]:
    """Return canonical location segments, never a province-wide key.

    A provider city is translated through the Italian exonym table only when
    trailing segments attest Italy by country or region and none attest a
    foreign place. A truly bare user hint has no country to carry that
    evidence, so it may use the same table directly. Foreign and unknown cities
    retain their folded qualifiers, so homonyms in different regions or
    countries do not collapse to one city token.
    """
    parts = [_fold_place(part) for part in str(location or '').split(',') if part.strip()]
    if not parts:
        return ()
    qualifiers = set(parts[1:])
    italian_qualifiers = _ITALY_NAMES | _ITALIAN_REGION_NAMES
    says_italy = bool(qualifiers) and qualifiers <= italian_qualifiers
    if (bare_hint and len(parts) == 1) or says_italy:
        parts[0] = ITALIAN_CITY_EXONYMS.get(parts[0], parts[0])
    if says_italy:
        return parts[0], 'italia'
    return tuple(parts)


_NORTH_WEST = ('Lombardy, Italy', 'Piedmont, Italy', 'Liguria, Italy', 'Aosta Valley, Italy')
_NORTH_EAST = (
    'Veneto, Italy',
    'Emilia-Romagna, Italy',
    'Friuli-Venezia Giulia, Italy',
    'Trentino-Alto Adige, Italy',
)
_CENTRE = ('Tuscany, Italy', 'Lazio, Italy', 'Marche, Italy', 'Umbria, Italy')
_SOUTH = (
    'Campania, Italy',
    'Apulia, Italy',
    'Sicily, Italy',
    'Calabria, Italy',
    'Sardinia, Italy',
    'Abruzzo, Italy',
    'Basilicata, Italy',
    'Molise, Italy',
)

#: Macro-regions → the places a people index actually knows. Probed on both
#: Apollo and AI Ark on 2026-09-29 (title "Marketing Manager"): "Northern
#: Italy", "Nord Italia", "DACH" and "Benelux" return 0 people on both, while
#: every member below returns people (Apollo: Lombardy 10,070, Veneto 2,831 …;
#: Germany 66,117). Keys are folded (see ``_fold_place``), Italian and English.
_MACRO_REGIONS: dict[str, tuple[str, ...]] = {
    **dict.fromkeys(
        (
            'northern italy',
            'north italy',
            'nord italia',
            'italia del nord',
            'italia settentrionale',
            'settentrione',
        ),
        _NORTH_WEST + _NORTH_EAST,
    ),
    **dict.fromkeys(
        (
            'north-west italy',
            'northwest italy',
            'nord-ovest',
            'nord ovest',
            'nordovest',
            'nord-ovest italia',
            'nord ovest italia',
            'italia nord-occidentale',
        ),
        _NORTH_WEST,
    ),
    **dict.fromkeys(
        (
            'north-east italy',
            'northeast italy',
            'nord-est',
            'nord est',
            'nordest',
            'nord-est italia',
            'nord est italia',
            'italia nord-orientale',
        ),
        _NORTH_EAST,
    ),
    **dict.fromkeys(
        (
            'central italy',
            'centre italy',
            'center italy',
            'centro italia',
            'italia centrale',
            'italia del centro',
        ),
        _CENTRE,
    ),
    **dict.fromkeys(
        (
            'southern italy',
            'south italy',
            'sud italia',
            'italia del sud',
            'italia meridionale',
            'mezzogiorno',
            'meridione',
        ),
        _SOUTH,
    ),
    'dach': ('Germany', 'Austria', 'Switzerland'),
    'benelux': ('Belgium', 'Netherlands', 'Luxembourg'),
}


def expand_macro_regions(values: list[str] | tuple[str, ...]) -> list[str]:
    """``["Nord Italia"]`` → ``["Lombardy, Italy", "Piedmont, Italy", …]``.

    Every other value passes through untouched; duplicates are dropped, order
    kept. Both people-search clients call this on their location filters, so
    onboarding and chat searches get the same answer for the same words.
    """
    out: list[str] = []
    for value in values:
        for place in _MACRO_REGIONS.get(_fold_place(str(value)), (value,)):
            if place not in out:
                out.append(place)
    return out
