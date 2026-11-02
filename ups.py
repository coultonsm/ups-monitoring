from pysnmp.hlapi import *
from oid_maps import *
from datetime import datetime
import threading
import hashlib
import dateutil
from config import SNMP_RETRIES, SNMP_TIMEOUT, LOCAL_ADDRESS

#Data gathering functions
def fetch_basic_ups_info(ip, user_data, model, local_address=LOCAL_ADDRESS) -> list:
    """
    Fetch and decode APC UPS scalar information.
    Returns a dictionary of decoded UPS values.

    Example output (APC):
    {'model': 'Smart-UPS X 3000', 
    'name': 'Residence Hall 2F UPS', 
    'location': 'Residence Hall 2F room 211', 
    'batteryStatus': 'batteryNormal', 
    'batteryLastReplace': '08/15/2025', 
    'batteryReplaceIndicator': 'noBatteryNeedsReplacing', 
    'batteryTempC': 26, 'batteryRunTimeMin': 76.31666666666666, 
    'inputVoltage': 211, 
    'inputFailCause': 'noTransfer', 
    'outputStatus': 'onLine', 
    'serialNumber': 'X', 
    'dateOfManufacture': '04/17/2018', 
    'skuNumber': 'X'}
    
    """

    transport_target = UdpTransportTarget((ip, 161), retries=SNMP_RETRIES, timeout=SNMP_TIMEOUT)
    transport_target.setLocalAddress((local_address, 0))

    oid_map = model_oid_maps.get(model)
    if not oid_map:
        print(f"fetch_basic_ups_info:: [ERROR] No OID map found for model {model}")
        return None

    oid_request = [ObjectType(ObjectIdentity(oid_str)) for oid_str in oid_map]

    walk = getCmd(
        SnmpEngine(),
        user_data,
        transport_target,
        ContextData(),
        *oid_request,
        lookupMib=False
    )

    errorIndication, errorStatus, errorIndex, varBinds = next(walk)

    if errorIndication:
        print(f"fetch_basic_ups_info:: [SNMP ERROR] {ip}: {errorIndication}")

        return None

    if errorStatus:
        print(
            f"fetch_basic_ups_info:: [SNMP ERROR] {ip}: {errorStatus.prettyPrint()} at "
            f"{errorIndex and varBinds[int(errorIndex) - 1][0] or '?'}"
        )
        return None

    info = {}
    try:
        for oid, value in varBinds:
            if isinstance(value, EndOfMibView):
                continue

            oid_str = oid.prettyPrint()
            if oid_str not in oid_map:
                continue

            name, enum_map = oid_map[oid_str]

            if enum_map:
                info[name] = enum_map.get(int(value), int(value))
            else:
                if name == "batteryRunTimeMin":
                    info[name] = (int(value) / 100) / 60 # timeticks → seconds -> minutes
                elif name in ("batteryTempC", "inputVoltage", "loadPct"):
                    info[name] = int(value)
                else:
                    info[name] = str(value)
    except:
        #In some instances if the NMC is booting the script may not fulfill all SNMP requests.
        #This can result in a ValueError when attempting to typecast an empty bytestring.
        #This except block will treat the entire query as failed.
        return None

    return info

def walk_table(ip, base_oid, user_data, local_address=LOCAL_ADDRESS):
    """
    Walk an SNMP table and yield OID/value pairs.

    Performs an SNMP GETNEXT walk starting at `base_oid` on the given device.
    Errors encountered during the walk are printed to stdout and terminate
    the walk early.

    Parameters
    ----------
    ip : str
        IP address or hostname of the SNMP agent.
    base_oid : str
        Base OID to walk.

    Yields
    ------
    tuple[str, Any]
        A tuple containing the OID (as a dotted string) and its value.

    Notes
    -----
    - Transport and engine errors (e.g., timeouts, authentication failures)
      are reported via `errorIndication`.
    - Agent-side errors (e.g., authorization errors, invalid OIDs) are
      reported via `errorStatus` and `errorIndex`.
    - On any error, the function prints a message and stops walking.
    """

    transport_target = UdpTransportTarget((ip, 161), retries=SNMP_RETRIES, timeout=SNMP_TIMEOUT)
    transport_target.setLocalAddress((local_address, 0))

    for (errorIndication,
         errorStatus,
         errorIndex,
         varBinds) in nextCmd(
            SnmpEngine(),
            user_data,
            transport_target,
            ContextData(),
            ObjectType(ObjectIdentity(base_oid)),
            lexicographicMode=False
        ):

        if errorIndication:
            print(f"walk_table: [SNMP ERROR] {ip}: {errorIndication}")
            return

        if errorStatus:
            bad_oid = (
                varBinds[int(errorIndex) - 1][0]
                if errorIndex else "unknown"
            )
            print(
                f"walk_table: [SNMP ERROR] {ip}: {errorStatus.prettyPrint()} "
                f"at {bad_oid}"
            )
            return

        for oid, value in varBinds:
            yield oid.prettyPrint(), value

