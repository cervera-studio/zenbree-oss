import type { Content } from '../content';

export const fr: Content = {
  sections: { how: 'fonctionnement', why: 'pourquoi', openSource: 'open-source', faq: 'faq' },
  meta: {
    title: 'Zenbree · Agent IA pour la prospection B2B',
    description:
      'Dites à l’agent qui vous voulez atteindre. L’agent vous montre le plan et vous l’approuvez. Il vous livre ensuite des personnes avec un e-mail contrôlé par un vérificateur et la source de chaque donnée.',
  },
  nav: {
    homeLabel: 'Zenbree, accueil',
    label: 'Sections',
    how: 'Fonctionnement',
    why: 'Pourquoi',
    openSource: 'Open source',
    faq: 'FAQ',
    signIn: 'Se connecter',
    contact: 'Nous contacter',
  },
  menu: {
    open: 'Ouvrir le menu',
    close: 'Fermer le menu',
    themeLabel: 'Thème',
    dark: 'Sombre',
  },
  hero: {
    eyebrow: 'Agent IA pour la prospection B2B',
    titleLine: 'Décrivez votre client idéal.',
    titleEm: 'Zenbree trouve qui contacter.',
    cta: 'Demander l’accès',
    seeHow: 'Voir le fonctionnement',
    fine: 'Open source · Auto-hébergé ou service géré sur demande',
  },
  stage: {
    label: 'Aperçu du produit',
    emailFloat: {
      key: 'E-mail vérifié',
      value: 'giulia@officinanove.it',
      source: 'Source : officinanove.it/team',
    },
    refundFloat: {
      key: '+6 crédits restitués',
      value: '3 lignes non livrées',
      source: 'Pas d’e-mail, pas de débit',
    },
    brief:
      'Agences de marketing indépendantes à Milan, 10 à 50 salariés. Je veux le CEO, avec un e-mail contrôlé.',
    planTitle: 'Plan · 60 lignes',
    planBudget: 'jusqu’à 120 crédits',
    columns: ['Nom *', 'Fonction *', 'E-mail contrôlé *'],
    aiColumn: 'Secteur · IA',
    planFooter: 'Vous ne payez que les lignes avec les champs *',
    rowsSummary: '57 livrées · 31 entreprises',
  },
  ui: {
    verified: '✓ vérifié',
    people: 'Personnes',
    approveStart: 'Approuver et lancer',
    toApprove: 'À approuver',
    skip: 'Ignorer',
    approveSend: 'Approuver et envoyer',
    to: 'À :',
    subject: 'Objet :',
  },
  people: [
    { initials: 'GF', name: 'Giulia Ferri', role: 'CEO', company: 'Officina Nove', score: 92 },
    {
      initials: 'MB',
      name: 'Marco Bellini',
      role: 'Directeur commercial',
      company: 'Studio Brezza',
      score: 88,
    },
    {
      initials: 'CG',
      name: 'Chiara Gallo',
      role: 'Fondatrice et CEO',
      company: 'Lanterna Digital',
      score: 85,
    },
    { initials: 'EM', name: 'Elena Moretti', role: 'CEO', company: 'Ponte Creativo', score: 81 },
  ],
  works: {
    label: 'Fonctionne avec ce que vous utilisez déjà',
    items: [
      { name: 'Gmail', logo: 'gmail' },
      { name: 'Outlook', logo: 'outlook' },
      { name: 'LinkedIn', logo: 'linkedin' },
      { name: 'CSV et Excel', logo: 'excel' },
      { name: 'API', logo: 'api' },
      { name: 'HubSpot · bientôt', logo: 'hubspot', soon: true },
    ],
  },
  statement: {
    line: 'Une bonne liste n’est pas une liste plus longue.',
    em: 'Elle est plus courte, et chaque ligne a une source que vous pouvez ouvrir.',
  },
  how: {
    eyebrow: 'Fonctionnement',
    titleLine: 'Du brief à la première réponse,',
    titleRest: 'sans perdre la main.',
    tablistLabel: 'Étapes',
    steps: [
      {
        n: '01',
        title: 'Décrivez',
        short: 'Une phrase, avec vos mots',
        body: 'Secteur, zone, taille, fonctions, signaux comme « recrute ». Écrivez-le comme vous le diriez à un collègue : s’il manque quelque chose, l’agent demande avant de chercher.',
        points: [
          'Une question à la fois, seulement quand il le faut',
          'Retient le profil de votre entreprise et ce que vous lui demandez',
          'Ignore ce que vous avez exclu : e-mails, domaines, entreprises',
        ],
        art: {
          kind: 'brief',
          brief: 'Responsables achats dans les chaînes de supermarchés du nord-est de l’Italie',
          question:
            'Avant de chercher : voulez-vous seulement les chaînes, ou aussi les grossistes ?',
          choices: ['Seulement les chaînes', 'Les grossistes aussi'],
        },
      },
      {
        n: '02',
        title: 'Approuvez le plan',
        short: 'Coût maximal, annoncé d’avance',
        body: 'Vous voyez le plan, avec les lignes et les colonnes, et vous l’approuvez. Les colonnes marquées d’un astérisque décident de ce qui est livré.',
        points: [
          'Rien ne démarre sans votre accord',
          'Vous voyez lignes et colonnes avant le départ',
          'Les lignes se règlent dans le plan ; colonnes et étapes se demandent à l’agent',
        ],
        art: {
          kind: 'plan',
          title: 'Plan · 80 lignes',
          budget: 'jusqu’à 160 crédits',
          steps: [
            '1 · Trouver 40 chaînes dans le nord-est de l’Italie',
            '2 · 2 responsables achats par chaîne',
            '3 · Vérifier les e-mails et une colonne IA',
          ],
          note: 'Rien ne démarre sans votre accord',
        },
      },
      {
        n: '03',
        title: 'Recevez la liste',
        short: 'Personnes et entreprises',
        body: 'Deux listes liées, personnes et entreprises, avec un e-mail contrôlé par un vérificateur et un score d’adéquation pour chacune. Chaque donnée recherchée a sa source.',
        points: [
          'Les premières lignes arrivent pendant que l’agent travaille',
          'Les lignes incomplètes ne sont pas livrées',
          'Export en CSV ou Excel quand vous voulez',
        ],
        art: {
          kind: 'list',
          delivered: '74 livrées sur 80',
          people: [
            {
              initials: 'MC',
              name: 'Marta Colombo',
              role: 'Acheteuse produits frais',
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
            title: 'Pourquoi ça correspond',
            quote: '« Gère le fournisseur local depuis 2023 » · source : superest.it/news',
          },
        },
      },
      {
        n: '04',
        title: 'Contactez',
        short: 'E-mail et LinkedIn',
        body: 'Des séquences de 12 étapes au plus, envoyées depuis vos boîtes mail et votre LinkedIn. Vous approuvez chaque message, quiconque répond quitte la séquence, et la réponse arrive déjà étiquetée.',
        points: [
          'Limite quotidienne qui augmente pendant les deux premières semaines',
          'Arrêt automatique en cas de réponse ou de désinscription',
          'Brouillon de réponse prêt, sauf pour un « non »',
        ],
        art: {
          kind: 'mail',
          queue: '3 sur 12',
          to: 'Marta Colombo',
          subject: 'Fournisseurs locaux de produits frais',
          body: 'Bonjour Marta, j’ai lu que vous misez davantage cette année sur les producteurs locaux…',
        },
      },
    ],
  },
  why: {
    eyebrow: 'Pourquoi Zenbree',
    titleLine: 'Conçu pour ceux qui vendent,',
    titleRest: 'pas pour ceux qui achètent des listes.',
    source: {
      title: 'Chaque donnée a sa source',
      body: 'Les colonnes IA ne gardent une valeur que si elle est écrite sur une page que vous pouvez ouvrir.',
      cellValue: 'Salariés : 18',
      cellSource: '↗ linkedin.com/company/officina-nove',
    },
    approve: {
      title: 'Vous approuvez chaque message',
      body: 'Les campagnes partent de vos propres boîtes mail, avec une limite quotidienne qui augmente progressivement. Rien n’est envoyé sans votre accord.',
      caption: 'de 10 à 50 envois par jour, par boîte mail',
    },
    replies: {
      title: 'Des réponses déjà triées',
      body: 'Quiconque répond quitte la séquence. L’agent étiquette la réponse et prépare le brouillon.',
      labels: [
        { label: 'Intéressé', tone: 'pos' },
        { label: 'Demande de rendez-vous', tone: 'info' },
        { label: 'Pas maintenant' },
        { label: 'Pas intéressé' },
        { label: 'Désinscrit', tone: 'neg' },
      ],
    },
    market: {
      title: 'Entreprises et personnes du monde entier',
      body: 'Cherchez dans n’importe quel pays. L’app et l’agent parlent français, anglais, italien et espagnol, dans la langue que vous choisissez, et les brouillons de réponse de la boîte de réception suivent la langue de la personne qui vous écrit. Les exclusions (clients, concurrents, toute personne désinscrite) sont définitives.',
    },
  },
  openSource: {
    eyebrow: 'Open source',
    title: 'Open source. Hébergez-le vous-même, ou on s’en occupe.',
    sub: 'Zenbree est sous licence MIT. Clonez-le, utilisez vos propres clés de fournisseurs et adaptez-le à votre façon de travailler.',
    selfHostTitle: 'Auto-hébergement',
    selfHostBody: 'Docker Compose lance toute la pile : API, worker, web et base de données.',
    repoCta: 'Voir sur GitHub',
    hostedTitle: 'Hébergé par nous',
    hostedBody:
      'Vous ne voulez pas gérer serveurs et comptes fournisseurs ? Nous proposons un service hébergé payant. Écrivez-nous et nous l’activons.',
    hostedCta: 'Nous écrire',
  },
  faq: {
    eyebrow: 'FAQ',
    title: 'Ce qu’on demande avant de commencer.',
    moreBefore: 'Autre chose ? Écrivez-nous à ',
    moreAfter: '.',
    items: [
      {
        q: 'Et si un e-mail est introuvable ?',
        a: 'Si l’e-mail est obligatoire dans le plan, cette ligne n’est pas livrée. Nous signalons les e-mails « catch-all » comme étant à vérifier.',
      },
      {
        q: 'Dois-je connecter LinkedIn ou ma messagerie ?',
        a: 'Non, vous n’avez rien à connecter pour créer des listes. Les boîtes mail et LinkedIn ne servent qu’à envoyer des campagnes depuis vos propres comptes.',
      },
      {
        q: 'Est-ce que cela fonctionne hors d’Italie ?',
        a: 'Oui. Vous cherchez des entreprises et des personnes dans n’importe quel pays, toujours avec la source de chaque donnée, même si la couverture des fournisseurs de données varie d’un pays à l’autre. Le site, l’app et l’agent parlent français, anglais, italien et espagnol, dans la langue que vous choisissez dans l’app.',
      },
      {
        q: 'Puis-je importer une liste que j’ai déjà ?',
        a: 'Oui : importez un CSV et l’agent le complète avec des e-mails, des numéros de téléphone et des colonnes de recherche. Les lignes présentes dans vos exclusions sont écartées.',
      },
      {
        q: 'D’où viennent les données, et est-ce conforme au RGPD ?',
        a: 'De fournisseurs de données professionnelles et de pages publiques, toujours avec la source. Chaque e-mail de campagne permet de se désinscrire, et toute personne qui le fait entre définitivement dans les exclusions du projet. Les exclusions s’appliquent à toutes les campagnes du projet. La base légale et l’information de l’art. 14 du RGPD relèvent de l’expéditeur : Zenbree ne donne pas de conseil juridique.',
      },
      {
        q: 'Combien d’e-mails et de messages puis-je envoyer par jour ?',
        a: 'Par boîte mail : 10 par jour la première semaine, 25 la deuxième, puis jusqu’à 50. Sur LinkedIn : 20 invitations et 30 messages par jour, 100 invitations par semaine au maximum. Ces limites protègent vos comptes.',
      },
      {
        q: 'Puis-je garder mes clients séparés ?',
        a: 'Oui. Chaque client est un projet avec ses listes, exclusions, boîtes mail et LinkedIn, campagnes et boîte de réception. Les exclusions d’un projet ne touchent pas les autres.',
      },
    ],
  },
  final: {
    title: 'Qui voulez-vous atteindre ?',
    lede: 'Écrivez-nous et nous activons votre accès.',
    button: 'Demander l’accès',
  },
  footer: {
    legalLabel: 'Liens légaux',
    terms: 'Conditions',
    privacy: 'Confidentialité',
    signIn: 'Se connecter',
    langLabel: 'Langue',
  },
};
