# Zenbree

Zenbree is an open-source AI agent for outbound sales. Describe who you want to reach: a chat brief becomes a live lead list, verified contacts, and email or LinkedIn sequences.

Self-host it, bring your own provider keys, and adapt it to your workflow.

- Discover companies and people, enrich contacts, and export lists.
- Draft and review email and LinkedIn campaigns.
- Connect Google or Microsoft mailboxes, Unipile, HubSpot, and Instantly.
- Use the API, webhooks, automations, and the administration interface.

## Stack

- `api/`: FastAPI, Celery, Python 3.14, and PostgreSQL with plain SQL migrations.
- `web/`: Next.js, React, Better Auth, and Tailwind CSS; Node 24 and pnpm.
- `landing/`: the static Astro marketing site.
- `compose.yml`: the local stack and optional production services.

## Run locally

You need Docker with Compose. From a clone:

```sh
git clone https://github.com/cervera-studio/zenbree-oss.git
cd zenbree-oss
cp .env.example .env
```

Add the settings you need to the root `.env`; `api/.env.example` and `web/.env.example` document the available provider and application settings. Compose passes the root file to both applications.

- Chat needs `DEEPSEEK_API_KEY` or `ANTHROPIC_API_KEY`.
- Contact search and enrichment need the respective providers' API keys.
- Without `RESEND_API_KEY`, verification email is not sent; the web logs print the verification link for local development.
- Better Auth Sentinel can use your own project identification URL through `BETTER_AUTH_IDENTIFY_URL`. The examples leave it empty.

```sh
docker compose up --build
```

The app is at http://localhost:3000, the API at http://localhost:8000, and the landing at http://localhost:4321. Flower is at http://localhost:5555/flower when `FLOWER_BASIC_AUTH` is set; the example contains a local development value.

The `migrate` service applies the SQL migrations before the application services start. After pulling schema changes into a running stack:

```sh
docker compose build migrate
docker compose run --rm migrate
```

To add sample lists and a local admin account, run `dev/seed.sh`. Its development credentials are `dev@zenbree.local` / `zenbree-dev-1234`; existing sample data is preserved. Keep the sample account and example passwords confined to local development.

## Develop and contribute

Install Node 24, pnpm, and [uv](https://docs.astral.sh/uv/). `corepack enable` provides the pnpm version pinned in `web/package.json`.

```sh
cd web
pnpm install
cd ../api
uv sync
cd ..
```

The web install provides the git hooks. If they are missing, run `web/node_modules/.bin/lefthook install`. Use Conventional Commit subjects.

```sh
./harness/init.sh all --fast  # static checks; tests and application builds are omitted
./harness/init.sh all         # full checks with a disposable test database
```

The full check starts the shared local test database when `TEST_DATABASE_URL` is not set. Read [AGENTS.md](AGENTS.md) and [harness/CHECKPOINTS.md](harness/CHECKPOINTS.md) before changing shared behavior. Open issues and pull requests in this repository.

## Self-hosting

Configure your own secrets, public URLs, OAuth applications, and provider accounts. GitHub Actions is disabled on this repository. The included workflows require no repository secrets and contain checks only; they do not deploy an installation or publish production images. Build your own images before using `ops/deploy-production.sh`.

The scripts in `ops/` are optional deployment examples. Before using them:

- Pass your SSH destination explicitly to `ops/deploy.sh deploy@your-server`.
- Set `INFISICAL_PROJECT_ID` in the server environment used by deployment and backup, and configure your read token as the scripts describe. Replace the placeholder workspace in `.infisical.json`.
- Set your tunnel ID, credential file, and hostnames in `ops/cloudflared.yml`.
- Replace the account ID, URLs, and support contacts in `ops/hubspot/src/hsprofile.prod.json` before uploading a HubSpot app.
- Review brand URLs, legal details, pricing, and marketing copy for your installation.

This public snapshot is a baseline for fresh databases. Keep existing installations on their original migration files: the runner verifies migration checksums, including comments.

Production schema changes are forward-only. Read [docs/migrations.md](docs/migrations.md) for backup, restore, and rollback procedures.

## Documentation

- [API and stream contracts](docs/contracts-m1.md)
- [Public API](docs/public-api.md)
- [Implementation recipes](docs/recipes.md)

Some design documents retain the original project's decisions and issue numbers as historical context. This public repository starts from a source snapshot and has its own history and issue tracker.

## Acknowledgments

Thanks to **Alessandro Poli**, **Arthur Betto**, and **Piero Cervera** for their help with the project.

## License

[MIT](LICENSE). Bundled code and fonts retain the licenses listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