def fetch_ups_temp_info(ip, user_data, model, local_address=LOCAL_ADDRESS) -> list:
    """
    Walks the UPS temperature sensor table and returns a dictionary:
    {sensor_index: {name, temp_f, alarm_raw, violation_raw}}
    """
    if model == 'APC':
        rows = {}
        for oid_str, value in walk_table(ip, '1.3.6.1.4.1.318.1.1.25.1.2', user_data):
            parts = oid_str.split('.')
            col = parts[-3]  # column in the table
            idx = parts[-2]  # sensor index

            rows.setdefault(idx, {})

            if col == '3':  # Sensor Name
                rows[idx]['name'] = str(value)
            elif col == '5':  # Temperature in °F
                rows[idx]['temp_f'] = int(value)
            elif col == '8':  # Violation Status (bitmap)
                rows[idx]['violation_raw'] = int(value)
            elif col == '9':  # Alarm Status
                rows[idx]['alarm_raw'] = int(value)

        return rows
    else:
        print(f"fetch_ups_temp_info:: [ERROR] No OID map found for model {model}")
        return None

def fetch_APC_contact_sensor_info(ip, user_data, local_address=LOCAL_ADDRESS) -> dict:
    """    
    Retrieves information for the Universal I/O contact sensors.

    Example output:
    {
    '1_1': {'name': 'water bug', 
            'location': 'X', 
            'state': 'closed', 
            'alarm': 'normal', 
            'comm': 'commsOK'}, 
    
    '1_2': {'name': 'water bug power fail', 
            'location': 'X', 
            'state': 'closed', 
            'alarm': 
            'normal', 
            'comm': 'commsOK'}
    }

    """
    BASE_OID = '1.3.6.1.4.1.318.1.1.25.2.2'
    rows = {}

    INPUT_STATE_MAP = {1: 'closed', 2: 'open', 3: 'disabled', 4: 'notApplicable'}
    ALARM_STATUS_MAP = {1: 'normal', 2: 'warning', 3: 'critical', 4: 'notApplicable'}
    COMM_STATUS_MAP = {1: 'notInstalled', 2: 'commsOK', 3: 'commsLost'}

    transport_target = UdpTransportTarget((ip, 161), retries=SNMP_RETRIES, timeout=SNMP_TIMEOUT)
    transport_target.setLocalAddress((local_address, 0))

    for (_, _, _, varBinds) in nextCmd(
        SnmpEngine(),
        user_data,
        transport_target,
        ContextData(),
        ObjectType(ObjectIdentity(BASE_OID)),
        lexicographicMode=False
    ):
        for oid, value in varBinds:
            parts = oid.prettyPrint().split('.')
            column = parts[-3]
            # combine last two OID components to get unique row index
            index = f"{parts[-2]}_{parts[-1]}"

            rows.setdefault(index, {})

            if column == '3':  # Contact Name
                rows[index]['name'] = str(value)
            elif column == '4':  # Location
                rows[index]['location'] = str(value)
            elif column == '5':  # Current State
                rows[index]['state'] = INPUT_STATE_MAP.get(int(value), 'unknown')
            elif column == '6':  # Alarm Status
                rows[index]['alarm'] = ALARM_STATUS_MAP.get(int(value), 'unknown')
            elif column == '7':  # Comm Status
                rows[index]['comm'] = COMM_STATUS_MAP.get(int(value), 'unknown')

    return rows

