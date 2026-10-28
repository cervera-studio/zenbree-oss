"""The service email layout (#221): escaping, the text part, and what each notification email may say."""

from datetime import UTC, datetime

from app.core.config import settings
from app.notifications import service as n
from app.notifications.layout import render


def test_layout_escapes_and_has_a_text_part():
    mail = render(
        subject='S',
        preheader='pre <b>',
        title='T & co',
        paragraphs=['<script>x</script>'],
        button='Go',
        href='/billing?a=1&b=2',
        reason='Why.',
        locale='it',
    )
    assert '<script>' not in mail.html and '&lt;script&gt;' in mail.html and 'T &amp; co' in mail.html
    assert 'pre &lt;b&gt;' in mail.html and '/billing?a=1&amp;b=2' in mail.html
    assert 'Gestisci notifiche' not in mail.html  # not manageable unless asked
    assert mail.text.startswith('T & co\n') and 'Go: ' in mail.text and '/billing?a=1&b=2' in mail.text
    assert 'prefers-color-scheme: dark' in mail.html and mail.html.count('<h1') == 1
    assert f'<img src="{settings.web_origin}/apple-icon.png"' in mail.html


def test_payment_email_says_it_cannot_be_turned_off():
    mail = n._render('payment_failed', '/billing', {}, 'it')
    assert 'Non possiamo disattivarla' in mail.text and 'Gestisci notifiche' not in mail.text


def test_layout_secondary_button_tiles_and_language():
    mail = render(
        subject='S',
        preheader='P',
        title='T',
        paragraphs=['Body'],
        stats=[('47', 'people')],
        boxes=[('With 180', '~90 <emails>', 'or 18')],
        note='Note',
        button='Primary',
        href='/a',
        secondary=('Second & co', '/b?x=1&y=2'),
        fine_print='Fine',
        reason='Why.',
        manage=True,
        locale='en',
    )
    assert '<html lang="en">' in mail.html and 'Manage notifications' in mail.html and 'Gestisci' not in mail.html
    assert 'Second &amp; co' in mail.html and '/b?x=1&amp;y=2' in mail.html and '~90 &lt;emails&gt;' in mail.html
    assert mail.html.index('Note') < mail.html.index('Primary') < mail.html.index('Second') < mail.html.index('Fine')
    assert '47 people\n' in mail.text and 'With 180: ~90 <emails>, or 18' in mail.text
    assert 'Primary: http://localhost:3000/a\n\nSecond & co: http://localhost:3000/b?x=1&y=2' in mail.text


_DONE = {
    'chat': 'Trova CEO a Milano',
    'list': 'Agenzie marketing Milano',
    'list_id': 'L1',
    'people_list_id': 'L1',
    'people': 47,
    'companies': 1031,
    'spent': 298,
    'refunded': 14,
    'undelivered': 3,
    'balance': 942,
}


def test_search_done_email_has_the_list_its_numbers_and_the_campaign_button_in_both_languages():
    it = n._render('search_done', '/?s=1', _DONE, 'it')
    assert it.subject == 'La lista «Agenzie marketing Milano» è pronta'
    assert "L'agente ha finito la ricerca «Trova CEO a Milano»." in it.text
    assert '47 persone · 1.031 aziende · 298 crediti spesi · 14 resi' in it.text
    assert '3 righe non consegnate: non le paghi. Saldo: 942 crediti.' in it.text
    assert 'Apri la lista: http://localhost:3000/lists/L1' in it.text
    assert 'Prepara la campagna: http://localhost:3000/campaigns?list=L1' in it.text
    assert '47 persone, 1.031 aziende. 14 crediti resi.' in it.html  # the preheader
    en = n._render('search_done', '/?s=1', _DONE, 'en')
    assert en.subject == 'Your list "Agenzie marketing Milano" is ready' and '<html lang="en">' in en.html
    assert '47 people · 1,031 companies · 298 credits spent · 14 refunded' in en.text
    assert "3 rows not delivered: you don't pay for them. Balance: 942 credits." in en.text
    assert 'Open the list: ' in en.text and 'Prepare the campaign: ' in en.text and '"Search finished"' in en.text


def test_search_done_email_without_people_or_refunds_says_less():
    data = {'chat': 'X', 'list': 'Y', 'list_id': 'L2', 'companies': 1, 'spent': 1, 'balance': 5}
    mail = n._render('search_done', '/?s=1', data, 'it')
    assert '1 azienda · 1 credito speso\n' in mail.text and 'Saldo: 5 crediti.' in mail.text
    assert 'non consegnat' not in mail.text and 'resi' not in mail.text and 'Prepara la campagna' not in mail.text
    bare = n._render('search_done', '/?s=1', {'spent': 0}, 'en')  # an old item, or no lists
    assert bare.subject == 'Your Zenbree search is ready' and 'Open the list: http://localhost:3000/?s=1' in bare.text


