// @vitest-environment happy-dom
import { act } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { click, control, field, render } from '@/app/auth/testing';
import { toast } from '@/components/ui/sonner';
import type { Webhook } from '@/lib/webhooks/schemas';
import { WebhookManager } from './webhook-manager';

const actions = vi.hoisted(() => ({
  createWebhookAction: vi.fn(),
  deleteWebhookAction: vi.fn(),
  getDeliveriesAction: vi.fn(),
  testWebhookAction: vi.fn(),
}));
vi.mock('@/app/(app)/settings/webhooks/actions', () => actions);
vi.mock('@/components/ui/sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const EVENTS = ['list.completed', 'contact.replied', 'campaign.finished', 'sequence.stopped'];
const SECRET = 'whsec_c2VjcmV0LWZvci10ZXN0cw==';
const hook = (id: string, label = 'n8n'): Webhook => ({
  id,
  label,
  host: 'hooks.example.com',
  eventNames: ['campaign.finished'],
  createdAt: '2026-10-10T10:00:00Z',
  goneAt: null,
});
const text = (c: HTMLElement) => c.textContent ?? '';

async function type(el: HTMLInputElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

beforeEach(() => vi.resetAllMocks());

describe('WebhookManager', () => {
  it('shows the signing secret once, after the create, and never in the list', async () => {
    actions.createWebhookAction.mockResolvedValue({
      success: true,
      data: { ...hook('11111111-1111-4111-8111-111111111111', 'Zap'), secret: SECRET },
    });
    const c = await render(
      <WebhookManager initialWebhooks={[]} events={EVENTS} loadFailed={false} />,
    );
    expect(text(c)).toContain('Non hai ancora aggiunto webhook.');
    await type(field(c, 'Nome'), 'Zap');
    await type(field(c, 'URL del destinatario'), 'https://hooks.example.com/zap');
    await click(control(c, 'Aggiungi webhook'));

    expect(actions.createWebhookAction).toHaveBeenCalledWith({
      url: 'https://hooks.example.com/zap',
      label: 'Zap',
      eventNames: EVENTS,
    });
    expect(c.querySelector<HTMLInputElement>('input[aria-label="Segreto di firma"]')?.value).toBe(
      SECRET,
    );
    expect(text(c)).toContain('Zap');
    await click(control(c, 'Ho salvato il segreto'));
    expect(text(c)).not.toContain(SECRET);
    expect(c.querySelector('input[aria-label="Segreto di firma"]')).toBeNull();
  });

  it('prints the receiver host', async () => {
    const c = await render(
      <WebhookManager initialWebhooks={[hook('a')]} events={EVENTS} loadFailed={false} />,
    );
    expect(text(c)).toContain('hooks.example.com');
  });

  it('sends only the events that stay checked', async () => {
    actions.createWebhookAction.mockResolvedValue({
      success: false,
      error: { code: 'WEBHOOKS.INVALID_URL' },
    });
    const c = await render(
      <WebhookManager initialWebhooks={[]} events={EVENTS} loadFailed={false} />,
    );
    await type(field(c, 'Nome'), 'Zap');
    await type(field(c, 'URL del destinatario'), 'http://127.0.0.1/x');
    const boxes = [...c.querySelectorAll<HTMLElement>('[role="checkbox"]')];
    expect(boxes).toHaveLength(EVENTS.length);
    await click(boxes[0] as HTMLElement);
    await click(control(c, 'Aggiungi webhook'));
    expect(actions.createWebhookAction.mock.calls[0]?.[0].eventNames).toEqual(EVENTS.slice(1));
    expect(c.querySelector('[role="alert"]')?.textContent).toBe(
      'Inserisci un URL https pubblico valido.',
    );
    expect(c.querySelector('input[aria-label="Segreto di firma"]')).toBeNull();
  });

  it('refuses a new webhook at the cap the API enforces', async () => {
    const full = Array.from({ length: 10 }, (_, i) => hook(`id-${i}`, `W${i}`));
    const c = await render(
      <WebhookManager initialWebhooks={full} events={EVENTS} loadFailed={false} />,
    );
    expect(text(c)).toContain('Hai raggiunto il limite');
    expect(control(c, 'Aggiungi webhook').hasAttribute('disabled')).toBe(true);
  });

  it('reports a test by the status the receiver sent, and none as no answer', async () => {
    const c = await render(
      <WebhookManager initialWebhooks={[hook('a')]} events={EVENTS} loadFailed={false} />,
    );
    actions.testWebhookAction.mockResolvedValueOnce({ success: true, data: { status: 503 } });
    await click(control(c, 'Invia un test a n8n'));
    expect(toast.error).toHaveBeenCalledWith('Il destinatario ha risposto 503.');
    actions.testWebhookAction.mockResolvedValueOnce({ success: true, data: { status: 204 } });
    await click(control(c, 'Invia un test a n8n'));
    expect(toast.success).toHaveBeenCalledWith('Il destinatario ha risposto 204.');
    actions.testWebhookAction.mockResolvedValueOnce({ success: true, data: { status: null } });
    await click(control(c, 'Invia un test a n8n'));
    expect(toast.error).toHaveBeenLastCalledWith('Il destinatario non ha risposto.');
  });

  it('marks the endpoint revoked when the test answer is 410, and keeps its Delete', async () => {
    const c = await render(
      <WebhookManager initialWebhooks={[hook('a')]} events={EVENTS} loadFailed={false} />,
    );
    actions.testWebhookAction.mockResolvedValueOnce({ success: true, data: { status: 410 } });
    await click(control(c, 'Invia un test a n8n'));
    expect(toast.error).toHaveBeenCalledWith(
      'Il destinatario ha risposto 410 Gone: «n8n» è stato revocato.',
    );
    expect(text(c)).toContain('Revocato dal destinatario');
    expect(control(c, 'Invia un test a n8n').hasAttribute('disabled')).toBe(true);
    expect(control(c, 'Elimina il webhook n8n').hasAttribute('disabled')).toBe(false);
  });

  it('lists the deliveries with the failure category, marks a test, and prints no payload', async () => {
    actions.getDeliveriesAction.mockResolvedValue({
      success: true,
      data: {
        deliveries: [
          {
            id: '1',
            eventId: 'abc123',
            eventType: 'webhook.test',
            isTest: true,
            attempt: 1,
            attemptedAt: '2026-10-10T10:00:00Z',
            statusCode: null,
            category: 'timeout',
            willRetry: false,
          },
          {
            id: '2',
            eventId: 'def456',
            eventType: 'campaign.finished',
            isTest: false,
            attempt: 2,
            attemptedAt: '2026-10-10T09:00:00Z',
            statusCode: 503,
            category: 'server_error',
            willRetry: true,
          },
          {
            id: '3',
            eventId: 'def456',
            eventType: 'campaign.finished',
            isTest: false,
            attempt: 1,
            attemptedAt: '2026-10-10T08:00:00Z',
            statusCode: 200,
            category: null,
            willRetry: false,
          },
        ],
      },
    });
    const c = await render(
      <WebhookManager initialWebhooks={[hook('a')]} events={EVENTS} loadFailed={false} />,
    );
    await click(control(c, 'Mostra le consegne di n8n'));
    expect(actions.getDeliveriesAction).toHaveBeenCalledWith('a');
    const rows = [...c.querySelectorAll('section tbody tr')].map((r) => r.textContent ?? '');
    expect(rows).toHaveLength(3);
    expect(rows[0]).toContain('Test');
    expect(rows[0]).toContain('Tempo scaduto');
    expect(rows[0]).toContain('Nessuna risposta');
    expect(rows[1]).toContain('Errore del server');
    expect(rows[1]).toContain('503');
    expect(rows[1]).toContain('Nuovo tentativo in coda');
    expect(rows[1]).toContain('def456');
    expect(rows[2]).toContain('Consegnato');
    await click(control(c, 'Mostra le consegne di n8n'));
    expect(c.querySelector('section')).toBeNull();
  });

  it('asks before it deletes, and drops the row and its history', async () => {
    actions.deleteWebhookAction.mockResolvedValue({ success: true, data: undefined });
    const c = await render(
      <WebhookManager initialWebhooks={[hook('a')]} events={EVENTS} loadFailed={false} />,
    );
    await click(control(c, 'Elimina il webhook n8n'));
    expect(actions.deleteWebhookAction).not.toHaveBeenCalled();
    await click(control(document.body, 'Elimina webhook'));
    expect(actions.deleteWebhookAction).toHaveBeenCalledWith('a');
    expect(text(c)).toContain('Non hai ancora aggiunto webhook.');
  });
});
