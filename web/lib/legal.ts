// ponytail: Cervera Studio is the legal entity for now; add the registered address
// here when it should appear. Empty parts are left out of the pages.
const company = 'Cervera Studio';
const address = '';
const vatId = 'P.IVA 04904730167';

export const LEGAL = {
  entity: [company, address, vatId].filter(Boolean).join(', '),
  contactEmail: 'info@cervera.studio',
  updated: '2026-10-02',
};

export const GOOGLE_USER_DATA_POLICY =
  'https://developers.google.com/terms/api-services-user-data-policy';
