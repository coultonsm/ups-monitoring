import threading
from datetime import datetime, timedelta
import os
import csv
import sqlite3
import queue
import argparse
import signal
import time

#custom modules
from send_mail import send_email, load_txt
from ups import *
from config import *

start_time = datetime.now()

#Load the CSV of (IP,NAME) pairings, skip the header row, and create UpsDevice objects for each
with open(DEVICE_LIST_CSV, 'r', encoding='utf-8-sig') as file:
    upsIpAddresses=list(csv.reader(file))
#Create list of UpsDevice objects from the CSV, skipping the header row
upsDevices = [UpsDevice(ip=ip, model='APC', custom_name=name, importance=importance) for ip, name, importance in upsIpAddresses[1:] if not ip.startswith("#")]

#Create a queue of devices to be shared among threads for updating
#Queues are thread safe and do not need Locks.
work_queue= queue.Queue()
for device in upsDevices:
    work_queue.put(device)

#Graceful Shutdown event
shutdown_event = threading.Event()

#Create local DB for alarm events for history and in case of power failure. Recovered on startup
con = sqlite3.connect(ALARM_DB)
con.execute("PRAGMA journal_mode=WAL;")
cur = con.cursor()
cur.execute('''CREATE TABLE IF NOT EXISTS alarm_events
               (id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_ip TEXT,
                device_name TEXT,
                alarm_type INTEGER,
                severity INTEGER,
                heading TEXT,
                body TEXT,
                start_time TEXT,
                end_time TEXT,
                sent_email INTEGER DEFAULT 0)''')
con.commit()

#Database lock
db_lock = threading.Lock()

def update_target(queue,id,debug=False):
    """
    Target function for threads to continuously update devices in the queue.
    Each thread will take a device from the queue, update it, and then put it in the back of the queue to be updated again later.
    """
    #End loop when shutting down
    while not shutdown_event.is_set():
        if queue.empty():
            # this block should only be reached during the time test
            shutdown_event.set()
            continue
        device = queue.get() #Grab new device from the queue
        try:
            if debug:
                print(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + f"update_target::{id} updating device in queue {device.ip}")
            update_device(device) #update the device (check for new alarms, and update the log)
        finally:
            queue.task_done() #Signal the task is done, even if an exception occurred
            if not args.time_test:
                queue.put(device) #Place the device back into the work queue for a thread to update it again later

