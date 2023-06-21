# Copyright (c) 2023 Scott Wright. All rights reserved.
# This code is licensed under the MIT License.

import os, math, time, threading, queue, datetime, paho.mqtt.client as mqtt, configparser, urllib.request, socket, haversine, atexit, logging
from gps import *

# Read the MQTT broker settings from the config file
config = configparser.ConfigParser(inline_comment_prefixes=';')
config.read('gps2mqtt.ini')
log_level = config['General']['logging'].upper()
log_file = config['General'].get('logfile')
interval_always = int(config['General']['interval_always'])
interval_move = int(config['General']['interval_move'])
interval_track = int(config['General']['interval_track'])
dist_move = int(config['General']['dist_move'])
chg_speed = int(config['General']['chg_speed'])
chg_track = int(config['General']['chg_track'])
gps_timeout = int(config['General']['gps_timeout'])
ignore_speed = int(config['General']['ignore_speed'])
mqtt_enabled = config['MQTT'].get('enabled').lower() in ['true', 'yes', '1']
mqtt_broker = config['MQTT']['broker']
mqtt_port = int(config['MQTT']['port'])
mqtt_username = config['MQTT']['username']  
mqtt_password = config['MQTT']['password'] 
mqtt_topic_prefix = config['MQTT']['topic_prefix'] 
mqtt_retain = config['MQTT'].get('retain').lower() in ['true', 'yes', '1']
traccar_enabled = config['Traccar'].get('enabled').lower() in ['true', 'yes', '1']
traccar_url = config['Traccar']['url']
traccar_id = config['Traccar']['id']

def on_connect(client, userdata, flags, rc):
    logging.info("Connected to MQTT broker with result code " + str(rc))

def on_publish(client, userdata, mid):
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logging.info(f"{current_time}: Message published to MQTT broker with message id {mid}")

def on_disconnect(client, userdata, rc):
    if rc != 0:
        logging.warning("Unexpected MQTT disconnection. Reconnecting...")

def mqtt_disconnect(client):
    try:
        client.loop_stop()
        client.disconnect()
    except Exception as e:
        logging.error(f"Failed to disconnect from MQTT broker. Error: {e}")

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
    url = '%s/?id=%s&timestamp=%d%s' % (traccar_url, traccar_id, int(time.time()), msg)
    logging.info(f"Sending report to Traccar: {url}")
    try:
        r = urllib.request.urlopen(url)
        return 0
    except urllib.error.URLError as e:
        logging.error(f"Error in send_report: {e}")
        return 1

def calculate_accuracy(hdop):
    return round(meters_to_feet(hdop * 5), 1)


