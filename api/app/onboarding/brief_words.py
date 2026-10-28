"""The words of the first chat message onboarding writes for the user, one entry per interface language."""

from app.core.locale import Locale

BRIEF: dict[Locale, dict[str, str]] = {
    'it': {
        'intro': 'Crea la mia prima lista di contatti per il segmento "{name}".',
        'company': 'La mia azienda: {v}',
        'who': 'Chi cerco: {v}',
        'problem': 'Il loro problema: {v}',
        'outcome': 'Cosa ottengono: {v}',
        'filters': 'Filtri di ricerca persone:',
        'exclusions': 'Da escludere: {v}',
    },
    'en': {
        'intro': 'Build my first contact list for the "{name}" segment.',
        'company': 'My company: {v}',
        'who': 'Who I am looking for: {v}',
        'problem': 'Their problem: {v}',
        'outcome': 'What they get: {v}',
        'filters': 'People search filters:',
        'exclusions': 'Exclude: {v}',
    },
    'es': {
        'intro': 'Crea mi primera lista de contactos para el segmento "{name}".',
        'company': 'Mi empresa: {v}',
        'who': 'A quién busco: {v}',
        'problem': 'Su problema: {v}',
        'outcome': 'Qué consiguen: {v}',
        'filters': 'Filtros de búsqueda de personas:',
        'exclusions': 'Excluir: {v}',
    },
    'fr': {
        'intro': 'Crée ma première liste de contacts pour le segment "{name}".',
        'company': 'Mon entreprise : {v}',
        'who': 'Qui je cherche : {v}',
        'problem': 'Leur problème : {v}',
        'outcome': 'Ce qu’ils obtiennent : {v}',
        'filters': 'Filtres de recherche de personnes :',
        'exclusions': 'À exclure : {v}',
    },
}
FILTER_LABELS: dict[Locale, dict[str, str]] = {
    'it': {
        'titles': 'ruoli',
        'seniorities': 'seniority',
        'industries': 'settori',
        'employee_ranges': 'dipendenti',
        'locations': 'paesi',
        'keywords': 'parole chiave',
    },
    'en': {
        'titles': 'titles',
        'seniorities': 'seniorities',
        'industries': 'industries',
        'employee_ranges': 'employees',
        'locations': 'countries',
        'keywords': 'keywords',
    },
    'es': {
        'titles': 'cargos',
        'seniorities': 'nivel',
        'industries': 'sectores',
        'employee_ranges': 'empleados',
        'locations': 'países',
        'keywords': 'palabras clave',
    },
    'fr': {
        'titles': 'fonctions',
        'seniorities': 'niveau',
        'industries': 'secteurs',
        'employee_ranges': 'effectif',
        'locations': 'pays',
        'keywords': 'mots-clés',
    },
}
