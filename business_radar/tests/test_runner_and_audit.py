from datetime import timedelta
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from business_radar.audit.action_log import hash_parameters, record_action
from business_radar.monitoring.registry import RuleDefinition, RuleOutcome, register_rule, unregister_rule
from business_radar.monitoring.runner import acquire_run, build_job_id, enqueue_run, run_rule

TEST_RULE = "BR_TEST_RULE"
FAILING_RULE = "BR_TEST_FAILING"
RULE_DOCTYPE = "Business Radar Monitoring Rule"
RUN_DOCTYPE = "Business Radar Detection Run"


def _ok_evaluator(context):
	return RuleOutcome(records_scanned=7)


def _failing_evaluator(context):
	raise RuntimeError("simulated evaluator failure")


def _company():
	return frappe.get_all("Company", pluck="name", limit=1)[0] if frappe.db.count("Company") else None


class TestRunnerAndAudit(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		register_rule(
			RuleDefinition(
				code=TEST_RULE,
				name="Test rule",
				category="Financial",
				target_doctype="Sales Invoice",
				description="Test-only rule",
				evaluate=_ok_evaluator,
			)
		)
		register_rule(
			RuleDefinition(
				code=FAILING_RULE,
				name="Failing rule",
				category="Financial",
				target_doctype="Sales Invoice",
				description="Test-only rule that always fails",
				evaluate=_failing_evaluator,
			)
		)

	@classmethod
	def tearDownClass(cls):
		unregister_rule(TEST_RULE)
		unregister_rule(FAILING_RULE)
		super().tearDownClass()

	def setUp(self):
		self.company = _company()
		if not self.company:
			self.skipTest("No Company on this site; company-scoped runner tests need one.")
		self.created_rules = []
		for code in (TEST_RULE, FAILING_RULE):
			self._create_rule(code)

	def tearDown(self):
		frappe.db.delete(RUN_DOCTYPE, {"rule": ["in", self.created_rules]})
		for code in self.created_rules:
			frappe.db.delete(RULE_DOCTYPE, {"name": code})
		# Action Logs are append-only in the application. Tests remove their own rows directly.
		frappe.db.delete("Business Radar Action Log", {"action_type": ["like", "test.%"]})
		frappe.db.delete("Error Log", {"method": ["like", "Business Radar run failed%"]})
		frappe.db.commit()

	def _create_rule(self, code):
		frappe.db.delete(RULE_DOCTYPE, {"name": code})
		rule = frappe.get_doc(
			{
				"doctype": RULE_DOCTYPE,
				"rule_code": code,
				"rule_name": code,
				"category": "Financial",
				"enabled": 1,
				"frequency": "Manual",
				"cooldown_hours": 24,
				"lookback_days": 30,
			}
		)
		rule.insert(ignore_permissions=True)
		self.created_rules.append(code)
		return rule

	def test_successful_run_records_status_and_counts(self):
		run_name = run_rule(TEST_RULE, self.company, triggered_by="Manual")
		run = frappe.get_doc(RUN_DOCTYPE, run_name)
		self.assertEqual(run.status, "Success")
		self.assertEqual(run.records_scanned, 7)
		self.assertIsNotNone(run.period_start)
		self.assertIsNotNone(run.ended_at)

	def test_failed_run_is_recorded_and_does_not_raise(self):
		run_name = run_rule(FAILING_RULE, self.company, triggered_by="Manual")
		run = frappe.get_doc(RUN_DOCTYPE, run_name)
		self.assertEqual(run.status, "Failed")
		self.assertIn("simulated evaluator failure", run.error_message)

	def test_disabled_rule_does_not_create_a_run(self):
		frappe.db.set_value(RULE_DOCTYPE, TEST_RULE, "enabled", 0)
		frappe.db.commit()
		self.assertIsNone(run_rule(TEST_RULE, self.company))
		self.assertFalse(frappe.db.exists(RUN_DOCTYPE, {"rule": TEST_RULE}))

	def test_fresh_running_run_blocks_second_run(self):
		first = acquire_run(TEST_RULE, self.company, "Manual")
		self.assertIsNotNone(first)
		self.assertIsNone(acquire_run(TEST_RULE, self.company, "Scheduler"))

	def test_expired_heartbeat_is_marked_stale_and_superseded(self):
		first = acquire_run(TEST_RULE, self.company, "Manual")
		old = now_datetime() - timedelta(hours=5)
		frappe.db.set_value(RUN_DOCTYPE, first.name, "heartbeat_at", old)
		frappe.db.commit()

		second = acquire_run(TEST_RULE, self.company, "Scheduler")
		self.assertIsNotNone(second)
		self.assertEqual(frappe.db.get_value(RUN_DOCTYPE, first.name, "status"), "Stale")

	def test_enqueue_is_deduplicated_by_job_id(self):
		with mock.patch("frappe.enqueue") as enqueue:
			enqueue_run(TEST_RULE, self.company, triggered_by="Scheduler")
		kwargs = enqueue.call_args.kwargs
		self.assertTrue(kwargs["deduplicate"])
		self.assertEqual(kwargs["job_id"], build_job_id(TEST_RULE, self.company))
		self.assertEqual(kwargs["queue"], "long")

	def test_detection_run_cannot_be_edited_by_users(self):
		run_name = run_rule(TEST_RULE, self.company, triggered_by="Manual")
		run = frappe.get_doc(RUN_DOCTYPE, run_name)
		run.records_scanned = 999
		self.assertRaises(frappe.ValidationError, run.save)

	def test_action_log_is_append_only(self):
		name = record_action("test.action", "Recorded", result="ok")
		log = frappe.get_doc("Business Radar Action Log", name)
		log.result = "changed"
		self.assertRaises(frappe.ValidationError, log.save)
		self.assertRaises(frappe.ValidationError, frappe.delete_doc, "Business Radar Action Log", name)

	def test_action_log_rejects_unknown_event(self):
		self.assertRaises(ValueError, record_action, "test.action", "Hacked")

	def test_parameters_are_hashed_not_stored(self):
		name = record_action("test.action", "Attempted", parameters={"secret": "value"})
		stored = frappe.db.get_value("Business Radar Action Log", name, "parameters_hash")
		self.assertEqual(stored, hash_parameters({"secret": "value"}))
		self.assertNotIn("value", stored)
