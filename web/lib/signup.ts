// Open by default so a self-hosted install can register its first user; the hosted service sets SIGNUP_ENABLED=false
// and creates accounts from the admin. Read at request time, never baked into a build.
export const signupEnabled = () => process.env.SIGNUP_ENABLED !== 'false';
