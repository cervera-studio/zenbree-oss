import type { Locale } from '@/i18n/config';

// The words of the emails the web sends (verification, password reset, email change, team invitation),
// one entry per interface language. `email-layout.ts` lays them out.

export const FALLBACK: Record<Locale, string> = {
  en: "If the button doesn't work, copy this address into your browser:",
  it: 'Se il pulsante non va, copia questo indirizzo nel browser:',
  es: 'Si el botón no funciona, copia esta dirección en tu navegador:',
  fr: 'Si le bouton ne fonctionne pas, copiez cette adresse dans votre navigateur :',
};

interface Copy {
  securityReason: string;
  verify: {
    subject: string;
    preheader: string;
    title: string;
    intro: (first: string | undefined, to: string) => string;
    note: string;
    button: string;
  };
  reset: {
    subject: string;
    preheader: string;
    title: string;
    paragraphs: (to: string) => string[];
    button: string;
  };
  changeEmail: {
    subject: string;
    preheader: string;
    title: string;
    paragraphs: (to: string, next: string) => string[];
    button: string;
  };
  invite: {
    subject: string;
    preheader: (team: string) => string;
    title: (team: string) => string;
    paragraphs: (inviter: string, team: string) => string[];
    button: string;
    reason: string;
    team: (name: string) => string;
    anonymousTeam: string;
  };
}

