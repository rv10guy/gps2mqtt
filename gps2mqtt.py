# Copyright (c) 2023 Scott Wright. All rights reserved.
# This code is licensed under the MIT License.

import os, math, time, threading, queue, datetime, paho.mqtt.client as mqtt, configparser, urllib.request, socket
from gps import *

# Read the MQTT broker settings from the config file
config = configparser.ConfigParser(inline_comment_prefixes=';')
config.read('gps2mqtt.ini')
mqtt_broker = config['MQTT']['broker']
port_value = config['MQTT']['port']
mqtt_port = int(port_value.split(';')[0].strip()) 
mqtt_username = config['MQTT']['username']  
mqtt_password = config['MQTT']['password'] 
mqtt_topic_prefix = config['MQTT']['topic_prefix'] 
mqtt_retain = config['MQTT'].get('retain').lower() in ['true', 'yes', '1']
debug = config['General'].get('debug').lower() in ['true', 'yes', '1']
traccar_enabled = config['Traccar'].get('enabled').lower() in ['true', 'yes', '1']
TRACCARURL = config['Traccar']['url']
TRACCARID = config['Traccar']['id'] 

def on_connect(client, userdata, flags, rc):
    if debug:
        print("Connected to MQTT broker with result code " + str(rc))

def on_publish(client, userdata, mid):
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if debug:
        print(f"{current_time}: Message published to MQTT broker with message id {mid}")

def on_disconnect(client, userdata, rc):
    if rc != 0:
        print(f"Unexpected MQTT disconnection. Reconnecting...")

def meters_to_feet(meters):
    return meters * 3.28084

def meters_per_second_to_mph(mps):
    return mps * 2.23694

# Prepare GPS Data for Traccar
def generate_traccar_report(report):
    pos = {}
    
    # Extract the necessary data from the report
    if hasattr(report, 'lat'):
        pos['lat'] = report.lat
    if hasattr(report, 'lon'):
        pos['lon'] = report.lon
    if hasattr(report, 'alt'):
        pos['alt'] = report.alt
    if hasattr(report, 'speed'):
        pos['speed'] = meters_per_second_to_mph(report.speed)
    if hasattr(report, 'track'):
        pos['track'] = report.track
    if hasattr(report, 'epx'):
        pos['epx'] = report.epx
    if hasattr(report, 'epy'):
        pos['epy'] = report.epy
    if hasattr(report, 'epv'):
        pos['epv'] = report.epv
    
    # Create the Traccar report message
    msg_parts = []
    for key, value in pos.items():
        msg_parts.append(f"{key}={value}")
    
    msg = '&' + '&'.join(msg_parts)
    
    return msg

# Send a report to the Traccar server
def send_report(msg):
    msg = msg + ('&sendtime=%f' % (time.time()))
    url = '%s/?id=%s&timestamp=%d%s' % (TRACCARURL, TRACCARID, int(time.time()), msg)
    print(url)
    try:
        r = urllib.request.urlopen(url)
        return 0
    except urllib.error.URLError as e:
        print(f"Error in send_report: {e}")
        return 1