# Convert the track value to a compass direction
def track_to_compass_direction(track):
    if track is None:
        return None

    directions = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW', 'N']
    index = int((track + 22.5) % 360 // 45)
    return directions[index]

# Initialize the GPS session
def init_gps_session():
    global session
    session = gps(mode=WATCH_ENABLE | WATCH_NEWSTYLE)
    logging.info("GPS session initialized")
    return session

# Worker thread that reads GPS reports from the GPS session and puts them in a queue
def gps_reports_worker():
    global session
    last_report_time = time.monotonic()
    while True:
        try:
            report = session.next()
            gps_reports_queue.put(report)
            last_report_time = time.monotonic()
        except StopIteration:
            continue

        # Check if no report has been received in the last minute
        if time.monotonic() - last_report_time > 60:
            logging.warning("No GPS report received in the last minute. Reinitializing GPS session.")
            session.close()
            session = init_gps_session()
            last_report_time = time.monotonic()

# Start the GPS worker thread
def start_gps_worker_thread():
    global gps_reports_thread
    gps_reports_thread = threading.Thread(target=gps_reports_worker, daemon=True)
    gps_reports_thread.start()

# Check if the MQTT connection is still alive. If not, reconnect.
def ensure_mqtt_connection():
    if not client.is_connected():
        logging.warning("MQTT connection lost. Reconnecting...")
        client.reconnect()

def mqtt_process_and_publish(report, attr, conversion=None, threshold=None, topic_suffix=''):
    if hasattr(report, attr):
        new_value = getattr(report, attr)
        if conversion:
            new_value = conversion(new_value)
        if threshold and new_value < threshold:
            new_value = 0
        client.publish(mqtt_topic_prefix + "/" + topic_suffix, str(new_value), retain=mqtt_retain)
        logging.debug(f"{attr.capitalize()}: {new_value} - Published to MQTT broker")
        last_values[attr] = new_value

def make_mqtt_report(report):
    data_to_process = [
        ('lat', None, None, 'latitude'),
        ('lon', None, None, 'longitude'),
        ('alt', meters_to_feet, None, 'altitude'),
        ('speed', meters_per_second_to_mph, ignore_speed, 'speed'),
        ('track', track_to_compass_direction, None, 'direction'),
        ('hdop', calculate_accuracy, None, 'accuracy'),
        ('uSat', None, None, 'used_satellites'),
        ('nSat', None, None, 'visible_satellites'),
    ]

    for attr, conversion, threshold, topic_suffix in data_to_process:
        if attr in report:
            mqtt_process_and_publish(report, attr, conversion, threshold, topic_suffix)

def bearing_change(b1, b2):
    r = (b2-b1) % 360.0
    if r>=180:
        r -= 360
    return(abs(r))

# Check if the retrieved level is valid
if log_level not in ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']:
    raise ValueError(f"Invalid log level: {log_level}")

# Set up logging
log_handlers = [logging.StreamHandler()]  # Always log to console
if log_file:
    log_handlers.append(logging.StreamHandler(open(log_file, 'a')))  # Also log to file if specified

# Set up logging
logging.basicConfig(level=log_level)

# Connect to the MQTT broker
client = None

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
last_report_time = 0
last_report_time_TPV = 0
last_report_time_SKY = 0
dist_moved = 0

# Create a queue to store GPS reports
gps_reports_queue = queue.Queue()

# Initialize the GPS session and start the GPS worker thread
session = init_gps_session()
start_gps_worker_thread()

# Initialize previous values
prev_lat, prev_lon, prev_speed, prev_track, sat_count = 0, 0, 0, 0, 0   
second = int(time.time())

atexit.register(mqtt_disconnect, client)

# Loop forever, reading GPS reports from the queue and publish them to MQTT
while True:
    try:
        report = gps_reports_queue.get(timeout=gps_timeout)
        logging.debug(f"Received GPS report: {report}")
    except queue.Empty:
        logging.warning(f"GPS report not received within {gps_timeout} seconds. Retrying...")
        logging.warning("Reconnecting to the GPS device...")
        init_gps_session()
        start_gps_worker_thread()
        continue

    num_reports += 1
    logging.debug("Received GPS report number " + str(num_reports))
    ensure_mqtt_connection()

    second = int(time.time())

    if report['class'] == 'TPV' and 'mode' in report and report['mode'] in [2, 3]:
        if 'lat' in report and 'lon' in report:
            dist_moved = haversine.haversine((prev_lat, prev_lon), (report['lat'], report['lon'])) * 3280.84 # convert to feet
        else:
            dist_moved = 0  # or some other default value, or raise an error

    should_make_report_TPV = (
        report['class'] == 'TPV' and (
            (interval_always and second - last_report_time_TPV >= interval_always) or 
            ('speed' in report and abs(meters_per_second_to_mph(prev_speed) - meters_per_second_to_mph(report.get('speed', 0))) > chg_speed) or 
            ('lat' in report and 'lon' in report and dist_moved > dist_move and (
                (interval_move and second % interval_move == 0) or 
                ('track' in report and interval_track and second % interval_track == 0 and bearing_change(prev_track, report.get('track', 0)) > chg_track))
            )
        )
    )

    should_make_report_SKY = report['class'] == 'SKY' and (
        (interval_always and second - last_report_time_SKY >= interval_always) or 
        (('uSat' in report and report['uSat'] != sat_count) and (second - last_report_time_SKY >= 2))
    )

    should_make_report = should_make_report_TPV or should_make_report_SKY

    if should_make_report:
        logging.info("Condition met to make report")
        if mqtt_enabled:
            logging.info("Making MQTT report")
            make_mqtt_report(report)
        
        if traccar_enabled and should_make_report_TPV:
            logging.info("Making Traccar report")
            msg = generate_traccar_report(report)
            send_report(msg)
        
        if report['class'] == 'TPV':
            last_report_time_TPV = second
            prev_lat = report.get('lat', prev_lat)
            prev_lon = report.get('lon', prev_lon)
            prev_speed = report.get('speed', prev_speed)
            prev_track = report.get('track', prev_track)
        elif report['class'] == 'SKY':
            last_report_time_SKY = second
            sat_count = report.get('uSat', sat_count)
            
        
    