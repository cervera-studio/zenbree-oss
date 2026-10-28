const button =
  'inline-flex cursor-pointer items-center justify-center gap-1.5 rounded-md border whitespace-nowrap transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent motion-reduce:transition-none';

const buttonVariants = {
  primary: 'border-transparent bg-accent text-on-accent hover:bg-accent/90',
  outline: 'border-line bg-ground text-ink hover:bg-raised',
  ghost: 'border-transparent text-soft hover:text-ink',
} as const;

const buttonSizes = {
  sm: 'h-6.5 px-2 text-ui-strong',
  default: 'h-7.5 px-2.5 text-ui-strong',
  md: 'h-9 px-3.5 text-ui-strong',
  lg: 'h-11.5 px-4.5 text-button',
} as const;

export function btn(
  variant: keyof typeof buttonVariants,
  size: keyof typeof buttonSizes = 'default',
): string {
  return `${button} ${buttonVariants[variant]} ${buttonSizes[size]}`;
}

const tones = {
  positive: 'bg-positive-tint text-positive',
  accent: 'bg-accent-tint text-accent-ink',
  warning: 'bg-warning-tint text-warning',
  negative: 'bg-negative-tint text-negative',
  neutral: 'bg-hair text-soft',
} as const;

export type Tone = keyof typeof tones;

/** `wrap` lets a long tag break onto a second line instead of leaving a narrow card. */
export function tag(tone: Tone = 'neutral', wrap = false): string {
  const size = wrap ? 'min-h-4.5 py-0.5' : 'h-4.5 whitespace-nowrap';
  return `inline-flex ${size} items-center rounded-sm px-1.25 font-mono text-data-sm ${tones[tone]}`;
}

export function avatar(): string {
  return 'inline-flex size-6.5 shrink-0 items-center justify-center rounded-sm bg-accent-tint text-label text-accent-ink';
}

/** The mono roles: a text token cannot carry the family, case or figures. */
export const eyebrow = 'font-mono text-eyebrow uppercase tabular-nums';
export const data = 'font-mono text-data tabular-nums';
export const dataSm = 'font-mono text-data-sm tabular-nums';

export const wrap = 'mx-auto w-full max-w-360 px-25 tablet:px-8 phone:px-5';
export const section = `${wrap} py-26 tablet:py-20 phone:py-14`;
export const h2 = 'text-headline text-balance phone:text-title';
export const headRow = 'flex items-end gap-20 tablet:flex-col tablet:items-stretch tablet:gap-4';
export const headSide = 'max-w-110 flex-1 text-soft tablet:max-w-none';

/** A bordered strip whose 1px gaps show the line colour, so the dividers follow any column count. */
export const cells = 'grid gap-px overflow-hidden rounded-lg border border-line bg-line';
export const panel = 'flex flex-col rounded-lg border border-line bg-ground';
export const panelHead = 'flex h-11 items-center gap-2.5 border-b border-line px-3';
