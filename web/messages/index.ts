// One file per workstream so parallel branches don't fight over one JSON.
import type { Locale } from '../i18n/config';
import accountEn from './account.en.json';
import accountEs from './account.es.json';
import accountFr from './account.fr.json';
import accountIt from './account.it.json';
import adminEn from './admin.en.json';
import adminEs from './admin.es.json';
import adminFr from './admin.fr.json';
import adminIt from './admin.it.json';
import automationEditorEn from './automation-editor.en.json';
import automationEditorEs from './automation-editor.es.json';
import automationEditorFr from './automation-editor.fr.json';
import automationEditorIt from './automation-editor.it.json';
import automationsEn from './automations.en.json';
import automationsEs from './automations.es.json';
import automationsFr from './automations.fr.json';
import automationsIt from './automations.it.json';
import campaignBuilderEn from './campaign-builder.en.json';
import campaignBuilderEs from './campaign-builder.es.json';
import campaignBuilderFr from './campaign-builder.fr.json';
import campaignBuilderIt from './campaign-builder.it.json';
import campaignsEn from './campaigns.en.json';
import campaignsEs from './campaigns.es.json';
import campaignsFr from './campaigns.fr.json';
import campaignsIt from './campaigns.it.json';
import en from './en.json';
import errorsEn from './errors.en.json';
import errorsEs from './errors.es.json';
import errorsFr from './errors.fr.json';
import errorsIt from './errors.it.json';
import es from './es.json';
import fr from './fr.json';
import inboxEn from './inbox.en.json';
import inboxEs from './inbox.es.json';
import inboxFr from './inbox.fr.json';
import inboxIt from './inbox.it.json';
import it from './it.json';
import listsEn from './lists.en.json';
import listsEs from './lists.es.json';
import listsFr from './lists.fr.json';
import listsIt from './lists.it.json';
import notificationsEn from './notifications.en.json';
import notificationsEs from './notifications.es.json';
import notificationsFr from './notifications.fr.json';
import notificationsIt from './notifications.it.json';
import onboardingEn from './onboarding.en.json';
import onboardingEs from './onboarding.es.json';
import onboardingFr from './onboarding.fr.json';
import onboardingIt from './onboarding.it.json';
import publicEn from './public.en.json';
import publicEs from './public.es.json';
import publicFr from './public.fr.json';
import publicIt from './public.it.json';
import reportsEn from './reports.en.json';
import reportsEs from './reports.es.json';
import reportsFr from './reports.fr.json';
import reportsIt from './reports.it.json';
import segmentsEn from './segments.en.json';
import segmentsEs from './segments.es.json';
import segmentsFr from './segments.fr.json';
import segmentsIt from './segments.it.json';
import settingsEn from './settings.en.json';
import settingsEs from './settings.es.json';
import settingsFr from './settings.fr.json';
import settingsIt from './settings.it.json';
import systemEn from './system.en.json';
import systemEs from './system.es.json';
import systemFr from './system.fr.json';
import systemIt from './system.it.json';
import teamEn from './team.en.json';
import teamEs from './team.es.json';
import teamFr from './team.fr.json';
import teamIt from './team.it.json';

const CATALOGS = {
  it: {
    ...it,
    ...errorsIt,
    ...adminIt,
    ...accountIt,
    ...onboardingIt,
    ...campaignsIt,
    ...campaignBuilderIt,
    ...automationsIt,
    ...automationEditorIt,
    ...inboxIt,
    ...publicIt,
    ...systemIt,
    ...settingsIt,
    ...notificationsIt,
    ...teamIt,
    ...listsIt,
    ...reportsIt,
    ...segmentsIt,
  },
  en: {
    ...en,
    ...errorsEn,
    ...adminEn,
    ...accountEn,
    ...onboardingEn,
    ...campaignsEn,
    ...campaignBuilderEn,
    ...automationsEn,
    ...automationEditorEn,
    ...inboxEn,
    ...publicEn,
    ...systemEn,
    ...settingsEn,
    ...notificationsEn,
    ...teamEn,
    ...listsEn,
    ...reportsEn,
    ...segmentsEn,
  },
  es: {
    ...es,
    ...errorsEs,
    ...adminEs,
    ...accountEs,
    ...onboardingEs,
    ...campaignsEs,
    ...campaignBuilderEs,
    ...automationsEs,
    ...automationEditorEs,
    ...inboxEs,
    ...publicEs,
    ...systemEs,
    ...settingsEs,
    ...notificationsEs,
    ...teamEs,
    ...listsEs,
    ...reportsEs,
    ...segmentsEs,
  },
  fr: {
    ...fr,
    ...errorsFr,
    ...adminFr,
    ...accountFr,
    ...onboardingFr,
    ...campaignsFr,
    ...campaignBuilderFr,
    ...automationsFr,
    ...automationEditorFr,
    ...inboxFr,
    ...publicFr,
    ...systemFr,
    ...settingsFr,
    ...notificationsFr,
    ...teamFr,
    ...listsFr,
    ...reportsFr,
    ...segmentsFr,
  },
} satisfies Record<Locale, object>;

// The JSON literals make a message argument typed (`{n}` takes a string), and `en`, the base language,
// is the type that call sites check against. Every other language has other literal strings, so it is
// seen through the `en` type; `messages.test.ts` keeps its keys and arguments the same as `en`.
export const MESSAGES = CATALOGS as unknown as Record<Locale, typeof CATALOGS.en>;
