app_name = "business_radar"
app_title = "Business Radar"
app_publisher = "Hazem"
app_description = "Deterministic business monitoring, anomaly detection and decision support for Frappe and ERPNext"
app_email = "business-radar@example.com"
app_license = "MIT"

required_apps = ["frappe", "erpnext"]

# Roles must exist before DocType permissions are synced, hence before_install.
before_install = "business_radar.install.before_install"
after_install = "business_radar.install.after_install"
after_migrate = ["business_radar.install.after_migrate"]

# The dispatcher runs every 15 minutes and enqueues only rules that are due
# according to their configured frequency. Heavy work never runs in the scheduler.
scheduler_events = {
    "cron": {
        "*/15 * * * *": [
            "business_radar.monitoring.scheduler.dispatch_due_runs",
        ],
    },
    "daily": [
        "business_radar.monitoring.retention.purge_expired_logs",
    ],
}
