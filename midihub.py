#!/usr/bin/python3

import sys
import os
import logging
import signal
import time
import subprocess
import boto3
import requests
import json
import alsa_midi

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
SLEEP_CHECK_INTERVAL = 3
MIDI_INPUT_DAEMON = '/home/ubuntu/pymidi/alsaserver.py'
MIDI_OUTPUT_DAEMON = '/opt/rtpmidi_1.1.2-ubuntu22.04/bin/rtpmidi'

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

    logging.basicConfig()
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    if alreadyRunning():
        logger.debug('This is the second copy - stopping')
        sys.exit(0)

    configure('', '')

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
    inputDaemonName = os.path.basename(MIDI_INPUT_DAEMON)
    outputDaemonName = os.path.basename(MIDI_OUTPUT_DAEMON)

    stream = os.popen('/usr/bin/ps -ef')
    psLine = stream.readline()
    while psLine:
        if psLine.find(inputDaemonName) > -1 or psLine.find(outputDaemonName) > -1:
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

                if port == midiPorts[group][0]: # Input port
                    os.execlp(MIDI_INPUT_DAEMON, inputDaemonName, str(port), name)
                else:
                    os.execlp(MIDI_OUTPUT_DAEMON, outputDaemonName, f'multilisten', '-u', str(port), '-C', name, '-P', name)

#
# Use the alsa midi interface to see all of the MIDI "ports" or "clients"
# that are open on this server; and all of the participants in those ports.
# We're not interested in the low numbered prots (below 128) - when the MIDI
# daemon starts it is allocated a port number from 128 upwards.
#
# We want to connect an input port (the first port in the group) to an
# output port (the second port in the group). This will prevent MIDI packet
# loops (which are bad).
#
def checkMidiParticipants():
    global logger

    groupPorts = {}

    alsaClient = alsa_midi.SequencerClient('midiHubController')
    otherClients = alsaClient.list_ports()
    for client in otherClients:
        logger.debug(f' Client: {client.name}')
        if client.name.startswith('midiHub-'):
            nameList = client.name.split('-')
            groupPorts[f'{nameList[1]}-{nameList[2]}'] = client

    for group in midiPorts:
        try:
            inPortClient = groupPorts[f'{group}-{midiPorts[group][0]}']
            outPortClient = groupPorts[f'{group}-{midiPorts[group][1]}']
        except Exception as e:
            logger.warning(f'  alsa client not found: {e}')
            continue

        inputSubs = alsaClient.list_port_subscribers(inPortClient)
        alreadySubscribed = False
        for sub in inputSubs:
            if sub.addr.client_id == outPortClient.client_id:
                alreadySubscribed = True
                logger.debug(f'  {inPortClient.name} already connected to {outPortClient.name}')
                break

        if not alreadySubscribed:
            logger.info(f'  Adding connection for group {group} from client {inPortClient.name} to {outPortClient.name}')
            alsaClient.subscribe_port(inPortClient, outPortClient)

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
    if not os.path.isfile(MIDI_INPUT_DAEMON):
        logger.warning(f'pymidi daemon not found at {MIDI_INPUT_DAEMON} - stopping')
        return False
    if not os.path.isfile(MIDI_OUTPUT_DAEMON):
        logger.warning(f'McLaren daemon not found at {MIDI_OUTPUT_DAEMON} - stopping')
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
    global logger, location, midiPorts

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