#########################

# Custom data structures:

#constants
ALARM_SNMP=0
ALARM_OUTPUT_STAT=1
ALARM_REPLACE_BATTERY=2
ALARM_TEMPERATURE=3
ALARM_CONTACT=4
ALARM_CONTACT_SENSOR_NA=5
ALARM_TEMP_SENSOR_NA=6
ALARM_BATTERY_RUNTIME=7
ALARM_BATTERY_AGE_WARNING=8
ALARM_BATTERY_RUNTIME_15 = 9
ALARM_BATTERY_RUNTIME_10 = 10

ALARM_CONSTANTS = [ALARM_SNMP, 
                   ALARM_OUTPUT_STAT, 
                   ALARM_REPLACE_BATTERY, 
                   ALARM_TEMPERATURE, 
                   ALARM_CONTACT, 
                   ALARM_CONTACT_SENSOR_NA, 
                   ALARM_TEMP_SENSOR_NA, 
                   ALARM_BATTERY_RUNTIME, 
                   ALARM_BATTERY_AGE_WARNING,
                   ALARM_BATTERY_RUNTIME_15,
                   ALARM_BATTERY_RUNTIME_10
                   ]

class UioTempSensor:
    """
    Docstring for UioTempSensor
    
    Custom data structure to store each table entry for the Universal I/O Temperature sensors
    """
    def __init__(self, name='', temp_f=0, alarm='unknown', violation_status=None):
        self.name = name
        self.temp_f = temp_f
        self.alarm = alarm
        self.violation_status = violation_status

class UioContactSensor:
    """
    Custom data structure to store each table entry for the Universal I/O Contact Sensors.
    Example devices using this type of connection is the waterbugs.
    """

    def __init__(self, name='', location='', state='unknown', alarm='unknown', comm='unknown'):
        self.name = name
        self.location = location
        self.state = state
        self.alarm = alarm
        self.comm = comm

class UpsAlarm:
    def __init__(self, type: int, severity=1, sensor_idx = None, start_time=None, end_time=None):
        self.severity = severity
        self.start_time = start_time if start_time != None else datetime.now()
        self.end_time = end_time
        self.type = type
        self.sensor_name = sensor_idx
        self.resolved_time = None
        self.active = True
        self.alerted = False
        self._local_id = None #used for writing the alarms to disk

    def __repr__(self):
        return f'{self.type}: {self.severity}'
    
    def __lt__(self, other):
        return self.severity < other.severity
    
    def resolve(self):
        self.active = False
        self.resolved_time = datetime.now()

    def _set_local_id(self,id):
        self._local_id = id

    def _get_local_id(self):
        return self._local_id

