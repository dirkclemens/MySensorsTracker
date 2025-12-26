# -*- coding: utf-8 -*-
#
# @file          app.py
# Author       : Bernd Waldmann
# Created      : Sun Oct 27 23:01:35 2019
#
# Tracker for MySensors messages, with web viewer
#
#   Copyright (C) 2019,2021 Bernd Waldmann
#
#   This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0. 
#   If a copy of the MPL was not distributed with this file, You can obtain one at http://mozilla.org/MPL/2.0/
#
#   SPDX-License-Identifier: MPL-2.0
#
#   Ergänzungen 2025 Dirk Clemens
#   * OTA Firmware Management
#   * Datenbank Bereinigung nach Aufbewahrungszeitraum
#   * Bootstrapped Web UI
#   * Diverse Verbesserungen
#
#   Start this app with:
#       venv/bin/python app.py
#   Stop this app with Ctrl-C or
#       sudo kill -9 $(lsof -ti:5555)
#       sudo lsof -ti:5555 | xargs -r sudo kill -9
#   
# adjust these constants to your environment

WEB_PORT = 5555                        # port for web server
GATEWAY_HOST = "192.168.2.211"          # MySensors Gateway IP address
GATEWAY_PORT = 5003                     # MySensors Gateway TCP port
DATABASE_FILE = 'mysensors.db'
DB_DIR = '/var/lib/mytracker/'

import sys,re,time,os
import math, json
import socket
import threading
import logging
import logging.config
import schedule
from queue import Queue, Empty
from datetime import datetime,timedelta
from peewee import *                    # MIT license
import flask                            # BSD license
from flask import Flask,render_template,request,url_for,redirect,Response,jsonify
from playhouse.flask_utils import FlaskDB
from playhouse.hybrid import hybrid_property
from playhouse.flask_utils import object_list
from playhouse.migrate import *
from playhouse.reflection import Introspector
import wtforms as wtf                   # BSD license

import mysensors
import ota_firmware

##############################################################################
#region Logging

def init_logging():

    logging.config.dictConfig({
        'version': 1,
        'formatters': {
            'default': {
                'format': '[%(asctime)s] %(levelname)s in %(module)s: %(message)s',
            },
            'brief': {
                'format': '%(levelname)s in %(module)s: %(message)s',
            }
        },
        'handlers': {
            'wsgi': {
                'class': 'logging.StreamHandler',
                'stream': 'ext://flask.logging.wsgi_errors_stream',
                'formatter': 'default'
            },
            'console': {
                'class': 'logging.StreamHandler',
                'stream': 'ext://sys.stderr',
                'formatter': 'default',
            },
        },
        'loggers': {
            'root': {
                'level': logging.WARNING,
                'handlers': ['console']  # Only console, wsgi causes duplicates
            },
            'app': {
                'level': logging.INFO,
                'handlers': ['console'],
                'propagate': False  # Don't propagate to root to avoid duplicates
            },
            'werkzeug': {
                'level': logging.WARNING,
                'handlers': ['console'],
                'propagate': False
            },
        },
        'disable_existing_loggers': False,
    })

    return logging.getLogger('app')

applog = init_logging()

#endregion

if not os.path.isdir(DB_DIR):
    DB_DIR = os.path.dirname(os.path.realpath(__file__))
# DATABASE_URI = 'sqlite:///%s' % os.path.join(DB_DIR, DATABASE_FILE)
applog.info('Using database at '+DB_DIR)

# Retention Policy Configuration
MESSAGE_RETENTION_DAYS = 30   # Delete messages older than 30 days
VALUE_RETENTION_DAYS = 365    # Delete values older than 1 year
CLEANUP_HOUR = 3              # Run cleanup daily at 3 AM

app = Flask(__name__)
app.config['FLASK_ENV'] = 'development'
# DEBUG wird durch app.run(debug=True) gesetzt
#app.secret_key = 'mysensors-tracker-secret-key-change-in-production'  # Für Flask Sessions (flash messages)
import secrets
app.secret_key = secrets.token_hex(32) 

##############################################################################
#region Model helpers

def make_usid(nid,cid):
    """calculate globally unique sensor id from node id and child id
    Args:
        nid (int): MySensors node id
        cid (int): MySensors child id
    Returns:
        int: unique id
    """
    return 1000*nid + cid

##----------------------------------------------------------------------------

def split_usid(usid):
    nid = usid // 1000
    cid = usid % 1000
    return (nid,cid)

##----------------------------------------------------------------------------

def make_uvid(nid,cid,typ):
    """calculate globally unique value id from node id, child id, message type
    Args:
        nid (int): MySensors node id
        cid (int): MySensors child id
        typ (int): MySensors type id
    Returns:
        int: unique id
    """
    return 1000000 *typ + 1000*nid + cid

#endregion
##############################################################################
#region Model definition

db = SqliteDatabase(None)

class BaseModel(Model):
    class Meta:
        database = db


class Node(BaseModel):
    """ table describing MySensor nodes
    """
    nid         = IntegerField( primary_key=True,       help_text="MySensors node id")      # e.g. '109'
    sk_name     = CharField( max_length=25, null=True,  help_text="sketch name")            # e.g. 'MyWindowSensor'
    sk_version  = CharField( max_length=25, null=True,  help_text="sketch version")         # e.g. '$Rev: 1685 $'
    sk_revision = IntegerField( default=0,              help_text="sketch SVN rev")          
    api_ver     = CharField( max_length=25, null=True,  help_text="MySensors API version")  # e.g. '2.3.1'
    lastseen    = DateTimeField( default=datetime.now,  help_text="last message" )
    location    = CharField( max_length=32, null=True,  help_text="where in the house is it?")
    bat_changed = DateField( null=True,                 help_text="date of last battery change")
    bat_level   = IntegerField(null=True,               help_text="battery level in %")
    parent      = IntegerField(null=True,               help_text="parent node Id")
    arc         = IntegerField(null=True,               help_text="ARC success rate")


class Sensor(BaseModel):    
    """ table describing MySensor sensors, 
        Each row is one sensor on one node, as reported in present() calls in presentation() function
    """
    usid        = IntegerField( primary_key=True,       help_text="unique id")
    nid         = ForeignKeyField(Node)             
    cid         = IntegerField(                         help_text="MySensors child id")     # e.g. '11' (contact)
    typ         = IntegerField( null=True,              help_text="MySensors sensor type")  # e.g. '0'=S_DOOR
    name        = CharField( max_length=25, null=True,  help_text="sensor description")     # e.g. "Contact L"
    values      = BigBitField( null=True,               help_text="which V_xxx types have been seen")
    lastseen    = DateTimeField( default=datetime.now,  help_text="last message" )


class ValueType(BaseModel):
    """ table describing a sensor sub-channel, as reported by type=V_xxx messages
        Each row is one V_xxx value type sent by one sensor on one node
    """
    uvid        = IntegerField( primary_key=True,       help_text="unique channel id" )
    usid        = ForeignKeyField(Sensor)    
    nid         = ForeignKeyField(Node, backref='tvalues')
    cid         = IntegerField(                         help_text="MySensors child id")         # e.g. '11' (contact)
    typ         = IntegerField(                         help_text="MySensors value type")       # e.g. '2'=V_STATUS
    value       = CharField( max_length=25, null=True,  help_text="Current value")
    received    = DateTimeField( default=datetime.now,  help_text="timestamp" )

    @hybrid_property
    def timestamp(self):
        return self.received.to_timestamp()


class Message(BaseModel):
    """ table for all information contained in one MySensors message, as reported by gateway.
        Each row is one message received via gateway
    """
    nid         = ForeignKeyField(Node)
    cid         = IntegerField(                     help_text="MySensors child id" )        # e.g. '11' (contact)
    cmd         = IntegerField(                     help_text="MySensors command")
    typ         = IntegerField(                     help_text="MySensors type")
    payload     = CharField( max_length=25)
    received    = DateTimeField(default=datetime.now, help_text="timestamp" )

    @hybrid_property
    def usid(self):
        return make_usid(self.nid.nid, self.cid)

    @hybrid_property
    def value(self):
        return self.payload

    @hybrid_property
    def timestamp(self):
        return self.received.to_timestamp()
        

class Firmware(BaseModel):
    """Table for storing OTA firmware files."""
    fw_type     = IntegerField(help_text="Firmware type")
    fw_ver      = IntegerField(help_text="Firmware version")
    blocks      = IntegerField(help_text="Number of blocks")
    crc         = IntegerField(help_text="CRC16 checksum")
    filename    = CharField(max_length=255, help_text="Original filename")
    hex_data    = TextField(help_text="Intel HEX file content")
    uploaded    = DateTimeField(default=datetime.now, help_text="Upload timestamp")
    
    class Meta:
        indexes = (
            (('fw_type', 'fw_ver'), True),  # Unique constraint on type+version
        )


