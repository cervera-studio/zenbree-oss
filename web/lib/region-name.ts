const NON_CLDR_SPELLINGS: Record<string, string> = {
  usa: 'US',
  us: 'US',
  uk: 'GB',
  'great britain': 'GB',
  'czech republic': 'CZ',
  turkey: 'TR',
  'the netherlands': 'NL',
};

let codesByEnglishName: Map<string, string> | undefined;
const namesByLocale = new Map<string, Intl.DisplayNames>();

// Two-letter codes only: Chrome has no names for UN M.49 areas such as Europe (150), so the server
// and the browser would render different text for them.
function* alpha2Codes() {
  const letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
  for (const a of letters) for (const b of letters) yield a + b;
}

function codeOf(englishName: string): string | undefined {
  if (!codesByEnglishName) {
    const english = new Intl.DisplayNames(['en'], { type: 'region', fallback: 'none' });
    codesByEnglishName = new Map(Object.entries(NON_CLDR_SPELLINGS));
    for (const code of alpha2Codes()) {
      const key = english.of(code)?.toLowerCase();
      if (key && !codesByEnglishName.has(key)) codesByEnglishName.set(key, code);
    }
  }
  return codesByEnglishName.get(englishName.trim().toLowerCase());
}

export function regionName(englishName: string, locale: string): string {
  const code = codeOf(englishName);
  if (!code) return englishName;
  let names = namesByLocale.get(locale);
  if (!names) {
    names = new Intl.DisplayNames([locale], { type: 'region' });
    namesByLocale.set(locale, names);
  }
  return names.of(code) ?? englishName;
}
