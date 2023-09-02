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
import time
import sys
import re

logger = None
dynamodb = boto3.resource('dynamodb')

def main():
    global logger

    logging.basicConfig()
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

#    try:
#        with open('dynamodbtable') as ddbfile:
#            tableName = ddbfile.read().strip()
#    except FileNotFoundError:
#        logger.error('No table name file found - stopping')
#        sys.exit(1)
#    except Exception as e:
#        logger.error(f'Cannot read table name: {e}')
#        sys.exit(1)

#    ddbTable = dynamodb.Table(tableName)
    latencyStats = {}
    maxLatency = {}
    minLatency = {}

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
        except Exception as e:
            logger.error(f'Failed to parse line: {e}')
            logger.error(line)
            continue

        id = f'{clientName}-{portNumber}'
        if id not in latencyStats: latencyStats[id] = []
        if id not in maxLatency: maxLatency[id] = (None, 0)
        if id not in minLatency: minLatency[id] = (None, 9999)

        latencyStats[id].append(latencyValue)

        if latencyValue > maxLatency[id][1]: maxLatency[id] = (timestamp, latencyValue)
        if latencyValue < minLatency[id][1]: minLatency[id] = (timestamp, latencyValue)

    print(latencyStats)
    print(maxLatency)
    print(minLatency)
#    with ddbTable.batch_writer() as batch:
#        for id in latencyStats:
#            average = round(sum(latencyStats[id])/len(latencyStats[id]), 1)
#
#            now = int(time.time())
#            expiry = now+86400
#
#            # Need to store floats as strings because DynamoDB doesn't support
#            # float typess here
#            item = {'clientId':id, 'timestamp':now, 'expiryTime':expiry,
#                    'lastLatency':str(latencyStats[id][-1]), 'averageLatency':str(average),
#                    'maxLatency': str(max(latencyStats[id])), 'minLatency':str(min(latencyStats[id]))}
#            batch.put_item(Item=item)

if __name__ == "__main__":
    main()