#endregion
##############################################################################
#region Model access
#     
 
def add_or_select_node(nid):
    """make sure node record exists, create if necessary
    Args:
        nid (int): MySensors node ID
    Returns:
        Node: instance
    """
    node, create = Node.get_or_create(nid=nid)
    return node 
    
##----------------------------------------------------------------------------
        
def add_or_select_sensor(nid,cid):
    """make sure sensor record exists, create if necessary
    Args:
        nid (int): node id
        cid (int): child id
    Returns:
        Sensor:    instance
    """
    sensor, create = Sensor.get_or_create(
        usid=make_usid(nid,cid),
        defaults={'nid':nid, 'cid':cid}
        )
    return sensor

##----------------------------------------------------------------------------

def cleanup_old_data():
    """Delete old messages and values based on retention policy.
    
    Returns:
        dict: Statistics about deleted records
    """
    from datetime import datetime, timedelta
    
    stats = {
        'messages_deleted': 0,
        'values_deleted': 0,
        'db_size_before': 0,
        'db_size_after': 0,
        'timestamp': datetime.now()
    }
    
    try:
        # Get DB file size before cleanup
        db_path = os.path.join(DB_DIR, DATABASE_FILE)
        if os.path.exists(db_path):
            stats['db_size_before'] = os.path.getsize(db_path)
        
        # Calculate cutoff dates
        message_cutoff = datetime.now() - timedelta(days=MESSAGE_RETENTION_DAYS)
        value_cutoff = datetime.now() - timedelta(days=VALUE_RETENTION_DAYS)
        
        # Delete old messages
        messages_query = Message.delete().where(Message.received < message_cutoff)
        stats['messages_deleted'] = messages_query.execute()
        applog.info(f"Deleted {stats['messages_deleted']} messages older than {MESSAGE_RETENTION_DAYS} days")
        
        # Delete old values
        values_query = ValueType.delete().where(ValueType.received < value_cutoff)
        stats['values_deleted'] = values_query.execute()
        applog.info(f"Deleted {stats['values_deleted']} values older than {VALUE_RETENTION_DAYS} days")
        
        # VACUUM database to reclaim space
        db.execute_sql('VACUUM')
        applog.info("Database VACUUM completed")
        
        # Get DB file size after cleanup
        if os.path.exists(db_path):
            stats['db_size_after'] = os.path.getsize(db_path)
            freed_mb = (stats['db_size_before'] - stats['db_size_after']) / (1024 * 1024)
            applog.info(f"Freed {freed_mb:.2f} MB of disk space")
        
    except Exception as e:
        applog.error(f"Error during cleanup: {e}")
        
    return stats

##----------------------------------------------------------------------------
        
def add_or_select_tvalue(nid,cid,typ,val=None,dt=None):
    """make sure TypedValue record exists, create if necessary
    Args:
        nid (int): node id
        cid (int): child id
        typ (int): type V_xxx constant
        val (str): value string or None
        dt (datetime): timestamp or None
    Returns:
        ValueType:    instance
    """
    tvalue, create = ValueType.get_or_create(
        uvid=make_uvid(nid,cid,typ),
        defaults={'nid':nid, 'cid':cid, 'typ':typ, 'usid':make_usid(nid,cid) }
        )
    if val is not None:
        tvalue.value = val 
    if dt is not None:
        tvalue.received = dt
    return tvalue

##----------------------------------------------------------------------------

def fill_tvalues():
    """ migrate older DB version by filling ValueType table from Message table
    """
    query = Sensor.select().order_by(Sensor.usid)
    for s in query:
        for typ in range(64):
            if s.values.is_set(typ):
                try:
                    msg = Message.select().where( 
                            Message.nid == s.nid, 
                            Message.cid == s.cid, 
                            Message.cmd == mysensors.Commands.C_SET,
                            Message.typ == typ
                        ).order_by(Message.received.desc()).get()
                    tvalue = add_or_select_tvalue(
                                s.nid_id,
                                s.cid,typ,
                                msg.payload,
                                msg.received )
                    tvalue.save()
                    applog.debug("added tvalue uvid:%d nid:%d cid:%d typ:%d = '%s'", 
                        tvalue.uvid, s.nid_id, s.cid, typ, msg.payload )
                except Message.DoesNotExist:
                    pass

##----------------------------------------------------------------------------

def new_battery( nid, date=datetime.today()):
    """ declare that new battery has been inserted
    Args:
        nid (int): MySensors node ID
        date (datetime.date): date of battery change
    """
    node = add_or_select_node(nid)
    node.bat_changed = date
    node.save()

##----------------------------------------------------------------------------

def delete_node( nid ):
    """ delete a node, and all table rows that refer to it
    Args:
        nid (int): MySensors node ID
    """
    with db.atomic() as txn:
        applog.debug("Deleting node {0}".format(nid))
        n = Message.delete().where(Message.nid==nid).execute()
        applog.debug("{0} messages removed".format(n))
        n = ValueType.delete().where(ValueType.nid==nid).execute()
        applog.debug("{0} types removed".format(n))
        n = Sensor.delete().where(Sensor.nid==nid).execute()
        applog.debug("{0} sensors removed".format(n))
        n = Node.delete().where(Node.nid==nid).execute()
        applog.debug("{0} nodes removed".format(n))

##----------------------------------------------------------------------------

def delete_node_requests( nid ):
    """ delete all request messages for this node
    Args:
        nid (int): MySensors node ID
    """
    with db.atomic() as txn:
        applog.debug("Deleting node requests {0}".format(nid))
        n = Message.delete().where( (Message.nid==nid) & (Message.cmd == mysensors.Commands.C_REQ) ).execute()
        applog.debug("{0} request messages removed".format(n))

##----------------------------------------------------------------------------

def delete_sensor( nid, cid ):
    """ delete a sensor, and all table rows that refer to it
    Args:
        nid (int): MySensors node ID
        cid (int): MySensors child ID
    """
    usid = make_usid(nid,cid)
    with db.atomic() as txn:
        applog.debug("Deleting node {0} sensor {1}".format( nid, cid ))

        n = Message.delete().where( (Message.nid==nid) & (Message.cid==cid) ).execute()
        applog.debug("{0} messages removed".format(n))

        n = ValueType.delete().where(ValueType.usid==usid).execute()
        applog.debug("{0} types removed".format(n))

        n = Sensor.delete().where(Sensor.usid==usid).execute()
        applog.debug("{0} sensors removed".format(n))

##----------------------------------------------------------------------------

def delete_old_stuff( ndays ):
    """ delete everything older than `ndays` days 
    Args:
        ndays (int): no of days to keep
    """
    cutoff = (datetime.today()-timedelta(days=ndays)).timestamp()
    applog.info("Deleting everything older than {0} days".format(ndays))

    n = ValueType.delete().where( ValueType.timestamp < cutoff ).execute()
    applog.debug("{0} values removed".format(n))

    n = Message.delete().where( Message.timestamp < cutoff ).execute()
    applog.debug("{0} messages removed".format(n))


#endregion
##############################################################################
#region message handling
      
def add_message( nid,cid,cmd,typ,pay ):
    """ add a record to 'messages' table
    Args:
        nid (int): MySensors node ID
        cid (int): MySensors child ID
        cmd (int): MySensors C_xxx command
        typ (int): MySensors I_xxx type
        pay (string): payload
    """
    tnow = datetime.now()

    node = add_or_select_node(nid)
    node.lastseen = tnow
    node.save()
    sensor = add_or_select_sensor(nid,cid)
    sensor.lastseen = tnow
    sensor.save()
    
    # Push sensor update to SSE queue
    try:
        sensor_data = {
            'nid': nid,
            'cid': cid,
            'usid': sensor.usid,
            'lastseen': tnow.strftime('%d.%m.%Y %H:%M:%S')
        }
        try:
            sensor_queue.put_nowait(sensor_data)
        except:
            pass  # Queue full, skip this update
    except Exception as e:
        applog.debug("Error adding sensor to SSE queue: %s", str(e))
    
    # Push node update to SSE queue
    try:
        node_data = {
            'nid': nid,
            'lastseen': tnow.strftime('%d.%m.%Y %H:%M:%S')
        }
        try:
            node_queue.put_nowait(node_data)
        except:
            pass
    except Exception as e:
        applog.debug("Error adding node to SSE queue: %s", str(e))
    
    msg = Message.create(nid=nid,cid=cid,cmd=cmd,typ=typ,payload=pay)
    msg.save()

##----------------------------------------------------------------------------

