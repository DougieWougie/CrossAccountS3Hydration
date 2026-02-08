# S3 Hydration Deployment Guide

This guide walks through the end-to-end deployment of the S3 Hydration solution, which performs cross-account S3 data transfer using the Consumer Pull model. In this model, a Lambda function in the consumer account assumes a role in the producer account to read objects and copy them into the consumer's own S3 bucket, re-encrypting with the consumer's KMS key.

## Prerequisites

Before beginning deployment, ensure the following are in place:

- **Two AWS accounts in the same AWS Organisation.** The solution relies on Organisation-level trust policies and condition keys (`aws:PrincipalOrgID`) for secure cross-account access.
- **AWS CLI configured with appropriate credentials for both accounts.** You will need permissions to deploy CloudFormation stacks, create IAM roles, S3 buckets, KMS keys, Lambda functions, and related resources in each respective account.
- **Python 3.12+** (for Lambda packaging). The Lambda runtime target is Python 3.12.
- **An existing VPC with private subnets in the consumer account.** The Lambda function is deployed inside this VPC to ensure traffic to S3 traverses a VPC Gateway Endpoint rather than the public internet.
- **Route table IDs for the VPC subnets.** These are required to configure the S3 Gateway Endpoint so that Lambda traffic is routed correctly.

## Step 1: Package the Lambda Function

Build the Lambda deployment package using the provided Makefile:

```bash
cd service-catalog/lambda
make build
```

This produces `build/s3-hydration-lambda.zip`. Upload this zip file to an S3 bucket in the consumer account:

```bash
aws s3 cp build/s3-hydration-lambda.zip s3://<your-lambda-artifacts-bucket>/s3-hydration-lambda.zip
```

Note the bucket name and object key. You will supply these as parameters to the consumer stack.

## Step 2: Generate an External ID

Generate a random ExternalId (at least 16 characters) that will be shared between both stacks. The ExternalId prevents the confused deputy problem by ensuring that only the intended consumer can assume the cross-account role.

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Record this value securely. You will provide the same ExternalId to both the producer and consumer stack deployments.

## Step 3: Deploy the Producer Stack

Deploy the producer stack first, in the producer account. This creates the source S3 bucket, a KMS key for server-side encryption, and an IAM role that the consumer Lambda will assume.

```bash
aws cloudformation deploy \
  --template-file service-catalog/producer/template.yaml \
  --stack-name s3-hydration-producer \
  --parameter-overrides \
    ConsumerAccountId=222222222222 \
    OrganisationId=o-abc123 \
    ExternalId=<generated-external-id> \
  --capabilities CAPABILITY_NAMED_IAM
```

Replace `222222222222` with the actual consumer account ID, `o-abc123` with your AWS Organisation ID, and `<generated-external-id>` with the value from Step 2.

Once the stack reaches `CREATE_COMPLETE`, retrieve the outputs:

```bash
aws cloudformation describe-stacks \
  --stack-name s3-hydration-producer \
  --query "Stacks[0].Outputs"
```

Note the following output values -- you will need them for the consumer stack:

- **BucketArn** -- the ARN of the producer S3 bucket
- **BucketName** -- the name of the producer S3 bucket
- **RoleArn** -- the ARN of the cross-account IAM role
- **KmsKeyArn** -- the ARN of the producer KMS key
- **KmsKeyId** -- the ID of the producer KMS key

## Step 4: Deploy the Consumer Stack

Deploy the consumer stack in the consumer account, feeding the producer stack outputs as parameters. This creates the destination S3 bucket, a consumer-side KMS key, the Lambda function (inside the VPC), an S3 Gateway Endpoint, EventBridge scheduling, CloudWatch alarms, and associated IAM roles.

```bash
aws cloudformation deploy \
  --template-file service-catalog/consumer/template.yaml \
  --stack-name s3-hydration-consumer \
  --parameter-overrides \
    ProducerAccountId=111111111111 \
    ProducerBucketName=<producer-bucket-name> \
    ProducerBucketArn=<producer-bucket-arn> \
    CrossAccountRoleArn=<producer-role-arn> \
    ProducerKmsKeyArn=<producer-kms-key-arn> \
    OrganisationId=o-abc123 \
    ExternalId=<generated-external-id> \
    VpcId=vpc-xxx \
    SubnetIds=subnet-aaa,subnet-bbb \
    RouteTableIds=rtb-xxx,rtb-yyy \
    LambdaS3Bucket=<lambda-zip-bucket> \
    LambdaS3Key=<lambda-zip-key> \
    AlarmEmail=ops@example.com \
  --capabilities CAPABILITY_NAMED_IAM
```

Replace each placeholder with the corresponding value:

