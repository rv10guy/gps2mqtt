#! /usr/bin/python3

from gps import *
import time
import urllib.request
import haversine
import socket

TRACCARURL      = 'https://your-traccar-server.example:5055'
TRACCARID       = 'example'


#   We will report every INT_ALWAYS seconds. 
#   While we are moving more than DIST_MOVE between reports
#   we will report both every INT_MOVE seconds 
#   as well as immediately when we are stopping or
#   starting to move or
#   changing our speed by more than CHG_SPEED;
#   on the move will also report every INT_TRACK seconds
#   if our track changes by more than CHG_TRACK.
#   
#   We will include additional vehicle data in the report
#   every INT_FULL seconds if it is availabe.
#
#   This timing assumes a position is available every second.
#   Our GPS reports 10 fixes a second.
#   You may need to adjust the timings if your GPS reports less frequently.
#   The 'sendtime' value in the report is intended to help with this adjustment.

SLEEPTIME       =   1   # check position every SLEEPTIME seconds
                        # interval times in seconds, 0 turns off a report
INT_ALWAYS      =  60   # always report every INT_ALWAYS seconds
INT_FULL        =   0   # send a full report including FHEM data (best a multiple of INT_ALWAYS)
INT_MOVE        =  10   # report every INT_MOVE seconds if moving
INT_TRACK       =   2   # report every INT_TRACK seconds if on the move and track changed more than CHG_TRACK degrees

DIST_MOVE     =   0.01  # distance to detect movement [km]
CHG_TRACK     =   5     # minimum track change to cause track report [degrees]
CHG_SPEED     =  10     # report speed change above this value immediately [km/h]

MIN_SPEED     =   2     # minimum speed to report (ignore GPS pos jitter) [km/h]

USE_WALLCLOCK   = True  # synchronises reporting times to wall clock

DEBUG = False

# persistent global state
prev_lat = 0
prev_lon = 0
prev_track = 0
prev_speed = 0
second = -SLEEPTIME


def get_pos():      # get position report from gpsd
    gpsd = gps(mode=WATCH_ENABLE|WATCH_NEWSTYLE) 
    while(1):       # loop until we get a position report with mode '3'
        pos = gpsd.next() 
        if pos['class'] == 'TPV' and pos['mode'] == 3:
            pos['speed'] *= 1.852   # knots to km/h
            if pos['speed'] < MIN_SPEED:
                pos['speed'] = 0
            return(pos)
 

def send_report(msg):   # send a report to the server
    msg = msg + ('&sendtime=%f' % (time.time()))
    url = '%s/?id=%s&timestamp=%d%s' % (TRACCARURL, TRACCARID, int(time.time()), msg)
    try:
        if DEBUG:
            print(url)
        else:
            r = urllib.request.urlopen(url)
        return(0)
    except urllib.error.URLError as e:             
        print(f"Error in send_report: {e}")
        return(1)

def report(pos):        # format report
    msg = '&lat=%s&lon=%s&altitude=%s&speed=%s&bearing=%s&epx=%s&epy=%s&epv=%s&track=%s'  % (  
        pos['lat'], 
        pos['lon'], 
        pos['alt'], 
        pos['speed'], 
        pos['track'], 
        pos['epx'],
        pos['epy'],
        pos['epv'],
        pos['track'], 
    )
    return(msg)

def make_report(pos):
    global prev_track, prev_speed, prev_lat, prev_lon

    send_report(report(pos))

    # store reported values
    prev_track = pos['track']
    prev_speed = pos['speed']
    prev_lon = pos['lon']
    prev_lat = pos['lat']

def bearing_change(b1, b2):
    r = (b2-b1) % 360.0
    if r>=180:
        r -= 360
    return(abs(r))

while(1):       # daemon, keep running

    if USE_WALLCLOCK:
        second = int(time.time())
    else:
        second += SLEEPTIME

    pos = get_pos()
    dist_moved = haversine.haversine((prev_lat, prev_lon), (pos['lat'], pos['lon']))
    speed = pos['speed']

    if INT_ALWAYS and second % INT_ALWAYS == 0:     # unconditional reports
        make_report(pos)
    elif abs(prev_speed - speed) > CHG_SPEED:       # we accelerated or breaked hard
        make_report(pos)
    elif prev_speed == 0 and speed > 0:             # we started moving
        make_report(pos)
    elif prev_speed > 0 and speed == 0:             # we stopped
        make_report(pos)
    elif dist_moved > DIST_MOVE:                    # we are moving
        if INT_MOVE and second % INT_MOVE == 0:     # normal report in motion
            make_report(pos)
        elif INT_TRACK and second % INT_TRACK == 0: # report when turning
            if  bearing_change(prev_track, pos['track']) > CHG_TRACK:
                make_report(pos)

    time.sleep(SLEEPTIME - (time.time()%1))         # sleep for the remainder of SLEEPTIME assuming iteration took <1s