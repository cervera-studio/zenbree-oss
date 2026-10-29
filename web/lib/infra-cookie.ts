const INFRA_COOKIE = '__infra-rid';

/** The `Cookie` header value without `__infra-rid`; undefined when nothing else is left. */
export function withoutInfraCookie(cookie: string | null): string | undefined {
  const kept = cookie?.split(/;\s*/).filter((c) => c && !c.startsWith(`${INFRA_COOKIE}=`));
  return kept?.length ? kept.join('; ') : undefined;
}
