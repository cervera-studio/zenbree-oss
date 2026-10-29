import { type ClassValue, clsx } from 'clsx';
import { extendTailwindMerge } from 'tailwind-merge';

/**
 * tailwind-merge only dedupes classes it can classify, and it classifies by
 * matching against Tailwind's *stock* scales. The text sizes and styles, the
 * spacing names, the shadows, the easings and the tracking step we add in `app/theme.css`
 * are not in those scales, so they have to be registered here or `cn()`
 * silently stops merging them.
 *
 * That silence is the dangerous part, and `Eyebrow` is where it bites: the atom
 * relies on tailwind-merge keeping the LAST class of a conflicting group to make
 * its typography unoverridable (see the comment in `components/ui/eyebrow.tsx`).
 * An unregistered `tracking-label` does not conflict with the atom's
 * `tracking-wide`, so both survive into the DOM and stylesheet order picks the
 * winner — the lock looks like it holds and doesn't.
 *
 * Verified against tailwind-merge 3.5:
 *   unregistered  `tracking-wide tracking-label` → "tracking-wide tracking-label"
 *   registered    `tracking-wide tracking-label` → "tracking-label"
 *
 * `font-size` is listed for the same reason even though the stock heuristic
 * happens to classify `text-2xs` correctly today — that is luck, not contract,
 * and it is the kind of luck that changes in a minor release.
 */
const twMerge = extendTailwindMerge({
  extend: {
    theme: {
      spacing: [
        'section',
        'control-sm',
        'control',
        'control-md',
        'control-lg',
        'row',
        'row-head',
        'topbar',
        'sidebar',
      ],
    },
    classGroups: {
      'font-size': [
        {
          text: [
            '3xs',
            '2xs',
            'xs-plus',
            'sm-plus',
            'lede',
            'display-sm',
            'detail-title',
            'display',
            'headline',
            'title',
            'feature',
            'lead',
            'body',
            'ui',
            'ui-strong',
            'small',
            'label',
            'metric',
            'kpi',
            'data',
            'data-sm',
            'eyebrow',
          ],
        },
      ],
      duration: [{ duration: ['press', 'in', 'out'] }],
      ease: [{ ease: ['snap', 'exit', 'spring'] }],
      'max-h': [{ 'max-h': ['dropdown-available', 'popover-available'] }],
      leading: [{ leading: ['display'] }],
      shadow: [{ shadow: ['tab-rule', 'float', 'frame', 'snippet'] }],
      tracking: [{ tracking: ['label'] }],
    },
  },
});

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
