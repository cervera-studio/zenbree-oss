// Better Auth's admin plugin stores multiple roles comma-separated ("admin,user").
export function hasRole(role: string | null | undefined, wanted: string): boolean {
  return (role ?? '').split(',').some((r) => r.trim() === wanted);
}