def on_parent_message( nid,val ):
    """ update parent field for a node
    Args:
        nid (int): MySensors node ID
        val (string): payload
    """
    node = add_or_select_node(nid)       # make sure node exists
    parent = int(val[8:].strip())
    node.parent = parent
    node.save()
    
    # Push parent update to SSE queue
    try:
        node_data = {
            'nid': nid,
            'parent': parent
        }
        try:
            node_queue.put_nowait(node_data)
        except:
            pass
    except Exception as e:
        applog.debug("Error adding parent to SSE queue: %s", str(e))
        
    applog.debug("on_parent_message( nid:%d parent:%d'", nid,parent)

##----------------------------------------------------------------------------

def on_arc_message( nid,val ):
    """ update arc field for a node
    Args:
        nid (int): MySensors node ID
        val (string): payload like '{P:5460,R:3638,S:60}'
    """
    applog.info("on_arc_message( nid:%d ARC:'%s'", nid,val)

    node = add_or_select_node(nid)       # make sure node exists
    # convert "pseudo-JSON" like '{P:5460,R:3638,S:60}' to real JSON like '{"P":5460","R":3638","S":60}'
    nojs = val
    nojs=nojs.replace('{','{"')
    nojs=nojs.replace(':','":')
    nojs=nojs.replace(',',',"')
    try:
        js = json.loads(nojs)
        success = js["S"]
        node.arc = success
        node.save()
        applog.info("ARC success: %d%%", success)
        
        # Push ARC update to SSE queue
        try:
            node_data = {
                'nid': nid,
                'arc': success
            }
            try:
                node_queue.put_nowait(node_data)
            except:
                pass
        except Exception as e:
            applog.debug("Error adding arc to SSE queue: %s", str(e))
    except:
        applog.warn("error in ARC message: '%s'", val)
        pass
        

##----------------------------------------------------------------------------

def on_value_message( nid,cid,typ,val ):
    """ add a record to 'values' table, for a sensor
    Args:
        nid (int): MySensors node ID
        cid (int): MySensors child ID
        typ (int): MySensors I_xxx type
        val (string): payload
    """
    valname = mysensors.value_names.get(typ,"?")

    node = add_or_select_node(nid)       # make sure node exists
    
    sensor = add_or_select_sensor(nid,cid) # make sure sensor exists
    if typ >= 0:
        sensor.values.set_bit(typ)
    sensor.save()
    
    tvalue = add_or_select_tvalue(nid,cid,typ,val,datetime.now())
    tvalue.save()
    
    # Push value updates to SSE queues
    try:
        # For values.html (Message-based values with C_SET command)
        value_data = {
            'nid': nid,
            'cid': cid,
            'cmd': mysensors.Commands.C_SET,
            'typ': typ,
            'payload': val,
            'received': datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
            'type_name': valname
        }
        try:
            value_queue.put_nowait(value_data)
        except:
            pass
        
        # For types.html (Current values by type)
        tvalue_data = {
            'nid': nid,
            'cid': cid,
            'typ': typ,
            'value': val,
            'received': datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
            'type_name': valname
        }
        try:
            tvalue_queue.put_nowait(tvalue_data)
        except:
            pass
    except Exception as e:
        applog.debug("Error adding value to SSE queues: %s", str(e))
    
    # my convention: message sensor=98, type=47 is a report on parent node
    if (cid==98 and typ==47 and val.startswith('parent:')):
        on_parent_message(nid,val)

    # my convention: message sensor=98, type=28 (V_VAR5) is a report on ARC statistics, 
    if (cid==98 and typ==28):
        on_arc_message(nid,val)

    applog.debug("on_value_message( nid:%d cid:%d typ:%d (%s) = '%s'", nid,cid,typ,valname,val)

##----------------------------------------------------------------------------
        
def on_node_value_message( nid,typ,val ):
    """ add a record to 'values' table, for sensor==255, i.e. node itself
    Args:
        nid (int): MySensors node ID
        typ (int): MySensors I_xxx type
        val (string): payload
    """
    valname = mysensors.value_names.get(typ,"?")
    applog.debug("on_node_value_message( nid:%d typ:%d (%s) = '%s'", nid,typ,valname,val)
    on_value_message( nid, 255, typ, val )

##----------------------------------------------------------------------------

def on_internal_message( nid, cid, typ, val ):
    """handle INTERNAL messages
    Args:
        nid (int): MySensors node ID
        cid (int): MySensors child ID
        typ (int): MySensors I_xxx type
        val (string): payload
    """
    typname = mysensors.internal_names.get(typ,"?")
    applog.debug("on_internal_message( nid:%d cid:%d typ:%d (%s) = '%s'", nid,cid,typ,typname,val)
    node = add_or_select_node(nid)

    #  my/2/stat/123/255/3/0/11 bwWindowSensor
    if (cid==255 and typ==mysensors.Internal.I_SKETCH_NAME):
        node.sk_name = val 
        applog.debug("sk_name='%s'", val)
        node.save()
    #  my/2/stat/123/255/3/0/12 $ Rev: 826 $ 11:34:24
    #  or
    #  my/2/stat/199/255/3/0/12 586
    elif (cid==255 and typ==mysensors.Internal.I_SKETCH_VERSION):
        node.sk_version = val
        applog.debug("sk_version='%s'", val)
        rev = 0
        if val.strip().isdigit():
            rev = int(val.strip())
        else:
            m = re.search(r"\$Rev: (\d+) *\$.*",val)
            if (m):
                rev = int(m.group(1))
        node.sk_revision = rev
        applog.debug("revision=%d", rev)
        node.save()
    elif (cid==255 and typ==mysensors.Internal.I_BATTERY_LEVEL):
        on_node_value_message( nid, int(mysensors.Values.V_PERCENTAGE), val)
        # Push battery update to SSE queue
        try:
            node_data = {
                'nid': nid,
                'bat_level': int(val)
            }
            try:
                node_queue.put_nowait(node_data)
            except:
                pass
        except Exception as e:
            applog.debug("Error adding battery to SSE queue: %s", str(e))
        return
    else:
        return

##----------------------------------------------------------------------------

def on_presentation_message( nid, cid, typ, val ):
    """handle PRESENTATION messages for sensors
    Args:
        nid (int): MySensors node ID
        cid (int): MySensors child ID
        typ (int): MySensors I_xxx type
        val (string): payload
    """
    applog.debug("on_presentation_message( nid:%d cid:%d typ:%d = '%s'", nid,cid,typ,val)
    node = add_or_select_node(nid)
    sensor = add_or_select_sensor(nid,cid)

    #  my/2/stat/123/11/0/0/0 Contact L
    # or
    #  my/2/stat/199/81/0/0/37 Gas flow&vol [ct,l,l/h]
    if (cid!=255):
        sensor.name = val
        sensor.typ = typ
        sensor.save()

##----------------------------------------------------------------------------

def on_node_presentation_message( nid, typ, val ):
    """handle PRESENTATION messages where cid==255
    Args:
        nid (int): MySensors node ID
        typ (int): MySensors S_xxx type
        val (string): payload
    """
    applog.debug("on_node_presentation_message( nid:%d typ:%d = '%s'", nid,typ,val)
    node = add_or_select_node(nid)

    #  my/2/stat/123/255/0/0/17 2.3.1
    if (typ==mysensors.Sensors.S_ARDUINO_NODE or typ==mysensors.Sensors.S_ARDUINO_REPEATER_NODE):
        node.api_ver = val   # update node API version in payload
        node.save() 

##----------------------------------------------------------------------------

last_message = ""
last_time = time.time()
gateway_socket = None
gateway_running = False
ota_manager = None  # OTA Firmware Manager
message_queue = Queue(maxsize=100)  # Queue for SSE message streaming
sensor_queue = Queue(maxsize=100)   # Queue for SSE sensor updates
value_queue = Queue(maxsize=100)    # Queue for SSE value updates (values.html)
tvalue_queue = Queue(maxsize=100)   # Queue for SSE typed value updates (types.html)
node_queue = Queue(maxsize=100)     # Queue for SSE node updates (nodes.html)

