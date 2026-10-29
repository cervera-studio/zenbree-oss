import type { Metadata } from 'next';
import Link from 'next/link';
import { getLocale } from 'next-intl/server';
import { LEGAL } from '@/lib/legal';
import { LegalLanguageNote } from '../legal-language-note';
import { SiteHeader } from '../site-header';

export const metadata: Metadata = { title: 'Terms of service · Zenbree' };

// ponytail: same shape as the privacy page, one component per language.
export default async function TermsPage() {
  const locale = await getLocale();
  return (
    <>
      <SiteHeader />
      <article className="prose prose-neutral dark:prose-invert mx-auto max-w-3xl px-4 py-12">
        <LegalLanguageNote />
        {locale === 'it' ? <TermsIt /> : <TermsEn />}
      </article>
    </>
  );
}

function TermsEn() {
  return (
    <>
      <h1>Terms of service</h1>
      <p>Last updated: {LEGAL.updated}</p>
      <p>
        These terms govern the use of Zenbree, provided by {LEGAL.entity}. By creating an account
        you accept them.
      </p>

      <h2>The service</h2>
      <p>
        Zenbree is an AI agent that builds lists of companies and contacts from a brief, finds
        contact details through third-party data providers, and sends email and LinkedIn sequences
        from accounts you connect. Results come from AI models and external sources and may be
        incomplete or inaccurate: check them before you rely on them.
      </p>

      <h2>Your account</h2>
      <p>
        You must be at least 18 and use Zenbree for business purposes. Keep your credentials safe:
        you are responsible for what happens in your account, including the API keys you create.
      </p>

      <h2>Credits and payments</h2>
      <p>
        Paid features consume credits. Contact lookups are charged only when a contact is found.
        Prices are shown before purchase and exclude VAT; payments are processed by Stripe. Credits
        are not refundable once used.
      </p>

      <h2>Your outreach</h2>
      <p>
        You decide whom to contact and what to send, and you are the controller of the personal data
        in your campaigns. You must comply with the laws that apply to you, including the GDPR, the
        ePrivacy rules and anti-spam laws, and honour every opt-out request. Zenbree keeps an
        exclusion list and stops sequences on reply, but these do not replace your own obligations.
      </p>
      <p>
        LinkedIn does not allow automation. When you connect a LinkedIn account you accept the risk
        that LinkedIn may restrict or suspend it; Zenbree applies daily limits to reduce that risk
        but cannot remove it.
      </p>

      <h2>Acceptable use</h2>
      <p>
        Do not use Zenbree to send spam, deceptive or unlawful content, to harass anyone, to contact
        people who asked not to be contacted, or to attack, overload or reverse engineer the
        service. We may suspend accounts that break these rules.
      </p>

      <h2>Third-party services</h2>
      <p>
        Zenbree relies on third-party services (mailbox providers, LinkedIn through Unipile, data
        providers, AI models). Their availability and terms are outside our control. How we handle
        personal data is described in the <Link href="/privacy">privacy policy</Link>.
      </p>

      <h2>Liability</h2>
      <p>
        The service is provided as is. To the extent permitted by law, we are not liable for
        indirect damages or lost profits, and our total liability is limited to the amount you paid
        us in the twelve months before the claim. Nothing in these terms limits liability that
        cannot be limited by law.
      </p>

      <h2>Termination</h2>
      <p>
        You can stop using Zenbree at any time and ask us to close your account by writing to{' '}
        <a href={`mailto:${LEGAL.contactEmail}`}>{LEGAL.contactEmail}</a>. We may terminate accounts
        that breach these terms.
      </p>

      <h2>Changes and governing law</h2>
      <p>
        We may update these terms; material changes are notified by email in advance. These terms
        are governed by Italian law.
      </p>
    </>
  );
}

function TermsIt() {
  return (
    <>
      <h1>Termini di servizio</h1>
      <p>Ultimo aggiornamento: {LEGAL.updated}</p>
      <p>
        Questi termini regolano l'uso di Zenbree, fornito da {LEGAL.entity}. Creando un account li
        accetti.
      </p>

      <h2>Il servizio</h2>
      <p>
        Zenbree è un agente AI che costruisce liste di aziende e contatti partendo da un brief,
        trova i recapiti tramite fornitori di dati terzi e invia sequenze email e LinkedIn dagli
        account che colleghi. I risultati vengono da modelli AI e fonti esterne e possono essere
        incompleti o inesatti: verificali prima di farci affidamento.
      </p>

      <h2>Il tuo account</h2>
      <p>
        Devi avere almeno 18 anni e usare Zenbree per scopi professionali. Custodisci le tue
        credenziali: sei responsabile di quello che succede nel tuo account, comprese le chiavi API
        che crei.
      </p>

      <h2>Crediti e pagamenti</h2>
      <p>
        Le funzioni a pagamento consumano crediti. La ricerca dei contatti si paga solo quando il
        contatto viene trovato. I prezzi sono indicati prima dell'acquisto e sono IVA esclusa; i
        pagamenti sono gestiti da Stripe. I crediti usati non sono rimborsabili.
      </p>

      <h2>Le tue campagne</h2>
      <p>
        Decidi tu chi contattare e cosa inviare, e sei titolare dei dati personali delle tue
        campagne. Devi rispettare le leggi che ti riguardano, tra cui GDPR, normativa ePrivacy e
        antispam, e accogliere ogni richiesta di non essere contattati. Zenbree mantiene una lista
        di esclusione e ferma le sequenze alla risposta, ma questo non sostituisce i tuoi obblighi.
      </p>
      <p>
        LinkedIn non consente l'automazione. Collegando un account LinkedIn accetti il rischio che
        LinkedIn lo limiti o lo sospenda; Zenbree applica limiti giornalieri per ridurre il rischio,
        ma non può eliminarlo.
      </p>

      <h2>Uso consentito</h2>
      <p>
        Non usare Zenbree per inviare spam o contenuti ingannevoli o illeciti, molestare qualcuno,
        contattare chi ha chiesto di non esserlo, o attaccare, sovraccaricare o decompilare il
        servizio. Possiamo sospendere gli account che violano queste regole.
      </p>

      <h2>Servizi di terzi</h2>
      <p>
        Zenbree si appoggia a servizi di terzi (provider email, LinkedIn tramite Unipile, fornitori
        di dati, modelli AI). La loro disponibilità e i loro termini non dipendono da noi. Come
        trattiamo i dati personali è descritto nella <Link href="/privacy">privacy policy</Link>.
      </p>

      <h2>Responsabilità</h2>
      <p>
        Il servizio è fornito così com'è. Nei limiti di legge, non rispondiamo di danni indiretti o
        mancati guadagni, e la nostra responsabilità complessiva è limitata a quanto ci hai pagato
        nei dodici mesi precedenti la richiesta. Nulla in questi termini limita responsabilità che
        la legge non consente di limitare.
      </p>

      <h2>Recesso</h2>
      <p>
        Puoi smettere di usare Zenbree quando vuoi e chiederci di chiudere l'account scrivendo a{' '}
        <a href={`mailto:${LEGAL.contactEmail}`}>{LEGAL.contactEmail}</a>. Possiamo chiudere gli
        account che violano questi termini.
      </p>

      <h2>Modifiche e legge applicabile</h2>
      <p>
        Possiamo aggiornare questi termini; le modifiche sostanziali sono comunicate via email in
        anticipo. Questi termini sono regolati dalla legge italiana.
      </p>
    </>
  );
}
