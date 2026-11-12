from datetime import date

import frappe
from frappe.tests.utils import FrappeTestCase

from business_radar.analytics.kpi.definitions import KPI_DEFINITIONS
from business_radar.analytics.kpi.service import compute_kpis
from business_radar.data_source.scope import AccessScope, require_company_level

RESTRICTED_EMAIL = "br-restricted-test@example.com"
NO_ROLE_EMAIL = "br-noroles-test@example.com"
VIEWER_EMAIL = "br-viewer-test@example.com"


def _ensure_user(email, roles):
	if not frappe.db.exists("User", email):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "Business Radar Test",
				"send_welcome_email": 0,
				"enabled": 1,
			}
		)
		user.insert(ignore_permissions=True)
	user = frappe.get_doc("User", email)
	user.roles = []
	for role in roles:
		user.append("roles", {"role": role})
	user.save(ignore_permissions=True)
	return email


class TestKPIScope(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = frappe.get_all("Company", pluck="name", limit=1)
		cls.company = cls.company[0] if cls.company else None

	def setUp(self):
		if not self.company:
			self.skipTest("No Company on this site; scope tests need one.")
		self.start = date(2026, 1, 1)
		self.end = date(2026, 1, 31)

	@classmethod
	def tearDownClass(cls):
		frappe.db.delete("User Permission", {"user": ["in", [RESTRICTED_EMAIL, VIEWER_EMAIL, NO_ROLE_EMAIL]]})
		for email in (RESTRICTED_EMAIL, VIEWER_EMAIL, NO_ROLE_EMAIL):
			if frappe.db.exists("User", email):
				frappe.delete_doc("User", email, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	def tearDown(self):
		frappe.db.delete("User Permission", {"user": RESTRICTED_EMAIL})

	def test_invalid_period_is_rejected(self):
		self.assertRaises(
			frappe.ValidationError,
			compute_kpis,
			self.company,
			date(2026, 2, 1),
			date(2026, 1, 1),
			None,
			"Administrator",
		)
		self.assertRaises(
			frappe.ValidationError,
			compute_kpis,
			self.company,
			date(2020, 1, 1),
			date(2026, 1, 1),
			None,
			"Administrator",
		)

	def test_user_without_financial_role_gets_not_permitted_for_every_kpi(self):
		email = _ensure_user(VIEWER_EMAIL, ["Business Radar Viewer"])
		results = compute_kpis(self.company, self.start, self.end, user=email)
		self.assertTrue(results)
		for result in results:
			self.assertEqual(result["status"], "not_permitted", result["code"])
			self.assertIsNone(result["value"])

	def test_user_without_any_roles_gets_not_permitted(self):
		email = _ensure_user(NO_ROLE_EMAIL, [])
		results = compute_kpis(self.company, self.start, self.end, user=email)
		self.assertTrue(all(result["status"] == "not_permitted" for result in results))

	def test_customer_restricted_user_never_receives_company_totals(self):
		email = _ensure_user(
			RESTRICTED_EMAIL, ["Business Radar Executive", "Accounts User"]
		)
		customer = frappe.get_all("Customer", pluck="name", limit=1)
		if not customer:
			self.skipTest("No Customer on this site; cannot create a customer restriction.")
		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": email,
				"allow": "Customer",
				"for_value": customer[0],
			}
		).insert(ignore_permissions=True)

		results = compute_kpis(self.company, self.start, self.end, user=email)
		company_level = [item for item in results if item["code"] in {d.code for d in KPI_DEFINITIONS if d.company_level_only}]
		self.assertTrue(company_level)
		for result in company_level:
			self.assertIn(result["status"], {"restricted", "not_permitted"}, result["code"])
			self.assertIsNone(result["value"], result["code"])

	def test_require_company_level_blocks_restricted_scope(self):
		restricted = AccessScope(
			user="someone@example.com",
			company=self.company,
			company_level=False,
			restrictions=("party-level user permission",),
		)
		self.assertRaises(PermissionError, require_company_level, restricted, self.company)

	def test_require_company_level_blocks_other_company(self):
		scope = AccessScope(user="Administrator", company=self.company, company_level=True)
		self.assertRaises(PermissionError, require_company_level, scope, self.company + "-other")

	def test_disabled_kpi_is_omitted(self):
		frappe.db.set_value("Business Radar KPI Configuration", "CASH_COLLECTED", "enabled", 0)
		try:
			results = compute_kpis(self.company, self.start, self.end, codes=["CASH_COLLECTED"], user="Administrator")
			self.assertEqual(results, [])
		finally:
			frappe.db.set_value("Business Radar KPI Configuration", "CASH_COLLECTED", "enabled", 1)
