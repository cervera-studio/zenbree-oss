import type { Metadata } from 'next';
import { getLocale } from 'next-intl/server';
import { GOOGLE_USER_DATA_POLICY, LEGAL } from '@/lib/legal';
import { LegalLanguageNote } from '../legal-language-note';
import { SiteHeader } from '../site-header';

export const metadata: Metadata = { title: 'Privacy policy · Zenbree' };

// ponytail: legal copy lives in TSX, one component per language, not in the message
// files: it is long prose with links that nobody translates key by key.
export default async function PrivacyPage() {
  const locale = await getLocale();
  return (
    <>
      <SiteHeader />
      <article className="prose prose-neutral dark:prose-invert mx-auto max-w-3xl px-4 py-12">
        <LegalLanguageNote />
        {locale === 'it' ? <PrivacyIt /> : <PrivacyEn />}
      </article>
    </>
  );
}

const Controller = () => (
  <p>
    {LEGAL.entity}. <a href={`mailto:${LEGAL.contactEmail}`}>{LEGAL.contactEmail}</a>
  </p>
);

function PrivacyEn() {
  return (
    <>
      <h1>Privacy policy</h1>
      <p>Last updated: {LEGAL.updated}</p>
      <p>
        This policy explains what personal data Zenbree collects, why, and what rights you have.
        Zenbree is an AI agent for outbound sales: it builds lists of companies and contacts from a
        brief and sends email and LinkedIn sequences from the user's own accounts.
      </p>

      <h2>Data controller</h2>
      <Controller />

      <h2>Data we process about our users</h2>
      <ul>
        <li>
          <strong>Account</strong>: name, email address, password (stored hashed) or the profile
          returned by Google or Microsoft sign-in.
        </li>
        <li>
          <strong>Content you create</strong>: chat messages, lists, campaigns, sequences, saved
          company profile and memory.
        </li>
        <li>
          <strong>Connected accounts</strong>: for a connected mailbox, the email address and the
          OAuth tokens, stored encrypted; for LinkedIn, only the account identifier issued by
          Unipile, never your LinkedIn password.
        </li>
        <li>
          <strong>Billing</strong>: purchases and credit balance. Card details are handled by Stripe
          and never reach our servers.
        </li>
        <li>
          <strong>Technical data</strong>: logs, error reports and product analytics (pages viewed,
          features used), including IP address and browser information.
        </li>
      </ul>

      <h2>Google user data</h2>
      <p>
        When you connect a Gmail or Google Workspace mailbox, Zenbree requests the{' '}
        <code>gmail.send</code> and <code>gmail.readonly</code> permissions, plus your basic profile
        to show which address is connected. We use them for two purposes: sending the campaign
        emails you create and approve, from your address, and reading the delivery failure notices
        (bounces) your mailbox receives for those emails, so that a campaign whose emails bounce is
        paused and the bounced address is not written to again. To find those notices, Zenbree reads
        the headers of each new message in your inbox, and the whole message only when its headers
        look like a delivery failure notice; it stores nothing of a message that is not one. After
        each campaign email it reads back the identifier your provider gave that email, which its
        notices quote. Replies reach Zenbree through a dedicated Reply-To address, not by reading
        your inbox.
      </p>
      <p>
        We store the OAuth tokens encrypted, together with the identifiers of the messages we sent.
        We do not sell Google user data, do not use it for advertising, do not use it to train AI
        models, and do not let humans read it except with your consent, for security reasons or when
        the law requires it. You can disconnect the mailbox at any time from Settings →
        Integrations, which revokes the access and deletes the tokens.
      </p>
      <p>
        Zenbree's use and transfer to any other app of information received from Google APIs will
        adhere to the <a href={GOOGLE_USER_DATA_POLICY}>Google API Services User Data Policy</a>,
        including the Limited Use requirements.
      </p>
      <p>
        The same rules apply to Microsoft 365 mailboxes, where Zenbree requests the{' '}
        <code>Mail.Send</code> and <code>Mail.Read</code> permissions for the same two purposes.
      </p>

      <h2>Data about prospects</h2>
      <p>
        Lists built by Zenbree contain business information about companies and professionals (name,
        role, company, work email, public profile URLs), collected from public sources and B2B data
        providers. Our users decide whom to contact and act as controllers for their campaigns;
        Zenbree processes this data on their behalf. Anyone who appears in a list can ask us for
        access or deletion at the address above, and anyone who asks not to be contacted is added to
        an exclusion list.
      </p>

      <h2>Purposes and legal bases</h2>
      <ul>
        <li>Providing the service you signed up for: performance of a contract.</li>
        <li>Billing and tax records: legal obligation.</li>
        <li>
          Security, abuse prevention, error monitoring and product analytics: legitimate interest.
        </li>
        <li>
          Service emails (verification, password reset, reply notices): performance of a contract.
        </li>
      </ul>

      <h2>Service providers</h2>
      <p>We share data only with the providers needed to run the service:</p>
      <ul>
        <li>Hosting and network: Hetzner (EU), Cloudflare.</li>
        <li>Payments: Stripe. Transactional email: Resend.</li>
        <li>Product analytics: PostHog (EU). Logs and error monitoring: Better Stack.</li>
        <li>
          AI models: DeepSeek and Anthropic, which process chat messages and list data to generate
          answers.
        </li>
        <li>
          Data enrichment and web search: Apollo, AI Ark, Treg, Firecrawl, Exa, Serper. LinkedIn
          connection: Unipile.
        </li>
        <li>Email sending from connected mailboxes: Google and Microsoft.</li>
      </ul>
      <p>
        Some of these providers are located outside the European Economic Area (including the United
        States and China). Where required, transfers rely on adequacy decisions or the European
        Commission's Standard Contractual Clauses.
      </p>

      <h2>Retention</h2>
      <p>
        We keep account data and content for as long as your account exists. To close your account,
        write to the address above: we delete your data, except for what we must keep by law (such
        as invoices). Logs are kept for a limited period for security and troubleshooting.
      </p>

      <h2>Your rights</h2>
      <p>
        Under the GDPR you can ask for access, rectification, erasure, restriction, portability, and
        object to processing based on legitimate interest, by writing to{' '}
        <a href={`mailto:${LEGAL.contactEmail}`}>{LEGAL.contactEmail}</a>. You can also lodge a
        complaint with the Italian Data Protection Authority (Garante per la protezione dei dati
        personali) or the authority of your country.
      </p>

      <h2>Cookies</h2>
      <p>
        We use cookies needed to keep you signed in and remember your language, and analytics
        cookies to understand how the product is used.
      </p>

      <h2>Changes</h2>
      <p>
        We will publish any change on this page and update the date above. For material changes we
        will notify you by email.
      </p>
    </>
  );
}

