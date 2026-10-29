import type { Content } from '../content';

export function sectionLinks(t: Content): readonly (readonly [string, string])[] {
  return [
    [`#${t.sections.how}`, t.nav.how],
    [`#${t.sections.why}`, t.nav.why],
    [`#${t.sections.openSource}`, t.nav.openSource],
    [`#${t.sections.faq}`, t.nav.faq],
  ];
}
