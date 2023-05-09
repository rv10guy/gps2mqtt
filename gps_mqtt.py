import os
import math
import time
import threading
import queue
import datetime
import paho.mqtt.client as mqtt
from gps import *

mqtt_broker = os.environ.get("mqtt_broker", "192.168.50.77")  # Default value: "core-mosquitto"
mqtt_username = os.environ.get("mqtt_username", "hassio")  # Default value: "hassio"
mqtt_password = os.environ.get("mqtt_password", "hassio")  # Default value: "hassio"

session = gps(mode=WATCH_ENABLE | WATCH_NEWSTYLE)

def on_connect(client, userdata, flags, rc):
    print("Connected to MQTT broker with result code " + str(rc))

def on_publish(client, userdata, mid):
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{current_time}: Message published to MQTT broker with message id {mid}")

def on_disconnect(client, userdata, rc):
    if rc != 0:
        print(f"Unexpected MQTT disconnection. Reconnecting...")

client = mqtt.Client()
client.username_pw_set(mqtt_username, mqtt_password)
client.on_connect = on_connect
client.on_publish = on_publish
client.on_disconnect = on_disconnect
client.loop_start()
client.connect(mqtt_broker, 1883, 60)

def meters_to_feet(meters):
    return meters * 3.28084

def meters_per_second_to_mph(mps):
    return mps * 2.23694

def track_to_compass_direction(track):
    if track is None:
        return None

    directions = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW', 'N']
    index = int((track + 22.5) % 360 // 45)
    return directions[index]

def hdop_to_accuracy_feet(hdop):
    accuracy_meters = hdop * 5
    return meters_to_feet(accuracy_meters)

def init_gps_session():
    global session
    session = gps(mode=WATCH_ENABLE | WATCH_NEWSTYLE)

def gps_reports_worker():
    while True:
        try:
            report = session.next()
            gps_reports_queue.put(report)
        except StopIteration:
            continue

def start_gps_worker_thread():
    global gps_reports_thread
    gps_reports_thread = threading.Thread(target=gps_reports_worker, daemon=True)
    gps_reports_thread.start()

def ensure_mqtt_connection():
    if not client.is_connected():
        print("MQTT connection lost. Reconnecting...")
        client.reconnect()

num_reports = 0
current_fix = 0
last_values = {}

# Create a queue to store GPS reports
gps_reports_queue = queue.Queue()

# Initialize the GPS session and start the GPS worker thread
init_gps_session()
start_gps_worker_thread()

# Add a timeout (in seconds) for retrieving reports from the queue
timeout = 5

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
#    print("Received GPS report number " + str(num_reports))
    ensure_mqtt_connection()
    if report['class'] == 'TPV':
        if hasattr(report, 'mode'):
            status = {1: "NO FIX", 2: "2D FIX", 3: "3D FIX"}.get(report.mode, "UNKNOWN")
            if status != last_values.get('status'):
                client.publish("gps/fix", status, retain=True)
                last_values['status'] = status
        if hasattr(report, 'lat'):
            new_value = report.lat
            if new_value != last_values.get('latitude') or time.time() - last_values.get('latitude_time') >=60:
                client.publish("gps/latitude", str(new_value), retain=True)
                last_values['latitude'] = new_value
                last_values['latitude_time'] = time.time()
        if hasattr(report, 'lon'):
            new_value = report.lon
            if new_value != last_values.get('longitude'):
                client.publish("gps/longitude", str(new_value), retain=True)
                last_values['longitude'] = new_value
        if hasattr(report, 'alt'):
            new_value = round(meters_to_feet(report.alt))
            if new_value != last_values.get('altitude'):
                client.publish("gps/altitude", str(new_value), retain=True)
                last_values['altitude'] = new_value
        if hasattr(report, 'speed'):
            speed_mph = meters_per_second_to_mph(report.speed)
            new_value = 0 if speed_mph < 3 else speed_mph
            if new_value != last_values.get('speed'):
                client.publish("gps/speed", str(new_value), retain=True)
                last_values['speed'] = new_value
        if hasattr(report, 'track'):
            new_value = track_to_compass_direction(report.track)
            if new_value != last_values.get('direction'):
                client.publish("gps/direction", new_value, retain=True)
                last_values['direction'] = new_value

        if hasattr(report, 'epx') and hasattr(report, 'epy'):
            epx = report.epx
            epy = report.epy
            cep_m = math.sqrt(epx**2 + epy**2)
            cep_ft = meters_to_feet(cep_m)
            cep = round(cep_ft, -1)
            if cep != last_values.get('cep'):
               client.publish("gps/2D_Accuracy", str(cep), retain=True)
               last_values['cep'] = cep

        if hasattr(report, 'epx') and hasattr(report, 'epy') and hasattr(report, 'epv'):
            epx = report.epx
            epy = report.epy
            epv = report.epv
            sep_m = math.sqrt(epx*2 + epy**2 + epv**2)
            sep_ft = meters_to_feet(sep_m)
            sep = round(sep_ft, -1)
            if sep != last_values.get('sep'):
               client.publish("gps/3D_Accuracy", str(sep), retain=True)
               last_values['sep'] = sep


    if report['class'] == 'SKY':
#       print(report)
        if hasattr(report, 'satellites'):
            satellites = report.get('satellites', [])
            used_satellites = sum (1 for sat in satellites if sat['used'])
            if used_satellites != last_values.get('satellites'):
                client.publish("gps/satellites", str(used_satellites), retain=True)
                last_values['satellites'] = used_satellites