class UpsDevice:
    """
    Custom data structure to organize information for each UPS device.
    Includes a custom "alarm" field which can be used e.g. to trigger e-mail notifications.

    Fields
    ------
    self.ip: IP address of the UPS device

    self.custom_name: Human readable name in case self.basic is empty

    self.basic: dictionary containing basic information (see update_basic_information)

    self.uio_contacts: dictionary containing contact sensor information (see update_uio_contact_information)

    self.uio_temps: dictionary containing temperature sensor information (see update_uio_temperature_information)

    self.alarm: boolean to indicate True or False of custom alarm status

    self.alarms: list of UpsAlarm objects explaining the cause for alarm

    self.max_alarm_severity: Max severity of the alarms in self.alarms. Otherwise 0 if no alarm.

    self.lock: Lock object assisting in multithreaded access

    self.last_successful_update: datetime object of the time of last update

    self.userdata: UsmUserData object generated from given IP of device. Provides credentials required for SNMP.
    """
    def __init__(self, ip, model: str, custom_name=None, importance="Normal", snmp_user=None, auth_key=None, priv_key=None, use_local_ip=LOCAL_ADDRESS):        
        self.ip = ip
        self.custom_name=custom_name
        self.model = model
        self.basic = None
        self.uio_contacts = None
        self.uio_temps = None
        self.alarm_status = False
        self.alarms = []
        self.max_alarm_severity = 0
        self.lock = threading.Lock()
        self.last_successful_update = None
        self.importance = importance
        self._use_local_ip = use_local_ip
        self.snmp_attempts = 0
        self.userdata = UsmUserData(snmp_user, authKey=auth_key, privKey=priv_key, 
                    authProtocol=usmHMACSHAAuthProtocol, privProtocol=usmAesCfb128Protocol)

    # Class specific functions
    def update_basic_information(self) -> bool:
        """
        Update self.basic containing information in the products.hardware.ups part of the SNMP tree.
        Example dictionary of self.basic:
            {'model': 'Smart-UPS X 2000', 
            'name': 'X', 
            'batteryStatus': 'batteryNormal', 
            'batteryLastReplace': '08/15/2025', 
            'batteryReplaceIndicator': 'noBatteryNeedsReplacing', 
            'batteryTempC': 21, 
            'batteryRunTimeMin': 190.28, 
            'inputVoltage': 116, 
            'inputFailCause': 'noTransfer', 
            'outputStatus': 'onLine', 
            'serialNumber': 'X', 
            'dateOfManufacture': '07/25/2014', 
            'skuNumber': 'X'}
        """
        data = fetch_basic_ups_info(self.ip, self.userdata, self.model, self._use_local_ip)
        if data:
            self.basic = data
            return True
        else:
            self.update_alarm_status(SNMP_ERROR=True)
            return False

    def update_uio_temperature_information(self) -> bool:
        """
        Creates/updates a dictionary self.uio_temps of the format:
        
        {sensor_index: <associated UioTempSensor object>}
        Example:
        {'2': <ups.UioTempSensor object at x>}

        each UioTempSensor object has the fields:
        - name: name of sensor configured on the NMC
        - temp_f: Temperature in degrees Fahrenheit
        - alarm: Alarm status (normal, warning, critical, notApplicable)
        - violation_status: violation which triggered the alarm status (see below)

        """
        data = fetch_ups_temp_info(self.ip, self.userdata, self.model)
        if not data:
            # self.update_alarm_status(SNMP_ERROR=True)
            return False

        # Map integer alarm to string
        ALARM_STATUS_MAP = {
            1: 'normal',
            2: 'warning',
            3: 'critical',
            4: 'notApplicable'
        }

        # Bitmask definitions for violation status
        VIOLATION_BITS = {
            0x0001: "max_temp_exceeded",
            0x0002: "high_temp_exceeded",
            0x0004: "low_temp_exceeded",
            0x0008: "min_temp_exceeded",
            0x0010: "short_term_increasing_rate_exceeded",
            0x0020: "short_term_decreasing_rate_exceeded",
            0x0040: "long_term_increasing_rate_exceeded",
            0x0080: "long_term_decreasing_rate_exceeded",
            0x0100: "max_humidity_exceeded",
            0x0200: "high_humidity_exceeded",
            0x0400: "low_humidity_exceeded",
            0x0800: "min_humidity_exceeded"
        }

        self.uio_temps = {}

        for idx, sensor_data in data.items():
            # Convert integer alarm to string
            raw_alarm = sensor_data.get('alarm_raw', 4)
            alarm_str = ALARM_STATUS_MAP.get(raw_alarm, 'unknown')

            # Decode violation bitmap
            raw_violation = sensor_data.get('violation_raw', 0)
            violation_list = []
            for bitmask, description in VIOLATION_BITS.items():
                if raw_violation & bitmask:
                    violation_list.append(description)

            self.uio_temps[idx] = UioTempSensor(
                name=sensor_data.get('name', ''),
                temp_f=sensor_data.get('temp_f', 0),
                alarm=alarm_str,
                violation_status=violation_list
            )
        
        return True

    def update_uio_contact_information(self) -> bool:
        """
        Fetches contact sensor data from UPS, stores it as UioContactSensor objects
        within self.uio_contacts

        Example:
        self.uio_contacts = {
        '1_1': <ups.UioContactSensor object at x>, 
        '1_2': <ups.UioContactSensor object at x>
        }

        Each UioContactSensor object has:
        name: Name of the contact sensor configured on the NMC
        location: Location of the contact sensor configured on the NMC
        state: State of the contact sensor (open/closed)
        alarm: Alarm status of the contact sensor (normal, warning, critical, notApplicable)
        comm (Communication status)

        """

        # Clear old data
        self.uio_contacts = {}
        
        data = fetch_APC_contact_sensor_info(self.ip, self.userdata)
        if not data:
            # self.update_alarm_status(SNMP_ERROR=True)
            return False
            #Leave self.uio_contacts empty if we cannot pull new info

        for idx, contact_data in data.items():
            self.uio_contacts[idx] = UioContactSensor(
                name=contact_data.get('name', ''),
                location=contact_data.get('location', ''),
                state=contact_data.get('state', 'unknown'),
                alarm=contact_data.get('alarm', 'unknown'),
                comm=contact_data.get('comm', 'unknown')
            )
        return True

    def update_alarm_status(self, SNMP_ERROR=False) -> bool:
        """
        Updates the custom alarm status to be used for sending alerts.
        alarm is True under any of these conditions:

        - Error retreiving SNMP info (SNMP_ERROR flag in call by updater function)
        - Output status is not onLine or any Selftest status
        - Battery replacement indicator is true
        - Temperature alarm is in abnormal status
        - Universal I/O contact sensor is Open / Alarm
        
        Alarm severity:
        - 1: Low (Daily summary only)
        - 2: Medium (unused)
        - 3: High (5-minute notification)
        - 4: Critical (Immediate Notification)

        """
        self.alarms = []
        #Self.alarms will have all active alarms which includes the time last updated (since we clear self.alarms).
        #This is useful later as the Log will contain the older alarm objects for time comparison.

        #Check if we got the SNMP information
        if SNMP_ERROR:
            if self.snmp_attempts < 2:
                self.snmp_attempts += 1
                self.alarm_status = False #no alarm until too many attempts
                return self.alarm_status
            else:
                # print('snmp error found')
                self.alarms += [UpsAlarm(ALARM_SNMP,
                                        severity=4)]
                self.alarm_status=True
                # end execution here
                self.max_alarm_severity=4
                return self.alarm_status

        #Basic information: output status, battery replace indicator
        if self.basic:
            self.snmp_attempts = 0 #reset snmp attempts if we successfully pulled basic info
            if self.basic['outputStatus'] not in ['onLine', 'selfTest', 'onBatteryTest'] and self.basic['inputFailCause'] != 'selfTest':
                #outputStatus can be onBattery during a selftest, so we need to also check the input fail cause to avoid false alarms during selftests.
                lc_sv = 2 if self.basic['outputStatus']=='onSmartBoost' else 3 #if output status is on smart boost, severity is 2 since this is a common occurrence in buildings like _
                self.alarms += [UpsAlarm(ALARM_OUTPUT_STAT, 
                                         severity=lc_sv)]

            if self.basic['batteryReplaceIndicator'] == 'batteryNeedsReplacing':
                self.alarms += [UpsAlarm(type=ALARM_REPLACE_BATTERY, 
                                         severity=3)]
            if self.basic['batteryRunTimeMin'] < 10:
                self.alarms += [UpsAlarm(ALARM_BATTERY_RUNTIME_10, 
                                         severity=4)]
            elif self.basic['batteryRunTimeMin'] < 15:
                self.alarms += [UpsAlarm(ALARM_BATTERY_RUNTIME_15, 
                                         severity=4)]
            if ((datetime.now() - dateutil.parser.parse(self.basic['batteryLastReplace'])).days / 365) >= 4.5:
                self.alarms += [UpsAlarm(type=ALARM_BATTERY_AGE_WARNING,
                                         severity=1)]
        
        #Universal I/o Temperature Sensors
        if self.uio_temps:
            for sensor in self.uio_temps.keys():
                if self.uio_temps[sensor].alarm in ['warning', 'critical']:
                    severity = 2 if self.uio_temps[sensor].alarm == 'warning' else 3 #if alarm is critical
                    self.alarms += [UpsAlarm(type=ALARM_TEMPERATURE,
                                             severity=severity,
                                             sensor_idx=sensor)]
                
                if self.uio_temps[sensor].alarm == "notApplicable":
                    self.alarms += [UpsAlarm(ALARM_TEMP_SENSOR_NA,
                                             severity=3,
                                             sensor_idx=sensor)]

                if len(self.uio_temps[sensor].violation_status) > 0:
                    self.alarms = [UpsAlarm(ALARM_TEMPERATURE,
                                           severity=2,
                                           sensor_idx=sensor)]

        #Universal I/o Contact Sensors
        if self.uio_contacts:
            for sensor in self.uio_contacts.keys():
                if self.uio_contacts[sensor].alarm in ["warning", "critical"]:
                    self.alarms += [UpsAlarm(ALARM_CONTACT,
                                             severity=4,
                                             sensor_idx=sensor)]
                    
                if self.uio_contacts[sensor].alarm == "notApplicable":
                    self.alarms += [UpsAlarm(ALARM_CONTACT_SENSOR_NA,
                                             severity=3,
                                             sensor_idx=sensor)]
                
        #Based on whether the cause log is empty, update self.alarm
        if self.alarms != []: 
            self.alarm_status = True
            self.max_alarm_severity = max(self.alarms).severity
        else: 
            self.alarm_status=False
            self.max_alarm_severity = 0

        return self.alarm_status

    def refresh(self):
        """
        Refresh all UPS information:
        - Query basic information
        - Query contact sensor information
        - Query temperature sensor information
        - Update alarm status based on new information

        If at any point there is an SNMP error, update_alarm_status is called with SNMP_ERROR=True
        which will force an SNMP error alarm. The function then will update alarms based on the information it has.

        """
        if self.model == 'APC':
            if self.update_basic_information(): #True when no SNMP error, skip rest of refresh if there is SNMP error
                self.update_uio_contact_information()
                self.update_uio_temperature_information()
                self.last_successful_update = datetime.now()
                self.update_alarm_status()

        elif self.model == 'APC_DC':
            if self.update_basic_information():
                self.last_successful_update = datetime.now()
                self.update_alarm_status()

    def display_all_info(self) -> str:
        """
        Returns all UPS information as a formatted string:
        basic data, temperature sensors, and contact sensors.
        """
        lines = []
        lines.append(f"--- UPS Device Info ({self.ip} | {self.custom_name}) ---\n")

        # --- Basic UPS Information ---
        if self.basic:
            lines.append("Basic UPS Information:")
            for key, value in self.basic.items():
                lines.append(f"  {key}: {value}")
            lines.append("")  # blank line
        else:
            lines.append("No basic information available.\n")

        # --- Temperature Sensors ---
        if self.uio_temps:
            lines.append("Temperature Sensors:")
            for idx, sensor in self.uio_temps.items():
                lines.append(f"  Sensor {idx}:")
                lines.append(f"    Name: {sensor.name}")
                lines.append(f"    Temperature (*F): {sensor.temp_f}")
                lines.append(f"    Alarm: {sensor.alarm}")
                if hasattr(sensor, 'violation_status') and sensor.violation_status:
                    lines.append(f"    Violations: {', '.join(sensor.violation_status)}")
                else:
                    lines.append("    Violations: None")
            lines.append("")
        else:
            lines.append("No temperature sensor data available.\n")

        # --- Contact Sensors ---
        if self.uio_contacts:
            lines.append("Contact Sensors:")
            for idx, contact in self.uio_contacts.items():
                lines.append(f"  Contact {idx}:")
                lines.append(f"    Name: {contact.name}")
                lines.append(f"    Location: {contact.location}")
                lines.append(f"    State: {contact.state}")
                lines.append(f"    Alarm: {contact.alarm}")
                lines.append(f"    Comm: {contact.comm}")
            lines.append("")
        else:
            lines.append("No contact sensor data available.\n")

        lines.append(f"Alarm status: {self.alarm_status}")

        if len(self.alarms) > 0:
            lines.append("Alarm cause:")
            for alarm in self.alarms:
                lines.append(f"  {synthesize_email_message(self, alarm)[0]}")

        return "\n".join(lines)

    def check_alarm_type(self, alarmType) -> bool:
        """
        Check if an alarm of type alarmType exists for this UpsDevice

        :param alarmType: alarm type constant 
        :return: True or False if an alarm with the specified type is active since last refresh
        :rtype: bool
        """
        for alarm in self.alarms:
            if alarm.type == alarmType:
                return True
        return False

    # def alarm_cause_string(self):
    #     s = ""
    #     for alarm in self.alarms:
    #         s+=f"{synthesize_email_message(self, alarm)[0]}\n"
    #     return s
    
    def __eq__(self, other):
        return self.ip == other.ip

