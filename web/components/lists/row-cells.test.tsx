import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vite-plus/test';
import { TestIntl } from '@/app/auth/testing';
import emailFailed from '@/lib/api/fixtures/email-failed.json';
import emailFound from '@/lib/api/fixtures/email-found.json';
import emailInvalid from '@/lib/api/fixtures/email-invalid.json';
import emailNotFound from '@/lib/api/fixtures/email-not-found.json';
import emailNotSearched from '@/lib/api/fixtures/email-not-searched.json';
import emailRefunded from '@/lib/api/fixtures/email-refunded.json';
import emailSearching from '@/lib/api/fixtures/email-searching.json';
import phoneFailed from '@/lib/api/fixtures/phone-failed.json';
import phoneFound from '@/lib/api/fixtures/phone-found.json';
import phoneNotFound from '@/lib/api/fixtures/phone-not-found.json';
import phoneSearching from '@/lib/api/fixtures/phone-searching.json';
import { type ListColumn, type ListRow, rowSchema } from '@/lib/lists/api';
import type { CompositeId, Entity } from '@/lib/lists/column-model';
import { fixtureRow, lookups, strayStatus } from '@/lib/lists/testing';
import { COMPOSITE_CELLS, RowAvatar } from './row-cells';

function render(
  entity: Entity,
  cells: Record<string, unknown>,
  extra: Record<string, unknown> = {},
  column: CompositeId = entity,
) {
  const row = rowSchema.parse({
    id: 'r',
    seq: 1,
    cells,
    relevance_score: null,
    excluded: false,
    lookups: lookups(),
    ...extra,
  });
  return renderRow(row, entity, column);
}

function renderRow(row: ListRow, entity: Entity, column: CompositeId) {
  const Cell = COMPOSITE_CELLS[column];
  const columns: ListColumn[] = [
    { key: 'email', label: 'Email', kind: 'static', position: 0, config: { required: true } },
    { key: 'phone', label: 'Telefono', kind: 'static', position: 1, config: { required: true } },
  ];
  return renderToStaticMarkup(
    <TestIntl>
      <Cell row={row} entity={entity} onOpen={() => {}} chatHref="/?s=c1" columns={columns} />
    </TestIntl>,
  );
}

const openButton = (html: string) =>
  html.match(/<button[^>]*data-row-open[^>]*>(.*?)<\/button>/)?.[1];

describe('name cell', () => {
  it('labels the open button «Senza nome» on a row with no name, so it can be opened', () => {
    expect(openButton(render('person', { email: 'lia@acme.it', company_name: 'Acme' }))).toContain(
      'Senza nome',
    );
    expect(openButton(render('company', { domain: 'acme.it' }))).toContain('Senza nome');
  });

  it('labels the open button with the name when the row has one', () => {
    expect(openButton(render('person', { full_name: 'Lia Neri' }))).toBe('Lia Neri');
  });
});

describe('HubSpot hold in the person cell (#335)', () => {
  const lia = { full_name: 'Lia Neri' };

  it('shows «In pausa» with the reason and the cooldown date', () => {
    const html = render('person', lia, {
      crm_hold: { reason: 'HUBSPOT_COOLDOWN', until: '2026-11-03T10:00:00+00:00' },
    });
    expect(html).toContain('In pausa');
    expect(html).toContain('le campagne riprendono dal 3 nov 2026');
  });

  it('shows «Bloccato» for an opt-out, which never lifts', () => {
    const html = render('person', lia, { crm_hold: { reason: 'HUBSPOT_OPTED_OUT', until: null } });
    expect(html).toContain('Bloccato');
    expect(html).toContain('Si è disiscritto dalle email in HubSpot');
  });

  it('reads a reason it does not know as the generic one', () => {
    const html = render('person', lia, { crm_hold: { reason: 'HUBSPOT_NEW_RULE', until: null } });
    expect(html).toContain('Bloccato dal controllo HubSpot');
  });

  it('shows nothing on a row nobody holds', () => {
    for (const extra of [{}, { crm_hold: null }]) {
      const html = render('person', lia, extra);
      expect(html).not.toContain('In pausa');
      expect(html).not.toContain('HubSpot');
    }
  });
});

