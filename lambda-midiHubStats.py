import json
import boto3
import os
import logging

dynamodb = boto3.client('dynamodb')

tableName = os.environ.get('TableName')

logging.basicConfig()
logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event, context):
    global logger, tableName

    if not tableName:
        logger.error('TableName not set - stopping')
        return {'statusCode':500, 'body':'TableName not set'}

    paginator = dynamodb.get_paginator('scan')
    iterator = paginator.paginate(TableName=tableName)

    output = []
    for page in iterator:
        for stat in page['Items']:
            try:
                clientId = stat['clientId']['S']
                if clientId == 'TransmitPorts': continue

                hyphen = clientId.rfind('-')
                if hyphen == -1:
                    name = clientId
                    port = '????'
                else:
                    name = clientId[:hyphen]
                    port = clientId[hyphen+1:]

                item = {'clientName':name, 'clientPort':port, 'timestamp': stat['timestamp']['N'],
                        'averageLatency':stat['averageLatency']['S'], 'maxLatency':stat['maxLatency']['S'],
                        'minLatency':stat['minLatency']['S'], 'lastLatency':stat['lastLatency']['S'],
                        'maxLatencyTime':stat['maxLatencyTime']['N'], 'minLatencyTime':stat['minLatencyTime']['N'],
                        'lastLatencyTime':stat['lastLatencyTime']['N']
                }
            except Exception as e:
                logger.error(f'Cannot interpret item {stat}')
                logger.error(e)
                continue

            output.append(item)

    return(output)

