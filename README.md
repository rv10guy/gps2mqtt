# GPS to MQTT

This Python application uses gpsd to read GPS data from a connected GPS device, processes the information, and publishes the data to an MQTT broke and Traccar. It was built to run on a Raspberry Pi but should work with any platform running gpsd and a GPS module to transmit location data in real-time.

## Features

- Reads GPS data from gpsd and processes it
- If MQTT is enabled it will
  - Publishes the following GPS information to an MQTT broker:
    - Fix status
    - Latitude
    - Longitude
    - Altitude
    - Speed
    - Direction (compass)
    - 2D and 3D accuracy
    - Number of used satellites
  - Reconnects to the GPS device and MQTT broker if the connection is lost
- If Traccar is enabled it will publish the following information to Traccar
    - Latitude
    - Longitude
    - Altitude
    - Speed
    - Track
    - Accuracy Data
- Uses a configuration file to store MQTT broker settings

There sample ini file is fully documented. In an effort to reduce load on the MQTT server an Traccar, there are a number of variables that determie when updates are send. These are:
  - interval_always - This is how often a report is sent regardless of change in status.
  - interval_move - This is the interval reports are sent when movement is detected.
  - interval_track - This is the interval reports are sent if vehicle is moving and the track changes
  - dist_move - Distance GPS must move to trigger a report (feet)
  - chg_speed - Change in speed in mph to send report instantly
  - chg_track - Required change in track (degrees) to trigger a track change
  - ignore_speed - Ignore speeds below this value (mph). This is to prevent small gps position changes while sitting from registering as movement. 

## Requirements

- Python 3.6 or higher
- [paho-mqtt](https://pypi.org/project/paho-mqtt/) library
- [gps3](https://pypi.org/project/gps3/) library
- [haversine](https://pypi.org/project/haversine/)

## Installation

1. Clone the repository:
```
git clone https://github.com/rv10guy/gps2mqtt.git
```

2. Change to the `gps2mqtt` directory:
```
cd gps2mqtt
```

3. Install the required Python libraries:
```
pip install -r requirements.txt
```

4. Install and test gpsd on your platform.

## Usage
Run the application with:
```
python gps2mqtt.py
```

The application will start reading GPS data from gpsd and publish the information as configured in the ini file.

## License

This project is licensed under the MIT License - see the LICENSE file for details.