def process_gateway_message(line):
    """Process a message from MySensors Gateway
    Args:
        line (str): MySensors message in format: node-id;child-sensor-id;command;ack;type;payload
    """
    # example: 106;61;1;0;23;37
    global last_message, last_time, applog
    try:
        line = line.strip()
        if not line:
            return
            
        now = time.time()
        
        # remove duplicates
        isnew = (last_message != line) or ((now - last_time) > 1)
        last_message = line
        last_time = now
        if not isnew:
            return

        parts = line.split(';')
        if len(parts) < 6:
            applog.warning("Invalid message format: %s", line)
            return

        nid = int(parts[0])
        cid = int(parts[1])
        cmd = int(parts[2])
        ack = int(parts[3])
        typ = int(parts[4])
        val = parts[5] if len(parts) > 5 else ""
        
        applog.debug("message nid:%d cid:%d cmd:%d typ:%d = '%s'", nid, cid, cmd, typ, val)
        add_message(nid, cid, cmd, typ, val)
        
        # Push message to SSE queue for live updates
        try:
            # Node-Objekt für Location holen
            node_obj = None
            try:
                node_obj = Node.get(Node.nid == nid)
            except Exception:
                node_obj = None
            message_data = {
                'nid': nid,
                'cid': cid,
                'cmd': cmd,
                'typ': typ,
                'payload': val,
                'received': datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
                'cmd_name': mysensors.command_names.get(cmd, '?'),
                'type_name': None,
                'location': node_obj.location if node_obj and node_obj.location else None
            }
            # Get type name based on command
            if cmd in [mysensors.Commands.C_REQ, mysensors.Commands.C_SET]:
                message_data['type_name'] = mysensors.value_names.get(typ, '?')
            elif cmd == mysensors.Commands.C_PRESENTATION:
                message_data['type_name'] = mysensors.sensor_names.get(typ, '?')
            elif cmd == mysensors.Commands.C_INTERNAL:
                message_data['type_name'] = mysensors.internal_names.get(typ, '?')
            # Try to add to queue, drop if full
            try:
                message_queue.put_nowait(message_data)
            except:
                pass  # Queue full, skip this update
        except Exception as e:
            applog.debug("Error adding message to SSE queue: %s", str(e))

        # Handle OTA firmware updates (C_STREAM messages)
        if cmd == mysensors.Commands.C_STREAM:
            response = handle_stream_message(nid, cid, typ, val)
            if response:
                send_message_to_gateway(response)
        elif (cmd == mysensors.Commands.C_SET and cid != 255):
            on_value_message(nid, cid, typ, val)
        elif (cmd == mysensors.Commands.C_SET and cid == 255):
            on_node_value_message(nid, typ, val)
        elif (cmd == mysensors.Commands.C_PRESENTATION and cid != 255):
            on_presentation_message(nid, cid, typ, val)
        elif (cmd == mysensors.Commands.C_PRESENTATION and cid == 255):
            on_node_presentation_message(nid, typ, val)
        elif (cmd == mysensors.Commands.C_INTERNAL):
            on_internal_message(nid, cid, typ, val)
            # Check if node needs reboot for OTA
            if typ == mysensors.Internal.I_HEARTBEAT_RESPONSE or typ == mysensors.Internal.I_POST_SLEEP_NOTIFICATION:
                if ota_manager and ota_manager.is_reboot_required(nid):
                    send_reboot_request(nid)
    except Exception as err:
        print("Error: " + str(err))
        sys.exit(1)
        raise

#endregion  
##############################################################################
#region Routes

@app.route('/')
def index():
    return render_template('index.html')

##----------------------------------------------------------------------------

@app.route('/nodes')
def nodes():
    sort = flask.request.args.get('sort', default="nid", type=str)

    query = Node.select(Node,ValueType.value.alias('level')).join(
                ValueType, 
                JOIN.LEFT_OUTER, 
                on=(
                    (Node.nid==ValueType.nid_id) & 
                    (ValueType.cid==255) & 
                    (ValueType.typ==3),
                    ),
                )

    if (sort=="date"):
        query = query.order_by(Node.lastseen.desc())
    else:
        query = query.order_by(Node.nid)
    return object_list('nodes.html', query.objects(), sort=sort )

##----------------------------------------------------------------------------

@app.route('/sensors')
def sensors():
    sort = flask.request.args.get('sort', default="date", type=str)
    cid = flask.request.args.get('cid', default=None, type=int)
    nid = flask.request.args.get('nid', default=None, type=int)

    query = Sensor.select().join(Node)

    # sort as requested
    if sort=="cid": 
        query = query.order_by(Sensor.cid)
    elif sort=="usid":
        query = query.order_by(Sensor.usid)
    else: 
        query = query.order_by(Sensor.lastseen.desc())

    # filter by nid if requested
    if nid is not None:
        if nid >=0:
            query = query.where(Sensor.nid==nid)
        else:
            query = query.where(Sensor.nid!=-nid)
    return object_list( 'sensors.html', query, sort=sort, nid=nid, cid=cid )

##----------------------------------------------------------------------------

@app.route('/tvalues')
def tvalues():
    # get parameters
    sort = flask.request.args.get('sort', default="date", type=str)
    nid = flask.request.args.get('nid', default=None, type=str)
    cid = flask.request.args.get('cid', default=None, type=str)
    usid = flask.request.args.get('usid', default=None, type=str)

    query = ValueType.select().join(Node).switch(ValueType).join(Sensor)

    # sort as requested
    if sort=="cid": 
        query = query.order_by(ValueType.cid)
    elif sort=="date": 
        query = query.order_by(ValueType.received.desc())
    else: 
        query = query.order_by(ValueType.usid)

    # filter if requested
    if usid is not None and len(usid)>0:
        iusid = int(usid)
        query = query.where(ValueType.usid==iusid)
    elif nid is not None and len(nid)>0:
        inid = int(nid)
        if inid >=0:
            query = query.where(ValueType.nid==inid)
        else:
            query = query.where(ValueType.nid!=-inid)
    elif cid is not None and len(cid)>0:
        icid = int(cid)
        if (icid>=0):
            query = query.where(ValueType.cid==icid)
        else:
            query = query.where(ValueType.cid!=-icid)
    return object_list( 'types.html', query, sort=sort, nid=nid, cid=cid, usid=usid )

##----------------------------------------------------------------------------

@app.route('/values')
def values():
    # get parameters
    sort = flask.request.args.get('sort', default="date", type=str)
    nid = flask.request.args.get('nid', default=None, type=str)
    cid = flask.request.args.get('cid', default=None, type=str)
    usid = flask.request.args.get('usid', default=None, type=str)

    # Join Node and Sensor to get location and sensor type
    query = Message.select(Message, Node, Sensor).join(Node).switch(Message).join(
        Sensor,
        JOIN.LEFT_OUTER,
        on=((Message.nid == Sensor.nid) & (Message.cid == Sensor.cid))
    ).where(Message.cmd==mysensors.Commands.C_SET)

    # sort as requested
    if sort=="cid": 
        query = query.order_by(Message.cid)
    elif sort=="date": 
        query = query.order_by(Message.received.desc())
    else: 
        query = query.order_by(Message.nid, Message.cid)
    	    
    # filter if requested
    if usid is not None and len(usid)>0:
        iusid = int(usid)
        inid,icid = split_usid(iusid)
        query = query.where( (Message.nid==inid) & (Message.cid==icid) )
    elif nid is not None and len(nid)>0:
        inid = int(nid)
        if inid >=0:
            query = query.where(Message.nid==inid)
        else:
            query = query.where(Message.nid!=-inid)
    elif cid is not None and len(cid)>0:
        icid = int(cid)
        if (icid>=0):
            query = query.where(Message.cid==icid)
        else:
            query = query.where(Message.cid!=-icid)
    return object_list( 'values.html', query, sort=sort, nid=nid, cid=cid, usid=usid )

##----------------------------------------------------------------------------

@app.route('/messages')
def messages():
    # get parameters
    sort = flask.request.args.get('sort', default="date", type=str)
    cid = flask.request.args.get('cid', default=None, type=str)
    nid = flask.request.args.get('nid', default=None, type=str)
    usid = flask.request.args.get('usid', default=None, type=str)

    # sort as requested
    if sort=='nid':
        query = Message.select(Message, Node).join(Node).order_by(Message.nid)
    elif sort=="cid": 
        query = Message.select(Message, Node).join(Node).order_by(Message.cid)
    elif sort=="cmd":
        query = Message.select(Message, Node).join(Node).order_by(Message.cmd)
    elif sort=='typ':
        query = Message.select(Message, Node).join(Node).order_by(Message.typ)
    else: 
        query = Message.select(Message, Node).join(Node).order_by(Message.received.desc())

    # filter if requested
    if usid is not None and len(usid)>0:
        iusid = int(usid)
        query = query.where(Message.usid==iusid)
    elif nid is not None and len(nid)>0:
        inid = int(nid)
        if inid >=0:
            query = query.where(Message.nid==inid)
        else:
            query = query.where(Message.nid!=-inid)
    elif cid is not None and len(cid)>0:
        icid = int(cid)
        if (icid>=0):
            query = query.where(Message.cid==icid)
        else:
            query = query.where(Message.cid!=-icid)

    return object_list( 'messages.html', query, sort=sort, nid=nid, cid=cid, usid=usid )

##----------------------------------------------------------------------------

@app.route('/newbattery', methods=['GET','POST'])
def battery_today():
    if request.method=='POST':
        print("POST: ")
        print( request.form )
        if 'today' in request.form:
            nid = request.form['today']
            print("Node {0} battery changed today".format(nid))
            new_battery(int(nid))
    elif request.method=='GET':
        print("GET: ")
        print (request )
    return redirect(url_for('batteries'))

