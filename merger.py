import os
import xml.etree.ElementTree as ET
from xml.dom import minidom
from datetime import datetime, timedelta

def parse_date(date_str):
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S.%fZ")

def create_tcx(activity, streams, hr_data, output_path, include_creator=True):
    """
    activity: dict from Strava API
    streams: dict of streams from Strava API
    hr_data: dict of time (HH:MM:SS) -> hr_value from Fitbit
    """
    
    start_time_utc = parse_date(activity.get('start_date'))
    start_time_local = parse_date(activity.get('start_date_local'))
    
    # Namespaces
    ns = "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"
    ns_xsi = "http://www.w3.org/2001/XMLSchema-instance"
    ns_ext = "http://www.garmin.com/xmlschemas/ActivityExtension/v2"
    
    ET.register_namespace("", ns)
    ET.register_namespace("xsi", ns_xsi)
    ET.register_namespace("tpx", ns_ext)
    
    strava_type = activity.get("sport_type") or activity.get("type", "Other")
    sport_map = {
        "Ride": "Biking", 
        "MountainBikeRide": "MountainBikeRide", 
        "E-BikeRide": "EBikeRide",
        "GravelRide": "GravelRide", 
        "RoadRide": "Biking", 
        "Run": "Running", 
        "TrailRun": "Running",
        "Snowboard": "Snowboard", 
        "Snowboarding": "Snowboard", 
        "AlpineSki": "AlpineSki",
        "Biking": "Biking", 
        "Running": "Running"
    }

    tcx_sport = sport_map.get(strava_type, "Other")
    
    root = ET.Element(f"{{{ns}}}TrainingCenterDatabase", {
        f"{{{ns_xsi}}}schemaLocation": f"{ns} http://www.garmin.com/xmlschemas/TrainingCenterDatabasev2.xsd"
    })
    
    activities = ET.SubElement(root, f"{{{ns}}}Activities")
    act_elem = ET.SubElement(activities, f"{{{ns}}}Activity", Sport=tcx_sport)
    
    id_elem = ET.SubElement(act_elem, f"{{{ns}}}Id")
    id_elem.text = start_time_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    
    lap_elem = ET.SubElement(act_elem, f"{{{ns}}}Lap", StartTime=start_time_utc.strftime("%Y-%m-%dT%H:%M:%SZ"))
    
    max_time = 0
    max_dist = 0.0
    if streams.get("time", {}).get("data"): max_time = streams["time"]["data"][-1]
    if streams.get("distance", {}).get("data"): max_dist = streams["distance"]["data"][-1]

    ET.SubElement(lap_elem, f"{{{ns}}}TotalTimeSeconds").text = str(max_time)
    ET.SubElement(lap_elem, f"{{{ns}}}DistanceMeters").text = str(max_dist)
    ET.SubElement(lap_elem, f"{{{ns}}}Calories").text = "0"
    ET.SubElement(lap_elem, f"{{{ns}}}Intensity").text = "Active"
    ET.SubElement(lap_elem, f"{{{ns}}}TriggerMethod").text = "Manual"
    
    track_elem = ET.SubElement(lap_elem, f"{{{ns}}}Track")
    
    s_time = streams.get("time", {}).get("data", [])
    s_latlng = streams.get("latlng", {}).get("data", [])
    s_dist = streams.get("distance", {}).get("data", [])
    s_alt = streams.get("altitude", {}).get("data", [])
    s_watts = streams.get("watts", {}).get("data", [])
    s_cad = streams.get("cadence", {}).get("data", [])
    s_vel = streams.get("velocity_smooth", {}).get("data", [])

    for i in range(len(s_time)):
        tp_elem = ET.SubElement(track_elem, f"{{{ns}}}Trackpoint")
        
        offset = s_time[i]
        curr_utc = start_time_utc + timedelta(seconds=offset)
        curr_local = start_time_local + timedelta(seconds=offset)
        
        ET.SubElement(tp_elem, f"{{{ns}}}Time").text = curr_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        if i < len(s_latlng) and s_latlng[i]:
            pos = ET.SubElement(tp_elem, f"{{{ns}}}Position")
            ET.SubElement(pos, f"{{{ns}}}LatitudeDegrees").text = str(s_latlng[i][0])
            ET.SubElement(pos, f"{{{ns}}}LongitudeDegrees").text = str(s_latlng[i][1])
            
        if i < len(s_alt) and s_alt[i] is not None:
            ET.SubElement(tp_elem, f"{{{ns}}}AltitudeMeters").text = str(s_alt[i])
            
        if i < len(s_dist) and s_dist[i] is not None:
            ET.SubElement(tp_elem, f"{{{ns}}}DistanceMeters").text = str(s_dist[i])
            
        if i < len(s_cad) and s_cad[i] is not None:
            ET.SubElement(tp_elem, f"{{{ns}}}Cadence").text = str(int(s_cad[i]))

        hr_val = None
        for o in range(0, 5): 
            t_key = (curr_local - timedelta(seconds=o)).strftime("%H:%M:%S")
            if t_key in hr_data:
                hr_val = hr_data[t_key]
                break
                
        if hr_val:
            hr_wrap = ET.SubElement(tp_elem, f"{{{ns}}}HeartRateBpm")
            ET.SubElement(hr_wrap, f"{{{ns}}}Value").text = str(hr_val)

        has_watts = i < len(s_watts) and s_watts[i] is not None
        has_vel = i < len(s_vel) and s_vel[i] is not None
        if has_watts or has_vel:
            ext = ET.SubElement(tp_elem, f"{{{ns}}}Extensions")
            tpx = ET.SubElement(ext, f"{{{ns_ext}}}TPX")
            if has_vel: ET.SubElement(tpx, f"{{{ns_ext}}}Speed").text = str(s_vel[i])
            if has_watts: ET.SubElement(tpx, f"{{{ns_ext}}}Watts").text = str(int(s_watts[i]))

    if include_creator:
        creator = ET.SubElement(act_elem, f"{{{ns}}}Creator")
        creator.set(f"{{{ns_xsi}}}type", "Device_t")
        ET.SubElement(creator, f"{{{ns}}}Name").text = "Garmin Edge 130 Plus"
        ET.SubElement(creator, f"{{{ns}}}UnitId").text = "3318288765"
        ET.SubElement(creator, f"{{{ns}}}ProductID").text = "3558"
        v = ET.SubElement(creator, f"{{{ns}}}Version")
        ET.SubElement(v, f"{{{ns}}}VersionMajor").text = "6"
        ET.SubElement(v, f"{{{ns}}}VersionMinor").text = "10"
        ET.SubElement(v, f"{{{ns}}}BuildMajor").text = "0"
        ET.SubElement(v, f"{{{ns}}}BuildMinor").text = "0"
    
    xml_str = ET.tostring(root, encoding='utf-8')
    parsed = minidom.parseString(xml_str)
    with open(output_path, "w") as f:
        f.write(parsed.toprettyxml(indent="  "))