def synthesize_email_message(device: UpsDevice, alarm: UpsAlarm) -> tuple[str, str]:
    #BE SURE TO GRAB DEVICE LOCK BEFORE USING
    alarm_type = alarm.type
    if alarm_type == ALARM_SNMP:
        subject = "SNMP error."
        message = "There was an error while attemping SNMP communication with this UpsDevice."
    elif alarm_type == ALARM_OUTPUT_STAT:    
        subject = f"UPS Output status is abnormal. ({device.basic['outputStatus']})"
        message = f"outputStatus equals {device.basic['outputStatus']}. inputFailCause equals {device.basic['inputFailCause']}"
    elif alarm_type == ALARM_REPLACE_BATTERY:
        subject = "Battery needs replacing"
        message = "Battery replacement indicator is active."
    elif alarm_type == ALARM_TEMPERATURE:
        subject = f"Temperature Sensor status is {device.uio_temps[alarm.sensor_idx].alarm}"
        message = f"Universal I/O Temperature Sensor has an irregular status: {device.uio_temps[alarm.sensor_idx].alarm}\nThe current temperature is {device.uio_temps[alarm.sensor_idx].temp_f}*F"
    elif alarm_type == ALARM_CONTACT:
        subject = f"Contact Sensor status is {device.uio_contacts[alarm.sensor_idx].alarm}"
        message = f"Universal I/O Contact Sensor has an irregular status: {device.uio_contacts[alarm.sensor_idx].alarm}\nThe current state is {device.uio_contacts[alarm.sensor_idx].state}"
    elif alarm_type == ALARM_CONTACT_SENSOR_NA:
        subject = f"Contact Sensor has a generic error."
        message = f"Universal I/O Contact Sensor has an irregular status: {device.uio_contacts[alarm.sensor_idx].alarm}\nIt may not be functioning properly.\nThe current state is {device.uio_contacts[alarm.sensor_idx].state}"
    elif alarm_type == ALARM_TEMP_SENSOR_NA:
        subject = f"Temperature Sensor has a generic error."
        message = f"Universal I/O Temperature Sensor has an irregular status: {device.uio_temps[alarm.sensor_idx].alarm}\nIt may not be functioning properly.\nThe current temperature is {device.uio_temps[alarm.sensor_idx].temp_f}*F"
    elif alarm_type == ALARM_BATTERY_RUNTIME:
        subject = f"Estimated runtime less than 30 minutes ({device.basic['batteryRunTimeMin']:.2f})"
        message = "Estimated battery runtime is less than thirty minutes."
    elif alarm_type == ALARM_BATTERY_AGE_WARNING:
        subject = f"Battery age is longer than 4.5 years. Last replaced {device.basic['batteryLastReplace']}"
        message = f"APC defined battery age warning. Last replaced {device.basic['batteryLastReplace']}"
    elif alarm_type == ALARM_BATTERY_RUNTIME_15:
        subject = f"Estimated runtime less than 15 minutes ({device.basic['batteryRunTimeMin']:.2f})"
        message = "Estimated battery runtime is less than fifteen minutes."
    elif alarm_type == ALARM_BATTERY_RUNTIME_10:
        subject = f"Estimated runtime less than 10 minutes ({device.basic['batteryRunTimeMin']:.2f})"
        message = "Estimated battery runtime is less than ten minutes."

    return subject, message