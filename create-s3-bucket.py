#!/usr/bin/python3

#
# Create a s3 bucket with a (semi) random name then point the CloudFront
# distribution at it and secure it using OAI.
#
# Normally I'd do this in CloudFormation but there are difficulties including
# not being able to sanitise user input.
#
# NOTE: Only run this once; there's no particular harm in running it more times
# but doing so will create a new (randomly named) S3 bucket and a new
# CloudFront origin identity.
#

import sys
import random
import logging
import string
import requests
import json
import boto3
import botocore.exceptions

logger = None
randomSource = string.ascii_lowercase+string.digits

random.seed()

def main():
    global logger

    logging.basicConfig()
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    randomName = 'midihubv2-'+''.join(random.choice(randomSource) for i in range(16))
    logger.info(f'Random name: {randomName}')

    try:
        with open('../cloudformationstackname') as cfnfile:
            stackName = cfnfile.read().strip()
    except FileNotFoundError:
        logger.error('No CloudFormation stack name file found - stopping')
        return # Don't throw an error otherwise the whole instance bootstrap process stops
    except Exception as e:
        logger.error(f'Cannot read CloudFormation stack name: {e}')
        return

    cfn = boto3.client('cloudformation')
    try:
        response = cfn.describe_stacks(StackName=stackName)
    except Exception as e:
        logger.error(f'Cannot get stack information: {e}')
        return

    apiGatewayEndpoint = ''
    cloudFrontDistribution = ''
    for output in response['Stacks'][0]['Outputs']:
        if output['OutputKey'] == 'APIGatewayEndpoint': apiGatewayEndpoint = output['OutputValue']
        if output['OutputKey'] == 'CloudFrontDistribution': cloudFrontDistribution = output['OutputValue']

    if not apiGatewayEndpoint:
        logger.error('Did not find API Gateway endpoint in CloudFormation outputs')
        return
    if not cloudFrontDistribution:
        logger.error('Did not find CloudFront dstribution in CloudFormation outputs')
        return

    try:
        with open('latency.html') as webfile:
            originalLatencyHTML = webfile.read().strip()
    except FileNotFoundError:
        logger.error('Cannot read HTML latency source - stopping')
        return
    except Exception as e:
        logger.error(f'Cannot read HTML latency source file: {e}')

    newLatencyHTML = originalLatencyHTML.replace('--APIGATEWAYEDNPOINT--', apiGatewayEndpoint+'/latency')

    try:
        response = requests.get('http://169.254.169.254/latest/dynamic/instance-identity/document')
        instanceInfo = json.loads(response.content)
    except:
        logger.info('Did not get instance metadata')
        return

    regionName = instanceInfo.get('region')
    if not regionName:
        logger.warning('No region name in instance metadata')
        return

    accountId = instanceInfo.get('accountId')
    if not regionName:
        logger.warning('No account id in instance metadata')
        return

    cloudfront = boto3.client('cloudfront')
    s3 = boto3.client('s3')

    try:
        config = cloudfront.get_distribution_config(Id=cloudFrontDistribution)
    except Exception as e:
        logger.error(f'Failed to get distribution information for {cloudFrontDistribution}: {e}')
        return

    try:
        oac = cloudfront.create_origin_access_control(OriginAccessControlConfig={
                                                       'Name':randomName, 'Description':'midiHub OAC',
                                                       'SigningProtocol':'sigv4', 'SigningBehavior':'always',
                                                       'OriginAccessControlOriginType':'s3'})
    except Exception as e:
        logger.error(f'Failed to create OAC: {e}')
        return
            
    try:
        response = s3.create_bucket(Bucket=randomName,
                                    CreateBucketConfiguration={'LocationConstraint':regionName})
    except s3.exceptions.from_code('BucketAlreadyOwnedByYou'):
        logger.warning(f'Bucket {randomName} already exists and belongs to us - continuing')
    except Exception as e:
        logger.error(f'Failed to create S3 bucket {randomName}: {e}')
        return

    policyStatement = {}
    policyStatement['Effect'] = 'Allow'
    policyStatement['Principal'] = {'Service':'cloudfront.amazonaws.com'}
    policyStatement['Action'] = 's3:GetObject'
    policyStatement['Resource'] = f'arn:aws:s3:::{randomName}/*'
    policyStatement['Condition'] = {'StringEquals': {'AWS:SourceArn':f'arn:aws:cloudfront::{accountId}:distribution/{cloudFrontDistribution}'}}

    bucketPolicy = {}
    bucketPolicy['Version'] = '2008-10-17'
    bucketPolicy['Statement'] = [policyStatement]

    try:
        response = s3.put_bucket_policy(Bucket=randomName, Policy=json.dumps(bucketPolicy))
    except Exception as e:
        logger.error(f'Failed to create S3 bucket policy: {e}')
        return

    originItem = config['DistributionConfig']['Origins']['Items'][0]
    originItem['DomainName'] = f'{randomName}.s3.amazonaws.com'
    originItem['OriginAccessControlId'] = oac['OriginAccessControl']['Id']
    originItem['S3OriginConfig'] = {'OriginAccessIdentity':''}
    if 'CustomOriginConfig' in originItem:
        del(originItem['CustomOriginConfig'])

    originUpdate = {}
    originUpdate['Quantity'] = 1
    originUpdate['Items'] = [originItem]

    newConfig = config['DistributionConfig']
    newConfig['Origins'] = originUpdate
    newConfig['DefaultRootObject'] = 'latency.html'

    try:
        response = cloudfront.update_distribution(DistributionConfig=newConfig,
                                                  Id=cloudFrontDistribution,
                                                  IfMatch=config['ETag'])
    except Exception as e:
        logger.error(f'Failed to update CloudFront origins: {e}')
        return

    try:
        response = s3.put_object(Bucket=randomName, Key='latency.html', Body=newLatencyHTML, ContentType='text/html')
    except Exception as e:
        logger.error(f'Failed to upload latency HTML to S3: {e}')
        return

    logger.info(f'Created S3 bucket {randomName} and updated origin {config["ETag"]}')

if __name__ == "__main__":
    main()
