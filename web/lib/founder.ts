/** The user an organization belongs to, read off its slug, which the server sets and nobody can change: a user's
 * own organization has their id (#305), a team they created `<their id>/<uuid>` (#676). They stay its admin. */
export const founderOf = (slug: string) => slug.split('/')[0] ?? slug;
