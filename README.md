# FinPilot — proactive financial planning

FinPilot imports INR transactions, finds spending changes, forecasts cash flow, simulates purchases and recommends evidence-linked actions. FastAPI, SQLite, LangGraph and APScheduler power a responsive web interface. Every LLM call goes through Mesh API.

This is a single-user hackathon prototype, not a production banking service.

## Run locally

Use Python 3.11 or newer:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-lock.txt
cp .env.example .env
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

On Windows activate with `.venv\Scripts\activate`. Open http://127.0.0.1:8000. Keep the process running for scheduled briefs. Run one worker.

Add a working `MESH_API_KEY` to the gitignored `.env`. The default `MESH_MODEL` is `openai/gpt-4o`; use a model available to your account supporting tool calls. The fixed base URL is `https://api.meshapi.ai/v1`. There is no direct-provider fallback. A configured-key badge does not establish working credit or completion access.

## Agents and approval boundaries

The distinct tools are Categorizer (merchant/direction rules), Analyzer (equal 30-day spending comparisons), Forecaster (trailing cash-flow projection), PurchaseSimulator (purchase/reserve scenarios) and Advisor (evidence-linked proposals).

The coordinator invokes a LangGraph StateGraph:

```text
stored categorized transactions
  → analyze → forecast → [optional simulate] → advise → assemble
```

Reports expose `execution_trace`, `orchestration` and a workflow version. Each graph invocation owns its state; the compiled topology is cached. Ingestion categorizes before storage. The graph does not move money, cancel services, or save plans. User saves are explicit UI/API actions. Successful chats and scheduled briefs save audit snapshots automatically.

## Use the app

1. **Transactions:** import CSV or enter transactions, then separately save the actual current bank balance. **Try sample data** seeds an empty database with synthetic transactions and a ₹45,000 balance.
2. **Overview:** inspect spending changes, forecast and “Why this recommendation?”
3. **Subscriptions:** review detected merchants. Confirm billing cycle, amount, active status and whether you use a service. Only an active, user-confirmed unused service triggers a cancellation suggestion. Transactions alone cannot establish usage. Annual and weekly costs are converted to approximate monthly equivalents.
4. **Savings goals:** save a target, deadline within two years, amount already saved and planned monthly contribution. Update savings as you progress. Goals show achieved, on-track, at-risk, overdue or needs-history status. Forecast surplus is shared proportionally across unfinished goals rather than allocated in full to each.
5. **Plan a purchase:** simulate a cost, horizon, minimum reserve and extra monthly savings without changing your finances.
6. **Ask FinPilot:** ask free-form questions grounded in the current report. Try “Can I afford a ₹30,000 trip in 30 days while keeping ₹15,000?” Mesh may invoke the same read-only simulator. Missing details require clarification. Up to 12 recent messages support follow-ups; at most three tool calls per request. Successful tool arguments/results and the financial context are recorded in audit history.
7. **Daily briefs:** inspect a saved snapshot, one selected insight and one action. Optional SMTP sends the same digest on a real APScheduler schedule.

## CSV format and forecast assumptions

```csv
date,merchant,amount
2026-10-01,Salary,60000
2026-10-02,Apartment rent,-18000
2026-10-03,Zomato,-560
```

Use INR only, negative expenses, positive inflows and dates YYYY-MM-DD. Future transactions, nonfinite amounts and uploads over 2 MB are rejected. Imports are atomic; exact date/merchant/amount duplicates are skipped. This also collapses legitimate identical same-day purchases. The transaction list displays the latest 500; analysis reads all applicable history.

Spending comparisons use equal recent and preceding 30-day windows, with alerts at increases of at least 20%. This is a rule-based signal, not statistical anomaly detection. Forecasts average signed cash flow over up to 90 days, assuming complete history and zero activity on missing days. Fewer than 30 observed days are flagged.

Projected balance = entered balance + average daily net × horizon.
Scenario balance = projected balance − purchase + extra monthly saving × days / 30.

Affordability checks only the ending reserve. Bill timing, salary dates, interest, seasonality and unseen future changes are not modeled. Savings goals cap contributions at positive historical monthly surplus, proportionally across goals. Entered savings are not inferred from bank balance. Goal and purchase models are separate estimates, not a full accounting ledger.

Synthetic CSV scenarios and expected results are in `data/finpilot_csv_test_pack/`. Fixed-date samples change their trailing-window metrics as time passes. Do not combine independent profiles in one database.

## Scheduled email

Email is disabled by default. Add private settings to `.env`, then restart:

```dotenv
DIGEST_EMAIL_ENABLED=true
DIGEST_HOUR_UTC=3
SMTP_HOST=your-smtp-host
SMTP_PORT=587
SMTP_SECURITY=starttls
SMTP_USERNAME=your-smtp-username
SMTP_PASSWORD=your-smtp-app-password
SMTP_FROM=your-sender-address
DIGEST_EMAIL_TO=your-recipient-address
```

For implicit TLS use `SMTP_SECURITY=ssl` and port 465. Do not commit credentials. The default schedule is 03:00 UTC / 08:30 IST. A daily brief is saved while the process runs, and email is sent when enabled. Missed downtime runs are not replayed. “Email today’s brief” supports an explicit send/retry.

