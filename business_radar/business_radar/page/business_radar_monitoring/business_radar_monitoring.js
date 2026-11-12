frappe.pages["business-radar-monitoring"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Monitoring"),
		single_column: true,
	});
	const $body = $('<div class="br-page"></div>').appendTo(page.main);
	const h = business_radar.escape;
	const PAGE_LENGTH = 20;
	const state = { start: 0, total: 0, rule: null, status: null, is_admin: false };

	page.rule_field = page.add_field({
		fieldname: "rule",
		label: __("Rule"),
		fieldtype: "Link",
		options: "Business Radar Monitoring Rule",
		change() {
			state.rule = page.rule_field.get_value() || null;
			state.start = 0;
			load_runs();
		},
	});
	page.status_field = page.add_field({
		fieldname: "status",
		label: __("Run Status"),
		fieldtype: "Select",
		options: ["", "Running", "Success", "Failed", "Stale"].join("\n"),
		change() {
			state.status = page.status_field.get_value() || null;
			state.start = 0;
			load_runs();
		},
	});

	// The button is only a convenience. The server re-checks the Administrator role on every run.
	state.is_admin = frappe.user.has_role("Business Radar Administrator");
	load_rules();
	load_runs();

	function load_rules() {
		business_radar.request("business_radar.api.monitoring.list_rules", {}, {
			on_success(rules) {
				render_rules(rules);
			},
			on_error() {
				$body.prepend(`<div class="br-card br-empty">${__("Could not load rules.")}</div>`);
			},
		});
	}

	function render_rules(rules) {
		const rows = rules.length
			? rules
					.map(
						(rule) => `<tr>
							<td><a href="/app/business-radar-monitoring-rule/${encodeURIComponent(rule.name)}">${h(rule.rule_name)}</a>
								<div class="br-muted">${h(rule.name)}</div></td>
							<td>${h(__(rule.category))}</td>
							<td>${rule.enabled ? h(__("Enabled")) : `<span class="br-muted">${h(__("Disabled"))}</span>`}</td>
							<td>${h(__(rule.frequency))}</td>
							<td>${h(rule.company || __("All companies"))}</td>
							<td>${h(rule.last_run_on || __("Never"))}</td>
							<td>${h(rule.last_status || "—")}</td>
							<td>${state.is_admin && rule.enabled ? `<button class="btn btn-default btn-xs" data-run="${h(rule.name)}">${h(__("Run now"))}</button>` : ""}</td>
						</tr>`
					)
					.join("")
			: `<tr><td colspan="8" class="br-empty">${h(__("No monitoring rules yet."))}</td></tr>`;

		const html = `<div class="br-card"><h4>${h(__("Rules"))}</h4>
			<table class="br-table"><thead><tr>
				<th>${h(__("Rule"))}</th><th>${h(__("Category"))}</th><th>${h(__("State"))}</th>
				<th>${h(__("Frequency"))}</th><th>${h(__("Company"))}</th><th>${h(__("Last run"))}</th>
				<th>${h(__("Last status"))}</th><th></th>
			</tr></thead><tbody>${rows}</tbody></table></div>`;
		$body.find(".br-rules").remove();
		$body.prepend(`<div class="br-rules">${html}</div>`);
		$body.find("[data-run]").on("click", function () {
			run_rule($(this).data("run"));
		});
	}

	function run_rule(rule_code) {
		frappe.prompt(
			[
				{
					fieldname: "company",
					label: __("Company (leave empty for all companies)"),
					fieldtype: "Link",
					options: "Company",
				},
			],
			(values) => {
				business_radar.request(
					"business_radar.api.monitoring.run_rule",
					{ rule_code, company: values.company || null },
					{
						on_success(result) {
							frappe.show_alert({
								message: __("Queued {0} run(s). Refresh this page to see the result.", [result.queued]),
								indicator: "green",
							});
							setTimeout(load_runs, 3000);
						},
					}
				);
			},
			__("Run {0} now", [rule_code]),
			__("Run")
		);
	}

	function load_runs() {
		business_radar.request(
			"business_radar.api.monitoring.list_runs",
			{ rule_code: state.rule, status: state.status, start: state.start, page_length: PAGE_LENGTH },
			{
				on_success(data) {
					state.total = data.total;
					render_runs(data.rows);
				},
				on_error() {
					$body.find(".br-runs").html(`<div class="br-empty">${__("Could not load run history.")}</div>`);
				},
			}
		);
	}

	function render_runs(rows) {
		const body = rows.length
			? rows
					.map(
						(run) => `<tr>
							<td>${h(run.rule)}<div class="br-muted">${h(run.company)}</div></td>
							<td>${h(__(run.status))}${run.error_message ? `<div class="br-impact-negative">${h(run.error_message)}</div>` : ""}</td>
							<td>${h(__(run.triggered_by || "Scheduler"))}${run.retry_count ? ` · ${h(__("retry {0}", [run.retry_count]))}` : ""}</td>
							<td>${h(run.started_at || "")}</td>
							<td>${run.duration_ms != null ? h((run.duration_ms / 1000).toFixed(2)) + " s" : "—"}</td>
							<td>${h(run.records_scanned ?? 0)}</td>
							<td>${h(run.alerts_created ?? 0)} / ${h(run.alerts_updated ?? 0)} / ${h(run.alerts_suppressed ?? 0)}</td>
						</tr>`
					)
					.join("")
			: `<tr><td colspan="7" class="br-empty">${h(__("No runs match these filters."))}</td></tr>`;
		const to = state.start + rows.length;
		$body.find(".br-runs").remove();
		$body.append(`<div class="br-card br-runs"><h4>${h(__("Execution history"))}</h4>
			<table class="br-table"><thead><tr>
				<th>${h(__("Rule and company"))}</th><th>${h(__("Status"))}</th><th>${h(__("Trigger"))}</th>
				<th>${h(__("Started"))}</th><th>${h(__("Duration"))}</th><th>${h(__("Records scanned"))}</th>
				<th>${h(__("Alerts created / updated / suppressed"))}</th>
			</tr></thead><tbody>${body}</tbody></table>
			<div style="display:flex;justify-content:space-between;margin-top:12px">
				<span class="br-muted">${h(__("Showing {0}–{1} of {2}", [state.start + 1, to, state.total]))}</span>
				<span>
					<button class="btn btn-default btn-sm" data-page="prev" ${state.start === 0 ? "disabled" : ""}>${h(__("Previous"))}</button>
					<button class="btn btn-default btn-sm" data-page="next" ${to >= state.total ? "disabled" : ""}>${h(__("Next"))}</button>
				</span></div></div>`);
		$body.find("[data-page=prev]").on("click", () => {
			state.start = Math.max(0, state.start - PAGE_LENGTH);
			load_runs();
		});
		$body.find("[data-page=next]").on("click", () => {
			state.start += PAGE_LENGTH;
			load_runs();
		});
	}
};
