
# Ergänzungen 2025 Dirk Clemens
- OTA Firmware Management
    - u.a. per REST API für CI/CD
- Datenbank Bereinigung nach Aufbewahrungszeitraum
- Bootstrapped Web UI
- dynamische Aktualisierungen der UI (Javascript)
- Diverse Verbesserungen


---
Tracker for MySensors messages
===========================================

I needed a simple tool for keeping track of all the MySensors nodes which I had built and deployed around the house over the years:
* what is the battery level of a sensor node?
* what was the last time I changed the battery on that sensor node, i.e. how many months has it been running with the current battery?
* see at a glance if a sensor has crashed, i.e. has not sent any messages for, say, more than a day
* is a sensor node sending strange messages?

For a detailed description of the intended behavior of the app (i.e. the requirements specification), see [requirements](requirements.md).

This is part of my home automation setup. For an overview, see my [blog](https://requireiot.com/my-home-automation-story-part-1/).

Prerequisites
-------------
The app assumes that all MySensors messages are captured by an ~~MQTT~~Ethernet gateway, as described on the [MySensors website](https://www.mysensors.org/build/ethernet_gateway)

The app is written in Python 3. I have tested this both on ~~my Microsoft Windows 10~~ macOS development machine, and on a Ubuntu Linux server.

The app uses an Sqlite database.

The app uses the [**Peewee**](http://docs.peewee-orm.com/en/latest/#) library  to access the database, the [**Flask**](https://palletsprojects.com/p/flask/) web framework ~~, and the [**Eclipe Paho**](https://www.eclipse.org/paho/) MQTT library to listen to the MQTT messages published by the MySensors gateways.~~

On a linux server that runs the app, I just did
```sh
sudo apt-get install sqlite3
sudo apt-get install python3 python3-venv python3-dev
```

Installation
------------
Install the source files in any folder, say `~/mytracker` .
Now install the required libraries
```sh
cd ~/mytracker
python3 -m venv venv
source venv/bin/activate
venv/bin/python -m pip install peewee flask wtforms schedule intelhex crcmod
or 
venv/bin/python -m pip install -r ./requirements.txt
```



Now you can just run the app
```sh
venv/bin/python app.py
```
This will start the built-in webserver on port 5555. 

The Flask people recommend not to use the built-in server for a production environment, but I decided it was good enough for my use at home. This has been running for >6 months now, without a glitch. logging messsages from ~20 MySensors nodes.

Browse to http://*servername*:5555/nodes, and you should see the MySensorsTracker UI.

Permanent Use
-------------
For long-term use, I am running this under supervisord (see http://supervisord.org/index.html). 

I created `/etc/supervisor/conf.d/mytracker.conf` and entered
```
[program:mytracker]
command=/home/admin/mytracker/venv/bin/python app.py
directory=/home/admin/mytracker
stdout_logfile=/home/admin/mytracker/stdout.log
stderr_logfile=/home/admin/mytracker/stderr.log
user=admin
startretries=1 
```
(adjust the path for your configuration)

I edited `/etc/supervisor/supervisord.conf` and made sure it contains these lines
```
[inet_http_server]
port=*:9001 
[include]
files=conf.d/*.conf
```
Now I can view the status of the app by browsing to http://*servername*:9001

