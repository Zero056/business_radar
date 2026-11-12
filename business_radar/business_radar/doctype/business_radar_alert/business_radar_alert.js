frappe.ui.form.on("Business Radar Alert", {
	refresh(frm) {
		frm.page.clear_custom_actions?.();
		if (frm.is_new()) {
			return;
		}
		load_investigation(frm);
	},
});

const CLOSED = ["Resolved", "Dismissed", "False Positive"];

function load_investigation(frm) {
	business_radar.request(
		"business_radar.api.alerts.get_alert",
		{ name: frm.doc.name },
		{
			on_success(data) {
				frm.dashboard.clear_headline?.();
				frm.dashboard.add_section(render_investigation(data), __("Investigation"));
				add_actions(frm, data);
			},
			on_error() {
				frm.dashboard.add_section(
					`<div class="br-empty">${__("You do not have access to the full investigation for this alert.")}</div>`,
					__("Investigation")
				);
			},
		}
	);
}

function render_investigation(data) {
	const h = business_radar.escape;
	const row = (label, value) => `<tr><th>${h(label)}</th><td>${value}</td></tr>`;
	const evidence = data.evidence.length
		? data.evidence
				.map((item) => {
					const source = item.source_name
						? `<a href="/app/${frappe.router.slug(item.source_doctype)}/${encodeURIComponent(item.source_name)}">${h(item.source_name)}</a>`
						: "";
					return `<tr><td>${item.order}</td><td>${h(item.label)}</td><td>${h(item.value ?? "")}</td>
						<td>${source}</td><td class="br-muted">${h(item.calculation || "")}</td></tr>`;
				})
				.join("")
		: `<tr><td colspan="5" class="br-empty">${h(__("No evidence rows."))}</td></tr>`;
	const actions = data.recommended_actions.length
		? `<ul>${data.recommended_actions.map((line) => `<li>${h(line)}</li>`).join("")}</ul>`
		: `<span class="br-muted">${h(__("None"))}</span>`;
	const feedback = data.feedback.length
		? data.feedback
				.map((f) => `<li>${h(__(f.verdict))} · ${h(f.by)} · ${h(f.on)}${f.comment ? " — " + h(f.comment) : ""}</li>`)
				.join("")
		: `<span class="br-muted">${h(__("No feedback yet."))}</span>`;
	const values =
		data.detected_value === null || data.detected_value === undefined
			? h(__("Hidden: you do not have access to the source document."))
			: `${h(business_radar.format_value(data.detected_value, data.unit))} ${h(__("detected"))} · ${h(
					business_radar.format_value(data.expected_value, data.unit)
			  )} ${h(__("expected"))}`;

	return `<div class="br-page">
		<div class="br-card">
			<h4>${h(__("What happened"))}</h4>
			<div>${business_radar.severity_badge(data.severity)} ${business_radar.badge(__(data.status))}
				${business_radar.badge(__(data.evidence_type))}</div>
			<p style="margin-top:8px">${h(data.explanation || "")}</p>
			<table class="br-table">
				${row(__("When"), h(data.detected_on) + " · " + h(__("last evaluated")) + " " + h(data.last_evaluated_on))}
				${row(__("Times detected"), h(data.occurrences) + (data.reopen_count ? " · " + h(__("reopened {0}×", [data.reopen_count])) : ""))}
				${row(__("Expected vs detected"), values)}
				${row(__("Affected customer"), h(data.affected_customer || "—"))}
				${row(__("Affected item"), h(data.affected_item || "—"))}
				${row(__("Source document"), data.related_name ? h(data.related_doctype) + " " + h(data.related_name) : "—")}
				${row(__("Potential impact"), h(data.potential_impact || "—"))}
				${row(__("Recommended actions"), actions)}
				${row(__("Assigned to"), h(data.assigned_to || __("Unassigned")))}
				${row(__("Cooldown until"), h(data.cooldown_until || "—"))}
			</table>
		</div>
		<div class="br-card">
			<h4>${h(__("Evidence"))}</h4>
			<table class="br-table"><thead><tr><th>#</th><th>${h(__("Fact"))}</th><th>${h(__("Value"))}</th>
				<th>${h(__("Source"))}</th><th>${h(__("Calculation"))}</th></tr></thead>
			<tbody>${evidence}</tbody></table>
			<p class="br-muted" style="margin-top:8px">${h(__("Evidence type: {0}. Facts come from ERPNext records. Statistical and heuristic items are observations, not confirmed facts.", [__(data.evidence_type)]))}</p>
		</div>
		<div class="br-card"><h4>${h(__("Feedback"))}</h4><ul>${feedback}</ul></div>
	</div>`;
}

