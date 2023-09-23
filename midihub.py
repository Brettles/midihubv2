#!/usr/bin/python3

import sys
import os
import logging
import signal
import time
import subprocess
import re
import boto3
import requests
import json

#
# Configuration:
#  SLEEP_CHECK_INTERVAL:
#      How often to check that the daemon(s) are running and when ned
#      participants have joined. Default is five seconds which seems
#      reasonable.
#  MIDI_DAEMON:
#      Path to the RTP MIDI daemon.
#
#  midiPorts:
#      List of ports to open to listen to MIDI connections. Each port will
#      be opened as a separate process using the RTP MIDI daemon.
#      These can also be configured by creating a file in the running
#      directory called "midiports". Put a comma-seperate list of ports into
#      the file and it will be read during startup or if SIGHUP is sent.
#
SLEEP_CHECK_INTERVAL = 5
MIDI_DAEMON = '/opt/rtpmidi_1.1.2-ubuntu22.04/bin/rtpmidi'

midiPorts = {'GroupOne': [5040, 5042], 'GroupTwo': [5050, 5052]}
logger = None

#
# Main loop which does a few startup checks and runs forever.
# Checks to make sure all of the daemons are running which is important at
# startup but also just in case they crash at some point.
# After that, looks at the participants on each daemon and automatically
# joins all of the MIDI sessions to each other - acting as a type of hub.
#
def main():
    global logger

    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGHUP, configure)

    configure('', '')

    if alreadyRunning():
        logger.info('This is the second copy - stopping')
        sys.exit(0)

    if not checkPrerequisites():
        sys.exit(1)

    logger.info('Entering main loop')
    while True:
        checkDaemon()
        checkMidiParticipants()

        time.sleep(SLEEP_CHECK_INTERVAL)

#
# See if we have daemons running on the ports specified in the global
# midiPorts list. If we don't find a process running with the name of
# the daemon and the port number then we fork() and create one.
#
def checkDaemon():
    global logger, midiPorts

    midiStatus = {}
    daemonName = os.path.basename(MIDI_DAEMON)

    stream = os.popen('/usr/bin/ps -ef')
    psLine = stream.readline()
    while psLine:
        if psLine.find(daemonName) > -1:
            for group in midiPorts:
                for port in midiPorts[group]:
                    if psLine.find(str(port)) > -1:
                        midiStatus[port] = True
                        break
        psLine = stream.readline()

    for group in midiPorts:
        for port in midiPorts[group]:
            if port in midiStatus: continue

            logger.warning(f'Midi daemon {group}-{port} not running - starting')
            if os.fork() == 0: # We are the child process
                name = f'midiHub-{group}-{port}';

                newStdErr = os.open(f'../output-{port}.log', os.O_WRONLY|os.O_CREAT|os.O_APPEND)
                os.dup2(newStdErr, sys.stderr.fileno())
                os.close(newStdErr)
                os.close(1) # Close STDOUT

                os.execlp(MIDI_DAEMON, daemonName, f'multilisten', '-u', str(port), '-C', name, '-P', name)

