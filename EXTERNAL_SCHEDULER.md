# External scheduler fallback (optional)

GitHub's built-in `schedule` trigger is best-effort. This repository also accepts a
`repository_dispatch` event named `lab-price-update`, so an external scheduler can
start the same tested price-update job if GitHub's scheduler stops delivering events.

## Configure cron-job.org

Create a fine-grained personal access token on GitHub. Restrict it to **only**
`takdoo/lab-price-bot` and grant the repository **Contents: Read and write**
permission. Do not use a classic token or paste the token into chat, an issue, or
a public file.

Create a cron-job.org job with:

- Schedule: every 5 minutes
- Request method: `POST`
- Request URL: `https://api.github.com/repos/takdoo/lab-price-bot/dispatches`
- Header `Accept`: `application/vnd.github+json`
- Header `Authorization`: `Bearer YOUR_FINE_GRAINED_TOKEN`
- Header `X-GitHub-Api-Version`: `2022-11-28`
- Header `Content-Type`: `application/json`
- Request body: `{"event_type":"lab-price-update"}`

Store the token only in the scheduler's secret/credential field if available. Never
put it in the URL or request body. A successful dispatch should create an Actions
run whose event is `repository_dispatch`; check that run's conclusion before
assuming the Telegram message was updated.

## Important

- This fallback is **not active** until the external scheduler and token are configured.
- The normal GitHub `schedule` trigger remains enabled and continues to run independently.
- If a token is exposed, revoke it immediately and create a replacement.