function PrivacyIt() {
  return (
    <>
      <h1>Privacy policy</h1>
      <p>Ultimo aggiornamento: {LEGAL.updated}</p>
      <p>
        Questa informativa spiega quali dati personali raccoglie Zenbree, perché e quali diritti
        hai. Zenbree è un agente AI per le vendite outbound: costruisce liste di aziende e contatti
        partendo da un brief e invia sequenze email e LinkedIn dagli account dell'utente.
      </p>

      <h2>Titolare del trattamento</h2>
      <Controller />

      <h2>Dati che trattiamo sui nostri utenti</h2>
      <ul>
        <li>
          <strong>Account</strong>: nome, indirizzo email, password (salvata come hash) oppure il
          profilo restituito dall'accesso con Google o Microsoft.
        </li>
        <li>
          <strong>Contenuti che crei</strong>: messaggi in chat, liste, campagne, sequenze, profilo
          aziendale e memoria salvati.
        </li>
        <li>
          <strong>Account collegati</strong>: per una casella email collegata, l'indirizzo e i token
          OAuth, salvati cifrati; per LinkedIn, solo l'identificativo dell'account fornito da
          Unipile, mai la tua password LinkedIn.
        </li>
        <li>
          <strong>Pagamenti</strong>: acquisti e saldo crediti. I dati della carta sono gestiti da
          Stripe e non arrivano mai ai nostri server.
        </li>
        <li>
          <strong>Dati tecnici</strong>: log, segnalazioni di errore e statistiche d'uso (pagine
          viste, funzioni usate), compresi indirizzo IP e informazioni sul browser.
        </li>
      </ul>

      <h2>Dati degli utenti Google</h2>
      <p>
        Quando colleghi una casella Gmail o Google Workspace, Zenbree chiede i permessi{' '}
        <code>gmail.send</code> e <code>gmail.readonly</code>, più il profilo di base per mostrarti
        quale indirizzo è collegato. Li usiamo per due scopi: inviare dal tuo indirizzo le email
        delle campagne che crei e approvi, e leggere gli avvisi di mancata consegna (rimbalzi) che
        la tua casella riceve per quelle email, così una campagna con troppi rimbalzi viene messa in
        pausa e all'indirizzo rimbalzato non si scrive più. Per trovarli Zenbree legge le
        intestazioni di ogni nuovo messaggio della tua posta in arrivo, e il messaggio intero solo
        quando le intestazioni sembrano quelle di un avviso di mancata consegna; di un messaggio che
        non lo è non salva nulla. Dopo ogni email di campagna rilegge l'identificativo che il tuo
        provider le ha dato, quello citato dai suoi avvisi. Le risposte arrivano a Zenbree tramite
        un indirizzo Reply-To dedicato, non leggendo la tua posta.
      </p>
      <p>
        Salviamo i token OAuth cifrati, insieme agli identificativi dei messaggi inviati. Non
        vendiamo i dati degli utenti Google, non li usiamo per pubblicità né per addestrare modelli
        AI, e nessuna persona li legge salvo tuo consenso, per motivi di sicurezza o per obbligo di
        legge. Puoi scollegare la casella in qualsiasi momento da Impostazioni → Integrazioni:
        l'accesso viene revocato e i token cancellati.
      </p>
      <p>
        L'uso e il trasferimento ad altre app delle informazioni ricevute dalle API di Google da
        parte di Zenbree rispettano la{' '}
        <a href={GOOGLE_USER_DATA_POLICY}>Google API Services User Data Policy</a>, compresi i
        requisiti di Limited Use.
      </p>
      <p>
        Le stesse regole valgono per le caselle Microsoft 365, per cui Zenbree chiede i permessi{' '}
        <code>Mail.Send</code> e <code>Mail.Read</code> per gli stessi due scopi.
      </p>

      <h2>Dati sui potenziali clienti</h2>
      <p>
        Le liste costruite da Zenbree contengono informazioni professionali su aziende e persone
        (nome, ruolo, azienda, email di lavoro, URL di profili pubblici), raccolte da fonti
        pubbliche e da fornitori di dati B2B. I nostri utenti decidono chi contattare e sono
        titolari delle proprie campagne; Zenbree tratta questi dati per loro conto. Chiunque compaia
        in una lista può chiederci accesso o cancellazione all'indirizzo sopra, e chi chiede di non
        essere contattato viene inserito in una lista di esclusione.
      </p>

      <h2>Finalità e basi giuridiche</h2>
      <ul>
        <li>Fornire il servizio a cui ti sei iscritto: esecuzione del contratto.</li>
        <li>Fatturazione e obblighi fiscali: obbligo di legge.</li>
        <li>
          Sicurezza, prevenzione degli abusi, monitoraggio degli errori e statistiche d'uso:
          legittimo interesse.
        </li>
        <li>
          Email di servizio (verifica, reset password, avvisi di risposta): esecuzione del
          contratto.
        </li>
      </ul>

      <h2>Fornitori</h2>
      <p>Condividiamo i dati solo con i fornitori necessari a far funzionare il servizio:</p>
      <ul>
        <li>Hosting e rete: Hetzner (UE), Cloudflare.</li>
        <li>Pagamenti: Stripe. Email transazionali: Resend.</li>
        <li>Statistiche d'uso: PostHog (UE). Log e monitoraggio errori: Better Stack.</li>
        <li>
          Modelli AI: DeepSeek e Anthropic, che elaborano i messaggi in chat e i dati delle liste
          per generare le risposte.
        </li>
        <li>
          Arricchimento dati e ricerca web: Apollo, AI Ark, Treg, Firecrawl, Exa, Serper.
          Collegamento LinkedIn: Unipile.
        </li>
        <li>Invio email dalle caselle collegate: Google e Microsoft.</li>
      </ul>
      <p>
        Alcuni di questi fornitori si trovano fuori dallo Spazio economico europeo (tra cui Stati
        Uniti e Cina). Dove richiesto, i trasferimenti si basano su decisioni di adeguatezza o sulle
        Clausole contrattuali standard della Commissione europea.
      </p>

      <h2>Conservazione</h2>
      <p>
        Conserviamo i dati dell'account e i contenuti finché l'account esiste. Per chiudere
        l'account scrivi all'indirizzo sopra: cancelliamo i tuoi dati, tranne ciò che dobbiamo
        conservare per legge (come le fatture). I log sono conservati per un periodo limitato, per
        sicurezza e diagnostica.
      </p>

      <h2>I tuoi diritti</h2>
      <p>
        In base al GDPR puoi chiedere accesso, rettifica, cancellazione, limitazione e portabilità
        dei dati, e opporti ai trattamenti basati sul legittimo interesse, scrivendo a{' '}
        <a href={`mailto:${LEGAL.contactEmail}`}>{LEGAL.contactEmail}</a>. Puoi anche presentare
        reclamo al Garante per la protezione dei dati personali.
      </p>

      <h2>Cookie</h2>
      <p>
        Usiamo i cookie necessari per mantenerti connesso e ricordare la lingua, e cookie di analisi
        per capire come viene usato il prodotto.
      </p>

      <h2>Modifiche</h2>
      <p>
        Pubblichiamo ogni modifica su questa pagina aggiornando la data qui sopra. Per le modifiche
        sostanziali ti avvisiamo via email.
      </p>
    </>
  );
}
