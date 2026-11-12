frappe.pages["business-radar-alerts"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Alert Center"),
		single_column: true,
	});
	const $body = $('<div class="br-page"></div>').appendTo(page.main);
	const h = business_radar.escape;
	const PAGE_LENGTH = 25;
	const state = { start: 0, total: 0 };

	const filter_defs = [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company" },
		{
			fieldname: "severity",
			label: __("Severity"),
			fieldtype: "Select",
			options: ["", "Critical", "High", "Medium", "Low", "Informational"].join("\n"),
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: [
				"",
				"New",
				"Acknowledged",
				"Investigating",
				"Action Required",
				"Waiting for Approval",
				"In Progress",
				"Resolved",
				"Dismissed",
				"False Positive",
			].join("\n"),
		},
		{
			fieldname: "category",
			label: __("Category"),
			fieldtype: "Select",
			options: ["", "Financial", "Sales", "Inventory", "Operations"].join("\n"),
		},
		{ fieldname: "assigned", label: __("Assigned to me"), fieldtype: "Check" },
	];
	page.filters = {};
	filter_defs.forEach((def) => {
		page.filters[def.fieldname] = page.add_field({
			...def,
			change() {
				state.start = 0;
				load();
			},
		});
	});

	function current_filters() {
		const values = {};
		Object.entries(page.filters).forEach(([key, field]) => {
			const value = field.get_value();
			if (value) {
				values[key] = value;
			}
		});
		return values;
	}

	function apply_route_options() {
		const options = frappe.route_options || {};
		Object.entries(options).forEach(([key, value]) => {
			if (page.filters[key]) {
				page.filters[key].set_value(value);
			}
		});
		frappe.route_options = null;
	}

	function load() {
		const values = current_filters();
		business_radar.request(
			"business_radar.api.alerts.list_alerts",
			{
				company: values.company,
				severity: values.severity,
				status: values.status,
				category: values.category,
				assigned: values.assigned ? "me" : null,
				start: state.start,
				page_length: PAGE_LENGTH,
			},
			{
				on_success(data) {
					state.total = data.total;
					render(data.rows);
				},
				on_error() {
					$body.html(`<div class="br-empty">${__("Could not load alerts. Try again.")}</div>`);
				},
			}
		);
	}

	function render(rows) {
		if (!rows.length) {
			$body.html(
				`<div class="br-card br-empty">${__(
					"No alerts match these filters. Monitoring creates alerts when a rule finds a problem."
				)}</div>`
			);
			return;
		}
		const body = rows
			.map(
				(row) => `<tr>
					<td>${business_radar.severity_badge(row.severity)}</td>
					<td><a href="/app/business-radar-alert/${encodeURIComponent(row.name)}">${h(row.alert_title)}</a>
						<div class="br-muted">${h(row.rule_code)} · ${h(row.company)}</div></td>
					<td>${h(__(row.status))}</td>
					<td>${h(__(row.category))}</td>
					<td>${h(row.detected_on)}</td>
					<td>${h(row.assigned_to || __("Unassigned"))}</td>
					<td>${h(row.occurrences)}${row.reopen_count ? " · " + h(__("reopened {0}×", [row.reopen_count])) : ""}</td>
				</tr>`
			)
			.join("");
		const from = state.start + 1;
		const to = state.start + rows.length;
		const pager = `<div style="display:flex;justify-content:space-between;align-items:center;margin-top:12px">
			<span class="br-muted">${h(__("Showing {0}–{1} of {2}", [from, to, state.total]))}</span>
			<span>
				<button class="btn btn-default btn-sm" data-action="prev" ${state.start === 0 ? "disabled" : ""}>${h(__("Previous"))}</button>
				<button class="btn btn-default btn-sm" data-action="next" ${to >= state.total ? "disabled" : ""}>${h(__("Next"))}</button>
			</span></div>`;
		$body.html(`<div class="br-card">
			<table class="br-table"><thead><tr>
				<th>${h(__("Severity"))}</th><th>${h(__("Alert"))}</th><th>${h(__("Status"))}</th>
				<th>${h(__("Category"))}</th><th>${h(__("Detected"))}</th><th>${h(__("Assigned to"))}</th>
				<th>${h(__("Occurrences"))}</th>
			</tr></thead><tbody>${body}</tbody></table>${pager}</div>`);
		$body.find("[data-action=prev]").on("click", () => {
			state.start = Math.max(0, state.start - PAGE_LENGTH);
			load();
		});
		$body.find("[data-action=next]").on("click", () => {
			state.start += PAGE_LENGTH;
			load();
		});
	}

	apply_route_options();
	load();
};