def inject_hr_to_fit(input_path, hr_data, output_path):
    """
    Reads a binary FIT file, injects heart rate data into every record message,
    and saves a new binary FIT file.
    """
    from fit_tool.fit_file import FitFile
    from fit_tool.profile.messages.record_message import RecordMessage
    
    fit_file = FitFile.from_file(input_path)
    FIT_EPOCH = 631065600
    
    modified_count = 0
    for record in fit_file.records:
        message = record.message
        if isinstance(message, RecordMessage):
            ts_val = message.timestamp
            
            # CRITICAL: Handle if fit-tool gives int or datetime
            if isinstance(ts_val, int):
                dt = datetime.fromtimestamp(ts_val + FIT_EPOCH)
            else:
                dt = ts_val
            
            hr_val = None
            for offset_sec in range(0, 5):
                check_time = dt - timedelta(seconds=offset_sec)
                time_key = check_time.strftime("%H:%M:%S")
                if time_key in hr_data:
                    hr_val = hr_data[time_key]
                    break
            
            if hr_val:
                message.heart_rate = int(hr_val)
                modified_count += 1
                    
    print(f"  [FIT] Injected heart rate into {modified_count} records.")
    fit_file.to_file(output_path)

def parse_fit(file_path):
    import fitparse
    fitfile = fitparse.FitFile(file_path)
    
    streams = {k: {"data": []} for k in ["time", "latlng", "distance", "altitude", "watts", "cadence", "velocity_smooth"]}
    
    start_dt = None
    sport = "Other"
    
    first_record = next(fitfile.get_messages("record"), None)
    if first_record:
        print(f"  [Debug] Available FIT fields: {sorted([f.name for f in first_record.fields])}")

    for record in fitfile.get_messages("record"):
        values = record.get_values()
        timestamp = values.get("timestamp")
        if not timestamp: continue
        if start_dt is None: start_dt = timestamp
            
        offset = int((timestamp - start_dt).total_seconds())
        streams["time"]["data"].append(offset)
        
        lat = values.get("position_lat")
        lon = values.get("position_long")
        if lat is not None and lon is not None:
            streams["latlng"]["data"].append([lat * (180.0 / 2**31), lon * (180.0 / 2**31)])
        else:
            streams["latlng"]["data"].append(None)
            
        streams["altitude"]["data"].append(values.get("altitude"))
        streams["distance"]["data"].append(values.get("distance"))
        
        pwr = values.get("power") or values.get("instantaneous_power")
        streams["watts"]["data"].append(pwr)
        
        streams["cadence"]["data"].append(values.get("cadence"))
        streams["velocity_smooth"]["data"].append(values.get("speed"))

    for msg in fitfile.get_messages("sport"):
        sport_val = msg.get_value("sport")
        if sport_val: sport = str(sport_val).replace('_', '').capitalize()

    start_time_str = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ") if start_dt else None
    
    raw_alt = streams["altitude"]["data"]
    if raw_alt and any(a is not None for a in raw_alt):
        smoothed_alt = []
        last_val = None
        for val in raw_alt:
            if val is None:
                smoothed_alt.append(None)
                continue
            if last_val is None:
                last_val = val
            elif abs(val - last_val) > 1.0: # 1.0m threshold
                last_val = val
            smoothed_alt.append(last_val)
        streams["altitude"]["data"] = smoothed_alt

    return {"type": sport, "start_date": start_time_str, "start_date_local": start_time_str, "name": f"Restored FIT Activity ({start_time_str})"}, streams

