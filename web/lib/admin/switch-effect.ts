export type SwitchEffect =
  | 'lookup'
  | 'mailbox'
  | 'email'
  | 'linkedin'
  | 'unipile'
  | 'crm'
  | 'instantly'
  | 'other';

const LOOKUP = [
  'apollo',
  'hunter',
  'pdl',
  'treg',
  'aiark',
  'firecrawl',
  'serper',
  'exa',
  'millionverifier',
];
const VENDOR_EFFECT: Record<string, SwitchEffect> = {
  ...Object.fromEntries(LOOKUP.map((name) => [name, 'lookup'])),
  google: 'mailbox',
  microsoft: 'mailbox',
  unipile: 'unipile',
  hubspot: 'crm',
  instantly: 'instantly',
};
const CHANNEL_EFFECT: Record<string, SwitchEffect> = { email: 'email', linkedin: 'linkedin' };

/** What a switch holds back, which decides the warning before it is turned on. */
export function switchEffect(key: string): SwitchEffect {
  const [kind, name = ''] = key.split(':');
  const effects = kind === 'vendor_disabled' ? VENDOR_EFFECT : CHANNEL_EFFECT;
  const known =
    (kind === 'vendor_disabled' || kind === 'channel_disabled') && Object.hasOwn(effects, name);
  return (known && effects[name]) || 'other';
}
