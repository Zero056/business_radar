# Read-only overlap review of existing bench apps

Scope: `tst`, `tst_admin_panel`, `support_hub`, `smart_code`, `wassenger_integration`.
Method: read `hooks.py`, DocType folders, and the permission modules referenced
from hooks. No files in those apps were modified. Review date: 2026-11-03.

Limitation: which apps are installed on each site is stored in the database. The
bench has no per-site `apps.txt`, so installed-app status is **not verified** yet.
It will be verified on the `zoma` test site once database access is available.

## Summary

| App | Overlap with Business Radar | Impact | Action |
|---|---|---|---|
| `tst` | **High.** Overrides Sales Invoice and Payment Entry classes. Adds portfolio-denial `permission_query_conditions` on Sales Invoice, Payment Entry, Sales Order, Delivery Note, Customer, Lead, Quotation and others. | Business Radar aggregates would see records a restricted user must not see. | Scope gate in `data_source/scope.py` detects these conditions generically through `DatabaseQuery.get_permission_query_conditions()`. Restricted users get `restricted` status, not totals. |
| `tst_admin_panel` | None. Website content DocTypes. `doc_events` only for website sections. Scheduler commented out. | None. | None. |
| `support_hub` | None in DocTypes or business logic. Its own permission hooks apply only to `Support *` DocTypes. | None. | None. |
| `smart_code` | Low. Development scanning tool (`dev_scan_run`, `dev_project`, `dev_code_finding`). Scheduler and doc events are commented out. | Its test fixtures under `tests/fixtures/synth_bench` contain deliberately unsafe DocTypes (`evil_controller`, `evil`, `gadget`). These must never be installed on a business site. | Do not install on `zoma` or production sites. Confirm not installed. |
| `wassenger_integration` | **Medium** for later phases. `on_submit` hooks on Sales Invoice, Purchase Invoice, Delivery Note and Payment Entry insert `WH Massage` records when enabled. | A future payment-reminder action could send a duplicate WhatsApp message. | Business Radar does not send WhatsApp in MVP. Phase 4 must check existing `WH Massage` records before any reminder. |

## Details

### tst

- `override_doctype_class`: Sales Invoice (`CustomSalesInvoice`), Payment Entry (`CustomPaymentEntry`), Quotation, Lead, Performa Invoice.
- `permission_query_conditions`: Customer, Lead, Quotation, Sales Order, Delivery Note, Main Installation Order, Payment Entry, Sales Invoice, Customer Data Complete, Lead Visit, Request for Change Performa.
  Implementation in `tst/portfolio_denial_permissions.py`: denied customers come from submitted Sales Rep Portfolio Handover rows. A bypass applies to some roles. `_portfolio_denial_bypass` returns true for privileged users.
- `has_permission` hooks for the same DocTypes (document level).
- `doc_events` on Sales Invoice, Payment Entry, Sales Order, Customer and others (not read by Business Radar).
- Scheduler: `hourly`, `monthly`, and cron `*/15` (`release_held_whatsapp_messages`) and `0 * * * *` (subscription and RFC workflows). No name collision with Business Radar jobs.
- `fixtures` on Custom Field, Property Setter, Server Script, Notification and others. Not affected by Business Radar.

Consequence: aggregates computed with SQL do not evaluate these conditions. The Data Source Layer therefore allows aggregates only for users without any record-level condition on the sensitive DocTypes. This decision is in `docs/architecture.md`.

### tst_admin_panel

- DocTypes: website and marketing content (Banner Section, Partners Section, Testimonial, Company Branch and others).
- `doc_events` clear website cache only.
- No business DocTypes overlap.

### support_hub

- `required_apps = ["frappe", "erpnext"]`.
- DocTypes: Support Ticket, Support Message, Support Hold History, Support Team, Support Agent Profile and others.
- `permission_query_conditions` and `has_permission` for Support DocTypes only.
- No overlap with Business Radar DocType names.

### smart_code

- Visible DocTypes (`dev_scan_run`, `dev_project`, `dev_code_finding`, `widget`, `widget_child`, `ba.json`) are part of a code-scanning tool.
- `tests/fixtures/synth_bench/apps/synth_app` contains `evil_controller`, `evil`, `gadget`. These are intentionally unsafe test fixtures and must not be installed.
- No scheduler or `doc_events` active.

### wassenger_integration

- `doc_events` `on_submit` for Sales Invoice, Purchase Invoice, Delivery Note and Payment Entry call `send_document_whatsapp_on_submit`.
- When the setting is enabled, the function inserts a `WH Massage` record. It does not send the message directly.
- Consequence for Business Radar: any reminder feature must check for pending or sent `WH Massage` entries for the same document to avoid duplicates. Decision deferred to Phase 4.

## Conflicts with Business Radar naming

- No existing DocType or module named `Business Radar`, `Business Radar *`, or `Business Radar Alert`.
- ERPNext and Frappe core have no DocType with these names. `Notification Log` and `Comment` are reused as native Frappe features.