export const COPY: Record<Locale, Copy> = {
  it: {
    securityReason:
      "Ricevi questa email perché qualcuno l'ha richiesta per il tuo account. Non possiamo disattivarla: riguarda la sicurezza.",
    verify: {
      subject: 'Verifica la tua email',
      preheader: "Un clic per attivare l'account e partire con la prima ricerca.",
      title: 'Conferma il tuo indirizzo email',
      intro: (first, to) =>
        `${first ? `Ciao ${first}, manca` : 'Manca'} un passaggio: conferma che ${to} è tuo e l'account è attivo.`,
      note: "Il link vale 1 ora. Non ti sei registrato tu? Ignora questa email: senza verifica l'account non si attiva.",
      button: 'Verifica email',
    },
    reset: {
      subject: 'Reimposta la password',
      preheader: 'Hai chiesto di cambiare la password. Il link vale 1 ora.',
      title: 'Reimposta la password',
      paragraphs: (to) => [
        `Abbiamo ricevuto una richiesta per cambiare la password di ${to}.`,
        'Il link vale 1 ora e funziona una volta sola. Dopo il cambio, gli altri dispositivi vengono disconnessi.',
        'Non sei stato tu? Non serve fare nulla: la password resta quella di prima.',
      ],
      button: 'Scegli una nuova password',
    },
    changeEmail: {
      subject: "Conferma il cambio dell'email",
      preheader: "Hai chiesto di cambiare l'email dell'account. Il link vale 1 ora.",
      title: "Conferma il cambio dell'email",
      paragraphs: (to, next) => [
        `Abbiamo ricevuto una richiesta per cambiare l'email di ${to} in ${next}.`,
        'Se confermi, ti scriviamo al nuovo indirizzo per verificarlo: il cambio vale solo dopo quella verifica.',
        "Non sei stato tu? Non serve fare nulla: l'email resta quella di prima.",
      ],
      button: 'Conferma il cambio',
    },
    invite: {
      subject: 'Sei stato invitato in un team su Zenbree',
      preheader: (team) => `Entra in ${team}: liste, campagne e inbox condivise.`,
      title: (team) => `Unisciti a ${team} su Zenbree`,
      paragraphs: (inviter, team) => [
        `${inviter} ti ha invitato in ${team} su Zenbree.`,
        "L'invito vale 48 ore. Accedi o crea un account con questo indirizzo, poi accetta o rifiuta. Non conosci chi ti invita? Ignora questa email.",
      ],
      button: "Apri l'invito",
      reason:
        'Ricevi questa email perché un amministratore di un team Zenbree ha invitato questo indirizzo.',
      team: (name) => `il team ${name}`,
      anonymousTeam: 'un team',
    },
  },
  en: {
    securityReason:
      'You get this email because someone requested it for your account. It cannot be turned off: it is about security.',
    verify: {
      subject: 'Verify your email',
      preheader: 'One click to activate your account and start your first search.',
      title: 'Confirm your email address',
      intro: (first, to) =>
        `${first ? `Hi ${first}, one` : 'One'} step left: confirm that ${to} is yours to activate your account.`,
      note: "The link is valid for 1 hour. Didn't sign up? Ignore this email: without verification the account stays inactive.",
      button: 'Verify email',
    },
    reset: {
      subject: 'Reset your password',
      preheader: 'You asked to change your password. The link is valid for 1 hour.',
      title: 'Reset your password',
      paragraphs: (to) => [
        `We received a request to change the password for ${to}.`,
        'The link is valid for 1 hour and works once. After the change, your other devices are signed out.',
        "Didn't ask for this? You don't need to do anything: your password stays the same.",
      ],
      button: 'Choose a new password',
    },
    changeEmail: {
      subject: 'Confirm your email change',
      preheader: 'You asked to change your account email. The link is valid for 1 hour.',
      title: 'Confirm your email change',
      paragraphs: (to, next) => [
        `We received a request to change the email of ${to} to ${next}.`,
        'If you confirm, we write to the new address to verify it: the change only takes effect after that.',
        "Didn't ask for this? You don't need to do anything: your email stays the same.",
      ],
      button: 'Confirm the change',
    },
    invite: {
      subject: "You've been invited to a team on Zenbree",
      preheader: (team) => `Join ${team}: shared lists, campaigns and inbox.`,
      title: (team) => `Join ${team} on Zenbree`,
      paragraphs: (inviter, team) => [
        `${inviter} invited you to ${team} on Zenbree.`,
        "The invitation is valid for 48 hours. Sign in or create an account with this address, then accept or decline. Don't know who invited you? Ignore this email.",
      ],
      button: 'Open the invitation',
      reason: 'You get this email because an admin of a Zenbree team invited this address.',
      team: (name) => `the ${name} team`,
      anonymousTeam: 'a team',
    },
  },
  es: {
    securityReason:
      'Recibes este email porque alguien lo ha solicitado para tu cuenta. No podemos desactivarlo: se trata de seguridad.',
    verify: {
      subject: 'Verifica tu email',
      preheader: 'Un clic para activar tu cuenta y empezar tu primera búsqueda.',
      title: 'Confirma tu dirección de email',
      intro: (first, to) =>
        `${first ? `Hola, ${first}: falta` : 'Falta'} un paso: confirma que ${to} es tuyo para activar tu cuenta.`,
      note: 'El enlace vale 1 hora. ¿No te has registrado tú? Ignora este email: sin verificación la cuenta no se activa.',
      button: 'Verificar email',
    },
    reset: {
      subject: 'Restablece tu contraseña',
      preheader: 'Has pedido cambiar tu contraseña. El enlace vale 1 hora.',
      title: 'Restablece tu contraseña',
      paragraphs: (to) => [
        `Hemos recibido una solicitud para cambiar la contraseña de ${to}.`,
        'El enlace vale 1 hora y solo funciona una vez. Después del cambio, se cierra la sesión en tus otros dispositivos.',
        '¿No has sido tú? No tienes que hacer nada: tu contraseña sigue siendo la misma.',
      ],
      button: 'Elegir una nueva contraseña',
    },
    changeEmail: {
      subject: 'Confirma el cambio de email',
      preheader: 'Has pedido cambiar el email de tu cuenta. El enlace vale 1 hora.',
      title: 'Confirma el cambio de email',
      paragraphs: (to, next) => [
        `Hemos recibido una solicitud para cambiar el email de ${to} a ${next}.`,
        'Si confirmas, escribimos a la nueva dirección para verificarla: el cambio solo vale después de esa verificación.',
        '¿No has sido tú? No tienes que hacer nada: tu email sigue siendo el mismo.',
      ],
      button: 'Confirmar el cambio',
    },
    invite: {
      subject: 'Te han invitado a un equipo en Zenbree',
      preheader: (team) => `Únete a ${team}: listas, campañas y bandeja de entrada compartidas.`,
      title: (team) => `Únete a ${team} en Zenbree`,
      paragraphs: (inviter, team) => [
        `${inviter} te ha invitado a ${team} en Zenbree.`,
        'La invitación vale 48 horas. Inicia sesión o crea una cuenta con esta dirección y después acepta o rechaza. ¿No conoces a quien te invita? Ignora este email.',
      ],
      button: 'Abrir la invitación',
      reason:
        'Recibes este email porque un administrador de un equipo de Zenbree ha invitado a esta dirección.',
      team: (name) => `el equipo ${name}`,
      anonymousTeam: 'un equipo',
    },
  },
  fr: {
    securityReason:
      'Vous recevez cet e-mail parce que quelqu’un l’a demandé pour votre compte. Il ne peut pas être désactivé : il concerne la sécurité.',
    verify: {
      subject: 'Vérifiez votre e-mail',
      preheader: 'Un clic pour activer votre compte et lancer votre première recherche.',
      title: 'Confirmez votre adresse e-mail',
      intro: (first, to) =>
        `${first ? `Bonjour ${first}, il reste` : 'Il reste'} une étape : confirmez que ${to} est bien à vous pour activer votre compte.`,
      note: 'Le lien est valable 1 heure. Vous ne vous êtes pas inscrit ? Ignorez cet e-mail : sans vérification, le compte reste inactif.',
      button: 'Vérifier l’e-mail',
    },
    reset: {
      subject: 'Réinitialisez votre mot de passe',
      preheader: 'Vous avez demandé à changer votre mot de passe. Le lien est valable 1 heure.',
      title: 'Réinitialisez votre mot de passe',
      paragraphs: (to) => [
        `Nous avons reçu une demande de changement du mot de passe de ${to}.`,
        'Le lien est valable 1 heure et ne fonctionne qu’une fois. Après le changement, vos autres appareils sont déconnectés.',
        'Ce n’était pas vous ? Vous n’avez rien à faire : votre mot de passe reste le même.',
      ],
      button: 'Choisir un nouveau mot de passe',
    },
    changeEmail: {
      subject: 'Confirmez le changement d’e-mail',
      preheader:
        'Vous avez demandé à changer l’e-mail de votre compte. Le lien est valable 1 heure.',
      title: 'Confirmez le changement d’e-mail',
      paragraphs: (to, next) => [
        `Nous avons reçu une demande de changement de l’e-mail de ${to} en ${next}.`,
        'Si vous confirmez, nous écrivons à la nouvelle adresse pour la vérifier : le changement ne prend effet qu’après cette vérification.',
        'Ce n’était pas vous ? Vous n’avez rien à faire : votre e-mail reste le même.',
      ],
      button: 'Confirmer le changement',
    },
    invite: {
      subject: 'Vous êtes invité dans une équipe sur Zenbree',
      preheader: (team) => `Rejoignez ${team} : listes, campagnes et boîte de réception partagées.`,
      title: (team) => `Rejoignez ${team} sur Zenbree`,
      paragraphs: (inviter, team) => [
        `${inviter} vous a invité dans ${team} sur Zenbree.`,
        'L’invitation est valable 48 heures. Connectez-vous ou créez un compte avec cette adresse, puis acceptez ou refusez. Vous ne connaissez pas la personne qui vous invite ? Ignorez cet e-mail.',
      ],
      button: 'Ouvrir l’invitation',
      reason:
        'Vous recevez cet e-mail parce qu’un administrateur d’une équipe Zenbree a invité cette adresse.',
      team: (name) => `l’équipe ${name}`,
      anonymousTeam: 'une équipe',
    },
  },
};
