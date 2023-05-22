# Copyright (c) 2023 Scott Wright. All rights reserved.
# This code is licensed under the MIT License.

import os, math, time, threading, queue, datetime, paho.mqtt.client as mqtt, configparser, urllib.request, socket, haversine, atexit
from gps import *

# Read the MQTT broker settings from the config file
config = configparser.ConfigParser(inline_comment_prefixes=';')
config.read('gps2mqtt.ini')
mqtt_broker = config['MQTT']['broker']
mqtt_port = int(config['MQTT']['port'])
mqtt_username = config['MQTT']['username']  
mqtt_password = config['MQTT']['password'] 
mqtt_topic_prefix = config['MQTT']['topic_prefix'] 
mqtt_retain = config['MQTT'].get('retain').lower() in ['true', 'yes', '1']
debug = config['General'].get('debug').lower() in ['true', 'yes', '1']
traccar_enabled = config['Traccar'].get('enabled').lower() in ['true', 'yes', '1']
TRACCARURL = config['Traccar']['url']
TRACCARID = config['Traccar']['id']
interval_always = int(config['General']['interval_always'])
interval_move = int(config['General']['interval_move'])
interval_track = int(config['General']['interval_track'])
ignore_speed = int(config['General']['ignore_speed'])
dist_move = int(config['General']['dist_move'])
chg_speed = int(config['General']['chg_speed'])
chg_track = int(config['General']['chg_track'])
gps_timeout = int(config['General']['gps_timeout'])
mqtt_enabled = config['MQTT'].get('enabled').lower() in ['true', 'yes', '1']

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

def mqtt_disconnect(client):
    try:
        client.loop_stop()
        client.disconnect()
    except Exception as e:
        print(f"Failed to disconnect from MQTT broker. Error: {e}")

def meters_to_feet(meters):
    return meters * 3.28084

def meters_per_second_to_mph(mps):
    return mps * 2.23694

def debug_print(debug, message):
    if debug:
        print(message)

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
    try:
        r = urllib.request.urlopen(url)
        return 0
    except urllib.error.URLError as e:
        print(f"Error in send_report: {e}")
        return 1

def calculate_2d_accuracy(report):
    if hasattr(report, 'epx') and hasattr(report, 'epy'):
        return round(meters_to_feet(math.sqrt(report.epx**2 + report.epy**2)), -1)
    else:
        return None

def calculate_3d_accuracy(report):
    if hasattr(report, 'epx') and hasattr(report, 'epy') and hasattr(report, 'epv'):
        return round(meters_to_feet(math.sqrt(report.epx**2 + report.epy**2 + report.epv**2)), -1)
    else:
        return None

def count_used_satellites(report):
    if 'satellites' in report:
        return sum(1 for sat in report['satellites'] if sat['used'])
    return None

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

def mqtt_process_and_publish(report, attr, conversion=None, threshold=None, topic_suffix=''):
    if hasattr(report, attr):
        new_value = getattr(report, attr)
        if conversion:
            new_value = conversion(new_value)
        if threshold and new_value < threshold:
            new_value = 0
        if new_value != last_values.get(attr):
            client.publish(mqtt_topic_prefix + "/" + topic_suffix, str(new_value), retain=mqtt_retain)
#            debug_print(debug, f"{attr.capitalize()}: {new_value}")
            last_values[attr] = new_value

def make_mqtt_report(report):
    data_to_process = [
        ('lat', None, None, 'latitude'),
        ('lon', None, None, 'longitude'),
        ('alt', meters_to_feet, None, 'altitude'),
        ('speed', meters_per_second_to_mph, ignore_speed, 'speed'),
        ('track', track_to_compass_direction, None, 'direction'),
        ('cep', calculate_2d_accuracy, None, '2D_Accuracy'),
        ('sep', calculate_3d_accuracy, None, '3D_Accuracy'),
        ('satellites', count_used_satellites, None, 'satellites'),
    ]

    for attr, conversion, threshold, topic_suffix in data_to_process:
        if attr in report:
            mqtt_process_and_publish(report, attr, conversion, threshold, topic_suffix)


def bearing_change(b1, b2):
    r = (b2-b1) % 360.0
    if r>=180:
        r -= 360
    return(abs(r))

# Connect to the MQTT broker
if mqtt_enabled:
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

# Initialize previous values
prev_lat, prev_lon, prev_speed, prev_track = 0, 0, 0, 0
second = int(time.time())

atexit.register(mqtt_disconnect, client)

# Loop forever, reading GPS reports from the queue and publish them to MQTT
while True:
    try:
        report = gps_reports_queue.get(timeout=gps_timeout)
    except queue.Empty:
        print(f"GPS report not received within {gps_timeout} seconds. Retrying...")
        print("Reconnecting to the GPS device...")
        init_gps_session()
        start_gps_worker_thread()
        continue

    num_reports += 1
    debug_print(debug, "Received GPS report number " + str(num_reports))
    ensure_mqtt_connection()

    second = int(time.time())

    if report['class'] == 'TPV' and 'mode' in report and report['mode'] in [2, 3]:
        if 'lat' in report and 'lon' in report:
            dist_moved = haversine.haversine((prev_lat, prev_lon), (report['lat'], report['lon'])) * 3280.84 # convert to feet
        else:
            dist_moved = 0  # or some other default value, or raise an error

    should_make_report = (
        report['class'] in ['TPV', 'SKY'] and (
            (interval_always and second % interval_always == 0) or 
            ('speed' in report and abs(meters_per_second_to_mph(prev_speed) - meters_per_second_to_mph(report.get('speed', 0))) > chg_speed) or 
            ('lat' in report and 'lon' in report and dist_moved > dist_move and (
                (interval_move and second % interval_move == 0) or 
                ('track' in report and interval_track and second % interval_track == 0 and bearing_change(prev_track, report.get('track', 0)) > chg_track))
            )
        )
    )

    if should_make_report:
        if mqtt_enabled:
            make_mqtt_report(report)
        
        if traccar_enabled:
            msg = generate_traccar_report(report)
            send_report(msg)
            
        prev_lat = report.get('lat', prev_lat)
        prev_lon = report.get('lon', prev_lon)
        prev_speed = report.get('speed', prev_speed)
        prev_track = report.get('track', prev_track)
    