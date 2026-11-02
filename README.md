# UPS Monitoring

A lightweight, multi-threaded monitoring service for **APC UPS devices**. It polls each unit over **SNMPv3**, evaluates its health against a set of alarm conditions, stores alarm history in a local SQLite database, and dispatches email alerts and daily summaries.

The project is written for **Python 3.9** and depends on `pysnmp` for SNMP access.
An in-progress experimental version is being tested using **Python 3.13** for a more up-to-date codebase.

---

## Features

- **Multi-threaded polling** of many UPS devices via SNMPv3 (SHA auth, AES-128 encryption),
  with a configurable worker-pool size and a graceful-shutdown mechanism (`SIGINT` / `SIGTERM`).
- **Health & alarm detection** for:
  - SNMP communication loss
  - Abnormal output status (e.g. `onBattery`, `off`, `hardwareFailureBypass`, …)
  - Battery replacement indicator
  - Battery runtime thresholds (< 15 min, < 10 min)
  - Battery age warning (> 4.5 years since last replacement)
  - Universal I/O **temperature** sensor alarms / violations
  - Universal I/O **contact** sensor alarms / faults
- **Severity-aware alerting** — critical alarms alert immediately, lower severities are
  delayed, and a daily summary email consolidates the rest.
- **Durable alarm history** in a local SQLite database (WAL mode), so alarms survive a power
  failure and are recovered on startup.
- **CSV exports** for unit age and battery age.
- **Optional live console dashboard** and **debug / time-test / DC-test** modes.

---

## Project layout

| File | Purpose |
|------|---------|
| `monitoring.py` | Main entry point. Loads the device list, starts the worker pool + notifier + daily-summary threads, manages the alarm database, and handles CSV exports. |
| `ups.py` | SNMP data-gathering functions and the `UpsDevice`, `UpsAlarm`, `UioTempSensor`, and `UioContactSensor` data structures. Implements the alarm logic and email-message synthesis. |
| `config.py` | All global, runtime-tunable configuration values (timeouts, file paths, email sender, severity maps, thread count, schedules). Edit this to match your environment. |
| `oid_maps.py` | SNMP OID maps and enum decoders for the `APC` and `APC_DC` models (`model_oid_maps`). |
| `send_mail.py` | SMTP email helper (`send_email`, `load_txt`); can also be run as a standalone CLI. |
| `requirements.txt` | Pinned Python dependencies. |
| `pyproject.toml` | Project metadata. |
| `ups-list.csv` | *(you create)* List of UPS devices to monitor. |
| `ups_email_list.txt` | *(you create)* Recipients for alert emails, one per line. |
| `ups_alarms.db` | *(generated)* SQLite alarm-history database, created on first run. |

---

## Requirements

- **Python 3.9** (see `.python-version`; `pyproject.toml` pins `==3.9.25`).
- SNMPv3 credentials for your APC devices (username + auth key + privacy key).
- An SMTP server to send alerts.

Install dependencies into a virtual environment with `uv`:

```bash
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

---

## Configuration

### 1. Device list — `ups-list.csv`

A CSV with a header row and one device per line. Lines starting with `#` are ignored.
Expected columns:

```csv
ip,name,importance
10.1.2.30,Residence Hall 2F,High
10.1.2.31,Basement UPS,Normal
# 10.1.2.32,Decommissioned,Normal
```
Importance reflects whether the High Importance flag is added onto the notification e-mail.

### 2. SNMP credentials

Set the SNMPv3 user, auth key, and privacy key passed into each `UpsDevice` in
`ups.py` (`UpsDevice.__init__` → `UsmUserData(...)`):

```python
snmpuser = <username>
authKey  = <APC Auth Key>
privKey  = <APC Priv Key>
```

Connections use `usmHMACSHAAuthProtocol` and `usmAesCfb128Protocol`.
Also confirm `LOCAL_ADDRESS` in `config.py` is the IP of the interface that reaches the UPS devices.

### 3. Email configuration

- **Recipients:** create `ups_email_list.txt` (path configurable via `EMAIL_LIST_FILE` in `config.py`) with one email address per line.
- **Sender address & SMTP server:** edit `EMAIL_FROM` in `config.py`
  (default `ups_monitor@domain.com`) and the SMTP host/port in `send_mail.py`
  (`smtplib.SMTP('server_address', 25)`). Enable `starttls()` if your server requires it.

### 4. Adding a new UPS model

Extend `oid_maps.py` with the model's OID/enum maps and register it in `model_oid_maps`.

---

## Usage

Run the monitoring service:

```bash
python monitoring.py
```

### `monitoring.py` CLI options

| Option | Default | Description |
|--------|---------|-------------|
| `--debug` | off | Print additional runtime information for troubleshooting. |
| `--printer` | off | Continuously refresh a console dashboard of the event log. |
| `-s-h`, `--summary-hour` | `8` | Hour (0–23) to send the daily summary email. |
| `-s-m`, `--summary-minute` | `0` | Minute (0–59) to send the daily summary email. |
| `--thread-ct` | `5` | Number of worker threads used to poll devices. |
| `--time-test` | off | Measure how long it takes to update all devices, then exit. |
| `--dc-test` | off | Refresh the last device in the list and print its full info. |
| `--unit-age-export` | off | Export `ups_unit_age.csv` and exit. |
| `--battery-age-export` | off | Export `ups_battery_age.csv` and exit. |

Examples:

```bash
python monitoring.py --printer --debug
python monitoring.py --summary-hour 6 --summary-minute 30 --thread-ct 8
python monitoring.py --unit-age-export
```

### `send_mail.py` standalone

```bash
python send_mail.py --from ups_monitor@domain.com \
    --to-file ups_email_list.txt \
    --subject "Test" \
    --body "Hello" \
    --importance High
```

---

## How it works

1. **Load** `ups-list.csv` into a list of `UpsDevice` objects and push them onto a thread-safe
   work queue.
2. **Worker pool** — each thread pulls a device, calls `device.refresh()` (SNMP query + alarm
   evaluation), then re-queues it for the next polling cycle.
3. **Alarm evaluation** (`UpsDevice.update_alarm_status`) compares the freshly polled data
   against the alarm conditions and produces `UpsAlarm` objects with a severity.
4. **Persistence** — active alarms are recorded in `ups_alarms.db`; resolved alarms are closed
   out, and old/aged rows are periodically pruned.
5. **Notifying** — `email_notifier` (every 45 s) sends alerts that have met their severity
   delay; `daily_summary_email_target` sends a consolidated summary each day.

### Alarm severities

| Severity | Name | Alert timing |
|----------|------|--------------|
| 0 | None | — |
| 1 | Low | Daily summary only |
| 2 | Medium | Daily summary |
| 3 | High | 5-minute delay |
| 4 | Critical | Immediate |

---

## Data files

- `ups_alarms.db` — SQLite (WAL) alarm history; auto-created on first run.
- `ups_unit_age.csv` / `ups_battery_age.csv` — generated by the corresponding export flags.
- `ups-list.csv`, `ups_email_list.txt` — user-maintained configuration inputs.

