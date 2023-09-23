#!/usr/bin/python3
#
# fix-stuck-notes.py
#  Waits for a SQS message that will tell us to send bulk MIDI NoteOff events in
#  order to resolve stuck notes on one of the channels.
#

import sys
import logging
import boto3
import json
import os
import sys
import signal
import alsa_midi

logger = None
sqs = boto3.client('sqs')
cfn = boto3.client('cloudformation')

midiPorts = {}
transmitPorts = []
tableName = None
sqsQueueUrl = None
logger = None
alsaClients = {}

def main():
    global logger, sqsQueueUrl, transmitPorts, alsaClients

    signal.signal(signal.SIGINT, interrupted)

    configure()

    if alreadyRunning():
        logger.info('This is the second copy - stopping')
        sys.exit(0)

    portRanges = {'Low':range(0, 43), 'Mid':range(43, 86), 'High':range(86, 127), 'All':range(0, 127)}

    while True:
        try:
            response = sqs.receive_message(QueueUrl=sqsQueueUrl, WaitTimeSeconds=10)
        except Exception as e:
            logger.error(f'SQS receive failed: {e}')
            continue

        for message in response['messages']:
            body = message['body']

            print(f'Message body: {body}')

            resetRange = body['range']
            port = body['port']

            if port not in alsaClients:
                logger.warning(f'Port {port} is not defined - skipping')
            else:
                if resetRange not in portRanges:
                    logger.warning(f'Range {resetRange} not specified - using All')
                    resetRange = 'All'

                logger.info(f'Sending NoteOff to {port} for {portRanges[resetRange]}')

                for midiNote in portRanges[resetRange]:
                    event = alsa_midi.NoteOffEvent(note=midiNote, velocity=64, channel=0)
                    alsaClients[port].event_output(event)
                alsaClients[port].drain_output()

            try:
                sqs.delete_message(QueueUrl=sqsQueueUrl, ReceiptHandle=message['ReceiptHandle'])
            except Exception as e:
                logger.error(f'SQS delete failed: {e}')
                continue

def configure():
    global logger, midiPorts, transmitPorts, tableName, alsaClients

    logging.basicConfig()
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    try:
        with open('midiports') as portsFile:
            portsList = portsFile.read()

        midiPorts = json.loads(portsList)
    except FileNotFoundError: # No ports file found - that's quite ok
        logger.error('No ports file found - stopping')
        sys.exit(1)
    except Exception as e:
        logger.error(f'Got error {e} - ports file badly formatted?')
        sys.exit(1)

    for group in midiPorts:
        transmitPorts.append(midiPorts[group][1])

    logger.info(f'MIDI ports: {midiPorts} Transmit ports: {transmitPorts}')

    try:
        with open('../cloudformationstackname') as cfnfile:
            stackName = cfnfile.read().strip()
    except FileNotFoundError:
        logger.error('No CloudFormation stack name file found - stopping')
        sys.exit(1)
    except Exception as e:
        logger.error(f'Cannot read CloudFormation stack name: {e}')
        sys.exit(1)

    try:
        response = cfn.describe_stacks(StackName=stackName)
    except Exception as e:
        logger.error(f'Cannot get stack information: {e}')
        sys.exit(1)

    for output in response['Stacks'][0]['Outputs']:
        if output['OutputKey'] == 'DynamoDBTableName': tableName = output['OutputValue']
        if output['OutputKey'] == 'SQSQueueURL': sqsQueueUrl = output['OutputValue']

    if not tableName:
        logger.error('Did not find DynamoDB table name')
        sys.exit(1)
    if not sqsQueueUrl:
        logger.error('Did not find SQS queue URL')
        sys.exit(1)

    dynamodb = boto3.resource('dynamodb').Table(tableName)
    try:
        dynamodb.put_item(Item={'clientId':{'S':'TransmitPorts'},'list':{'L':transmitPorts}})
    except Exception as e:
        logger.warning(f'Failed to save transmit ports to DynamoDB - continuing: {e}')

    checkClient = alsa_midi.SequencerClient('checker')
    clientList = checkClient.list_ports(output=True)

    for portNumber in transmitPorts:
        client = alsa_midi.SequencerClient('fix-stuck-notes')
        port = client.create_port('output', caps=alsa_midi.READ_PORT)

        destinationClient = None
        for item in clientList:
            if item.name.endswith(str(portNumber)):
                destinationClient = item
                break          

        if destinationClient:
            port.connect_to(destinationClient)
        else:
            logger.warning(f'Did not find destination for port {portNumber}')
        alsaClients[portNumber] = client

#
# Although it's not completely harmful we don't really want more than one
# copy of this running at any one time. The worst that can happen is that
# two copies of this script will compete for SQS messages. Best that this
# doesn't happen.
#
def alreadyRunning():
    global logger

    myName = os.path.basename(sys.argv[0])

    logger.debug('Checking to see if we are already running')
    output = os.popen('/usr/bin/ps -e').read()
    if output.count(myName) > 1: return True

    return False

def interrupted(signal, frame):
    global logger

    logger.info('Interrupt - stopping')
    sys.exit(0)

if __name__ == "__main__":
    main()