function add_actions(frm, data) {
	if (!data.can_write) {
		return;
	}
	const group = __("Investigate");
	const set = (status, prompt_resolution) => {
		const apply = (resolution) => {
			business_radar.request(
				"business_radar.api.alerts.set_status",
				{ name: frm.doc.name, status, resolution: resolution || null },
				{
					on_success() {
						frappe.show_alert({ message: __("Status updated to {0}", [__(status)]), indicator: "green" });
						frm.reload_doc();
					},
				}
			);
		};
		if (prompt_resolution) {
			frappe.prompt(
				[{ fieldname: "resolution", label: __("Resolution or reason"), fieldtype: "Small Text", reqd: 1 }],
				(values) => apply(values.resolution),
				__("{0} alert", [__(status)]),
				__("Confirm")
			);
		} else {
			apply(null);
		}
	};

	if (!CLOSED.includes(data.status)) {
		frm.add_custom_button(__("Acknowledge"), () => set("Acknowledged"), group);
		frm.add_custom_button(__("Start Investigation"), () => set("Investigating"), group);
		frm.add_custom_button(__("Action Required"), () => set("Action Required"), group);
		frm.add_custom_button(__("Resolve"), () => set("Resolved", true), group);
		frm.add_custom_button(__("Dismiss"), () => set("Dismissed", true), group);
		frm.add_custom_button(__("False Positive"), () => set("False Positive", true), group);
	}

	frm.add_custom_button(
		__("Assign"),
		() =>
			frappe.prompt(
				[{ fieldname: "user", label: __("User"), fieldtype: "Link", options: "User", reqd: 1 }],
				(values) =>
					business_radar.request(
						"business_radar.api.alerts.assign",
						{ name: frm.doc.name, user: values.user },
						{
							on_success() {
								frm.reload_doc();
							},
						}
					),
				__("Assign alert"),
				__("Assign")
			),
		__("Collaborate")
	);
	frm.add_custom_button(
		__("Add Note"),
		() =>
			frappe.prompt(
				[{ fieldname: "content", label: __("Note"), fieldtype: "Small Text", reqd: 1 }],
				(values) =>
					business_radar.request(
						"business_radar.api.alerts.add_note",
						{ name: frm.doc.name, content: values.content },
						{
							on_success() {
								frm.reload_doc();
							},
						}
					),
				__("Investigation note"),
				__("Save")
			),
		__("Collaborate")
	);
	frm.add_custom_button(
		__("Give Feedback"),
		() =>
			frappe.prompt(
				[
					{
						fieldname: "verdict",
						label: __("Verdict"),
						fieldtype: "Select",
						options: "Correct\nIncorrect\nPartially correct",
						reqd: 1,
					},
					{ fieldname: "comment", label: __("Comment"), fieldtype: "Small Text" },
				],
				(values) =>
					business_radar.request(
						"business_radar.api.alerts.add_feedback",
						{ name: frm.doc.name, verdict: values.verdict, comment: values.comment || null },
						{
							on_success() {
								frm.reload_doc();
							},
						}
					),
				__("Was this alert correct?"),
				__("Submit")
			),
		__("Collaborate")
	);
}
