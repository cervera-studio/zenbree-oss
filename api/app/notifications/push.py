"""Web Push (#343): a browser alert with no Zenbree tab open.

A subscription is the browser's push-service endpoint plus its keys. The endpoint is a URL the browser chose and
the server posts to, so ``valid_endpoint`` is the SSRF gate: https, port 443 and a host of a known push service.
A push never carries a reply's text; it names the event and opens the item's page.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

import requests
import structlog
from pywebpush import WebPushException, webpush  # type: ignore[import-untyped]  # the library ships no stubs

from app.core.config import settings
from app.core.db import get_conn
from app.core.locale import Locale

logger = structlog.get_logger()

PUSH_HOSTS = frozenset({'fcm.googleapis.com', 'updates.push.services.mozilla.com', 'web.push.apple.com'})
PUSH_HOST_SUFFIXES = ('.notify.windows.com',)
GONE = (404, 410)
SEND_TIMEOUT = 10
TTL_SECONDS = 24 * 3600  # a browser offline for a day no longer needs the alert


def enabled() -> bool:
    return bool(settings.vapid_public_key and settings.vapid_private_key and settings.vapid_subject)


def valid_endpoint(endpoint: str) -> bool:
    try:
        url = urlsplit(endpoint)
        host, port = url.hostname, url.port
    except ValueError:
        return False
    if url.scheme != 'https' or url.username or url.password or port not in (None, 443) or not host:
        return False
    return host in PUSH_HOSTS or any(host.endswith(s) and len(host) > len(s) for s in PUSH_HOST_SUFFIXES)


def subscribe(conn, user_id: str, endpoint: str, p256dh: str, auth: str) -> bool:
    """Upsert by endpoint: the browser is one device, so a new user on it takes the subscription over. Proof of
    being that browser is its ``auth`` secret, which only the browser and the server know: an endpoint alone
    (it travels in URLs and logs) moves nothing. False when the row stays another user's."""
    return (
        conn.execute(
            """INSERT INTO push_subscriptions (endpoint, user_id, p256dh, auth) VALUES (%s, %s, %s, %s)
               ON CONFLICT (endpoint) DO UPDATE SET user_id = EXCLUDED.user_id, p256dh = EXCLUDED.p256dh,
                   auth = EXCLUDED.auth, created_at = now()
               WHERE push_subscriptions.user_id = EXCLUDED.user_id OR push_subscriptions.auth = EXCLUDED.auth""",
            (endpoint, user_id, p256dh, auth),
        ).rowcount
        == 1
    )


def unsubscribe(conn, user_id: str, endpoint: str) -> int:
    return conn.execute(
        'DELETE FROM push_subscriptions WHERE endpoint = %s AND user_id = %s', (endpoint, user_id)
    ).rowcount


def endpoints(conn, user_id: str) -> list[str]:
    rows = conn.execute('SELECT endpoint FROM push_subscriptions WHERE user_id = %s', (user_id,)).fetchall()
    return [r['endpoint'] for r in rows]


def no_redirect_session() -> requests.Session:
    """A push service that redirects would turn the allowlist into a way to reach any host: zero redirects
    makes ``requests`` raise instead of following one."""
    session = requests.Session()
    session.max_redirects = 0
    return session


def _send_one(sub: dict, payload: str) -> int | None:
    """The push service's status code; ``None`` when it was unreachable."""
    try:
        resp = webpush(
            {'endpoint': sub['endpoint'], 'keys': {'p256dh': sub['p256dh'], 'auth': sub['auth']}},
            data=payload,
            vapid_private_key=settings.vapid_private_key,
            vapid_claims={'sub': settings.vapid_subject},
            ttl=TTL_SECONDS,
            timeout=SEND_TIMEOUT,
            requests_session=no_redirect_session(),
        )
        return resp.status_code
    except WebPushException as exc:
        return exc.response.status_code if exc.response is not None else None
    except Exception as exc:  # one browser's failure never costs the others their alert
        # The type only: the message can quote the endpoint, whose path is the browser's token.
        logger.warning('push.send_failed', error_type=type(exc).__name__)
        return None