describe('email cell (#521)', () => {
  const email = (json: object, own: Parameters<typeof fixtureRow>[1] = {}) =>
    renderRow(fixtureRow(json, own), 'person', 'email');

  it('says «Non trovata» with «Da completare» once a search found no email', () => {
    const html = email(emailNotFound);
    expect(html).toContain('Non trovata');
    expect(html).toContain('Da completare');
  });

  it('says «Non ancora cercata» with no chip on a row never searched', () => {
    const html = email(emailNotSearched);
    expect(html).toContain('Non ancora cercata');
    expect(html).not.toContain('Non trovata');
    expect(html).not.toContain('Da completare');
  });

  it('says a search that got no answer failed, with no chip: the next batch retries it (#659)', () => {
    const html = email(emailFailed);
    expect(html).toContain('Ricerca non riuscita');
    expect(html).not.toContain('Non trovata');
    expect(html).not.toContain('Da completare');
  });

  it('says a refunded email was found and links a draft that asks for it again by row (#531)', () => {
    const html = email(emailRefunded);
    expect(html).toContain('Trovata, credito reso');
    expect(html).not.toContain('Non ancora cercata');
    expect(html).toContain('Chiedi in chat');
    const href =
      html
        .match(/href="([^"]*draft=[^"]*)"/)?.[1]
        ?.replaceAll('&amp;', '&')
        .replaceAll('&#x27;', "'") ?? '';
    expect(href.startsWith('/?s=c1&draft=')).toBe(true);
    expect(decodeURIComponent(href.split('draft=')[1] ?? '')).toBe(
      `Trova di nuovo l'email della riga ${emailRefunded.id} e completa le colonne obbligatorie che le mancano: Telefono.`,
    );
  });

  it('links no draft on an excluded row, which the agent never searches', () => {
    const html = email(emailRefunded, { excluded: true });
    expect(html).toContain('Trovata, credito reso');
    expect(html).not.toContain('Chiedi in chat');
  });

  it('says «In ricerca» while a turn holds the email it bought, and shows no address', () => {
    const html = email(emailSearching);
    expect(html).toContain('In ricerca');
    expect(html).not.toContain('mailto:');
    expect(html).not.toContain('Non ancora cercata');
    expect(html).toContain('motion-safe:animate-pulse');
  });

  it('shows the email with its verdict, and no link', () => {
    const html = email(emailFound);
    expect(html).toContain('mailto:anna@acme.it');
    expect(html).toContain('Verificata');
    expect(html).not.toContain('Chiedi in chat');
  });

  it('says an address the verifier rejected is not valid', () => {
    const html = email(emailInvalid);
    expect(html).toContain('bruno@acme.it');
    expect(html).toContain('Non valida');
  });
});

describe('phone cell', () => {
  const phone = (json: object) => renderRow(fixtureRow(json), 'person', 'phone');

  it('shows the number, never the raw status', () => {
    const html = phone(phoneFound.row);
    expect(html).toContain('+39333111222');
    expect(html).not.toContain(strayStatus('phone_status'));
  });

  it('reads «Non trovato» after a search that found nothing, never the raw status', () => {
    const html = phone(phoneNotFound.row);
    expect(html).toContain('Non trovato');
    expect(html).not.toContain(strayStatus('phone_status'));
  });

  it('says a search that got no answer failed', () => {
    expect(phone(phoneFailed.row)).toContain('Ricerca non riuscita');
  });

  it('stays empty on a row never searched', () => {
    expect(phone(emailNotSearched)).toBe('');
  });

  it('pulses «In ricerca» while a turn searches the number, and only then', () => {
    const html = phone(phoneSearching);
    expect(html).toContain('In ricerca');
    expect(html).toContain('motion-safe:animate-pulse');
    for (const row of [phoneFound.row, phoneNotFound.row, phoneFailed.row]) {
      expect(phone(row)).not.toContain('animate-pulse');
    }
  });
});

describe('row avatar', () => {
  const avatar = (name: string, entity: Entity = 'person') =>
    renderToStaticMarkup(<RowAvatar name={name} entity={entity} className="size-11" />);

  it('shows the initials of a row with a name', () => {
    expect(avatar('Lia Neri')).toContain('>LN<');
    expect(avatar('Acme', 'company')).toContain('>A<');
  });

  it('shows the icon of its entity for a row with no name, not an empty circle', () => {
    expect(avatar('')).toContain('lucide-user');
    expect(avatar('', 'company')).toContain('lucide-building-2');
  });
});
