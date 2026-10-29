import type { Content } from '../content';

export const en: Content = {
  meta: {
    title: 'Zenbree · AI agent for B2B outbound',
    description:
      'Tell the chat who you want to reach. The agent shows you the plan and you approve it. Then it delivers people with an email checked by a verifier and the source of every data point.',
  },
  sections: { how: 'how', why: 'why', openSource: 'open-source', faq: 'faq' },
  nav: {
    homeLabel: 'Zenbree, home',
    label: 'Sections',
    how: 'How it works',
    why: 'Why',
    openSource: 'Open source',
    faq: 'FAQ',
    signIn: 'Sign in',
    contact: 'Contact us',
  },
  menu: {
    open: 'Open menu',
    close: 'Close menu',
    themeLabel: 'Theme',
    dark: 'Dark',
  },
  hero: {
    eyebrow: 'AI agent for B2B outbound',
    titleLine: 'Describe your ideal customer.',
    titleEm: 'Zenbree finds who to contact.',
    cta: 'Request access',
    seeHow: 'See how it works',
    fine: 'Open source · Self-host or a managed service on request',
  },
  stage: {
    label: 'Product preview',
    emailFloat: {
      key: 'Verified email',
      value: 'giulia@officinanove.example',
      source: 'Source: officinanove.example/team',
    },
    fieldsFloat: {
      key: 'Required fields',
      value: 'Name, role, email',
      source: 'Complete rows only',
    },
    brief:
      'Independent marketing agencies in Milan, 10–50 employees. I want the CEO, with a checked email.',
    planTitle: 'Plan · 60 rows',
    columns: ['Name *', 'Role *', 'Checked email *'],
    aiColumn: 'Industry · AI',
    planFooter: 'Only rows with the * fields',
    rowsSummary: '57 delivered · 31 companies',
  },
  ui: {
    verified: '✓ verified',
    people: 'People',
    approveStart: 'Approve and start',
    toApprove: 'To approve',
    skip: 'Skip',
    approveSend: 'Approve and send',
    to: 'To:',
    subject: 'Subject:',
  },
  people: [
    { initials: 'GF', name: 'Giulia Ferri', role: 'CEO', company: 'Officina Nove', score: 92 },
    {
      initials: 'MB',
      name: 'Marco Bellini',
      role: 'Head of Sales',
      company: 'Studio Brezza',
      score: 88,
    },
    {
      initials: 'CG',
      name: 'Chiara Gallo',
      role: 'Founder and CEO',
      company: 'Lanterna Digital',
      score: 85,
    },
    { initials: 'EM', name: 'Elena Moretti', role: 'CEO', company: 'Ponte Creativo', score: 81 },
  ],
  works: {
    label: 'Works with what you already use',
    items: [
      { name: 'Gmail', logo: 'gmail' },
      { name: 'Outlook', logo: 'outlook' },
      { name: 'LinkedIn', logo: 'linkedin' },
      { name: 'CSV and Excel', logo: 'excel' },
      { name: 'API', logo: 'api' },
      { name: 'HubSpot · soon', logo: 'hubspot', soon: true },
    ],
  },
  statement: {
    line: 'A good list is not a longer one.',
    em: 'It is shorter, and every row has a source you can open.',
  },
  how: {
    eyebrow: 'How it works',
    titleLine: 'From brief to first reply,',
    titleRest: 'without losing control.',
    tablistLabel: 'Steps',
    steps: [
      {
        n: '01',
        title: 'Describe',
        short: 'One sentence, in your words',
        body: 'Industry, area, size, roles, signals like “is hiring”. Write it the way you would tell a colleague: if something is missing, the agent asks before it searches.',
        points: [
          'One question at a time, only when needed',
          'Remembers your company profile and what you ask it to',
          'Skips whoever you excluded: emails, domains, companies',
        ],
        art: {
          kind: 'brief',
          brief: 'Purchasing managers at supermarket chains in North-East Italy',
          question: 'Before I search: do you want only the chains, or wholesalers too?',
          choices: ['Chains only', 'Wholesalers too'],
        },
      },
      {
        n: '02',
        title: 'Approve the plan',
        short: 'Rows, columns and steps',
        body: 'You see the plan, with rows and columns, and approve it. The columns marked with an asterisk decide what gets delivered.',
        points: [
          'Nothing starts until you approve',
          'You see rows and columns before it starts',
          'You set the rows in the plan; ask the agent for other columns or steps',
        ],
        art: {
          kind: 'plan',
          title: 'Plan · 80 rows',
          steps: [
            '1 · Find 40 chains in North-East Italy',
            '2 · 2 purchasing managers per chain',
            '3 · Verify the emails and one AI column',
          ],
          note: 'Nothing starts until you approve',
        },
      },
      {
        n: '03',
        title: 'Get the list',
        short: 'People and companies',
        body: 'Two linked lists, people and companies, with an email checked by a verifier and a score for how well each one fits. Every researched data point has its source.',
        points: [
          'The first rows arrive while the agent works',
          'Incomplete rows are not delivered',
          'Export to CSV or Excel whenever you like',
        ],
        art: {
          kind: 'list',
          delivered: '74 of 80 delivered',
          people: [
            {
              initials: 'MC',
              name: 'Marta Colombo',
              role: 'Fresh produce buyer',
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
            title: 'Why it fits',
            quote: '“Has managed the local supplier since 2023” · source: superest.it/news',
          },
        },
      },
      {
        n: '04',
        title: 'Reach out',
        short: 'Email and LinkedIn',
        body: 'Sequences of up to 12 steps, sent from your mailboxes and your LinkedIn. You approve every message, anyone who replies leaves the sequence, and the reply arrives already labeled.',
        points: [
          'A daily limit that rises over the first two weeks',
          'Automatic stop on reply or unsubscribe',
          'A draft reply ready, except for a “no”',
        ],
        art: {
          kind: 'mail',
          queue: '3 of 12',
          to: 'Marta Colombo',
          subject: 'Local suppliers for fresh produce',
          body: 'Hi Marta, I read that this year you are focusing more on local producers…',
        },
      },
    ],
  },
  why: {
    eyebrow: 'Why Zenbree',
    titleLine: 'Find the right people,',
    titleRest: 'with the data to contact them.',
    source: {
      title: 'Every data point has a source',
      body: 'AI columns keep a value only if it is written on a page you can open.',
      cellValue: 'Employees: 18',
      cellSource: '↗ linkedin.com/company/officina-nove',
    },
    approve: {
      title: 'You approve every message',
      body: 'Campaigns go out from your own mailboxes, with a daily limit that rises gradually. Nothing is sent without your OK.',
      caption: 'from 10 to 50 sends a day, per mailbox',
    },
    replies: {
      title: 'Replies already sorted',
      body: 'Anyone who replies leaves the sequence. The agent labels the reply and prepares the draft.',
      labels: [
        { label: 'Interested', tone: 'pos' },
        { label: 'Meeting request', tone: 'info' },
        { label: 'Not now' },
        { label: 'Not interested' },
        { label: 'Unsubscribed', tone: 'neg' },
      ],
    },
    market: {
      title: 'Companies and people worldwide',
      body: 'Search in any country. The app and the agent speak English, Italian, Spanish and French, in the language you pick, and reply drafts in the inbox follow the language of whoever writes to you. Exclusions (customers, competitors, anyone who unsubscribed) last forever.',
    },
  },
  openSource: {
    eyebrow: 'Open source',
    title: 'Open source. Run it yourself, or let us run it.',
    sub: 'Zenbree is MIT licensed. Clone it, bring your own provider keys and adapt it to your workflow.',
    selfHostTitle: 'Self-host',
    selfHostBody: 'Docker Compose brings up the whole stack: API, worker, web and database.',
    repoCta: 'View on GitHub',
    hostedTitle: 'Hosted by us',
    hostedBody:
      'Prefer not to run servers and provider accounts? We offer a hosted service. Write to us and we will set it up.',
    hostedCta: 'Contact us',
  },
  faq: {
    eyebrow: 'FAQ',
    title: 'What people ask before they start.',
    moreBefore: 'Anything else? Write to us at ',
    moreAfter: '.',
    items: [
      {
        q: 'What if an email cannot be found?',
        a: 'If the email is required in the plan, that row is not delivered. We flag “catch-all” emails as needing verification.',
      },
      {
        q: 'Do I have to connect LinkedIn or my email?',
        a: 'No, you do not need to connect anything to build lists. Mailboxes and LinkedIn are only needed to send campaigns from your own accounts.',
      },
      {
        q: 'Does it work outside Italy?',
        a: 'Yes. You search for companies and people in any country, always with the source of every data point, though data provider coverage varies from country to country. The site, the app and the agent speak English, Italian, Spanish and French, in the language you pick in the app.',
      },
      {
        q: 'Can I upload a list I already have?',
        a: 'Yes: upload a CSV and the agent completes it with emails, phone numbers and research columns. Rows in your exclusions are discarded.',
      },
      {
        q: 'Where does the data come from, and is it GDPR compliant?',
        a: 'From professional data providers and public pages, always with the source. Every campaign email lets people unsubscribe, and anyone who does goes into the project’s exclusions forever. Exclusions apply to every campaign of the project. The legal basis and the GDPR art. 14 notice stay with the sender: Zenbree gives no legal advice.',
      },
      {
        q: 'How many emails and messages can I send a day?',
        a: 'Per mailbox: 10 a day in the first week, 25 in the second, then up to 50. On LinkedIn: 20 invitations and 30 messages a day, at most 100 invitations a week. The limits protect your accounts.',
      },
      {
        q: 'Can I keep my clients separate?',
        a: 'Yes. Each client is a project with its own lists, exclusions, mailboxes and LinkedIn, campaigns and inbox. One project’s exclusions do not touch the others.',
      },
    ],
  },
  final: {
    title: 'Who do you want to reach?',
    lede: 'Write to us and we will set it up for you.',
    button: 'Request access',
  },
  footer: {
    legalLabel: 'Legal links',
    terms: 'Terms',
    privacy: 'Privacy',
    signIn: 'Sign in',
    langLabel: 'Language',
  },
};