- `111111111111` -- the producer account ID
- `<producer-bucket-name>`, `<producer-bucket-arn>`, `<producer-role-arn>`, `<producer-kms-key-arn>` -- from the producer stack outputs in Step 3
- `o-abc123` -- your AWS Organisation ID
- `<generated-external-id>` -- the same ExternalId used in Step 3
- `vpc-xxx`, `subnet-aaa,subnet-bbb`, `rtb-xxx,rtb-yyy` -- your existing VPC, subnet, and route table identifiers
- `<lambda-zip-bucket>` and `<lambda-zip-key>` -- the S3 location of the Lambda zip from Step 1
- `ops@example.com` -- the email address for CloudWatch alarm notifications

After the stack deploys, confirm the SNS subscription by checking the provided email address for a confirmation link.

## Step 5: Verify Deployment

Once both stacks are deployed, run through these verification steps to confirm the solution is working end to end.

1. **Upload a test object to the producer bucket:**

   ```bash
   echo "test-content" > /tmp/test-object.txt
   aws s3 cp /tmp/test-object.txt s3://<producer-bucket-name>/test/test-object.txt
   ```

2. **Manually trigger the Lambda function:**

   ```bash
   aws lambda invoke \
     --function-name s3-hydration-transfer \
     --payload '{}' \
     out.json
   ```

   Inspect `out.json` for a successful response. Check for any errors in the output.

3. **Check the consumer bucket for the transferred object:**

   ```bash
   aws s3 ls s3://<consumer-bucket-name>/test/
   ```

   You should see `test-object.txt` listed.

4. **Verify encryption with the consumer KMS key:**

   ```bash
   aws s3api head-object \
     --bucket <consumer-bucket-name> \
     --key test/test-object.txt
   ```

   The response should show `ServerSideEncryption: aws:kms` and the `SSEKMSKeyId` should match the consumer KMS key ARN, confirming re-encryption occurred.

## Step 6: Verify Security

Validate that the security controls are functioning correctly.

1. **Confirm cross-account access is one-directional.** From the producer account, attempt to access the consumer bucket:

   ```bash
   aws s3 ls s3://<consumer-bucket-name>/
   ```

   This should be denied with an `AccessDenied` error. The producer account should have no access to the consumer bucket.

2. **Verify CloudTrail logs show the cross-account role assumption.** In the producer account's CloudTrail, look for `AssumeRole` events where the consumer Lambda role assumes the producer's cross-account role. The events should include the ExternalId in the request parameters.

3. **Confirm CloudWatch alarms are in OK state.** In the consumer account:

   ```bash
   aws cloudwatch describe-alarms \
     --alarm-name-prefix s3-hydration \
     --query "MetricAlarms[].{Name:AlarmName,State:StateValue}"
   ```

   All alarms should report `OK`. If any are in `ALARM` or `INSUFFICIENT_DATA`, investigate the corresponding metrics.

## Service Catalog Integration

Both templates can be registered as AWS Service Catalog products to enable self-service provisioning with guardrails.

1. **Upload templates to an S3 bucket** accessible by the Service Catalog service in each account.

2. **Create a Portfolio** in each account (or a shared portfolio in a hub account):

   ```bash
   aws servicecatalog create-portfolio \
     --display-name "S3 Hydration" \
     --provider-name "Platform Team"
   ```

3. **Create Products** referencing each template. Create one product for the producer template and one for the consumer template:

   ```bash
   aws servicecatalog create-product \
     --name "S3 Hydration Producer" \
     --owner "Platform Team" \
     --product-type CLOUD_FORMATION_TEMPLATE \
     --provisioning-artifact-parameters \
       Name="v1.0",Info={LoadTemplateFromURL=https://s3.amazonaws.com/<bucket>/producer/template.yaml},Type=CLOUD_FORMATION_TEMPLATE
   ```

4. **Share the portfolio** with the appropriate Organisational Units (OUs) so that member accounts can discover and launch the products.

5. **Add launch constraints** with IAM roles that have the necessary permissions to create the resources defined in each template. This ensures that end users do not need broad IAM permissions themselves.

## Updating the Lambda Code

When the Lambda function code needs to be updated:

1. **Build a new deployment package:**

   ```bash
   cd service-catalog/lambda
   make build
   ```

2. **Upload the new zip to S3:**

   ```bash
   aws s3 cp build/s3-hydration-lambda.zip s3://<lambda-zip-bucket>/<lambda-zip-key>
   ```

3. **Update the function code** using one of the following approaches:

   - **Update the CloudFormation stack** with a new `LambdaS3Key` parameter value (for example, by appending a version suffix to the key). This triggers a stack update and replaces the function code.

   - **Directly update the function code** using the AWS CLI:

     ```bash
     aws lambda update-function-code \
       --function-name s3-hydration-transfer \
       --s3-bucket <lambda-zip-bucket> \
       --s3-key <lambda-zip-key>
     ```

   The direct update approach is faster for iterative development, but updating via CloudFormation is recommended for production deployments to maintain stack consistency.
