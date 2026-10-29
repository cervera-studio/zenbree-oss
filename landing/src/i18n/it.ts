import type { Content } from '../content';

export const it: Content = {
  meta: {
    title: 'Zenbree · Agente AI per l’outbound B2B',
    description:
      'Scrivi in chat chi vuoi raggiungere. L’agente ti mostra il piano e lo approvi tu. Poi consegna persone con un’email controllata da un verificatore e la fonte di ogni dato.',
  },
  sections: { how: 'come', why: 'perche', openSource: 'open-source', faq: 'faq' },
  nav: {
    homeLabel: 'Zenbree, home',
    label: 'Sezioni',
    how: 'Come funziona',
    why: 'Perché',
    openSource: 'Open source',
    faq: 'Domande',
    signIn: 'Accedi',
    contact: 'Contattaci',
  },
  menu: {
    open: 'Apri il menu',
    close: 'Chiudi il menu',
    themeLabel: 'Tema',
    dark: 'Scuro',
  },
  hero: {
    eyebrow: "Agente AI per l'outbound B2B",
    titleLine: 'Descrivi il cliente ideale.',
    titleEm: 'Zenbree trova chi contattare.',
    cta: 'Richiedi l’accesso',
    seeHow: 'Guarda come funziona',
    fine: 'Open source · Self-host o servizio gestito su richiesta',
  },
  stage: {
    label: 'Anteprima del prodotto',
    emailFloat: {
      key: 'Email verificata',
      value: 'giulia@officinanove.it',
      source: 'Fonte: officinanove.it/team',
    },
    fieldsFloat: {
      key: 'Campi obbligatori',
      value: 'Nome, ruolo, email',
      source: 'Solo righe complete',
    },
    brief:
      'Agenzie di marketing indipendenti a Milano, 10–50 dipendenti. Voglio il CEO, con email controllata.',
    planTitle: 'Piano · 60 righe',
    columns: ['Nome *', 'Ruolo *', 'Email controllata *'],
    aiColumn: 'Settore · AI',
    planFooter: 'Solo righe con i campi *',
    rowsSummary: '57 consegnate · 31 aziende',
  },
  ui: {
    verified: '✓ verificata',
    people: 'Persone',
    approveStart: 'Approva e avvia',
    toApprove: 'Da approvare',
    skip: 'Salta',
    approveSend: 'Approva e invia',
    to: 'A:',
    subject: 'Oggetto:',
  },
  people: [
    { initials: 'GF', name: 'Giulia Ferri', role: 'CEO', company: 'Officina Nove', score: 92 },
    {
      initials: 'MB',
      name: 'Marco Bellini',
      role: 'Resp. commerciale',
      company: 'Studio Brezza',
      score: 88,
    },
    {
      initials: 'CG',
      name: 'Chiara Gallo',
      role: 'Founder e CEO',
      company: 'Lanterna Digital',
      score: 85,
    },
    { initials: 'EM', name: 'Elena Moretti', role: 'CEO', company: 'Ponte Creativo', score: 81 },
  ],
  works: {
    label: 'Funziona con quello che usi già',
    items: [
      { name: 'Gmail', logo: 'gmail' },
      { name: 'Outlook', logo: 'outlook' },
      { name: 'LinkedIn', logo: 'linkedin' },
      { name: 'CSV ed Excel', logo: 'excel' },
      { name: 'API', logo: 'api' },
      { name: 'HubSpot · presto', logo: 'hubspot', soon: true },
    ],
  },
  statement: {
    line: 'Una buona lista non è più lunga.',
    em: 'È più corta, e ogni riga ha una fonte che puoi aprire.',
  },
  how: {
    eyebrow: 'Come funziona',
    titleLine: 'Dal brief alla prima risposta,',
    titleRest: 'senza perdere il controllo.',
    tablistLabel: 'Passaggi',
    steps: [
      {
        n: '01',
        title: 'Descrivi',
        short: 'Una frase, come la diresti',
        body: 'Settore, zona, dimensione, ruoli, segnali come «sta assumendo». Scrivi come lo diresti a un collega: se manca qualcosa, l’agente te lo chiede prima di cercare.',
        points: [
          'Una domanda alla volta, solo quando serve',
          'Ricorda il profilo della tua azienda e ciò che gli chiedi',
          'Salta chi hai escluso: email, domini, aziende',
        ],
        art: {
          kind: 'brief',
          brief: 'Responsabili acquisti nella GDO del Nord Est',
          question: 'Prima di cercare: ti interessano solo le insegne o anche i grossisti?',
          choices: ['Solo insegne', 'Anche grossisti'],
        },
      },
      {
        n: '02',
        title: 'Approva il piano',
        short: 'Righe, colonne e passaggi',
        body: 'Vedi il piano, con righe e colonne, e lo approvi. Le colonne con l’asterisco decidono cosa ti viene consegnato.',
        points: [
          'Non parte nulla finché non approvi',
          'Righe e colonne le vedi prima di partire',
          'Le righe le cambi nel piano; colonne e passi li chiedi all’agente',
        ],
        art: {
          kind: 'plan',
          title: 'Piano · 80 righe',
          steps: [
            '1 · Trova 40 insegne nel Nord Est',
            '2 · 2 responsabili acquisti per insegna',
            '3 · Verifica le email e una colonna AI',
          ],
          note: 'Nulla parte finché non approvi',
        },
      },
      {
        n: '03',
        title: 'Ricevi la lista',
        short: 'Persone e aziende',
        body: 'Due liste collegate, persone e aziende, con un’email controllata da un verificatore e un punteggio di quanto sono in target. Ogni dato ricercato ha la sua fonte.',
        points: [
          'Le prime righe arrivano mentre l’agente lavora',
          'Righe incomplete non consegnate',
          'Esporti in CSV o Excel quando vuoi',
        ],
        art: {
          kind: 'list',
          delivered: '74 di 80 consegnate',
          people: [
            {
              initials: 'MC',
              name: 'Marta Colombo',
              role: 'Buyer freschi',
              company: 'Insegna Nord',
              score: 90,
            },
            {
              initials: 'PR',
              name: 'Paolo Rizzi',
              role: 'Category manager',
              company: 'Super Est',
              score: 84,
            },
          ],
          why: {
            title: 'Perché è in target',
            quote: '«Gestisce il fornitore locale dal 2023» · fonte: superest.it/news',
          },
        },
      },
      {
        n: '04',
        title: 'Contatta',
        short: 'Email e LinkedIn',
        body: 'Sequenze fino a 12 passi, dalle tue caselle e dal tuo LinkedIn. Approvi ogni messaggio, chi risponde esce dalla sequenza e la risposta arriva già etichettata.',
        points: [
          'Limite giornaliero che cresce nelle prime due settimane',
          'Stop automatico a risposta o disiscrizione',
          'Bozza pronta per ogni risposta, tranne i no',
        ],
        art: {
          kind: 'mail',
          queue: '3 di 12',
          to: 'Marta Colombo',
          subject: 'Fornitori locali per i freschi',
          body: 'Ciao Marta, ho letto che da quest’anno seguite di più i produttori del territorio…',
        },
      },
    ],
  },
  why: {
    eyebrow: 'Perché Zenbree',
    titleLine: 'Trova le persone giuste,',
    titleRest: 'con i dati per contattarle.',
    source: {
      title: 'Ogni dato con la fonte',
      body: 'Le colonne AI tengono un valore solo se è scritto in una pagina che puoi aprire.',
      cellValue: 'Dipendenti: 18',
      cellSource: '↗ linkedin.com/company/officina-nove',
    },
    approve: {
      title: 'Tu approvi ogni messaggio',
      body: 'Le campagne partono dalle tue caselle, con un limite giornaliero che cresce gradualmente. Niente parte senza il tuo ok.',
      caption: 'da 10 a 50 invii al giorno, per casella',
    },
    replies: {
      title: 'Risposte già divise',
      body: "Chi risponde esce dalla sequenza. L'agente etichetta la risposta e prepara la bozza.",
      labels: [
        { label: 'Interessato', tone: 'pos' },
        { label: 'Richiesta di incontro', tone: 'info' },
        { label: 'Non ora' },
        { label: 'Non interessato' },
        { label: 'Disiscritto', tone: 'neg' },
      ],
    },
    market: {
      title: 'Aziende e persone in tutto il mondo',
      body: 'Cerchi in qualunque paese. L’app e l’agente parlano italiano, inglese, spagnolo e francese, nella lingua che scegli, e le bozze di risposta in inbox seguono la lingua di chi ti scrive. Le esclusioni (clienti, concorrenti, chi si è disiscritto) valgono per sempre.',
    },
  },
  openSource: {
    eyebrow: 'Open source',
    title: 'Open source. Fai da te, o lo gestiamo noi.',
    sub: 'Zenbree ha licenza MIT. Clonalo, usa le tue chiavi dei fornitori e adattalo al tuo modo di lavorare.',
    selfHostTitle: 'Self-host',
    selfHostBody: 'Docker Compose avvia tutto lo stack: API, worker, web e database.',
    repoCta: 'Vedi su GitHub',
    hostedTitle: 'Gestito da noi',
    hostedBody:
      'Non vuoi occuparti di server e account dei fornitori? Offriamo un servizio già gestito. Scrivici e lo attiviamo.',
    hostedCta: 'Scrivici',
  },
  faq: {
    eyebrow: 'Domande',
    title: 'Quello che ci chiedono prima di iniziare.',
    moreBefore: 'Altro? Scrivici a ',
    moreAfter: '.',
    items: [
      {
        q: 'E se un’email non si trova?',
        a: 'Se l’email è obbligatoria nel piano, quella riga non viene consegnata. Le email «catch-all» le segnaliamo come da verificare.',
      },
      {
        q: 'Devo collegare LinkedIn o la mia email?',
        a: 'No, per creare liste non serve collegare nulla. Caselle email e LinkedIn servono solo per inviare campagne dai tuoi account.',
      },
      {
        q: 'Funziona fuori dall’Italia?',
        a: 'Sì. Cerchi aziende e persone in qualunque paese, sempre con la fonte di ogni dato, anche se la copertura dei fornitori cambia da paese a paese. Il sito, l’app e l’agente parlano italiano, inglese, spagnolo e francese, nella lingua che scegli nell’app.',
      },
      {
        q: 'Posso caricare una lista che ho già?',
        a: 'Sì: carichi un CSV e l’agente lo completa con email, telefoni e colonne di ricerca. Le righe nelle tue esclusioni vengono scartate.',
      },
      {
        q: 'Da dove vengono i dati, ed è a norma GDPR?',
        a: 'Da fornitori di dati professionali e da pagine pubbliche, sempre con la fonte. Ogni email di campagna permette di disiscriversi, e chi lo fa entra nelle esclusioni del progetto per sempre. Le esclusioni valgono per tutte le campagne del progetto. La base giuridica e l’informativa ex art. 14 GDPR restano a carico di chi invia: Zenbree non dà consulenza legale.',
      },
      {
        q: 'Quante email e messaggi posso inviare al giorno?',
        a: 'Per casella: 10 al giorno la prima settimana, 25 la seconda, poi fino a 50. Su LinkedIn: 20 inviti e 30 messaggi al giorno, al massimo 100 inviti a settimana. I limiti proteggono i tuoi account.',
      },
      {
        q: 'Posso tenere separati i miei clienti?',
        a: 'Sì. Ogni cliente è un progetto con le sue liste, esclusioni, caselle e LinkedIn, campagne e inbox. Le esclusioni di un progetto non toccano gli altri.',
      },
    ],
  },
  final: {
    title: 'Chi vuoi raggiungere?',
    lede: 'Scrivici e ti attiviamo l’accesso.',
    button: 'Richiedi l’accesso',
  },
  footer: {
    legalLabel: 'Link legali',
    terms: 'Termini',
    privacy: 'Privacy',
    signIn: 'Accedi',
    langLabel: 'Lingua',
  },
};
