# HubSpot app

The HubSpot app behind the read gate (E13.1, #120) is a HubSpot *project*: these files are its
whole configuration, uploaded with the HubSpot CLI. HubSpot no longer offers legacy apps on new
accounts, so there is nothing to click together in the developer UI. A service key is not an
alternative: it is one account's static token, and every Zenbree user connects their own portal
over OAuth.

| File | Holds |
|---|---|
| `src/app/app-hsmeta.json` | OAuth: redirect URLs, scopes, support contacts |
| `src/app/webhooks/webhooks-hsmeta.json` | Webhook target and subscriptions |
| `src/hsprofile.<name>.json` | Per environment: the target HubSpot account and the `${VARIABLES}` the two files above read |

HubSpot reads no shell environment and no `api/.env`. A profile is its equivalent, so
`API_PUBLIC_BASE_URL` in a profile must equal the `API_PUBLIC_BASE_URL` of the API it points at:
the webhook signature is computed over that URL.

## Upload

Set `accountId` in `src/hsprofile.prod.json` to your HubSpot developer account ID and replace its URLs and support email before uploading. The shipped account ID `0` is a placeholder.

```bash
npm install -g @hubspot/cli
hs account auth                      # the account you are logged into in the browser is the one linked
hs account list                      # check the default before uploading
cd ops/hubspot
hs project validate --profile=prod
hs project upload --profile=prod
hs project open                      # app page → Auth: client id and client secret
```

The client id and secret go into Infisical (project `zenbree`) as `HUBSPOT_OAUTH_CLIENT_ID` and
`HUBSPOT_OAUTH_CLIENT_SECRET`, and into your own `api/.env` for local runs, together with
`API_PUBLIC_BASE_URL` and `OAUTH_TOKEN_ENCRYPTION_KEY`. They never go in this directory.

## A test app

An app has one webhook target, so local webhooks need their own app:

```bash
hs project profile add dev --copy-from prod --target-account <test account id>
```

Set `API_PUBLIC_BASE_URL` in `src/hsprofile.dev.json` to the public https tunnel to port 8000, use
the same value in `api/.env`, then `hs project upload --profile=dev`. The OAuth redirect to
`http://localhost:8000` is registered on every profile and needs no tunnel.

## Changing it

Subscriptions use `legacyCrmObjects` on purpose: `app/crm/webhooks.py` reads the kind from
`contact.propertyChange`-style `subscriptionType`, and the newer `crmObjects` entries deliver a
different payload. A new scope or subscription is an edit here plus an upload, and the scope list in
`docs/contracts-m1.md` has to match.

The write scopes (E13.2, #526) are `optionalScopes`: a portal that cannot give them still connects for the
read gate, and a user who connected before them gets them on the next consent. They mirror
`HUBSPOT_WRITE_SCOPES` in `api/app/integrations/providers.py`, which sends them as `optional_scope`.
