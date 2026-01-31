#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { DealsNowBackendStack } from '../lib/dealsnow-backend-stack';

const app = new cdk.App();

// US Stack
new DealsNowBackendStack(app, 'DealsNowBackendStack-US', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: 'us-east-2'
  },
  stackName: 'dealsnow-backend-us',
  description: 'DealsNow Backend Infrastructure - US Region',
  tags: {
    Project: 'DealsNow',
    Environment: 'Production',
    Region: 'US',
    ManagedBy: 'CDK'
  },
  deploymentRegion: 'us',
  dbSchema: 'deals_master',
  dbSecretName: 'prod/dealsnow_master/aurora_db'
});

// India Stack
new DealsNowBackendStack(app, 'DealsNowBackendStack-India', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: 'ap-south-1'
  },
  stackName: 'dealsnow-backend-india',
  description: 'DealsNow Backend Infrastructure - India Region',
  tags: {
    Project: 'DealsNow',
    Environment: 'Production',
    Region: 'India',
    ManagedBy: 'CDK'
  },
  deploymentRegion: 'india',
  dbSchema: 'deals_india',
  dbSecretName: 'prod/dealsnow_india/aurora_db'
});

app.synth();
