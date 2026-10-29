import { describe, expect, it, vi } from 'vite-plus/test';
import { errorFrameCode, followStream, readFrames } from './event-stream';

const fetched = vi.hoisted(() => ({ ok: false, body: 'event: snapshot\ndata: {}\n\n', calls: 0 }));

vi.mock('@/lib/api/browser', () => ({
  authFetch: async () => {
    fetched.calls++;
    return fetched.ok ? new Response(fetched.body) : new Response(null, { status: 503 });
  },
}));

describe('followStream', () => {
  it('reports an attempt the server refused', async () => {
    fetched.ok = false;
    const ctrl = new AbortController();
    const refused = vi.fn(() => ctrl.abort());
    await followStream('/v1/x', null, () => {}, ctrl.signal, { onRefused: refused });
    expect(refused).toHaveBeenCalledOnce();
  });

  it('does not report an accepted connection that ends', async () => {
    fetched.ok = true;
    const ctrl = new AbortController();
    const refused = vi.fn();
    await followStream('/v1/x', null, () => ctrl.abort(), ctrl.signal, { onRefused: refused });
    expect(refused).not.toHaveBeenCalled();
  });

  it('stops for good after an error frame', async () => {
    fetched.ok = true;
    fetched.body = 'event: error\ndata: {"code":"AUTH.NOT_A_MEMBER"}\n\n';
    fetched.calls = 0;
    const frames: string[] = [];
    await followStream('/v1/x', null, (f) => frames.push(f.event), new AbortController().signal);
    expect(frames).toEqual(['error']);
    expect(fetched.calls).toBe(1);
  });
  it('ends for good on any other error frame, even for a stream that rejoins', async () => {
    fetched.ok = true;
    fetched.body = 'event: error\ndata: {"code":"LIST.NOT_FOUND"}\n\n';
    fetched.calls = 0;
    const rejoin = vi.fn();
    const frames: string[] = [];
    const signal = new AbortController().signal;
    await followStream('/v1/x', null, (f) => frames.push(f.event), signal, { rejoin });
    expect(frames).toEqual(['error']);
    expect(rejoin).not.toHaveBeenCalled();
    expect(fetched.calls).toBe(1);
  });
});

describe('readFrames', () => {
  it('parses a frame cut across chunks once its end arrives', async () => {
    const chunks = [
      'event: page\nda',
      'ta: {"url":"x"}\n',
      '\n: ping\n\nevent: drafting\ndata: {}\n\n',
    ];
    const body = new Response(
      new ReadableStream({
        start(c) {
          for (const chunk of chunks) c.enqueue(new TextEncoder().encode(chunk));
          c.close();
        },
      }),
    ).body;
    if (!body) throw new Error('no body');
    const batches = [];
    for await (const frames of readFrames(body)) batches.push(frames.map((f) => [f.event, f.data]));
    expect(batches).toEqual([
      [],
      [],
      [
        ['page', '{"url":"x"}'],
        ['drafting', '{}'],
      ],
    ]);
  });
});

describe('errorFrameCode', () => {
  const frame = (data: string) => ({ event: 'error', id: '', data });

  it('reads the code the server ended the stream with', () => {
    const data =
      '{"message":"You are no longer a member of this organization.","code":"AUTH.NOT_A_MEMBER"}';
    expect(errorFrameCode(frame(data))).toBe('AUTH.NOT_A_MEMBER');
  });

  it('still gives a code for a frame that carries none', () => {
    expect(errorFrameCode(frame('{"message":"x"}'))).toBe('INTERNAL.UNEXPECTED');
    expect(errorFrameCode(frame('not json'))).toBe('INTERNAL.UNEXPECTED');
  });
});