SQLite records delivery attempts per UTC day. Sent, in-flight and uncertain sends are not automatically repeated. Failed sends allow at most three attempts per day, reusing the original snapshot. Uncertain acceptance or an interrupted in-flight delivery needs mailbox/server review; the app does not blindly retry. SMTP acceptance does not prove inbox receipt. This is not a durable queue.

The actual scheduler and SMTP path are tested with a mock recipient `recipient@example.test`. No real email is sent by tests. Live SMTP remains a deployment configuration and verification step.

## API and security

Interactive API documentation: `/docs`.

| Route | Purpose |
|---|---|
| GET /health | App status and Mesh key presence |
| GET /api/dashboard | Read-only LangGraph report |
| GET/POST /api/transactions | List/log transactions |
| POST /api/import | CSV upload, multipart field file |
| POST /api/balance | Explicit balance save |
| POST /api/simulate | Read-only purchase scenario |
| POST /api/chat | Grounded Mesh conversation and optional simulation |
| GET/PUT /api/subscriptions | Review/save confirmed subscription details |
| GET/POST /api/goals | List/create savings goals |
| PUT/DELETE /api/goals/{id} | Update/delete a goal |
| POST /api/digest | Save a brief |
| GET /api/delivery | Redacted delivery configuration/status |
| POST /api/digest/send | Explicit opt-in email |
| GET /api/audit | Latest 30 saved reports/chats |
| POST /api/demo | Seed an empty database |

Set `FINPILOT_TOKEN` before exposing beyond localhost. APIs then require a bearer token; the UI stores it in session storage. This is single-user protection, not multi-user authorization. Databases and `.env` are gitignored.

## Verification and self-evaluation

```bash
DIGEST_EMAIL_ENABLED=false python -m pytest -q
python evaluation/run.py
python -m pip check
```

29 October 2026: **42 automated tests passed**. Coverage includes atomic imports, duplicate detection, auth, independent LangGraph state, forecast arithmetic, subscription confirmation, goal progress/shared surplus, Mesh routing/tool invocation/history, audit evidence, real background scheduling with mock SMTP, delivery deduplication, retry limits and uncertain acceptance. Isolated desktop/mobile browser checks cover subscriptions, goal updates, recommendations and daily briefs.

| Synthetic evaluation | Result |
|---|---:|
| Categorization, 25 labeled cases | 80% (20/25) |
| Forecast MAE, 30 holdout windows | ₹5,277.48 |
| Stable profile MAE | ₹0.00 |
| Variable spending MAE | ₹897.17 |
| Unseen spending shift MAE | ₹14,935.26 |

Reproducible seed: 20261018. Each forecast uses a 90-day training window and separate 30-day holdout. Prediction-level results are committed in `evaluation/results.json`. This is a small developer-authored synthetic evaluation, not independent real-world validation. Positive refunds are treated as income; unfamiliar merchants and unseen spending shifts are known weaknesses. Live LLM quality is not measured by these tests.

## Challenge mapping

| Requirement | Implementation |
|---|---|
| Multi-agent coordinator | Distinct financial tools coordinated through LangGraph |
| Ingestion/categorization/storage | CSV/manual input, merchant rules, SQLite |
| Behavior/anomalies | Data-dependent equal-window spending increases |
| Forecast/simulation | Cash-flow projection and shared purchase simulator |
| Grounded proactive advice | Spending targets, deficit alerts, confirmed-unused cancellation suggestions, goal shortfalls |
| Audit trail | Trigger/evidence on each action; saved report and chat/tool snapshots |
| Self-evaluation | Reproducible metrics, test fixtures and prediction results |
| Structured framework bonus | Explicit LangGraph analyze → forecast → advise |
| Goal bonus | Persistent targets/deadlines/progress and adapting advice |
| Conversation bonus | Mesh-only questions and bounded read-only tool calling |
| Scheduled delivery bonus | APScheduler plus opt-in SMTP, tested with mocked delivery |

No Telegram integration, autonomous financial actions or production-readiness claim is made.

## Submission

Repository: https://github.com/MaarreeJ/FinPilot_M

1. Push the final tested code and inspect the FinPilot tests workflow.
2. Add working `MESH_API_KEY` and the organizer-provided `SUBMISSION_TOKEN` as GitHub Actions secrets; never commit them.
3. Complete and submit the FinPilot entry at https://career.krishnaik.in/dashboard/hackathons?h=agent-arena-2026 using the public repository URL and any fields required by the form.
4. Push again after submission so the official organizer workflow can record a fresh result. An earlier run reported HTTP 403 because no entry existed even though its checks passed.
5. Confirm both the GitHub run and organizer dashboard record success.

The organizer workflow is preserved. Eligibility checks do not establish complete challenge scoring. A successful live Mesh conversation and real email demonstration remain distinct from mocked tests. Only the organizer determines acceptance.

## Deployment limitations

A Dockerfile is supplied; Docker build/run is not verified in this environment. Before a multi-user release add real identity/tenant isolation, migrations, durable workers, rate/cost limits, monitoring, backup/restore verification and security/load tests. Keep one worker with the current scheduler. Localhost is not a publicly accessible deployed demo URL.
