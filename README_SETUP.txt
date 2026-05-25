CastAdhan Portable v3.2 - Setup Guide
=====================================

WHAT IS THIS?
-------------
CastAdhan Portable is a self-contained Islamic prayer time system that runs on a
Linux single-board computer or mini PC and broadcasts the adhan and other audio
to Google Cast (Chromecast) speakers on your local network.

It automatically plays:
  - Adhan at all 5 daily prayer times
  - A fajr warning (5 minutes before sunrise) to signal the end of Fajr time
  - An Asr warning (5 minutes before Asr)
  - A Maghrib warning (5 minutes before Maghrib / sunset)
  - Morning Dhikr (Al-Mathurat) every morning at a set time
  - Surah Kahf on Friday mornings (instead of morning dhikr)
  - Evening Dhikr (Al-Mathurat) every evening after Maghrib
  - Suhoor alarm automatically during Ramadan (before Fajr)

IMPORTANT: This is NOT for Arduino, ESP32, or microcontrollers. It requires a
full Linux system with Python 3.9+.


HARDWARE REQUIREMENTS
---------------------
You need one of the following (or equivalent):

Recommended:
  - Raspberry Pi 3B / 3B+ / 4 / Zero 2W
  - Orange Pi Zero 2W / Orange Pi 3
  - Any x86/ARM mini PC running Linux (Ubuntu, Debian, Raspberry Pi OS)

Minimum specs:
  - 512 MB RAM (1 GB+ recommended)
  - 2 GB storage
  - WiFi or Ethernet (must be on the same network as your Chromecast speakers)
  - Linux OS (not macOS, not Windows, not ESP32)

You also need:
  - One or more Google Cast-compatible speakers (Chromecast Audio, Google Home,
    Nest Audio, Nest Mini, etc.) on the same WiFi network


STEP-BY-STEP SETUP
------------------

Step 1: Copy the folder to your device
  Copy the entire "CAST ADHAN PORTABLE" folder to your device.
  Suggested location: /home/<yourname>/castadhan/

  From a Mac/PC using scp:
    scp -r "CAST ADHAN PORTABLE/" pi@raspberrypi.local:/home/pi/castadhan/

  Or simply copy the folder from the USB drive to your device.

Step 2: Run the installer
  On your device, open a terminal and run:

    cd /home/<yourname>/castadhan/
    chmod +x install_castadhan.sh
    ./install_castadhan.sh

  The installer will guide you through the full setup including Python
  dependencies, location settings, and optional systemd service installation.

  --- OR manually ---

Step 2 (manual): Install Python dependencies
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt

Step 3: Edit config.yaml with your location
  Open config.yaml and set:
    - app.location.city / country / latitude / longitude
    - app.timezone  (e.g. "Europe/London", "America/New_York")
    - app.calculation_method  (ISNA, MWL, Egypt, Karachi, etc.)

Step 4: Run the app to test it
    python3 app.py

  You should see log output including "Scheduler started successfully".

Step 5: Open the control console
  On any device on your LAN, open a browser and go to:
    http://<device-ip>:8786/

  The console shows prayer times, speaker status, and all settings.

Step 6: Set up as a system service (optional but recommended)
  Run the installer and choose "Y" when asked about systemd, or create
  /etc/systemd/system/castadhan.service manually (template in installer).


AUDIO FILES
-----------
The audio/ folder contains:

INCLUDED (ready to use):
  audio/adhan.mp3             REQUIRED  Main adhan (plays at all 5 prayer times)
  audio/fajr_warning.mp3      REQUIRED  Fajr end warning (plays 5 min before sunrise)
  audio/asr_warning.mp3       REQUIRED  Asr warning (plays 5 min before Asr)
  audio/maghrib_warning.mp3   REQUIRED  Maghrib warning (plays 5 min before Maghrib)
  audio/morning_dhikr.mp3     included  Al-Mathurat morning (plays at morning_dhikr_time)
  audio/evening_dhikr.mp3     included  Al-Mathurat evening (plays after Maghrib daily)
  audio/surah_kahf.mp3        included  Surah Kahf (plays Friday mornings instead of dhikr)
  audio/suhoor_alarm.mp3      included  Suhoor alarm (plays during Ramadan before Fajr)

