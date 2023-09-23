#!/usr/bin/python3

#
# update-latency.py
#  Intended to be run as a pipe destination. For example:
#   grep Latency output-*.log | update-latency.py
#
#  Only processes lines from rtpmidid that have 'rtt' on them; takes the
#  client name, connected port and latency numbers and puts them into DynamoDB.
#  Expects there to be a local file called "dynamodbtable" with the name of the
#  table in it.
#  Output to DynamoDB is the maximum, minimum and last latency for each client
#  as well as the current timestamp.
#

import sys
import logging
import boto3
import datetime
import sys
import re
from decimal import Decimal

logger = None
dynamodb = boto3.resource('dynamodb')
cfn = boto3.client('cloudformation')

def main():
    global logger

    logging.basicConfig()
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    try:
        with open('cloudformationstackname') as cfnfile: # Is run from the parent directory
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

    tableName = ''
    for output in response['Stacks'][0]['Outputs']:
        if output['OutputKey'] == 'DynamoDBTableName': tableName = output['OutputValue']

    if not tableName:
        logger.error('Did not find DynamoDB table name')
        sys.exit(1)

    latencyStats = {}
    maxLatency = {}
    minLatency = {}
    lastLatency = {}

    for line in sys.stdin:
        latencyMarker = line.find('rtt: ')
        clientMarker = line.find('] [')

        if latencyMarker == -1 or clientMarker == -1:
            logger.warning('No latency info found in input - ignoring')
            continue

        try:
            latencyValue = round(float(line[latencyMarker+5:])*1000, 1)

            endClientMarker = line.find(']', clientMarker+3)
            clientName = line[clientMarker+3:endClientMarker].strip()

            portNumber = re.findall(r'\d+', line[:15])[0]

            timestamp = line[16:35]
            epochTime = int(datetime.datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S').timestamp())
        except Exception as e:
            logger.error(f'Failed to parse line: {e}')
            logger.error(line)
            continue

        id = f'{clientName}-{portNumber}'
        if id not in latencyStats: latencyStats[id] = []
        if id not in maxLatency: maxLatency[id] = (None, 0)
        if id not in minLatency: minLatency[id] = (None, 9999)
        if id not in lastLatency: lastLatency[id] = ''

        latencyStats[id].append(latencyValue)
        if latencyValue > maxLatency[id][1]: maxLatency[id] = (epochTime, latencyValue)
        if latencyValue < minLatency[id][1]: minLatency[id] = (epochTime, latencyValue)
        lastLatency[id] = epochTime

    ddbTable = dynamodb.Table(tableName)
    with ddbTable.batch_writer() as batch:
        for id in latencyStats:
            average = round(sum(latencyStats[id])/len(latencyStats[id]), 1)

            now = int(datetime.datetime.now().timestamp())
            expiry = now+(86400*7) # Expire this record in seven days

            # Need to store floats as strings because DynamoDB doesn't support
            # float typess here
            item = {'clientId':id, 'timestamp':now, 'expiryTime':expiry,
                    'lastLatency':str(latencyStats[id][-1]), 'lastLatencyTime':lastLatency[id],
                    'maxLatency':str(maxLatency[id][1]), 'maxLatencyTime':maxLatency[id][0],
                    'minLatency':str(minLatency[id][1]), 'minLatencyTime':minLatency[id][0],
                    'averageLatency':Decimal(average)}
            batch.put_item(Item=item)

if __name__ == "__main__":
    main()
