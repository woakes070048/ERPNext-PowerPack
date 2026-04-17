# Copyright (c) 2026, Cecypo.Tech and Contributors
# See license.txt

import json

import frappe
from frappe.tests.utils import FrappeTestCase


class TestResolveBillNumbersForCredit(FrappeTestCase):
	"""Tests for cecypo_powerpack.api.resolve_bill_numbers_for_credit."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = frappe.db.get_value("Company", {}, "name") or frappe.get_all("Company", limit=1)[0].name
		cls.supplier_name = _ensure_supplier("_Test ZAP Supplier")

	def setUp(self):
		# Ensure feature on before each test; individual tests may toggle it
		settings = frappe.get_single("PowerPack Settings")
		settings.enable_payment_reconciliation_powerup = 1
		settings.save()

	def tearDown(self):
		frappe.db.rollback()

	def test_feature_disabled_throws(self):
		from cecypo_powerpack.api import resolve_bill_numbers_for_credit

		settings = frappe.get_single("PowerPack Settings")
		settings.enable_payment_reconciliation_powerup = 0
		settings.save()

		with self.assertRaises(frappe.ValidationError):
			resolve_bill_numbers_for_credit(
				company=self.company,
				supplier=self.supplier_name,
				bill_numbers=json.dumps(["BILL/ANY"]),
			)


def _ensure_supplier(name: str) -> str:
	if frappe.db.exists("Supplier", name):
		return name
	supplier = frappe.get_doc({
		"doctype": "Supplier",
		"supplier_name": name,
		"supplier_group": frappe.db.get_value("Supplier Group", {}, "name"),
	}).insert(ignore_permissions=True)
	return supplier.name