#
# Using the aconnect command we can see all of the MIDI "ports" or "clients"
# that are open on this server; and all of the participants in those ports.
# We're not interested in the low numbered prots (below 128) - when the MIDI
# daemon starts it is allocated a port number from 128 upwards.
#
# Using the output we find all of the participants in the high numbered ports
# and then join them all together. They could already be joined together but
# aconnect doesn't care if we try to join two participants together that are
# already joined to each other. We want to create a mesh between the
# participants on each port/client; but not between ports/clients.
#
# Each port/client from the MIDI daemon will already have a "Network"
# participants with a participant number of zero. The daemon will also
# automatically discover the other daemons that are running so we ignore any
# other participants with "midiHub" in the name. We only want to connect
# remote participants to each other. We definitely don't want to connect
# the daemons to each other - that's a valid MIDI configuration but not
# suitable for our purposes here.
#
def checkMidiParticipants():
    global logger, connectInAndOut

    logger.debug('Getting output from aconnect')

    try:
        output = os.popen('aconnect -l').read().split('\n')
    except Exception as e:
        logger.error(f'Failed to get aconnect output: {e}')
        return

    groupPorts = {}
    for line in output:
        logger.debug(f' Line: {line}')
        if line.find('client ') == 0:
            try:
                clientNumber = re.findall(r'\d+', line)[0]
            except:
                logger.warning(f'  Did not see client id in {line} - skipping')
                continue
            if int(clientNumber) < 128: continue

            try:
                nameList = re.findall(r"midiHub-.+'", line)[0][:-1].split('-')
            except:
                logger.warning(f'  Did not see midiHub name in {line} - skipping')
                continue

            if nameList[1] not in groupPorts: groupPorts[nameList[1]] = []
            groupPorts[f'{nameList[1]}-{nameList[2]}'] = clientNumber

    for group in midiPorts:
        try:
            inPortClient = groupPorts[f'{group}-{midiPorts[group][0]}']
            outPortClient = groupPorts[f'{group}-{midiPorts[group][1]}']
        except Exception as e:
            logger.warning(f'  aconnect client not found: {e}')
            continue

        logger.debug(f'  Adding connection for group {group} from client {inPortClient} to {outPortClient}')
        os.system(f'aconnect {inPortClient}:0 {outPortClient}:0 >/dev/null 2>&1')

#
# Although it's not completely harmful we don't really want more than one
# copy of this running at any one time. The worst that can happen is that
# two copies of this script will try and run multiple copies of the daemon
# on the same ports - but the UDP port can only be bound to a single process
# so any subsequent daemon invocations will self-terminate. This script will
# also try and connect MIDI participants to each other but additional
# connection requests will be ignored if the connection already exists.
#
def alreadyRunning():
    global logger

    myName = os.path.basename(sys.argv[0])

    logger.debug('Checking to see if we are already running')
    output = os.popen('/usr/bin/ps -e').read()
    if output.count(myName) > 1: return True

    return False

#
# There's no point trying to run the MIDI daemon if there aren't a few
# drivers running on the system (soundcore and snd-dummy); and we want
# to make sure that the MIDI daemon itself is here somewhere too.
#
def checkPrerequisites():
    global logger

    logger.debug('Checking for soundcore module')
    output = os.popen('/usr/sbin/modinfo soundcore 2>&1').read()
    if output.find('not found') > -1:
        logger.warning('Kernel module soundcore not found - stopping')
        return False

    logger.debug('Checking for snd-dummy module')
    output = os.popen('/usr/sbin/modinfo snd-dummy 2>&1').read()
    if output.find('not found') > -1:
        logger.warning('Kernel module snd-dummy not found - stopping')
        return False

    logger.debug('Checking for MIDI daemon code')
    if not os.path.isfile(MIDI_DAEMON):
        logger.warning(f'Midi daemon not found at {MIDI_DAEMON} - stopping')
        return False

    return True

#
# A few things to do here.
# First we look for our configuration file which (if it exists) contains a
# comma-separated list of UDP ports that we are to listen to. If it's empty
# or malformed then we go with the defaults set at the start of this file.
# This is also called if we're sent SIGHUP mainly so that we can re-read the
# ports configuration file.
#
def configure(singal, frame):
    global logger, location, midiPorts, connectInAndOut

    logging.basicConfig()
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    try:
        with open('midiports') as portsFile:
            portsList = portsFile.read()

        midiPorts = json.loads(portsList)
    except FileNotFoundError: # No ports file found - that's quite ok
        logger.info('No ports file found - using defaults')

        #
        # But because our stuck note fixer-upper needs to know the same port
        # numbers we will write out the values we're given.
        #
        with open('midiports', 'w') as portsFile:
            portsFile.write(json.dumps(midiPorts))
    except Exception as e:
        logger.warning(f'Got error {e} - ports file badly formatted?')

    logger.info(f'MIDI ports: {midiPorts}')

def interrupted(signal, frame):
    global logger

    logger.info('Interrupt - stopping')
    sys.exit(0)

if __name__ == "__main__":
    main()
