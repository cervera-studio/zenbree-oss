import { z } from 'zod';
import { httpClient } from '@/lib/http-client';

const tokenSchema = z.string().regex(/^[\w-]{1,600}$/);

// RFC 8058 one-click (#220): the mail client POSTs `List-Unsubscribe=One-Click` to the link itself.
// The token is the authority, so the body is ignored and the API does the opt-out. A GET never gets
// here: proxy.ts renders the page instead, so a link scanner never unsubscribes anybody.
export async function POST(_request: Request, { params }: { params: Promise<{ token: string }> }) {
  const token = tokenSchema.safeParse((await params).token);
  if (!token.success) return new Response(null, { status: 404 });
  const res = await httpClient.post(`/v1/unsubscribe/${token.data}`, undefined, {
    schema: z.looseObject({ status: z.string() }),
  });
  if (res.success) return Response.json(res.data);
  return new Response(null, { status: res.error.status === 404 ? 404 : 502 });
}
