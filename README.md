# GPS to MQTT

This Python application uses gpsd to read GPS data from a connected GPS device, processes the information, and publishes the data to an MQTT broker. It was built to run on a Raspberry Pi but should work with any platform running gpsd and a GPS module to transmit location data in real-time.

## Features

- Reads GPS data from gpsd and processes it
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
- Uses a configuration file to store MQTT broker settings

## Requirements

- Python 3.6 or higher
- [paho-mqtt](https://pypi.org/project/paho-mqtt/) library
- [gps3](https://pypi.org/project/gps3/) library

## Installation

1. Clone the repository:
'''
git clone https://github.com/yourusername/gps2mqtt.git
'''

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

The application will start reading GPS data from gpsd and publish the information to the configured MQTT broker.

## License

This project is licensed under the MIT License - see the LICENSE file for details.