##----------------------------------------------------------------------------

@app.route('/ota')
def ota_index():
    """Display OTA firmware management page."""
    # Get firmware list from database with additional info
    firmware_list = []
    for fw in Firmware.select().order_by(Firmware.uploaded.desc()):
        firmware_list.append((fw.fw_type, fw.fw_ver, fw.blocks, fw.crc, fw.filename, fw.uploaded))
    
    nodes = Node.select().order_by(Node.nid)
    
    # Get OTA status for each node
    node_status = {}
    if ota_manager:
        for node in nodes:
            status = ota_manager.get_node_status(node.nid)
            if status:
                fw_id = (ota_manager.requested_nodes.get(node.nid) or 
                        ota_manager.unstarted_nodes.get(node.nid) or 
                        ota_manager.started_nodes.get(node.nid))
                node_status[node.nid] = {'status': status, 'firmware': fw_id}
    
    return render_template('ota.html', 
                          firmware_list=firmware_list, 
                          nodes=nodes,
                          node_status=node_status)


@app.route('/ota/upload', methods=['POST'])
def ota_upload():
    """Upload and register a new firmware."""
    global ota_manager
    
    if not ota_manager:
        flask.flash("OTA Manager not initialized", "error")
        return redirect('/ota')
    
    try:
        fw_type = int(request.form.get('fw_type', 0))
        fw_ver = int(request.form.get('fw_ver', 0))
        fw_file = request.files.get('fw_file')
        
        if not fw_file or fw_file.filename == '':
            flask.flash("No firmware file selected", "error")
            return redirect('/ota')
        
        # Save temporarily and read hex content
        import tempfile
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.hex', delete=False) as tmp:
            fw_file.save(tmp.name)
            tmp_path = tmp.name
        
        # Read hex file content for database storage
        with open(tmp_path, 'r') as f:
            hex_content = f.read()
        
        # Load firmware into OTA manager
        if ota_manager.load_firmware(fw_type, fw_ver, tmp_path):
            # Get firmware info from OTA manager
            fw_list = ota_manager.get_firmware_list()
            fw_info = next((f for f in fw_list if f[0] == fw_type and f[1] == fw_ver), None)
            
            if fw_info:
                blocks, crc = fw_info[2], fw_info[3]
                
                # Save to database (update or insert)
                try:
                    existing = Firmware.get((Firmware.fw_type == fw_type) & (Firmware.fw_ver == fw_ver))
                    existing.blocks = blocks
                    existing.crc = crc
                    existing.filename = fw_file.filename
                    existing.hex_data = hex_content
                    existing.uploaded = datetime.now()
                    existing.save()
                    applog.info("Updated firmware in DB: type %d version %d", fw_type, fw_ver)
                except Firmware.DoesNotExist:
                    Firmware.create(
                        fw_type=fw_type,
                        fw_ver=fw_ver,
                        blocks=blocks,
                        crc=crc,
                        filename=fw_file.filename,
                        hex_data=hex_content
                    )
                    applog.info("Saved new firmware to DB: type %d version %d", fw_type, fw_ver)
                
                flask.flash(f"Firmware type {fw_type} version {fw_ver} loaded and saved successfully", "success")
            else:
                flask.flash("Firmware loaded but info not available", "warning")
        else:
            flask.flash("Failed to load firmware", "error")
        
        # Cleanup
        os.unlink(tmp_path)
        
    except Exception as e:
        flask.flash(f"Error uploading firmware: {str(e)}", "error")
    
    return redirect('/ota')


@app.route('/ota/delete/<int:fw_type>/<int:fw_ver>', methods=['POST'])
def ota_delete(fw_type, fw_ver):
    """Delete a firmware from the database and OTA manager."""
    try:
        # Remove from OTA manager
        if ota_manager:
            ota_manager.delete_firmware(fw_type, fw_ver)
        
        # Remove from database
        fw = Firmware.get((Firmware.fw_type == fw_type) & (Firmware.fw_ver == fw_ver))
        fw.delete_instance()
        
        flash(f'Firmware {fw_type}/{fw_ver} wurde gelöscht', 'success')
    except Firmware.DoesNotExist:
        flash(f'Firmware {fw_type}/{fw_ver} nicht gefunden', 'error')
    except Exception as e:
        flash(f'Fehler beim Löschen der Firmware: {str(e)}', 'error')
    
    return redirect('/ota')


@app.route('/stats')
def stats():
    """Display database statistics and cleanup information."""
    from datetime import datetime, timedelta
    
    stats = {}
    
    try:
        # Database file information
        db_path = os.path.join(DB_DIR, DATABASE_FILE)
        if os.path.exists(db_path):
            stats['db_size'] = os.path.getsize(db_path)
            stats['db_size_mb'] = stats['db_size'] / (1024 * 1024)
        else:
            stats['db_size'] = 0
            stats['db_size_mb'] = 0
        
        # Record counts
        stats['message_count'] = Message.select().count()
        stats['value_count'] = ValueType.select().count()
        stats['node_count'] = Node.select().count()
        stats['sensor_count'] = Sensor.select().count()
        stats['firmware_count'] = Firmware.select().count()
        
        # Oldest and newest records
        oldest_message = Message.select().order_by(Message.received.asc()).first()
        newest_message = Message.select().order_by(Message.received.desc()).first()
        stats['oldest_message'] = oldest_message.received if oldest_message else None
        stats['newest_message'] = newest_message.received if newest_message else None
        
        oldest_value = ValueType.select().order_by(ValueType.received.asc()).first()
        newest_value = ValueType.select().order_by(ValueType.received.desc()).first()
        stats['oldest_value'] = oldest_value.received if oldest_value else None
        stats['newest_value'] = newest_value.received if newest_value else None
        
        # Calculate what would be deleted
        message_cutoff = datetime.now() - timedelta(days=MESSAGE_RETENTION_DAYS)
        value_cutoff = datetime.now() - timedelta(days=VALUE_RETENTION_DAYS)
        
        stats['messages_to_delete'] = Message.select().where(Message.received < message_cutoff).count()
        stats['values_to_delete'] = ValueType.select().where(ValueType.received < value_cutoff).count()
        
        # Retention policy settings
        stats['message_retention_days'] = MESSAGE_RETENTION_DAYS
        stats['value_retention_days'] = VALUE_RETENTION_DAYS
        stats['cleanup_hour'] = CLEANUP_HOUR
        
    except Exception as e:
        applog.error(f"Error gathering statistics: {e}")
        flask.flash(f"Error: {str(e)}", "error")
    
    return render_template('stats.html', stats=stats)


@app.route('/stats/cleanup', methods=['POST'])
def stats_cleanup():
    """Manually trigger database cleanup."""
    try:
        result = cleanup_old_data()
        flask.flash(f"Cleanup completed: {result['messages_deleted']} messages and {result['values_deleted']} values deleted", "success")
    except Exception as e:
        flask.flash(f"Cleanup failed: {str(e)}", "error")
    
    return redirect(url_for('stats'))


@app.route('/nodes/discover', methods=['POST'])
def nodes_discover():
    """Send I_PRESENTATION request to all nodes to refresh presentation data."""
    try:
        # Broadcast I_PRESENTATION request to all nodes (node 255)
        # Format: node_id;child_id;command;ack;type;payload
        # Command: C_INTERNAL (3), Type: I_PRESENTATION (19)
        message = f"255;255;{mysensors.Commands.C_INTERNAL};0;{mysensors.Internal.I_PRESENTATION};"
        send_message_to_gateway(message)
        applog.info("Sent I_PRESENTATION request to all nodes")
        flask.flash("Discovery request sent to all nodes. They should re-send their presentation data.", "success")
    except Exception as e:
        applog.error(f"Error sending discovery request: {e}")
        flask.flash(f"Failed to send discovery request: {str(e)}", "error")
    
    return redirect(request.referrer or url_for('nodes'))


@app.route('/nodes/discover-request', methods=['POST'])
def nodes_discover_request():
    """Send I_DISCOVER_REQUEST to all nodes to trigger discovery protocol."""
    try:
        # Broadcast I_DISCOVER_REQUEST to all nodes (node 255)
        # Format: node_id;child_id;command;ack;type;payload
        # Command: C_INTERNAL (3), Type: I_DISCOVER_REQUEST (21)
        message = f"255;255;{mysensors.Commands.C_INTERNAL};0;{mysensors.Internal.I_DISCOVER_REQUEST};"
        send_message_to_gateway(message)
        applog.info("Sent I_DISCOVER_REQUEST to all nodes")
        flask.flash("I_DISCOVER_REQUEST sent to all nodes. They should respond with their node information.", "success")
    except Exception as e:
        applog.error(f"Error sending discover request: {e}")
        flask.flash(f"Failed to send discover request: {str(e)}", "error")
    
    return redirect(request.referrer or url_for('nodes'))


