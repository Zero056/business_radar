# Architecture

Status: Phase 1 (Foundation). This document records the approved design and the changes made during implementation.

## Layers

```
Presentation   Desk DocTypes, workspaces (Phase 2), reports (Phase 2)
     ↓ whitelisted API only
Action         whitelisted actions, approvals, audit (Phase 4)
     ↑
Intelligence   optional AI, reads permission-filtered evidence only (Phase 3)
     ↑
Detection      rule registry (code), runner, locking, scheduler dispatcher
     ↑
Analytics      KPI definitions and service, pure formulas, health score (Phase 2)
     ↑
Data Source    scope evaluation, read-only ERPNext aggregates
```

Dependencies only point downward. The pure modules (`analytics/formulas.py`,
`monitoring/run_state.py`, `constants.py`) import no Frappe code and are unit-tested
without a site.

## Module map (Phase 1)

| Path | Responsibility |
|---|---|
| `hooks.py` | Install, migrate, scheduler wiring |
| `install.py` | Idempotent roles, KPI configuration sync |
| `constants.py` | Role names, categories, severities, limits |
| `data_source/scope.py` | `AccessScope`, the company-level gate |
| `data_source/erp_queries.py` | Read-only ERPNext aggregates, each gated by the scope |
| `analytics/formulas.py` | Pure calculation helpers |
| `analytics/kpi/definitions.py` | KPI registry with formulas, sources, and roles |
| `analytics/kpi/service.py` | Permission-checked KPI evaluation |
| `monitoring/registry.py` | Code registry of rules, parameter validation |
| `monitoring/run_state.py` | Pure frequency, retry, and lock-state decisions |
| `monitoring/runner.py` | Run lifecycle, row-lock acquisition, heartbeat, failure capture |
| `monitoring/scheduler.py` | 15-minute dispatcher: due runs and retries |
| `monitoring/retention.py` | Batched purge of finished detection runs |
| `audit/action_log.py` | Append-only audit writer with hashed parameters |
| `business_radar/doctype/*` | Settings, Monitoring Rule (+ child tables), KPI Configuration, Detection Run, Action Log |

## Approved decisions and how they are implemented

| Decision | Implementation |
|---|---|
| Rules are code, not expressions | `monitoring/registry.py`. The Monitoring Rule validates `rule_code` against the registry and accepts only declared numeric parameters. |
| Aggregates respect permissions | `data_source/scope.py` computes an `AccessScope`. `erp_queries` refuses to run unless `company_level` is true. KPI service returns `restricted` otherwise. |
| Scope detects record-level rules generically | `DatabaseQuery(doctype, user).get_permission_query_conditions()` is the same function Frappe uses for lists. It picks up tst's portfolio rules and any Server Script. |
| No direct trust in the frontend | Every KPI call re-checks company access, KPI roles, and source DocType read permission on the server. |
| Gross Profit reuses ERPNext | `erp_queries.gross_profit_totals` calls `GrossProfitGenerator` and sums the same fields as the report total row. |
| AR from GL | `erp_queries.receivable_balance`. |
| Collection rate definition | `allocated_payments_to_invoices / invoiced_total`, same period, per-invoice rate conversion. |
| Expenses from GL only | `erp_queries.expenses_from_gl`. Purchase Invoice totals are not added, so no double counting. Period Closing Vouchers and COGS are excluded. |
| Cash Collected separate | `erp_queries.cash_collected`. No cash-flow KPI. |
| Single dispatcher, per-rule frequency | One cron job every 15 minutes. `is_due` decides per rule and company. |
| No overlapping runs | Row lock on the Monitoring Rule during check-and-insert. A fresh Running heartbeat blocks a new run. Stale runs are marked Stale. |
| Duplicate queue prevention | `frappe.enqueue(deduplicate=True, job_id=business_radar:<rule>|<company>)`. |
| Retries with backoff | Failed runs retry at 5, 15, then 60 minutes, up to `max_run_retries`. The dispatcher reads the latest run. |
| Append-only Action Log | `on_update` and `on_trash` reject everything except the insert itself. `frappe.db.set_value` (system updates) skips hooks, so it is not used for Action Logs. |
| Action Log parameters hashed | `audit/action_log.hash_parameters` (SHA-256 over canonical JSON). |
| AI disabled by default | `Business Radar Settings.ai_enabled = 0`. No AI code runs in Phase 1. |
| Email disabled by default | `email_notifications_enabled = 0`. No email code in Phase 1. |

## Changes to the original plan

1. **Business Radar Health Snapshot** is added (approved). It is needed for historical health scores. It is implemented in Phase 2.
2. **Investigation Notes** use native Frappe Comments (approved). No separate DocType.
3. **Alert Evidence and Feedback** are child tables (approved). They belong to the Alert DocType in Phase 2.
4. **Severity bands and rule parameters** are child tables of Monitoring Rule (`Business Radar Severity Band`, `Business Radar Rule Parameter`). They are bounded structured data, so child tables are preferred to JSON.
5. **KPI role requirements are code, not configuration.** An administrator can enable or tune a KPI, but cannot grant roles to it. Otherwise a misconfiguration could expose financial data.
6. **Detection run statuses** are `Running`, `Success`, `Failed`, `Stale`. `Queued` was removed because jobs are enqueued without a stored intermediate state. The job queue itself shows queued jobs.
7. **Scheduled purge covers Detection Runs only.** Action Logs are never purged by the application, because they are the audit trail.

## Permission matrix (Phase 1 DocTypes)

| DocType | Administrator | Executive | Manager | Analyst | Viewer |
|---|---|---|---|---|---|
| Business Radar Settings | RW | – | – | – | – |
| Monitoring Rule | RWD | R | R | R | – |
| KPI Configuration | RW | R | R | R | R |
| Detection Run | R | R | R | R | – |
| Action Log | R | R | R | – | – |

Viewers have no access to Monitoring Rules, because rule thresholds are configuration detail they do not need. Business Radar roles are created in `install.before_install`, before DocType permissions are synced. `System Manager` keeps full access to Settings, as with other Frappe settings.

KPI values are visible only to roles listed in `FINANCIAL_KPI_ROLES`. This check is in code, not in the DocType permissions.

## Known gaps

- Phase 1 has no UI, workspace, or API endpoints. Those belong to Phase 2.
- Alerts, Recommendations, and AI are not implemented.
- Rule persistence of findings is not implemented. The runner executes the evaluator and records counts. Alert creation begins in Phase 2.
- Frappe v16 compatibility is not targeted.
