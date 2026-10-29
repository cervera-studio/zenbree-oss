// Cloudflare sets cf-connecting-ip to the real client and appends to a client-sent X-Forwarded-For, which
// Better Auth cannot key on once it holds two values (#977). The app is reached only through the tunnel.
export const AUTH_IP_ADDRESS = { ipAddressHeaders: ['cf-connecting-ip', 'x-forwarded-for'] };
