import { type ListRow, rowSchema } from './api';

type Lookups = ListRow['lookups'];

/** A row's `lookups` in a test: never searched, unless a state is given. */
export const lookups = (
  email: Lookups['email'] = { state: 'not_searched' },
  phone: Lookups['phone'] = { state: 'not_searched' },
): Lookups => ({ email, phone });

/** What a stray raw status reads like in a test: a value no screen text or filter may match. */
const STRAY_STATUS = { email_status: 'stray_email_status', phone_status: 'stray_phone_status' };

/** A row an API test wrote (`lib/api/fixtures`), parsed as the client parses it. Only fields the user controls
 * may differ from what the API sent. The API sends no raw status (`public_cells`), so the cells also get one
 * stray `email_status` and `phone_status`, as an older API sent them: a reader that took one would show it. */
export const fixtureRow = (
  json: { cells?: object },
  own: Partial<Pick<ListRow, 'excluded'>> = {},
) => rowSchema.parse({ ...json, cells: { ...json.cells, ...STRAY_STATUS }, ...own });

/** The stray status `fixtureRow` put in `key`, for an assertion that the user never reads it. */
export const strayStatus = (key: keyof typeof STRAY_STATUS): string => STRAY_STATUS[key];