# Convert the track value to a compass direction
def track_to_compass_direction(track):
    if track is None:
        return None

    directions = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW', 'N']
    index = int((track + 22.5) % 360 // 45)
    return directions[index]

# Convert the HDOP value to an accuracy in feet
def hdop_to_accuracy_feet(hdop):
    accuracy_meters = hdop * 5
    return meters_to_feet(accuracy_meters)

# Initialize the GPS session
def init_gps_session():
    return gps(mode=WATCH_ENABLE | WATCH_NEWSTYLE)

# Worker thread that reads GPS reports from the GPS session and puts them in a queue
def gps_reports_worker():
    while True:
        try:
            report = session.next()
            gps_reports_queue.put(report)
        except StopIteration:
            continue

# Start the GPS worker thread
def start_gps_worker_thread():
    global gps_reports_thread
    gps_reports_thread = threading.Thread(target=gps_reports_worker, daemon=True)
    gps_reports_thread.start()

# Check if the MQTT connection is still alive. If not, reconnect.
def ensure_mqtt_connection():
    if not client.is_connected():
        print("MQTT connection lost. Reconnecting...")
        client.reconnect()

print(mqtt_broker)
# Connect to the MQTT broker
client = mqtt.Client()
client.username_pw_set(mqtt_username, mqtt_password)
client.on_connect = on_connect
client.on_publish = on_publish
client.on_disconnect = on_disconnect
client.loop_start()
client.connect(mqtt_broker, mqtt_port, 60)

# Initialize variables
num_reports = 0
current_fix = 0
last_values = {}

# Create a queue to store GPS reports
gps_reports_queue = queue.Queue()

# Initialize the GPS session and start the GPS worker thread
session = init_gps_session()
start_gps_worker_thread()

# Add a timeout (in seconds) for retrieving reports from the queue
timeout = 5

# Loop forever, reading GPS reports from the queue and publish them to MQTT
while True:
    try:
        report = gps_reports_queue.get(timeout=timeout)
    except queue.Empty:
        print(f"GPS report not received within {timeout} seconds. Retrying...")
        print("Reconnecting to the GPS device...")
        init_gps_session()
        start_gps_worker_thread()
        continue

    num_reports = num_reports + 1
    if debug:
        print("Received GPS report number " + str(num_reports))
    ensure_mqtt_connection()

    # Publish the status of the GPS fix
    if report['class'] == 'TPV':
        if traccar_enabled:
            msg = generate_traccar_report(report)
            send_report(msg)

        if hasattr(report, 'mode'):
            status = {1: "NO FIX", 2: "2D FIX", 3: "3D FIX"}.get(report.mode, "UNKNOWN")
            if status != last_values.get('status'):
                client.publish("mqtt_topic_prefix/fix", status, retain=mqtt_retain)
                if debug:
                    print("GPS status: " + status)
                last_values['status'] = status

        # Latitude - Read latitude from the GPS report
        if hasattr(report, 'lat'):
            new_value = report.lat
            if new_value != last_values.get('latitude'):
                client.publish(mqtt_topic_prefix +"/latitude", str(new_value), retain=mqtt_retain)
                if debug:
                    print("Latitude: " + str(new_value))
                last_values['latitude'] = new_value

        # Longitude - Read longitude from the GPS report
        if hasattr(report, 'lon'):
            new_value = report.lon
            if new_value != last_values.get('longitude'):
                client.publish(mqtt_topic_prefix +"/longitude", str(new_value), retain=mqtt_retain)
                if debug:
                    print("Longitude: " + str(new_value))
                last_values['longitude'] = new_value

        # Altitude - Read altitude from the GPS report and convert it to feet
        if hasattr(report, 'alt'):
            new_value = round(meters_to_feet(report.alt))
            if new_value != last_values.get('altitude'):
                client.publish(mqtt_topic_prefix +"/altitude", str(new_value), retain=mqtt_retain)
                if debug:
                    print("Altitude: " + str(new_value))
                last_values['altitude'] = new_value

        # Speed - Read speed from the GPS report and convert it to MPH, Ignore speeds less than 3 MPH
        if hasattr(report, 'speed'):
            speed_mph = meters_per_second_to_mph(report.speed)
            new_value = 0 if speed_mph < 3 else speed_mph
            if new_value != last_values.get('speed'):
                client.publish(mqtt_topic_prefix +"/speed", str(new_value), retain=mqtt_retain)
                if debug:
                    print("Speed: " + str(new_value))   
                last_values['speed'] = new_value
        
        # Direction - Read track from the GPS report and convert it to a compass direction
        if hasattr(report, 'track'):
            new_value = track_to_compass_direction(report.track)
            if new_value != last_values.get('direction'):
                client.publish(mqtt_topic_prefix +"/direction", new_value, retain=mqtt_retain)
                if debug:
                    print("Direction: " + new_value)    
                last_values['direction'] = new_value

        # 2D accuracy - Read epx, epy from the GPS report and calculate the circular error probable (CEP) in feet
        if hasattr(report, 'epx') and hasattr(report, 'epy'):
            epx = report.epx
            epy = report.epy
            cep_m = math.sqrt(epx**2 + epy**2)
            cep_ft = meters_to_feet(cep_m)
            cep = round(cep_ft, -1)
            if cep != last_values.get('cep'):
               client.publish(mqtt_topic_prefix +"/2D_Accuracy", str(cep), retain=mqtt_retain)
               if debug:
                     print("2D Accuracy: " + str(cep))  
               last_values['cep'] = cep

        # 3D accuracy - Read epx, epy, epv from the GPS report and calculate the spherical error probable (SEP) in feet
        if hasattr(report, 'epx') and hasattr(report, 'epy') and hasattr(report, 'epv'):
            epx = report.epx
            epy = report.epy
            epv = report.epv
            sep_m = math.sqrt(epx*2 + epy**2 + epv**2)
            sep_ft = meters_to_feet(sep_m)
            sep = round(sep_ft, -1)
            if sep != last_values.get('sep'):
               client.publish(mqtt_topic_prefix +"/3D_Accuracy", str(sep), retain=mqtt_retain)
               if debug:
                     print("3D Accuracy: " + str(sep))  
               last_values['sep'] = sep

    # Satellite information
    if report['class'] == 'SKY':
        if hasattr(report, 'satellites'):
            satellites = report.get('satellites', [])
            used_satellites = sum (1 for sat in satellites if sat['used'])
            if used_satellites != last_values.get('satellites'):
                client.publish(mqtt_topic_prefix +"/satellites", str(used_satellites), retain=mqtt_retain)
                if debug:
                    print("Satellites: " + str(used_satellites))
                last_values['satellites'] = used_satellites


