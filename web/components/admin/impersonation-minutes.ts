/** Whole minutes until `expiresAt`, rounded up so the last partial minute still reads «1 min». */
export function minutesLeft(expiresAt: Date | string, now: Date): number {
  return Math.max(0, Math.ceil((new Date(expiresAt).getTime() - now.getTime()) / 60_000));
}