@app.route('/nodes/<int:nid>/presentation', methods=['POST'])
def node_presentation(nid):
    """Send I_PRESENTATION request to a specific node."""
    try:
        # Send I_PRESENTATION request to specific node
        # Format: node_id;child_id;command;ack;type;payload
        # Command: C_INTERNAL (3), Type: I_PRESENTATION (19)
        message = f"{nid};255;{mysensors.Commands.C_INTERNAL};0;{mysensors.Internal.I_PRESENTATION};"
        send_message_to_gateway(message)
        applog.info(f"Sent I_PRESENTATION request to node {nid}")
        flask.flash(f"Presentation request sent to node {nid}.", "success")
    except Exception as e:
        applog.error(f"Error sending presentation request to node {nid}: {e}")
        flask.flash(f"Failed to send presentation request: {str(e)}", "error")
    
    return redirect(request.referrer or url_for('nodes'))


@app.route('/nodes/<int:nid>/discover', methods=['POST'])
def node_discover(nid):
    """Send I_DISCOVER_REQUEST to a specific node."""
    try:
        # Send I_DISCOVER_REQUEST to specific node
        # Format: node_id;child_id;command;ack;type;payload
        # Command: C_INTERNAL (3), Type: I_DISCOVER_REQUEST (21)
        message = f"{nid};255;{mysensors.Commands.C_INTERNAL};0;{mysensors.Internal.I_DISCOVER_REQUEST};"
        send_message_to_gateway(message)
        applog.info(f"Sent I_DISCOVER_REQUEST to node {nid}")
        flask.flash(f"Discovery request sent to node {nid}.", "success")
    except Exception as e:
        applog.error(f"Error sending discover request to node {nid}: {e}")
        flask.flash(f"Failed to send discover request: {str(e)}", "error")
    
    return redirect(request.referrer or url_for('nodes'))


@app.route('/ota/update/<int:nid>', methods=['POST'])
def ota_update_node(nid):
    """Request firmware update for a specific node."""
    global ota_manager
    
    if not ota_manager:
        flask.flash("OTA Manager not initialized", "error")
        return redirect('/ota')
    
    try:
        fw_type = int(request.form.get('fw_type'))
        fw_ver = int(request.form.get('fw_ver'))
        
        if ota_manager.request_update(nid, fw_type, fw_ver):
            flask.flash(f"Node {nid} scheduled for firmware type {fw_type} version {fw_ver}", "success")
        else:
            flask.flash(f"Failed to schedule node {nid} for update", "error")
            
    except Exception as e:
        flask.flash(f"Error scheduling update: {str(e)}", "error")
    
    return redirect('/ota')


@app.route('/nodes/<int:nid>/reboot', methods=['POST'])
def reboot_node(nid):
    """Send reboot command to a node."""
    global gateway_socket
    try:
        if gateway_socket is None:
            flask.flash(f"Cannot reboot node {nid}: Gateway not connected!", "error")
            applog.error("Cannot reboot node %d: Gateway socket is None", nid)
            return redirect(url_for('nodes'))
        
        send_reboot_request(nid)
        flask.flash(f"Reboot command sent to node {nid}. Check logs for details.", "success")
    except Exception as e:
        applog.error(f"Error sending reboot to node {nid}: {e}")
        flask.flash(f"Error sending reboot to node {nid}: {str(e)}", "error")
    
    return redirect(url_for('nodes'))


@app.route('/api/stream/messages')
def stream_messages():
    """Server-Sent Events stream for live message updates."""
    def generate():
        """Generator function for SSE."""
        # Send initial comment to keep connection alive
        yield 'retry: 5000\n\n'
        
        while True:
            try:
                # Wait for new message with timeout
                message_data = message_queue.get(timeout=30)
                
                # Format as SSE event
                data_json = json.dumps(message_data)
                yield f'data: {data_json}\n\n'
                
            except Empty:
                # Send keepalive comment every 30 seconds
                yield ': keepalive\n\n'
            except GeneratorExit:
                # Client disconnected
                break
            except Exception as e:
                applog.error(f"Error in SSE stream: {e}")
                break
    
    return Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no'  # Disable nginx buffering
    })

##----------------------------------------------------------------------------

@app.route('/api/stream/sensors')
def stream_sensors():
    """Server-Sent Events stream for live sensor updates."""
    def generate():
        yield 'retry: 5000\n\n'
        
        while True:
            try:
                sensor_data = sensor_queue.get(timeout=30)
                data_json = json.dumps(sensor_data)
                yield f'data: {data_json}\n\n'
            except Empty:
                yield ': keepalive\n\n'
            except GeneratorExit:
                break
            except Exception as e:
                applog.error(f"Error in sensor SSE stream: {e}")
                break
    
    return Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no'
    })

##----------------------------------------------------------------------------

@app.route('/api/stream/values')
def stream_values():
    """Server-Sent Events stream for live value updates (values.html)."""
    def generate():
        yield 'retry: 5000\n\n'
        
        while True:
            try:
                value_data = value_queue.get(timeout=30)
                data_json = json.dumps(value_data)
                yield f'data: {data_json}\n\n'
            except Empty:
                yield ': keepalive\n\n'
            except GeneratorExit:
                break
            except Exception as e:
                applog.error(f"Error in value SSE stream: {e}")
                break
    
    return Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no'
    })

##----------------------------------------------------------------------------

@app.route('/api/stream/types')
def stream_types():
    """Server-Sent Events stream for live typed value updates (types.html)."""
    def generate():
        yield 'retry: 5000\n\n'
        
        while True:
            try:
                tvalue_data = tvalue_queue.get(timeout=30)
                data_json = json.dumps(tvalue_data)
                yield f'data: {data_json}\n\n'
            except Empty:
                yield ': keepalive\n\n'
            except GeneratorExit:
                break
            except Exception as e:
                applog.error(f"Error in tvalue SSE stream: {e}")
                break
    
    return Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no'
    })

##----------------------------------------------------------------------------

@app.route('/api/stream/nodes')
def stream_nodes():
    """Server-Sent Events stream for live node updates (nodes.html)."""
    def generate():
        yield 'retry: 5000\n\n'
        
        while True:
            try:
                node_data = node_queue.get(timeout=30)
                data_json = json.dumps(node_data)
                yield f'data: {data_json}\n\n'
            except Empty:
                yield ': keepalive\n\n'
            except GeneratorExit:
                break
            except Exception as e:
                applog.error(f"Error in node SSE stream: {e}")
                break
    
    return Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no'
    })

##----------------------------------------------------------------------------

@app.route('/messages/send', methods=['POST'])
def send_custom_message():
    """Send a custom message to the MySensors Gateway."""
    global gateway_socket
    try:
        if gateway_socket is None:
            flask.flash("Cannot send message: Gateway not connected!", "error")
            applog.error("Cannot send custom message: Gateway socket is None")
            return redirect(url_for('messages'))
        
        # Get form data
        node_id = int(request.form.get('node_id', 255))
        child_id = int(request.form.get('child_id', 255))
        command = int(request.form.get('command', 3))
        ack = int(request.form.get('ack', 0))
        msg_type = int(request.form.get('msg_type', 13))
        payload = request.form.get('payload', '').strip()
        
        # Validate ranges
        if not (0 <= node_id <= 255):
            flask.flash("Node ID must be between 0 and 255", "error")
            return redirect(url_for('messages'))
        if not (0 <= child_id <= 255):
            flask.flash("Child ID must be between 0 and 255", "error")
            return redirect(url_for('messages'))
        if not (0 <= command <= 4):
            flask.flash("Command must be between 0 and 4", "error")
            return redirect(url_for('messages'))
        if not (0 <= ack <= 1):
            flask.flash("ACK must be 0 or 1", "error")
            return redirect(url_for('messages'))
        if not (0 <= msg_type <= 255):
            flask.flash("Type must be between 0 and 255", "error")
            return redirect(url_for('messages'))
        
        # Format message
        message = f"{node_id};{child_id};{command};{ack};{msg_type};{payload}"
        
        # Send to gateway
        send_message_to_gateway(message)
        
        flask.flash(f"Message sent: {message}", "success")
        applog.info("Custom message sent via web UI: %s", message)
        
    except ValueError as e:
        flask.flash(f"Invalid input: {str(e)}", "error")
    except Exception as e:
        applog.error(f"Error sending custom message: {e}")
        flask.flash(f"Error sending message: {str(e)}", "error")
    
    return redirect(url_for('messages'))


#endregion
##############################################################################
#region Jinja helpers