def update_device(device: UpsDevice):
    """Refresh a device and persist alarm creation and resolution in SQLite."""
    with device.lock:
        alarm_ids = ALARM_CONSTANTS.copy()
        device.refresh()

        if device.alarm_status:
            for alarm in device.alarms:
                with db_lock:
                    with sqlite3.connect(ALARM_DB) as con_l:
                        cur = con_l.cursor()
                        cutoff = datetime.now() - timedelta(minutes=ALARM_DEDUP_WINDOW_MINUTES)
                        cur.execute('''SELECT id FROM alarm_events
                                       WHERE device_ip = ? AND alarm_type = ?
                                       AND (end_time IS NULL OR julianday(end_time) > julianday(?))''',
                                    (device.ip, alarm.type, str(cutoff)))
                        existing_alarm = cur.fetchone()
                        if existing_alarm is None:
                            heading, body = synthesize_email_message(device, alarm)
                            cur.execute('''INSERT INTO alarm_events
                                           (device_ip, device_name, alarm_type, severity, heading, body, start_time, sent_email)
                                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                                        (device.ip, device.custom_name, alarm.type, alarm.severity,
                                         heading, body, str(alarm.start_time), 0))
                            alarm_id = cur.lastrowid
                            con_l.commit()
                            print(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + f"update_device:: New alarm on {device.custom_name}: {heading}")
                        else:
                            alarm_id = existing_alarm[0]
                alarm._set_local_id(alarm_id)

                # Helper list to clear alarms
                if alarm.type in alarm_ids:
                    alarm_ids.remove(alarm.type)

        with db_lock:
            with sqlite3.connect(ALARM_DB) as con_l:
                cur = con_l.cursor()
                end_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
                for alarm_type in alarm_ids:
                    cur.execute('''UPDATE alarm_events
                                   SET end_time = ?
                                   WHERE device_ip = ? AND device_name = ? AND alarm_type = ? AND end_time IS NULL''',
                                (end_time, device.ip, device.custom_name, alarm_type))
                con_l.commit()

    with db_lock:
        with sqlite3.connect(ALARM_DB) as con_l:
            con_l.execute('''DELETE FROM alarm_events
                             WHERE end_time IS NOT NULL
                             AND julianday(end_time) <= julianday(?)''',
                          (str(datetime.now() - timedelta(minutes=ALARM_DEDUP_WINDOW_MINUTES)),))
            con_l.commit()

def print_event_log():
    """Print a summary of the current event log, including all active alarms and their details.
    Should be used only during testing and debug purposes"""
    os.system('clear')
    with db_lock:
        with sqlite3.connect(ALARM_DB) as con_l:
            events = con_l.execute('''SELECT device_ip, device_name, severity, heading, body,
                                             start_time, end_time
                                      FROM alarm_events ORDER BY start_time''').fetchall()
    summaryString = "UPS Event Log\n"
    summaryString += f"{len(upsDevices)} devices loaded.\n"
    summaryString += f"{len(events)} events in event log.\n"
    if events:
        summaryString += '\n'
        for device_ip, device_name, severity, heading, body, start_time, end_time in events:
            status = f"et={end_time} " if end_time else ""
            summaryString += f"{device_ip} / {device_name}\n"
            summaryString += f"t={start_time} {status}{heading}: {severity}\n{body}\n"

    print(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + summaryString)
    return summaryString

def printer():
    while not shutdown_event.is_set():
        print_event_log()
        shutdown_event.wait(1)

def email_notifier():
    """Send eligible SQLite alarm rows and mark successfully sent rows."""
    pending_alerts = []

    with db_lock:
        with sqlite3.connect(ALARM_DB) as con_l:
            cur = con_l.cursor()
            for severity in range(0, 5):
                allowed_time_delta = SEVERITY_TIME_MAP[severity]
                threshold = datetime.now() - timedelta(seconds=allowed_time_delta)
                cur.execute('''SELECT id, device_ip, device_name, severity, heading, body, start_time
                               FROM alarm_events
                               WHERE end_time IS NULL AND sent_email = 0 AND severity = ?
                               AND julianday(start_time) < julianday(?)''',
                            (severity, str(threshold)))
                pending_alerts.extend(cur.fetchall())

    for alarm_id, device_ip, device_name, severity, heading, alarm_body, start_time in pending_alerts:
        device = next((candidate for candidate in upsDevices if candidate.ip == device_ip), None)
        if device is None:
            continue

        with device.lock:
            device_info = device.display_all_info()
            device_importance = device.importance

        subject = f"[UPS] {SEVERITY_STRING_MAP.get(severity)}: {device_name} - {heading}"
        body = f"{alarm_body}\nInfo of the affected UPS:\n{device_info}"

        if send_email(EMAIL_FROM, load_txt(EMAIL_LIST_FILE), subject, body, device_importance):
            print(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + f"email_notifier:: New email alert {subject}")
            with db_lock:
                with sqlite3.connect(ALARM_DB) as con_l:
                    cur = con_l.cursor()
                    cur.execute('''UPDATE alarm_events SET sent_email = 1
                                   WHERE id = ? AND end_time IS NULL AND sent_email = 0''', (alarm_id,))
                    con_l.commit()


    #Cleanup
    with db_lock:
        with sqlite3.connect(ALARM_DB) as con_l:
            cur = con_l.cursor()
            cutoff = (datetime.now() - timedelta(days=ALARM_RETENTION_DAYS)).strftime('%Y-%m-%d %H:%M:%S')
            cur.execute('''DELETE FROM alarm_events 
                        WHERE end_time IS NOT NULL
                        AND end_time <= ?''', (cutoff,))
            con_l.commit()

def daily_summary_email():
    """Send a summary of active SQLite alarm rows that have not been alerted."""
    with db_lock:
        with sqlite3.connect(ALARM_DB) as con_l:
            active_alarms = con_l.execute('''SELECT id, device_ip, device_name, severity, heading, body, start_time
                                             FROM alarm_events
                                             WHERE end_time IS NULL AND sent_email = 0
                                             ORDER BY severity DESC, start_time''').fetchall()

    daily_sent_alarm_ids = [alarm[0] for alarm in active_alarms]
    if active_alarms:
        email_body = f"Daily Summary of new UPS Alarms {datetime.today().date()}:\n\n"
        for alarm_id, device_ip, device_name, severity, heading, alarm_body, start_time in active_alarms:
            email_body += f"Device: {device_name} ({device_ip})\n"
            email_body += f"[{SEVERITY_STRING_MAP.get(severity)}] {heading}\n"
            email_body += f"{alarm_body}\n"
            email_body += f"Started at: {start_time}\n\n"

    if daily_sent_alarm_ids:
        if send_email(EMAIL_FROM, load_txt(EMAIL_LIST_FILE), f"[UPS] Daily Summary {datetime.today().date()}", email_body):
            if daily_sent_alarm_ids:
                with db_lock:
                    with sqlite3.connect(ALARM_DB) as con_l:
                        cur = con_l.cursor()
                        cur.execute(f"UPDATE alarm_events SET sent_email = 1 WHERE id IN ({','.join('?' for _ in daily_sent_alarm_ids)})", tuple(daily_sent_alarm_ids))
                        con_l.commit()
    else:
        send_email(EMAIL_FROM, load_txt(EMAIL_LIST_FILE), f"[UPS] Daily Summary {datetime.today().date()}", "No new UPS alarms to report today.")

def email_notifier_target():
    """Target function for the email notifier thread to continuously check for new alarms and send email alerts."""
    shutdown_event.wait(NOTIFIER_INITIAL_DELAY) #Initial delay before first check to allow system to populate with information before sending notifications.
    while not shutdown_event.is_set():
        email_notifier()
        shutdown_event.wait(1)

def daily_summary_email_target(hour=DAILY_SUMMARY_HOUR, minute=DAILY_SUMMARY_MINUTE):
    """Target function for a thread to send a daily summary email at a specific time each day.
    (Default: 8:00 AM unless overridden)"""
    while not shutdown_event.is_set():
        now = datetime.now()
        next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if debug: print(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + f"daily_summary_email_target:: Current time: {now}, next run original: {next_run}")
        if next_run < now:
            next_run += timedelta(days=1)
            if debug: print(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + f"daily_summary_email_target:: Current time: {now}, next run new: {next_run}")
        time_to_wait = (next_run - now).total_seconds()
        if debug: print(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + f"daily_summary_email_target:: Waiting {time_to_wait} seconds to send daily summary email.")
        shutdown_event.wait(time_to_wait)
        if not shutdown_event.is_set(): #the daily summary email is triggered on shutdown if this is missing
            daily_summary_email()

def unit_age_export(debug=False):
    with open(UNIT_AGE_CSV, 'w', encoding='utf-8') as csvfile:
        csv_writer = csv.writer(csvfile)
        csv_writer.writerow(["Device Name", "Serial Number", "Manufacture Date", "Input Voltage", "Load Percent", "Output VA"])

    for device in upsDevices:
        with device.lock:
            device.refresh()
            if device.basic:
                deviceName = device.custom_name
                serialNumber = device.basic.get('serialNumber')
                manufactureDate = device.basic.get('dateOfManufacture')
                inputVoltage = device.basic.get('inputVoltage')
                loadPct = device.basic.get('loadPct')
                loadVA = device.basic.get('outputVA')
            else:
                print(f"WARNING: Device with IP {device.ip} has no basic information, skipping unit age export.")
                continue
            if debug:
                print(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + f"unit_age_export:: Exporting {deviceName}, Serial: {serialNumber}, Manufacture Date: {manufactureDate}, Input Voltage: {inputVoltage}, Load Percent: {loadPct}")

        with open(UNIT_AGE_CSV, 'a', encoding='utf-8') as csvfile:
            csv_writer = csv.writer(csvfile)
            csv_writer.writerow([deviceName, serialNumber, manufactureDate, inputVoltage, loadPct, loadVA])

def battery_age_export(debug=False):
    with open(BATTERY_AGE_CSV, 'w', encoding='utf-8') as csvfile:
        csv_writer = csv.writer(csvfile)
        csv_writer.writerow(["Device Name", "Battery Last Replace Date", "Input Voltage", "Load Percent"])

    for device in upsDevices:
        with device.lock:
            device.refresh()
            if device.basic:
                deviceName = device.custom_name
                batteryLastReplaceDate = device.basic.get('batteryLastReplace')
                inputVoltage = device.basic.get('inputVoltage')
                loadPct = device.basic.get('loadPct')
                device_ip = device.ip
            else:
                print(f"WARNING: Device with IP {device.ip} has no battery information, skipping battery age export.")
                continue
            if debug:
                print(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + f"battery_age_export:: Exporting {device_ip} {deviceName}, Battery Last Replace Date: {batteryLastReplaceDate}, Input Voltage: {inputVoltage}, Load Percent: {loadPct}")

        with open(BATTERY_AGE_CSV, 'a', encoding='utf-8') as csvfile:
            csv_writer = csv.writer(csvfile)
            csv_writer.writerow([deviceName, batteryLastReplaceDate, inputVoltage, loadPct])

def run_system(printer=False, summary_hour=DAILY_SUMMARY_HOUR, summary_minute=DAILY_SUMMARY_MINUTE):
    """Main function to run the UPS monitoring system.

    Then starts a thread pool to ping the UPS devices for new information

    And starts a notifier thread to check for new alarms and send email alerts

    Args:
        printer: Enable continuous console dashboard
        summary_hour: Hour (0-23) to send daily summary (default: 8)
        summary_minute: Minute (0-59) to send daily summary (default: 0)
    """
    
    if args.unit_age_export:
        print(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + "Exporting unit age CSV...")
        unit_age_export(debug=debug)
        print(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + "Unit age CSV exported.")
        return

    if args.battery_age_export:
        print(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + "Exporting battery age CSV...")
        battery_age_export(debug=debug)
        print(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + "Battery age CSV exported.")
        return

    thread_pool = [threading.Thread(target=update_target, args=(work_queue,id,debug)) for id in range(args.thread_count)]

    notifier_thread = threading.Thread(target=email_notifier_target)
    daily_summary_thread = threading.Thread(target=daily_summary_email_target, args=(summary_hour, summary_minute))
    
    for thread in thread_pool:
        print(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + f"Starting thread {thread.name}")
        thread.start()

    if printer:
        printer_thread = threading.Thread(target=printer)
        printer_thread.start()
        printer_thread.join()

    if args.dc_test:
        print(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + "DC TEST")
        dc_device = upsDevices[-1]
        with dc_device.lock:
            dc_device.refresh()
            print(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + dc_device.display_all_info())

    time.sleep(STARTUP_DELAY)

    notifier_thread.start()
    daily_summary_thread.start()

    notifier_thread.join()
    daily_summary_thread.join()

    for thread in thread_pool:
        thread.join()

def handle_shutdown(signum, frame):
    print(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + "\nReceived signal, shutting down...")
    shutdown_event.set()

if __name__ == "__main__":
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    parser = argparse.ArgumentParser(description="Start the UPS monitoring system.")
    parser.add_argument("--debug", action="store_true", help="Enable debug flag, prints additional information during runtime for troubleshooting purposes")
    parser.add_argument("--printer", action="store_true", help="Enable printer flag, continuously updates the console with a dashboard")
    parser.add_argument("--s-h", "--summary-hour", type=int, default=DAILY_SUMMARY_HOUR, dest="summary_hour", help="Hour (0-23) to send daily summary email (default: 8)")
    parser.add_argument("--s-m", "--summary-minute", type=int, default=DAILY_SUMMARY_MINUTE, dest="summary_minute", help="Minute (0-59) to send daily summary email (default: 0)")
    parser.add_argument("--thread-ct", type=int, default=THREAD_COUNT, dest="thread_count", help="Number of threads to use for updating UPS devices (default: 5)")
    parser.add_argument("--time-test", action="store_true", help="Enable time test flag, runs a test of the time required to update all devices and end execution")
    parser.add_argument("--dc-test", action="store_true", help="Enable APC DC test flag, runs a test of the APC DC functionality")
    parser.add_argument("--unit-age-export", action="store_true", help="Enable unit age export flag, exports a CSV of all UPS devices with their manufacture date and serial number")
    parser.add_argument("--battery-age-export", action="store_true", help="Enable battery age export flag, exports a CSV of all UPS devices with their battery manufacture date")
    args = parser.parse_args()

    debug = args.debug

    run_system(printer=args.printer, summary_hour=args.summary_hour, summary_minute=args.summary_minute)

    end_time = datetime.now()
    runtime_delta = end_time - start_time
    if args.time_test:
        print(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + f"Total runtime: {runtime_delta.total_seconds()} seconds")

# benchmark()