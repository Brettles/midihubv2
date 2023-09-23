#!/usr/bin/python3
#
# fix-stuck-notes.py
#  Waits for a SQS message that will tell us to send bulk MIDI NoteOff events in
#  order to resolve stuck notes on one of the channels.
#

import sys
import logging
import boto3
import sys
import alsa_midi

logger = None
dynamodb = boto3.client('dynamodb')
sqs = boto3.client('sqs')
cfn = boto3.client('cloudformation')

midiPorts = {}
transmitPorts = []
tableName = None
sqsQueueUrl = None
logger = None

def main():
    global logger

    signal.signal(signal.SIGINT, interrupted)

    configure()

    while True:
        try:
            response = sqs.receive_message(QueueUrl=sqsQueueUrl, WaitTimeSeconds=10)
        except Exception as e:
            logger.error(f'SQS receive failed: {e}')
            continue

        for message in response['messages']:
            body = message['body']

            print(f'Message body: {body}')

            try:
                sqs.delete_message(QueueUrl=sqsQueueUrl, ReceiptHandle=message['ReceiptHandle'])
            except Exception as e:
                logger.error(f'SQS delete failed: {e}')
                continue

def configure():
    global logger, midiPorts, transmitPorts, tableName

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
    if not SQSQueueURL:
        logger.error('Did not find SQS queue URL')
        sys.exit(1)

    try:
        dynamodb.put_item(TableName=tableName,
                          Item={'clientId':{'S':'TransmitPorts'},list:{'L':transmitPorts}})
    except Exception as e:
        logger.warning(f'Failed to save transmit ports to DynamoDB - continuing: {e}')

def interrupted(signal, frame):
    global logger

    logger.info('Interrupt - stopping')
    sys.exit(0)

if __name__ == "__main__":
    main()
