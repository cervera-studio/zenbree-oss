// The Oxlint plugin for the rules this folder owns (`.oxlintrc.json` loads it through jsPlugins).
// Rule names are the names of the Grit rules they replaced.
import dateTimeZone from './date-time-zone.mjs';
import resetAllState from './reset-all-state.mjs';
import tailwindScale from './tailwind-scale.mjs';

export default {
  meta: { name: 'zenbree' },
  rules: {
    'date-time-zone': dateTimeZone,
    'no-reset-all-state-on-prop-change': resetAllState,
    'tailwind-scale': tailwindScale,
  },
};