def send(user_id: str, payload: str, *, only: str | None = None) -> list[int | None]:
    """Push ``payload`` to the user's browsers (just ``only``, an endpoint, when given) and drop the ones the push
    service says are gone. One status per browser, in order. Each send waits on the network for up to
    ``SEND_TIMEOUT``, so no pool connection is held across them."""
    if not enabled():
        return []
    query = 'SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE user_id = %s'
    with get_conn() as conn:
        if only is None:
            subs = conn.execute(query, (user_id,)).fetchall()
        else:
            subs = conn.execute(query + ' AND endpoint = %s', (user_id, only)).fetchall()
    statuses = [_send_one(s, payload) for s in subs]
    gone = [s['endpoint'] for s, code in zip(subs, statuses, strict=True) if code in GONE]
    if gone:
        with get_conn() as conn:
            conn.execute('DELETE FROM push_subscriptions WHERE endpoint = ANY(%s) AND user_id = %s', (gone, user_id))
    return statuses


_COPY: dict[Locale, dict] = {
    'it': {
        'contact': 'Un contatto',
        'to': ('a una tua campagna', 'alla campagna «{}»'),
        'in': ('in una tua campagna', 'nella campagna «{}»'),
        'alerts': {
            'reply_urgent': ('Risposta interessata', '{who} ha risposto {to}.'),
            'reply': ('Nuova risposta', '{who} ha risposto {to}.'),
            'linkedin_accepted': ('Invito LinkedIn accettato', '{who} ha accettato il tuo invito.'),
            'approval_needed': ('Messaggi da approvare', '{approval}'),
            'search_done': ('Lista pronta', 'La ricerca che hai avviato è finita.'),
            'search_failed': ('Ricerca ferma', 'Un fornitore non risponde. Quello che manca non lo paghi.'),
            'agent_question': ('Zenbree ha una domanda', 'La ricerca aspetta una tua risposta.'),
            'bounces_high': ('Troppi rimbalzi', 'Molti indirizzi non sono validi {in_}.'),
            'low_credits': ('Crediti in esaurimento', 'Ricarica per non fermare ricerche e campagne.'),
        },
        'approval': {
            'none': 'Ci sono messaggi da approvare.',
            'one': '1 messaggio aspetta la tua approvazione.',
            'many': '{count} messaggi aspettano la tua approvazione.',
        },
        'unverified': 'Mittente non verificato.',
        'test': ('Notifiche attive', 'Questo è un avviso di prova da Zenbree.'),
    },
    'en': {
        'contact': 'A contact',
        'to': ('to one of your campaigns', 'to the campaign "{}"'),
        'in': ('in one of your campaigns', 'in the campaign "{}"'),
        'alerts': {
            'reply_urgent': ('Interested reply', '{who} replied {to}.'),
            'reply': ('New reply', '{who} replied {to}.'),
            'linkedin_accepted': ('LinkedIn invitation accepted', '{who} accepted your invitation.'),
            'approval_needed': ('Messages to approve', '{approval}'),
            'search_done': ('List ready', 'The search you started has finished.'),
            'search_failed': ('Search stopped', "A provider is not responding. You don't pay for what's missing."),
            'agent_question': ('Zenbree has a question', 'The search is waiting for your answer.'),
            'bounces_high': ('Too many bounces', 'Many addresses are invalid {in_}.'),
            'low_credits': ('Credits running low', 'Top up so searches and campaigns keep running.'),
        },
        'approval': {
            'none': 'Some messages are waiting for your approval.',
            'one': '1 message is waiting for your approval.',
            'many': '{count} messages are waiting for your approval.',
        },
        'unverified': 'Sender not verified.',
        'test': ('Notifications on', 'This is a test alert from Zenbree.'),
    },
    'es': {
        'contact': 'Un contacto',
        'to': ('a una de tus campañas', 'a la campaña «{}»'),
        'in': ('en una de tus campañas', 'en la campaña «{}»'),
        'alerts': {
            'reply_urgent': ('Respuesta interesada', '{who} ha respondido {to}.'),
            'reply': ('Nueva respuesta', '{who} ha respondido {to}.'),
            'linkedin_accepted': ('Invitación de LinkedIn aceptada', '{who} ha aceptado tu invitación.'),
            'approval_needed': ('Mensajes por aprobar', '{approval}'),
            'search_done': ('Lista preparada', 'La búsqueda que iniciaste ha terminado.'),
            'search_failed': ('Búsqueda detenida', 'Un proveedor no responde. Lo que falta no lo pagas.'),
            'agent_question': ('Zenbree tiene una pregunta', 'La búsqueda espera tu respuesta.'),
            'bounces_high': ('Demasiados rebotes', 'Muchas direcciones no son válidas {in_}.'),
            'low_credits': ('Créditos a punto de agotarse', 'Recarga para no detener búsquedas ni campañas.'),
        },
        'approval': {
            'none': 'Hay mensajes que esperan tu aprobación.',
            'one': '1 mensaje espera tu aprobación.',
            'many': '{count} mensajes esperan tu aprobación.',
        },
        'unverified': 'Remitente no verificado.',
        'test': ('Notificaciones activadas', 'Este es un aviso de prueba de Zenbree.'),
    },
    'fr': {
        'contact': 'Un contact',
        'to': ('à l’une de vos campagnes', 'à la campagne «\u00a0{}\u00a0»'),
        'in': ('dans l’une de vos campagnes', 'dans la campagne «\u00a0{}\u00a0»'),
        'alerts': {
            'reply_urgent': ('Réponse intéressée', '{who} a répondu {to}.'),
            'reply': ('Nouvelle réponse', '{who} a répondu {to}.'),
            'linkedin_accepted': ('Invitation LinkedIn acceptée', '{who} a accepté votre invitation.'),
            'approval_needed': ('Messages à approuver', '{approval}'),
            'search_done': ('Liste prête', 'La recherche que vous avez lancée est terminée.'),
            'search_failed': ('Recherche arrêtée', 'Un fournisseur ne répond pas. Vous ne payez pas ce qui manque.'),
            'agent_question': ('Zenbree a une question', 'La recherche attend votre réponse.'),
            'bounces_high': ('Trop de rebonds', 'De nombreuses adresses ne sont pas valides {in_}.'),
            'low_credits': ('Crédits bientôt épuisés', 'Rechargez pour que recherches et campagnes continuent.'),
        },
        'approval': {
            'none': 'Des messages attendent votre approbation.',
            'one': '1 message attend votre approbation.',
            'many': '{count} messages attendent votre approbation.',
        },
        'unverified': 'Expéditeur non vérifié.',
        'test': ('Notifications activées', 'Ceci est une alerte de test de Zenbree.'),
    },
}


