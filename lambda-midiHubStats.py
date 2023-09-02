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
            if stat['clientId']['S'] == 'Participants': continue

            try:
                (name,port) = stat['clientId']['S'].split('-')

                item = {'clientName':name, 'clientPort':port, 'timestamp': stat['timestamp']['N'],
                        'averageLatency':stat['averageLatency']['S'], 'maxLatency':stat['maxLatency']['S'],
                        'minLatency':stat['minLatency']['S'], 'lastLatency':stat['lastLatency']['S']}
            except:
                logger.error(f'Cannot interpret item {stat}')
                continue
 
            output.append(item)

    return(output) 