def test_low_credits_email_prices_the_balance_with_the_tools_and_shows_the_plan_in_both_languages(monkeypatch):
    from app.core.config import CreditPack, Plan, settings

    monkeypatch.setattr(
        settings,
        'billing_plans',
        [
            Plan(id='starter', credits=2000, price_eur_cents=2900, stripe_price_id='p1'),
            Plan(id='pro', credits=6000, price_eur_cents=7900, stripe_price_id='p2'),
        ],
    )
    monkeypatch.setattr(
        settings,
        'billing_packs',
        [
            CreditPack(id='a', credits=1000, price_eur_cents=1900, stripe_price_id='x'),
            CreditPack(id='b', credits=3000, price_eur_cents=4950, stripe_price_id='y'),
            CreditPack(id='hidden', credits=5, price_eur_cents=1),
        ],
    )
    renews = datetime(2030, 11, 1, 12, tzinfo=UTC)
    data = {'credits': 180, 'organization': 'Ritmo', 'plan': 'starter', 'renews_at': renews.isoformat()}
    it = n._render('low_credits', '/billing/switch?org=o', data, 'it')
    assert it.subject == '«Ritmo»: restano 180 crediti'
    assert 'Con 180 crediti: ~180 email trovate, oppure 22 telefoni' in it.text  # enrich_email 1, enrich_phone 8
    assert 'Piano Starter: 2.000 crediti il 1 nov, tra ' in it.text and 'Il piano si rinnova il 1 nov.' in it.html
    assert 'Passa a Pro: http://localhost:3000/billing/switch?org=o' in it.text
    assert 'Ricariche: 1.000 crediti €19 · 3.000 €49,50. I crediti ricaricati non scadono.' in it.text
    en = n._render('low_credits', '/billing', data, 'en')
    assert (
        en.subject == '"Ritmo": 180 credits left'
        and 'With 180 credits: ~180 emails found, or 22 phone numbers' in en.text
    )
    assert 'Starter plan: 2,000 credits on Nov 1, in ' in en.text and 'Upgrade to Pro: ' in en.text
    assert 'Top-ups: 1,000 credits €19 · 3,000 €49.50.' in en.text and 'It cannot be turned off.' in en.text


def test_low_credits_email_without_plan_or_anything_on_sale(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(
        settings, 'billing_plans', [p.model_copy(update={'stripe_price_id': ''}) for p in settings.billing_plans]
    )
    monkeypatch.setattr(settings, 'billing_packs', [])
    mail = n._render('low_credits', '/billing', {'credits': 1}, 'it')
    assert (
        mail.subject == 'Ti resta 1 credito'
        and n._render('low_credits', '/', {'credits': 1}, 'en').subject == 'You have 1 credit left'
    )
    assert 'Piano' not in mail.text and 'Passa a' not in mail.text and 'Ricariche' not in mail.text
    assert 'email trovate' not in mail.text  # 1 credit buys no phone: no box


def test_low_credits_offers_the_top_plan_only_to_a_smaller_or_no_plan(monkeypatch):
    """Review of #376: a cancelling or past-due top plan, or a plan priced outside BILLING_PLANS, gets no upgrade."""
    from app.core.config import Plan, settings

    monkeypatch.setattr(
        settings,
        'billing_plans',
        [
            Plan(id='starter', credits=2000, price_eur_cents=2900, stripe_price_id='p1'),
            Plan(id='pro', credits=6000, price_eur_cents=7900, stripe_price_id='p2'),
        ],
    )

    def offers(data: dict) -> bool:
        return 'Passa a Pro' in n._render('low_credits', '/billing', {'credits': 150} | data, 'it').text

    assert offers({}) and offers({'plan': 'starter'})  # no live plan; a smaller one, renewing or not
    assert not offers({'plan': 'pro'})  # already the top, e.g. cancelling or past due: no renewal box either
    assert not offers({'plan': None}) and not offers({'plan': 'legacy'})  # a price not in BILLING_PLANS
    assert 'Piano' not in n._render('low_credits', '/billing', {'credits': 150, 'plan': 'pro'}, 'it').text


def test_new_emails_name_what_happened_and_never_claim_a_pause():
    bounce = n._render('bounces_high', '/campaigns/c1', {'campaign': 'SDR', 'invalid': 3, 'attempted': 40}, 'it')
    assert '3 indirizzi non validi su 40' in bounce.text and 'pausa' not in bounce.text.lower()
    assert 'Non possiamo disattivarla' in bounce.text
    mailbox = n._render(
        'mailbox_error', '/settings/integrations', {'mailbox': 'me@acme.test', 'provider': 'google'}, 'it'
    )
    assert '«me@acme.test»' in mailbox.text and 'Ricollega la casella' in mailbox.text
    assert 'Non possiamo disattivarla' in mailbox.text
    linkedin = n._render(
        'mailbox_error', '/settings/integrations', {'mailbox': 'Anna Rossi', 'provider': 'unipile'}, 'it'
    )
    assert linkedin.subject == 'Account LinkedIn scollegato su Zenbree' and '«Anna Rossi»' in linkedin.text
    assert 'Ricollega l’account' in linkedin.text and 'casella' not in linkedin.text.lower()
    credits = n._render('low_credits', '/billing', {'credits': 10}, 'it')
    assert 'Non possiamo disattivarla' in credits.text and 'perché hai attivato' not in credits.text