def parse_tcx(file_path):
    tree = ET.parse(file_path)
    root = tree.getroot()
    ns = {"ns": "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2",
          "ns_ext": "http://www.garmin.com/xmlschemas/ActivityExtension/v2"}
    
    act_elem = root.find(".//ns:Activity", ns)
    sport = act_elem.get("Sport") if act_elem is not None else "Other"
    start_time_utc = root.find(".//ns:Id", ns).text
    start_dt = parse_date(start_time_utc)
    
    streams = {k: {"data": []} for k in ["time", "latlng", "distance", "altitude", "watts", "cadence", "velocity_smooth"]}
    
    for pt in root.findall(".//ns:Trackpoint", ns):
        pt_dt = parse_date(pt.find("ns:Time", ns).text)
        streams["time"]["data"].append(int((pt_dt - start_dt).total_seconds()))
        
        pos = pt.find("ns:Position", ns)
        streams["latlng"]["data"].append([float(pos.find("ns:LatitudeDegrees", ns).text), float(pos.find("ns:LongitudeDegrees", ns).text)] if pos is not None else None)
        
        alt = pt.find("ns:AltitudeMeters", ns)
        streams["altitude"]["data"].append(float(alt.text) if alt is not None else None)
        
        dist = pt.find("ns:DistanceMeters", ns)
        streams["distance"]["data"].append(float(dist.text) if dist is not None else None)
        
        cad = pt.find("ns:Cadence", ns)
        streams["cadence"]["data"].append(int(cad.text) if cad is not None else None)
        
        ext = pt.find("ns:Extensions", ns)
        w, s = None, None
        if ext is not None:
            # Strava TCX uses TPX namespace for extensions
            tpx = ext.find(".//ns_ext:TPX", ns)
            if tpx is not None:
                watts = tpx.find("ns_ext:Watts", ns)
                if watts is not None:
                    w = int(watts.text)
                
                speed = tpx.find("ns_ext:Speed", ns)
                if speed is not None:
                    s = float(speed.text)
        streams["watts"]["data"].append(w)
        streams["velocity_smooth"]["data"].append(s)

    return {"type": sport, "start_date": start_time_utc, "start_date_local": start_time_utc, "name": f"Restored Activity ({start_time_utc})"}, streams
