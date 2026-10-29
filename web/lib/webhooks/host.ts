/** The receiver's host: the full URL can carry a token (Zapier-style), so only the host reaches the browser. */
export function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return '';
  }
}
