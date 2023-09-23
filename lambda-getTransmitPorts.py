import json
import boto3
import os
import logging

tableName = os.environ.get('TableName', '')
dynamodb = boto3.resource('dynamodb').Table(tableName)

logging.basicConfig()
logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event, context):
    global logger, tableName

    if not tableName:
        logger.error('TableName not set - stopping')
        return {'statusCode':500, 'body':'TableName not set'}

    response = dynamodb.get_item(Key={'clientId':'TransmitPorts'}).get('Item')
    if not response:
        logger.error('TransmitPorts not found - stopping')
        return {'statusCode':500, 'body':'TransmitPorts not found'}

    transmitPorts = response.get('list', [])
    return(transmitPorts)