@app.context_processor
def my_processor():

    def command_string(cmd):
        """look up C_symbolic name for command
        Args:
            cmd (int): MySensors command, see API doc
        Returns:
            string: symbolic name like C_PRESENTATION
        """
        if cmd is None: return None
        return mysensors.command_names.get(cmd)

    def sensor_string(typ):
        """look up S_xxx symbolic name for sensor type <typ>
        Args:
            typ (int): MySensors sensor type, see API doc
        Returns:
            string: symbolic name like S_DOOR
        """
        if typ is None: return None
        return mysensors.sensor_names.get(typ)

    def type_string(cmd,typ):
        """look up symbolic name for type (sensor or value, depending on command)
        Args:
            cmd (int): MySensors command
            typ (int): MySensors type
        Returns:
            string: symbolic name like S_DOOR or V_STATUS
        """
        if (cmd is None) or (typ is None): return None
        if (cmd==mysensors.Commands.C_REQ) or (cmd==mysensors.Commands.C_SET):
            return mysensors.value_names.get(typ)
        elif (cmd==mysensors.Commands.C_PRESENTATION):
            return mysensors.sensor_names.get(typ)
        elif (cmd==mysensors.Commands.C_INTERNAL):
            return mysensors.internal_names.get(typ)
        else:
            return None

    def value_string(typ):
        """look up V_xxx symbolic name for value type
        Args:
            typ (int): MySensors value type, see API doc
        Returns:
            string: symbolic name like V_STATUS
        """
        if typ is None: return None
        return mysensors.value_names.get(typ)

    def values_string(values: BigBitField):
        """return a list of symbolic names of values types sent by this sensor
        Args:
            values (BigBitField): bit 0 set if type 0 found, etc
        Returns:
            string: comma-separated list of symbolic names
        """
        vnames = []
        for i in range(64):
            if values.is_set(i):
                vname = mysensors.value_names.get(i)
                if vname is not None:
                    vnames.append(vname)
        return ", ".join(vnames)
    
    def days_ago(dt: datetime):
        """calculate how many days ago a date was
        Args:
            dt (datetime): datestamp
        Returns:
            int: number of days in the past
        """
        if dt is not None:
            return math.floor((dt.now()-dt).total_seconds()/(60*60*24))
        else:
            return None

    def months_ago(dt: datetime):
        """calculate how many months ago a date was
        Args:
            dt (datetime): datestamp
        Returns:
            int: number of months in the past
        """
        if dt is not None:
            return round( (datetime.today().date() - dt).total_seconds() / (60*60*24*30) )
        else:
            return None

    def get_sensor_type(nid, cid):
        """Get sensor type for a given node and child id
        Args:
            nid (int): Node ID
            cid (int): Child ID
        Returns:
            int or None: Sensor type
        """
        try:
            sensor = Sensor.get((Sensor.nid == nid) & (Sensor.cid == cid))
            return sensor.typ
        except Sensor.DoesNotExist:
            return None

    return dict( 
        command_string=command_string,
        sensor_string=sensor_string,
        type_string=type_string,
        value_string=value_string,
        values_string=values_string,
        days_ago=days_ago,
        months_ago=months_ago,
        get_sensor_type=get_sensor_type,
        )

#endregion
##############################################################################
#region Forms

class ConfirmDeleteNodeForm(wtf.Form):
    f_nid = wtf.IntegerField("Node ID:", render_kw={"class":"edit edit-node"})

    @app.route("/nodes/<int:nid>/delete", methods=['GET','POST'])
    def confirm_delete_node(nid):
        form = ConfirmDeleteNodeForm(request.form)
        # if POST, then use data from form
        if (request.method=='POST'):
            print ("Delete node {0}".format(request.form['f_nid']))
            delete_node(nid)
            return redirect(url_for('nodes'))
        # else if GET, then display form
        form.f_nid.data = nid
        return render_template('confirm_delete_node.html', form=form )

##----------------------------------------------------------------------------

class ConfirmDeleteSensorForm(wtf.Form):
    f_nid = wtf.IntegerField("Node ID:", render_kw={"class":"edit edit-node"})
    f_cid = wtf.IntegerField("Sensor ID:", render_kw={"class":"edit edit-node"})

    @app.route("/sensors/<int:usid>/delete", methods=['GET','POST'])
    def confirm_delete_sensor(usid):
        nid,cid = split_usid(usid)
        form = ConfirmDeleteSensorForm(request.form)
        # if POST, then use data from form
        if (request.method=='POST'):
            print ("Delete node {0} sensor {1}".format( request.form['f_nid'], request.form['f_cid'] ))
            delete_sensor(nid,cid)
            return redirect(url_for('sensors'))
        # else if GET, then display form
        form.f_nid.data = nid
        form.f_cid.data = cid
        return render_template('confirm_delete_sensor.html', form=form )

##----------------------------------------------------------------------------

class ConfirmDeleteNodeRequestsForm(wtf.Form):
    f_nid = wtf.IntegerField("Node ID:", render_kw={"class":"edit edit-node"})

    @app.route("/nodes/<int:nid>/delete-requests", methods=['GET','POST'])
    def confirm_delete_node_requests(nid):
        form = ConfirmDeleteNodeRequestsForm(request.form)
        # if POST, then use data from form
        if (request.method=='POST'):
            print ("Delete node {0} requests".format(request.form['f_nid']))
            delete_node_requests(nid)
            return redirect(url_for('nodes'))
        # else if GET, then display form
        form.f_nid.data = nid
        return render_template('confirm_delete_node_req.html', form=form )

##----------------------------------------------------------------------------

class ConfirmDeleteOldForm(wtf.Form):
    f_ndays = wtf.IntegerField("", render_kw={"class":"edit edit-node"})

    @app.route("/messages/delete/<int:ndays>", methods=['GET','POST'])
    def confirm_delete_old(ndays):
        ndays = 365
        form = ConfirmDeleteOldForm(request.form)
        # if POST, then use data from form
        if (request.method=='POST'):
            ndays = int(request.form['f_ndays'])
            print ("Delete records older than {0} days".format(ndays))
            delete_old_stuff(ndays)
            return redirect(url_for('nodes'))
        # else if GET, then display form
        form.f_ndays.data = ndays
        return render_template('confirm_delete_old.html', form=form )

##----------------------------------------------------------------------------

class ConfirmNewBatteryForm(wtf.Form):
    f_nid = wtf.IntegerField("Node ID:", render_kw={"class":"edit edit-node"})
    f_bat = wtf.DateField("Date:", render_kw={"class":"edit edit-date"})

    @app.route("/nodes/<int:nid>/battery", methods=['GET','POST'])
    def confirm_new_battery(nid):
        form = ConfirmNewBatteryForm(request.form)
        # if POST, then use data from form
        if (request.method=='POST'):
            fnid = request.form['f_nid']
            fbat = request.form['f_bat']
            print ("New battery in node {0} at {1}".format(fnid, fbat))
            new_battery(fnid,fbat)
            return redirect(url_for('nodes'))
        # else if GET, then display form
        form.f_nid.data = nid
        form.f_bat.data = datetime.today()
        return render_template('confirm_new_battery.html', form=form )

##----------------------------------------------------------------------------

class LocationForm(wtf.Form):
    nid = wtf.IntegerField("Node:", render_kw={"class":"td-id edit ro", "tabindex":-1 })
    sketch = wtf.StringField("Sketch:", render_kw={"class":"edit ro", "tabindex":-1 })
    location = wtf.StringField("Location:", render_kw={"class":"edit", })

class LocationsForm(wtf.Form):
    locs = wtf.FieldList(wtf.FormField(LocationForm))

    @app.route('/locations', methods=['GET','POST'])
    def locations():
        form = LocationsForm(request.form)
        # if POST, then use data from form
        if (request.method=='POST'):
            for lf in form.locs.entries:
                try:
                    node = Node.get(Node.nid==lf.nid.data)
                    if node.location != lf.location.data:
                        applog.debug("update %d location to '%s'",lf.nid.data, lf.location.data)
                        node.location = lf.location.data
                        node.save()
                    elif lf.location.data is None or len(lf.location.data)==0:
                        node.location = None
                        applog.debug("update %d location to None",lf.nid.data)
                        node.save()
                except DoesNotExist:
                    print("Error: " + str(err))
                    sys.exit(1)
                    raise
            return redirect(url_for('nodes'))
        # else if GET, then display form
        nodes = Node.select().order_by(Node.nid)
        for node in nodes:
            lf = LocationForm()
            lf.nid = node.nid
            lf.sketch = node.sk_name
            lf.location = node.location
            form.locs.append_entry(lf)
        return render_template('locations.html', form=form )

##----------------------------------------------------------------------------

class BatteryForm(wtf.Form):
    nid = wtf.IntegerField("Node:", render_kw={"class":"td-id edit ro", "tabindex":-1 })
    sketch = wtf.StringField("Sketch:", render_kw={"class":"edit ro", "tabindex":-1 })
    location = wtf.StringField("Location:", render_kw={"class":"edit ro", "tabindex":-1 })
    bat_changed = wtf.DateField("Date:", render_kw={"class":"edit edit-date"})

