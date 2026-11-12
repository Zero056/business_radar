"""Shared constants. Keep this module free of Frappe imports so it stays cheap to import."""

ROLE_ADMINISTRATOR = "Business Radar Administrator"
ROLE_EXECUTIVE = "Business Radar Executive"
ROLE_MANAGER = "Business Radar Manager"
ROLE_ANALYST = "Business Radar Analyst"
ROLE_VIEWER = "Business Radar Viewer"

BUSINESS_RADAR_ROLES = (
	ROLE_ADMINISTRATOR,
	ROLE_EXECUTIVE,
	ROLE_MANAGER,
	ROLE_ANALYST,
	ROLE_VIEWER,
)

# Roles allowed to see financial KPIs. Viewers and users without accounting or
# Business Radar roles never receive financial values.
FINANCIAL_KPI_ROLES = (
	ROLE_ADMINISTRATOR,
	ROLE_EXECUTIVE,
	ROLE_MANAGER,
	ROLE_ANALYST,
	"Accounts Manager",
	"Accounts User",
)

CATEGORIES = ("Financial", "Sales", "Inventory", "Operations")

SEVERITIES = ("Critical", "High", "Medium", "Low", "Informational")

# Upper bound on a single KPI period, to keep API-triggered aggregates bounded.
MAX_PERIOD_DAYS = 370

DEFAULT_RETENTION_DAYS = 180
