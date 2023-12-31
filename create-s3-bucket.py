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
apiGatewayEndpoint = ''

randomSource = string.ascii_lowercase+string.digits
randomName = 'midihubv2-'+''.join(random.choice(randomSource) for i in range(16))

random.seed()

def main():
    global logger, apiGatewayEndpoint, randomName

    logging.basicConfig()
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    logger.info(f'Random bucket name: {randomName}')

    try:
        with open('../cloudformationstackname') as cfnfile:
            stackName = cfnfile.read().strip()
    except FileNotFoundError:
        logger.error('No CloudFormation stack name file found - stopping')
        return # Don't throw an error otherwise the whole instance bootstrap process stops
    except Exception as e:
        logger.error(f'Cannot read CloudFormation stack name: {e}')
        return

    #
    # We need some information - and the region name is key here because the default
    # (written to ~/.aws/config) may not have been written by the time that this is
    # run so we need to specify it explicitly.
    #
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
    if not accountId:
        logger.warning('No account id in instance metadata')
        return

    cfn = boto3.client('cloudformation', region_name=regionName)
    cloudfront = boto3.client('cloudfront', region_name=regionName)
    s3 = boto3.client('s3', region_name=regionName)

    try:
        response = cfn.describe_stacks(StackName=stackName)
    except Exception as e:
        logger.error(f'Cannot get stack information: {e}')
        return

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
    originItem['DomainName'] = f'{randomName}.s3.{regionName}.amazonaws.com'
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

    copyFileToS3(s3, 'latency.html')
    copyFileToS3(s3, 'fixstucknotes.html')

    logger.info(f'Created S3 bucket {randomName} and updated origin {config["ETag"]}')

def copyFileToS3(s3, filename):
    global logger, apiGatewayEndpoint, randomName

    try:
        with open(filename) as webfile:
            originalHtml = webfile.read().strip()
    except FileNotFoundError:
        logger.error(f'Cannot read source file {filename} - skipping')
        return
    except Exception as e:
        logger.error(f'Error reading source file {filename}: {e}')
        return

    newHtml = originalHtml.replace('--APIGATEWAYENDPOINT--', apiGatewayEndpoint)

    try:
        response = s3.put_object(Bucket=randomName, Key=filename, Body=newHtml, ContentType='text/html')
    except Exception as e:
        logger.error(f'Failed to upload {filename} to S3: {e}')
        return

if __name__ == "__main__":
    main()