class BatteriesForm(wtf.Form):
    bats = wtf.FieldList(wtf.FormField(BatteryForm))

    @app.route('/batteries', methods=['GET','POST'])
    def batteries():
        form = BatteriesForm(request.form)
        # if POST, then use data from form
        if (request.method=='POST'):
            for lf in form.bats.entries:
                try:
                    node = Node.get(Node.nid==lf.nid.data)
                    if node.bat_changed != lf.bat_changed.data:
                        applog.debug("update %d battery date to '%s'",lf.nid.data, lf.bat_changed.data)
                        node.bat_changed = lf.bat_changed.data
                        node.save()
                    elif lf.bat_changed.data is None:
                        node.bat_changed = None
                        applog.debug("update %d battery date to None",lf.nid.data)
                        node.save()
                except DoesNotExist:
                    print("Error: " + str(err))
                    sys.exit(1)
                    raise
            return redirect(url_for('nodes'))
        # else if GET, then display form
        nodes = Node.select().order_by(Node.nid)
        for node in nodes:
            lf = BatteryForm()
            lf.nid = node.nid
            lf.sketch = node.sk_name
            lf.location = node.location
            lf.bat_changed = node.bat_changed
            form.bats.append_entry(lf)
        return render_template('batteries.html', form=form )

#endregion
#############################################################################
#region OTA Firmware Functions

def send_message_to_gateway(message):
    """Send a message to the MySensors Gateway.
    
    Args:
        message: Message string in MySensors format (node;child;cmd;ack;type;payload)
    """
    global gateway_socket, applog
    try:
        if gateway_socket:
            msg = message + "\n"
            gateway_socket.sendall(msg.encode('utf-8'))
            applog.info("Sent to gateway: %s", message)  # Changed to INFO to always see it
        else:
            applog.warning("Cannot send message, gateway not connected")
    except Exception as e:
        applog.error("Error sending message to gateway: %s", str(e))


def send_reboot_request(node_id, request_ack=False):
    """Send reboot request to a node for OTA update.
    
    Args:
        node_id: Node ID to reboot
        request_ack: Whether to request acknowledgement (default: False)
    """
    # Format: node_id;child_id;command;ack;type;payload
    # Command: C_INTERNAL (3), Type: I_REBOOT (13)
    ack = 1 if request_ack else 0
    message = f"{node_id};255;{mysensors.Commands.C_INTERNAL};{ack};{mysensors.Internal.I_REBOOT};"
    applog.info("Preparing reboot request for node %d (ack=%d): '%s'", node_id, ack, message)
    send_message_to_gateway(message)
    applog.info("Sent reboot request to node %d for firmware update", node_id)


def handle_stream_message(node_id, child_id, stream_type, payload):
    """Handle C_STREAM messages for OTA firmware updates.
    
    Args:
        node_id: Node ID
        child_id: Child sensor ID (should be 255 for firmware)
        stream_type: Stream type (ST_FIRMWARE_CONFIG_REQUEST or ST_FIRMWARE_REQUEST)
        payload: Message payload
        
    Returns:
        str or None: Response message or None
    """
    global ota_manager, applog
    
    if not ota_manager:
        return None
        
    try:
        if stream_type == mysensors.Stream.ST_FIRMWARE_CONFIG_REQUEST:
            # Node is requesting firmware config
            response_payload = ota_manager.handle_firmware_config_request(node_id, payload)
            if response_payload:
                # Format response: node;255;C_STREAM;0;ST_FIRMWARE_CONFIG_RESPONSE;payload
                return f"{node_id};255;{mysensors.Commands.C_STREAM};0;{mysensors.Stream.ST_FIRMWARE_CONFIG_RESPONSE};{response_payload}"
                
        elif stream_type == mysensors.Stream.ST_FIRMWARE_REQUEST:
            # Node is requesting a firmware block
            response_payload = ota_manager.handle_firmware_request(node_id, payload)
            if response_payload:
                # Format response: node;255;C_STREAM;0;ST_FIRMWARE_RESPONSE;payload
                return f"{node_id};255;{mysensors.Commands.C_STREAM};0;{mysensors.Stream.ST_FIRMWARE_RESPONSE};{response_payload}"
    except Exception as e:
        applog.error("Error handling stream message from node %d: %s", node_id, str(e))
    
    return None

#endregion
#############################################################################

def gateway_listener():
    """Thread function to listen to MySensors Gateway"""
    global gateway_socket, gateway_running, applog
    
    buffer = ""
    
    while gateway_running:
        try:
            if gateway_socket is None:
                applog.info("Connecting to MySensors Gateway at %s:%d", GATEWAY_HOST, GATEWAY_PORT)
                gateway_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                gateway_socket.settimeout(5.0)
                gateway_socket.connect((GATEWAY_HOST, GATEWAY_PORT))
                applog.info("Connected to MySensors Gateway")
            
            # Read data from gateway
            data = gateway_socket.recv(1024)
            if not data:
                applog.warning("Gateway connection closed")
                gateway_socket.close()
                gateway_socket = None
                time.sleep(5)  # Wait before reconnecting
                continue
            
            # Process received data line by line
            buffer += data.decode('utf-8', errors='ignore')
            while '\n' in buffer:
                line, buffer = buffer.split('\n', 1)
                process_gateway_message(line)
                
        except socket.timeout:
            # Normal timeout, continue listening
            continue
        except Exception as e:
            applog.error("Gateway connection error: %s", str(e))
            if gateway_socket:
                try:
                    gateway_socket.close()
                except:
                    pass
                gateway_socket = None
            time.sleep(5)  # Wait before reconnecting
    
    # Cleanup on exit
    if gateway_socket:
        try:
            gateway_socket.close()
        except:
            pass


def main():
    db.init(os.path.join(DB_DIR, DATABASE_FILE))
    db.connect()
    tables = [Node,Sensor,ValueType,Message,Firmware]
    db.create_tables(tables)
    applog.info("opened database")

    introspector = Introspector.from_database(db)
    models = introspector.generate_models()
    if ('node' in models):
        print("Table 'node' exists")
        dbnode = models['node']

        hasp = hasattr(dbnode,'parent')
        if (hasp):
            print(" and it has a 'parent' field")
        else:
            print(" and it does NOT have a 'parent' field")
            migrator = SqliteMigrator(db)
            parent = IntegerField(null=True, help_text="parent node Id")
            migrate( migrator.add_column('node', 'parent', parent), )
            applog.info("Migration: add field 'parent'")

        hasArc = hasattr(dbnode,'arc')
        if (hasArc):
            print(" and it has a 'arc' field")
        else:
            print(" and it does NOT have a 'arc' field")
            migrator = SqliteMigrator(db)
            arc = IntegerField(null=True, help_text="ARC success rate [%]")
            migrate( migrator.add_column('node', 'arc', arc), )
            applog.info("Migration: add field 'arc'")

    if ValueType.select().count()==0:
        fill_tvalues()

    # Initialize OTA Firmware Manager
    global ota_manager
    ota_manager = ota_firmware.OTAFirmwareManager()
    applog.info("OTA Firmware Manager initialized")
    
    # Load existing firmware from database
    for fw in Firmware.select():
        try:
            # Write hex data to temporary file
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.hex', delete=False) as tmp:
                tmp.write(fw.hex_data)
                tmp_path = tmp.name
            
            # Load into OTA manager
            ota_manager.load_firmware(fw.fw_type, fw.fw_ver, tmp_path)
            os.unlink(tmp_path)
            applog.info("Loaded firmware from DB: type %d version %d", fw.fw_type, fw.fw_ver)
        except Exception as e:
            applog.error("Error loading firmware type %d version %d: %s", fw.fw_type, fw.fw_ver, str(e))

    # Start MySensors Gateway listener thread
    global gateway_running
    gateway_running = True
    gateway_thread = threading.Thread(target=gateway_listener, daemon=True)
    gateway_thread.start()
    applog.info("Listening to MySensors Gateway at %s:%d", GATEWAY_HOST, GATEWAY_PORT)

    # Start cleanup scheduler
    def run_scheduler():
        while gateway_running:
            schedule.run_pending()
            time.sleep(60)  # Check every minute
    
    # Schedule daily cleanup at configured hour
    schedule.every().day.at(f"{CLEANUP_HOUR:02d}:00").do(cleanup_old_data)
    applog.info(f"Scheduled daily cleanup at {CLEANUP_HOUR:02d}:00 (retention: messages={MESSAGE_RETENTION_DAYS}d, values={VALUE_RETENTION_DAYS}d)")
    
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()

    try:
        app.run(debug=True, use_reloader=False, host='0.0.0.0', port=WEB_PORT)
    finally:
        gateway_running = False
        if gateway_socket:
            gateway_socket.close()

if __name__ == '__main__':
    main()