def _campaign(data: dict) -> str:
    return ' '.join(str(data.get('campaign') or '').split())[:80]


def _fragment(pair: tuple[str, str], campaign: str) -> str:
    anonymous, named = pair
    return named.format(campaign) if campaign else anonymous


def text(kind: str, data: dict, locale: Locale) -> tuple[str, str]:
    """(title, body) of a kind's alert. Never a reply's text: the name, the label and the counts only."""
    copy = _COPY[locale]
    if kind not in copy['alerts']:
        raise ValueError(kind)
    title, body = copy['alerts'][kind]
    campaign = _campaign(data)
    count = data.get('count')
    approval = copy['approval']['one' if count == 1 else 'many' if count else 'none']
    body = body.format(
        who=' '.join(str(data.get('name') or copy['contact']).split())[:60],
        to=_fragment(copy['to'], campaign),
        in_=_fragment(copy['in'], campaign),
        approval=approval.format(count=count),
    )
    if data.get('unverified'):
        body = f'{body} {copy["unverified"]}'
    return title, body


def payload(item: dict, locale: Locale) -> str:
    title, body = text(item['kind'], item['data'], locale)
    return json.dumps({'id': item['id'], 'title': title, 'body': body, 'href': item['href']})


def check_payload(locale: Locale) -> str:
    title, body = _COPY[locale]['test']
    return json.dumps({'id': 'test', 'title': title, 'body': body, 'href': '/settings/notifications'})
