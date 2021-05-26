#     Copyright 2018 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
#     Licensed under the Apache License, Version 2.0 (the "License").
#     You may not use this file except in compliance with the License.
#     A copy of the License is located at
#
#         https://aws.amazon.com/apache-2-0/
#
#     or in the "license" file accompanying this file. This file is distributed
#     on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
#     express or implied. See the License for the specific language governing
#     permissions and limitations under the License.

import requests
from datetime import datetime, timedelta
import getopt, sys
import urllib3
import boto3
import json
import subprocess, sys, re
from io import StringIO
import pandas as pd

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Usage
usageInfo = """Usage:
This scripts checks if a notebook is idle for X seconds if it does, it'll stop the notebook:
python autostop.py --time <time_in_seconds> [--port <jupyter_port>] [--ignore-connections]
Type "python autostop.py -h" for available options.
"""
# Help info
helpInfo = """-t, --time
    Auto stop time in seconds
-p, --port
    jupyter port
-c --ignore-connections
    Stop notebook once idle, ignore connected users
-h, --help
    Help information
"""

# Read in command-line parameters
idle = True
port = '8443'
ignore_connections = False
try:
    opts, args = getopt.getopt(sys.argv[1:], "ht:p:c", ["help","time=","port=","ignore-connections"])
    if len(opts) == 0:
        raise getopt.GetoptError("No input parameters!")
    for opt, arg in opts:
        if opt in ("-h", "--help"):
            print(helpInfo)
            exit(0)
        if opt in ("-t", "--time"):
            time = int(arg)
        if opt in ("-p", "--port"):
            port = str(arg)
        if opt in ("-c", "--ignore-connections"):
            ignore_connections = True
except getopt.GetoptError:
    print(usageInfo)
    exit(1)

# Missing configuration notification
missingConfiguration = False
if not time:
    print("Missing '-t' or '--time'")
    missingConfiguration = True
if missingConfiguration:
    exit(2)


def is_idle(last_activity):
    last_activity = datetime.strptime(last_activity,"%Y-%m-%dT%H:%M:%S.%fz")
    if (datetime.now() - last_activity).total_seconds() > time:
        return True
    else:
        return False


def get_notebook_name():
    log_path = '/opt/ml/metadata/resource-metadata.json'
    with open(log_path, 'r') as logs:
        _logs = json.load(logs)
    return _logs['ResourceName']

def last_kernel_execution_activity(kernel):
    if kernel['execution_state'] != 'idle':
        return datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%fz")
    return kernel['last_activity'];

def last_kernel_connection_activity(kernel):
    if kernel['connections'] > 0:
        return datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%fz")
    return kernel['last_activity']

def top():
    output = subprocess.check_output(['top',"-bn1","-o","%CPU"])
    #output = subprocess.check_output(['top', "-bn1", "-o", "%MEM"])
    match = re.match(r'^[\w\W]*?\n( +PID.*COMMAND)\n([\w\W]*)', output.decode())

    header = match[1]
    data = match[2]

    df = pd.read_fwf( StringIO(data)
        , colspecs = [(0,5), (6,16), (16,18), (19,22), (23,30), (31,37), (38,44), (45,46), (47,52), (53,57), (58,67), (68,999) ]
        , names    = ['PID', 'USER',    'PR',    'NI',  'VIRT',   'RES',   'SHR',     'S',  '%CPU',  '%MEM', 'TIME+', 'COMMAND']
    )
    dat = df.to_dict(orient='index')
    process_output = []
    #print(type(dat))
    #lst.sort(key=lambda tup: tup[1])
    for key, val in dat.items():
        print(val['COMMAND'],"=",val['%CPU'])
        process_output.append((val['COMMAND'],val['%CPU']))

    #print(df.to_dict(orient='index'))
    process_output.sort(key=lambda tup: tup[1],reverse=True)
    return process_output[1]
    #print(process_output[0])

last_active_time = datetime.now() - timedelta(days=3*365)
response = requests.get('https://localhost:'+port+'/api/sessions', verify=False)
notebooks = response.json()
activities = []

execution_activities = [('execution', last_kernel_execution_activity(n['kernel'])) for n in notebooks]
activities.extend(execution_activities)

connection_activities = [('connection', last_kernel_connection_activity(n['kernel'])) for n in notebooks if not ignore_connections]
activities.extend(connection_activities)

client = boto3.client('sagemaker')
uptime = client.describe_notebook_instance(NotebookInstanceName=get_notebook_name())['LastModifiedTime']
activities.append(('instance configuration', uptime.strftime("%Y-%m-%dT%H:%M:%S.%fz")))

resource, last_active_time = max(activities, key=lambda x: x[1])

print(f"Last activity resource={resource} time={last_active_time}")

print(f"Result from top: {}".format(top()))
if is_idle(last_active_time) and top() < 20:
    print("Shutting down the instance as instance is idle and top is {}".format(top()))
    client = boto3.client('sagemaker')
    client.stop_notebook_instance(
        NotebookInstanceName=get_notebook_name()
    )