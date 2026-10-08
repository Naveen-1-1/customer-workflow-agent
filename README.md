# customer-workflow-agent

A customer-service chat agent for the [τ²-bench](https://github.com/sierra-research/tau2-bench)
retail store, built as a LangGraph **workflow**: code decides what happens, and the LLM only
**classifies** messages, **extracts** details and **phrases** friendly questions.

## How it works

A customer opens the chat page and types. The backend walks a flowchart:

**greet → verify identity → understand the message → handle each request in turn → "anything else?"**

- **One subgraph per request type:**
  - cancel an order
  - change an order's shipping address
  - change an order's payment method
  - change items in a pending order
  - return items
  - exchange items
  - change the default address
  - questions about orders, profile or products
  - transfer to a human
- **Several requests in one message are queued** and handled one after another. Anything new mentioned mid-request waits until the current one is done (say "skip" to drop it).
- **Before any change**, the customer sees a **Yes/No popup** with exactly what will change. Nothing is written without that click.
- **Optional supervisor approval for large returns.** When `APPROVAL_ENABLED=true`, a return whose refund is over `APPROVAL_THRESHOLD` waits for a supervisor on `/supervisor`:
  - **Approve:** the return goes through.
  - **Reject:** the customer sees the note, is offered a human agent, and further returns on that order are blocked until the demo is reset.
- **Every fact comes from fixed templates:** ids, amounts, statuses and results. The LLM never sees other customers' data, and anything it extracts (order numbers, emails) must appear in what the customer actually typed.

Each change request runs its policy checks as named steps, so the graph reads like the rules,
for example `cancel_order: find_order → check_pending → check_reason → prepare_summary →
confirm → execute`. Only `return_items` has the supervisor-approval path. `make graph` re-exports
the full diagram to [`docs/workflow-graph.mmd`](docs/workflow-graph.mmd) from the code.

All policy rules from [`data/policy.md`](data/policy.md) are enforced in code. For example:
- verification comes first
- customers can only act on their own orders
- status rules apply (pending → cancel/modify; delivered → return/exchange)
- cancellations need an allowed reason
- refunds go to the original payment method or a gift card
- gift card balances must cover the amount
- items can only be modified once
- exchanges stay within the same product

The rule → code map is in [`policy/rules.py`](src/customer_workflow_agent/policy/rules.py) and the subgraphs.

## Setup

Requires Python 3.13 and Node 22+.

```bash
make install
cp .env.example .env    # then put your NVIDIA API key in .env
```

Get a key at <https://build.nvidia.com/settings/api-keys>.
- **Models:** `nvidia/nemotron-3.5-lightning-30b-a3b` is the primary, with `nvidia/nemotron-3-super-120b-a12b` as fallback. Reasoning ("thinking") is off. Calls go through the [LiteLLM](https://docs.litellm.ai/) Router (pinned to an exact version, see `pyproject.toml`):
  - **429 / 5xx / bad output:** retried twice with backoff, then the fallback model;
  - **timeout (15 s):** straight to the fallback;
  - **every model failed:** the chat apologizes and stays usable.
- **Rate limit:** the free tier allows about 40 requests per minute, shared across your account. The app limits itself to 36 per minute (`LLM_REQUESTS_PER_MINUTE`).
- **Without a key:** the app still runs and verifies customers by email or by name + zip. Anything else gets an apology message.
- **Other endpoints:** `LLM_BASE_URL` points the app at any OpenAI-compatible server, such as the fake one used for tests.

## Run

```bash
make dev        # backend on :8000 + frontend on :5173 (Ctrl-C stops both)
```

- Customer chat: <http://localhost:5173/>
- Supervisor: <http://localhost:5173/supervisor>

`make serve` builds the frontend and serves everything from FastAPI on <http://localhost:8000>.

**When it's busy:** at most `MAX_OPEN_CHATS` (10) chats are open at once. A chat stays open until it ends or sits idle for `CHAT_IDLE_MINUTES` (10).
- **When full:** new customers see "We're busy right now, please try again in a minute" with a **Try again** button. An idle chat that comes back also has to wait for a slot.
- **Never refused:** popup answers and supervisor decisions.

**Data:** the store's changes go to `var/db.working.json`; `data/db.json` is never modified. Chats are kept in `var/checkpoints.sqlite`, so they survive restarts. The supervisor page's **Reset demo** restores the store and deletes all chats. `make reset-data` does the same with the server stopped.

**Dev-mode note:** when `make dev` auto-reloads the backend while pages are open, uvicorn logs a harmless `CancelledError` traceback as it closes their live-update connections. The pages reconnect by themselves.

### Demo script

Open the chat and the supervisor page side by side. Set `APPROVAL_ENABLED=true` in `.env` to try approvals. The customers below are τ²-bench's synthetic test data.

1. **Cancel an order:**
   1. Say "I'm Yusuf Rossi, zip 19122", or give an email such as `ivan.santos3158@example.com`.
   2. Ask to cancel a pending order and give the reason.
   3. In the popup, press **No** first, change something, then **Yes**.
2. **Exchange items:** for Yusuf, exchange the keyboard in `#W2378156` for clicky switches and the thermostat for Google Assistant. You'll pick from the in-stock keyboards.
3. **Large return, approved:** with `raj.sanchez2046@example.com`, return everything in `#W1067251` ($1,201.55). The chat waits until the supervisor page approves it.
4. **Large return, rejected:** do the same return in a new chat and reject it with a note on the supervisor page. The customer sees the note and is offered a human; a retry on that order is refused.
5. **Two requests at once:** ask for two things in one message and watch them run in order.

## Observability

- **Logs:** every line names its chat, as in `12:01:22 INFO [chat=91870f…] uvicorn.access: …`. Failed model attempts get one compact line each.
- **Tracing (LangSmith):** set `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` in `.env`.
  - Each chat shows up as one thread.
  - Every graph step and LLM call is traced, and each LLM call carries its prompt name and version.
  - Tests and load runs always run with tracing off.
- **Health:** `GET /healthz` (process up) and `GET /readyz` (store, chat DB, checkpoints and LLM key). `/readyz` never calls the LLM.
- **Metrics:** `GET /metrics` (Prometheus) covers:
  - LLM attempts by outcome (ok, timeout, rate_limited, server_error, bad_output);
  - which model answered (primary, fallback or none);
  - reply times by customer action;
  - open and refused chats, approvals waiting, rate-limiter wait, and HTTP requests.
- **Dashboard and alerts:** `make metrics` starts Prometheus (:9090) and Grafana (:3000, no login, local only) for an app running on :8000. Install them first with `brew install prometheus grafana`.
  - The dashboard is provisioned from `ops/grafana/`.
  - Alerts in `ops/prometheus/alerts.yml`: app down or not ready, every model failed, fallback above 30%, more than 5 NVIDIA 429s in 5 min, p95 reply above 10 s, 3+ refused chats in 5 min, an approval waiting over 10 min.
  - Alerts show only in Grafana and Prometheus; nothing is sent anywhere.
- **Prompt versions:** each prompt in `llm/prompts.py` has a version in `VERSIONS`. If you change its wording, bump the version and run `make prompt-fingerprints`. A test fails until you do.

## Load and failure testing

`make fake-llm` runs a fake OpenAI-compatible model server on :8100.
- It answers the load-test customers' messages from a small scenario table.
- Faults are set per model through `POST /_faults`: latency, 429s, 500s, timeouts and bad JSON.

`make load` runs a fake-model server, a separate copy of the app (:8010, with data in `var/load/`) and Locust, then prints a summary.
- **Simulated customers:** each one verifies by email, asks about an order, cancels or returns, and says bye, typing at human speed.
- **Report:** `var/load/report.html`.
- **Settings:** `USERS=20 DURATION=2m RPM=36` (the app's LLM limit; `RPM=6000` measures the app without it), `LATENCY_S`, `FAULTS='{"rate_limit": 0.2}'`, and any app setting such as `MAX_OPEN_CHATS`.

| | 15 users, `RPM=36` | 15 users, `RPM=6000`, 20% fake 429s |
|---|---|---|
| Reply time | median 11 s, p95 45 s | median 1.4 s, p95 5.3 s |
| Failures | 0 | 0 (every 429 was absorbed by retries) |

In the first run nearly all of that time is spent waiting for the 36-requests-per-minute limit.

## Tests

```bash
make test       # backend: store, policy, resolution, every flow, API (no network)
make test-web   # frontend (Vitest)
make test-live  # live NVIDIA checks, needs NVIDIA_API_KEY
make lint
make graph     # re-export docs/workflow-graph.mmd from the compiled graph
make test-alerts  # promtool unit tests for the alert rules (needs: brew install prometheus)
```

**What the backend tests cover:**
- the store: τ²-bench's own tool tests ported, plus every write action in its 114 tasks replayed
- conversation flows with a scripted LLM
- three τ²-bench tasks (0, 16, 19) played through the graph: the final store must equal replaying each task's expected actions
- API behavior: double clicks, stale answers, restarts, reset, capacity limits, health and metrics
- the whole app against the fake model server: retries and fallback on 429, 500, bad JSON and timeouts, and recovery when every model is down
- LiteLLM routing (with mocked responses), LangSmith traces (recorded locally), chat ids in logs, prompt versions

The real-server streaming tests skip themselves where binding a local port isn't allowed.

## Layout

```
data/                     τ²-bench retail data (unmodified) + license
src/customer_workflow_agent/
  store/                  store + operations, adapted from τ²-bench (two bugs fixed)
  policy/rules.py         the retail policy as plain checks
  resolve/                "the blue one", "my Visa", addresses → concrete ids
  llm/                    LiteLLM Router (models, retries, fallback), schemas, versioned prompts, reply guard
  templates/              every customer-facing fact and wording
  graph/                  parent graph, shared write flow, 9 subgraphs (each check a named node)
  api/                    FastAPI endpoints, chat runner (capacity limit), live updates (SSE), health
  obs/                    chat-id logging, LangSmith tracing, Prometheus metrics
  devtools/               fake OpenAI-compatible model server
frontend/                 React (Vite + TypeScript + shadcn/ui): chat and supervisor pages
ops/                      Prometheus config + alert rules (with tests), Grafana datasource + dashboard
load/                     Locust customers and the `make load` runner
tests/
```

## Attribution

The retail data, and the store code it's adapted from, come from τ²-bench (MIT, © 2025 Sierra
Research). See [NOTICE.md](NOTICE.md).
