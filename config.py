# ============================================================================
# UPS Monitoring - Global Configuration
# ============================================================================
#
# This module holds all global, runtime-tunable configuration values for the
# UPS monitoring system. Edit the values below to match your environment.
#
# SNMP device credentials (username / authKey / privKey) are intentionally NOT
# stored here; they remain configured on the UpsDevice objects in ups.py.
# ============================================================================

# --- SNMP transport ---
# Timeout (in seconds) for each SNMP request.
SNMP_TIMEOUT = 5

# Retries per SNMP request.
SNMP_RETRIES = 1

# Local IP of the interface used to reach the UPS devices.
LOCAL_ADDRESS = "127.0.0.1"  # Place IP of interface connecting to UPS devices

# --- File locations ---
# CSV of (IP, NAME, IMPORTANCE) pairings; header row and lines starting with
# '#' are skipped.
DEVICE_LIST_CSV = "ups-list.csv"

# SQLite database storing alarm history. Created on first run.
ALARM_DB = "ups_alarms.db"

# Plain-text file of email recipients, one address per line.
EMAIL_LIST_FILE = "ups_email_list.txt"

# Export output files.
UNIT_AGE_CSV = "ups_unit_age.csv"
BATTERY_AGE_CSV = "ups_battery_age.csv"

# --- Email ---
# Address used as the "From" for all alert and summary emails.
EMAIL_FROM = "ups_monitor@domain.com"

# --- Alarm timing ---
# Delay before an alarm is eligible for an email, keyed by severity (seconds).
#   0 (None)  -> 2 days
#   1 (Low)   -> 2 days
#   2 (Medium)-> 2 days (picked up by the daily summary)
#   3 (High)  -> 5 minutes
#   4 (Critical)-> immediate
SEVERITY_TIME_MAP = {
    0: 1440 * 60 * 2,
    1: 1440 * 60 * 2,
    2: 1440 * 60 * 2,
    3: 5 * 60,
    4: 0,
}

# Human-readable label for each severity level.
SEVERITY_STRING_MAP = {
    0: "None",
    1: "Low",
    2: "Medium",
    3: "High",
    4: "Critical",
}

# --- Scheduling / threading ---
# How many threads update UPS devices.
THREAD_COUNT = 5

# Pause (in seconds) after the device-update threads start, before the notifier
# and daily-summary threads begin.
STARTUP_DELAY = 30

# Initial delay before the notifier thread sends its first check, giving the
# system time to populate information.
NOTIFIER_INITIAL_DELAY = 45

# Hour (0-23) to send the daily summary email.
DAILY_SUMMARY_HOUR = 8

# Minute (0-59) to send the daily summary email.
DAILY_SUMMARY_MINUTE = 0

# --- Persistence / cleanup ---
# Minimum window between alarm re-occurrences before a new row is written
# (minutes). Alarms within this window of a recent event are deduplicated.
ALARM_DEDUP_WINDOW_MINUTES = 30

# How long resolved alarm rows are retained before being purged (days).
ALARM_RETENTION_DAYS = 30
