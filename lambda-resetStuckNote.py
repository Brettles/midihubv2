import json
import boto3
import os
import logging

sqs = boto3.client('sqs')

sqsQueueURL = os.environ.get('SQSQueueURL')

logging.basicConfig()
logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event, context):
    global logger, tableName
    
    if not sqsQueueURL:
        logger.error('sqsQueueURL not set - stopping')
        return {'statusCode':500, 'body':'sqsQueueURL not set'}

    portNumber = event.get('queryStringParameters', {}).get('port', '')
    noteRange = event.get('queryStringParameters', {}).get('range', '')

    if not portNumber:
        logger.error('portNumber not specified - stopping')
        return {'statusCode':400, 'body':'Specify port'}
    if not noteRange:
        logger.error('range not specified - stopping')
        return {'statusCode':400, 'body':'Specify range'}
        
    logger.info(f'Sending fix for port {portNumber} on range {noteRange}')

    message = {'port':portNumber, 'range':noteRange}
    try:
        sqs.send_message(QueueUrl=sqsQueueURL, MessageBody=json.dumps(message))
    except Exception as e:
        logger.error(f'SQS send failed: {e}')
        return {'statusCode':500, 'body':f'SQS send failed: {e}'}
        
    return