MISSING - YOU MUST PROVIDE THESE (optional features):
  audio/wakeup.mp3            optional  Wakeup audio (weekday mornings - rules.wakeup_time)
  audio/takbeeraat.mp3        optional  Eid Takbeeraat (auto on Eid days)
  audio/twilight.mp3          optional  Twilight reminder (summer high-latitude areas)
  audio/adhan_compatible.mp3  optional  Alternate adhan for older Chromecast devices

If any optional file is missing, that feature is silently skipped — the app
will not crash. Only adhan.mp3 and fajr_warning.mp3 are needed for basic use.


DAILY SCHEDULE
--------------
  Fajr time      → Adhan
  Sunrise - 5min → Fajr warning audio (end of Fajr time)
  morning_dhikr_time (default 07:00):
    Monday–Thursday, Saturday–Sunday → Morning Dhikr (Al-Mathurat)
    Friday         → Surah Kahf (full recitation)
  Dhuhr time     → Adhan
  Asr - 5min     → Asr warning audio
  Asr time       → Adhan
  Maghrib - 5min → Maghrib warning audio
  Maghrib time   → Adhan
  Maghrib + 30min → Evening Dhikr (Al-Mathurat) — every day
  Isha time      → Adhan

  Ramadan only:
    Fajr - 30min → Suhoor alarm on all speakers (suhoor_lead_minutes configurable)


PORTS
-----
The app listens on port 8786 by default (configurable in config.yaml).
Make sure your firewall allows access from other devices on your LAN.

  sudo ufw allow 8786   (Ubuntu/Debian)

To change the port, edit config.yaml:
  app:
    port: 8786


CONFIGURATION
-------------
All settings live in config.yaml. You can also change most of them live through
the web console at http://<device-ip>:8786/ without restarting the app.

Key settings:
  app.location.city/country/latitude/longitude  Your location for prayer times
  app.timezone                                  e.g. "Europe/London"
  app.calculation_method                        ISNA / MWL / Egypt / Karachi / etc.
  speakers.include_if_name_contains             Filter which speakers to use
  speakers.general_volume                       Default volume (0.0 to 1.0)
  rules.morning_dhikr_time                      Time for morning content (HH:MM)
  rules.evening_after_maghrib_minutes           Minutes after Maghrib for evening content
  rules.suhoor_lead_minutes                     Minutes before Fajr for Ramadan alarm


TROUBLESHOOTING
---------------
No speakers found:
  Make sure your device and speakers are on the same WiFi network.
  The app discovers Chromecast devices using mDNS/zeroconf.
  Try rebooting the device and running the app again.

Prayer times not loading:
  Check your latitude/longitude in config.yaml and your internet connection.
  Prayer times are fetched from api.aladhan.com.

Audio not playing (speakers found but silent):
  Check that port 8786 is reachable on your LAN. The Chromecast pulls audio
  from http://<device-ip>:8786/media/... — if the device IP is wrong or the
  port is blocked, speakers will launch but stay silent.
  Check the app log: tail -f castadhan.log

App crashes on start:
  Make sure all Python packages are installed:
    source venv/bin/activate && pip install -r requirements.txt

Port 8786 not accessible from other devices:
  sudo ufw allow 8786


VERSION HISTORY
---------------
v3.2  Friday Surah Kahf (morning), fajr warning, evening dhikr daily,
      IP re-detection fix, slider/tab UI fixes, audio routing matrix
v3.1  Portable edition — binary search twilight, validate_config,
      atomic state I/O, speaker audio routing
