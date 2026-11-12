import frappe
from frappe.tests.utils import FrappeTestCase

from business_radar.analytics.kpi.definitions import KPI_DEFINITIONS
from business_radar.constants import BUSINESS_RADAR_ROLES


class TestInstallAndSettings(FrappeTestCase):
	def test_roles_exist_after_install(self):
		for role in BUSINESS_RADAR_ROLES:
			self.assertTrue(frappe.db.exists("Role", role), role)

	def test_kpi_configuration_exists_for_every_definition(self):
		for definition in KPI_DEFINITIONS:
			self.assertTrue(
				frappe.db.exists("Business Radar KPI Configuration", definition.code), definition.code
			)

	def test_defaults_keep_ai_and_email_disabled(self):
		settings = frappe.get_single("Business Radar Settings")
		self.assertEqual(int(settings.ai_enabled or 0), 0)
		self.assertEqual(int(settings.email_notifications_enabled or 0), 0)
		self.assertEqual(int(settings.monitoring_enabled), 1)

	def test_settings_reject_out_of_range_values(self):
		settings = frappe.get_single("Business Radar Settings")
		settings.stale_run_timeout_minutes = 1
		self.assertRaises(frappe.ValidationError, settings.save)
		settings.reload()

	def test_monitoring_rule_rejects_unregistered_code(self):
		rule = frappe.get_doc(
			{
				"doctype": "Business Radar Monitoring Rule",
				"rule_code": "NOT_A_REAL_RULE",
				"rule_name": "Invalid",
				"category": "Financial",
				"frequency": "Manual",
			}
		)
		self.assertRaises(frappe.ValidationError, rule.insert)

	def test_kpi_configuration_rejects_unknown_code(self):
		config = frappe.get_doc(
			{
				"doctype": "Business Radar KPI Configuration",
				"kpi_code": "NOT_A_KPI",
			}
		)
		self.assertRaises(frappe.ValidationError, config.insert)

	def test_kpi_configuration_label_comes_from_code(self):
		# Validate in memory only, so the real configuration row is never saved by the test.
		config = frappe.get_doc("Business Radar KPI Configuration", "NET_REVENUE")
		config.kpi_label = "Tampered"
		config.validate()
		self.assertEqual(config.kpi_label, "Net Revenue")
