# Enum maps (APC)
battery_status_map = {
    1: "unknown", 2: "batteryNormal", 3: "batteryLow",
    4: "batteryInFaultCondition", 5: "noBatteryPresent"
}

battery_replace_map = {1: "noBatteryNeedsReplacing", 2: "batteryNeedsReplacing"}

input_fail_map = {
    1: "noTransfer", 2: "highLineVoltage", 3: "brownout", 4: "blackout",
    5: "smallMomentarySag", 6: "deepMomentarySag", 7: "smallMomentarySpike",
    8: "largeMomentarySpike", 9: "selfTest", 10: "rateOfVoltageChange"
}

output_status_map = {
    1: "unknown", 2: "onLine", 3: "onBattery", 4: "onSmartBoost",
    5: "timedSleeping", 6: "softwareBypass", 7: "off", 8: "rebooting",
    9: "switchedBypass", 10: "hardwareFailureBypass", 11: "sleepingUntilPowerReturn",
    12: "onSmartTrim", 13: "ecoMode", 14: "hotStandby", 15: "onBatteryTest",
    16: "emergencyStaticBypass", 17: "staticBypassStandby", 18: "powerSavingMode",
    19: "spotMode", 20: "eConversion", 21: "chargerSpotmode", 22: "inverterSpotmode",
    23: "activeLoad", 24: "batteryDischargeSpotmode", 25: "inverterStandby",
    26: "chargerOnly", 27: "distributedEnergyReserve", 28: "selfTest"
}

apc_oid_map = {
    '1.3.6.1.4.1.318.1.1.1.1.1.1.0': ('model', None),
    '1.3.6.1.4.1.318.1.1.1.1.1.2.0': ('name', None),
    '1.3.6.1.4.1.318.1.1.1.2.1.1.0': ('batteryStatus', battery_status_map),
    '1.3.6.1.4.1.318.1.1.1.2.1.3.0': ('batteryLastReplace', None),
    '1.3.6.1.4.1.318.1.1.1.2.2.4.0': ('batteryReplaceIndicator', battery_replace_map),
    '1.3.6.1.4.1.318.1.1.1.2.2.2.0': ('batteryTempC', None),
    '1.3.6.1.4.1.318.1.1.1.2.2.3.0': ('batteryRunTimeMin', None),
    '1.3.6.1.4.1.318.1.1.1.3.2.1.0': ('inputVoltage', None),
    '1.3.6.1.4.1.318.1.1.1.3.2.5.0': ('inputFailCause', input_fail_map),
    '1.3.6.1.4.1.318.1.1.1.4.1.1.0': ('outputStatus', output_status_map),
    '1.3.6.1.4.1.318.1.1.1.1.2.3.0': ('serialNumber', None),
    '1.3.6.1.4.1.318.1.1.1.1.2.2.0': ('dateOfManufacture', None),
    '1.3.6.1.4.1.318.1.1.1.1.2.5.0': ('skuNumber', None),
    '1.3.6.1.2.1.1.6.0': ('location', None),
    #Custom, data only (no alerts)
    '1.3.6.1.4.1.318.1.1.1.4.2.3.0': ('loadPct', None),
    '1.3.6.1.4.1.318.1.1.1.4.2.9.0': ('outputVA', None)
}

apc_dc_oid_map = {
    '1.3.6.1.4.1.318.1.1.1.1.1.1.0': ('model', None),
    '1.3.6.1.4.1.318.1.1.1.1.1.2.0': ('name', None),
    '1.3.6.1.4.1.318.1.1.1.2.1.1.0': ('batteryStatus', battery_status_map),
    '1.3.6.1.4.1.318.1.1.1.2.1.3.0': ('batteryLastReplace', None),
    '1.3.6.1.4.1.318.1.1.1.2.2.4.0': ('batteryReplaceIndicator', battery_replace_map),
    #DC unit does not have a batteryTempC property
    '1.3.6.1.4.1.318.1.1.1.2.2.3.0': ('batteryRunTimeMin', None),
    '1.3.6.1.4.1.318.1.1.1.3.2.1.0': ('inputVoltage', None),
    '1.3.6.1.4.1.318.1.1.1.3.2.5.0': ('inputFailCause', input_fail_map),
    '1.3.6.1.4.1.318.1.1.1.4.1.1.0': ('outputStatus', output_status_map),
    '1.3.6.1.4.1.318.1.1.1.1.2.3.0': ('serialNumber', None),
    '1.3.6.1.4.1.318.1.1.1.1.2.2.0': ('dateOfManufacture', None),
    #DC unit does not have skuNumber property
    '1.3.6.1.2.1.1.6.0': ('location', None)
}

#List all models here, so that each UPS correctly maps to an OID map.
model_oid_maps = {
    'APC': apc_oid_map,
    'APC_DC': apc_dc_oid_map
}