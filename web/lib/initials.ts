/** An avatar's letters; `?` when the name has none, so the circle never renders empty. */
export function initials(name: string) {
  const letters = name
    .split(/[\s@._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join('');
  return letters || '?';
}
