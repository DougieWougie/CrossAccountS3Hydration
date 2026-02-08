# S3 Hydration

Cross-account S3 data transfer for AWS Organisations using the Consumer Pull model. A Lambda function in the consumer account assumes a role in the producer account, reads objects, and writes them to the consumer's bucket -- re-encrypting with the consumer's own KMS key in transit.

Designed to be deployed as **AWS Service Catalog products** so that teams can self-service provision with built-in security guardrails.

## Architecture

```
Producer Account                          Consumer Account
┌──────────────────────┐                  ┌──────────────────────────────────┐
│  S3 Bucket (KMS)     │◄─── GetObject ───│  Lambda (VPC)                    │
│  CrossAccount Role   │    AssumeRole    │  ├── EventBridge (daily cron)    │
│                      │                  │  ├── S3 Bucket (consumer KMS)    │
│                      │                  │  ├── DLQ (SQS)                   │
│                      │                  │  └── CloudWatch Alarms           │
└──────────────────────┘                  └──────────────────────────────────┘
```

The producer stack creates the source bucket, KMS key, and a read-only cross-account IAM role. The consumer stack creates the destination bucket, Lambda function, scheduling, monitoring, and a VPC-bound S3 Gateway Endpoint.

## Prerequisites

- Two AWS accounts in the same AWS Organisation
- AWS CLI v2 configured with credentials for both accounts
- Python 3.12+ (for Lambda packaging)
- An existing VPC with private subnets in the consumer account
- Route table IDs for those subnets

## Repository Structure

```
service-catalog/
├── producer/
│   └── template.yaml        # Producer account CloudFormation template
├── consumer/
│   └── template.yaml        # Consumer account CloudFormation template
├── lambda/
│   ├── Makefile              # Build automation
│   ├── requirements.txt
│   ├── src/                  # Lambda source code
│   └── tests/                # Unit tests
└── docs/
    ├── architecture.md       # Detailed architecture and security analysis
    ├── deployment-guide.md   # Step-by-step deployment walkthrough
    └── runbook.md            # Operational procedures and troubleshooting
```

## Deploying as Service Catalog Products

This section covers the full workflow: uploading templates, creating a portfolio, registering products, granting access, adding launch constraints, and provisioning.

### Step 1: Package the Lambda Function

```bash
cd service-catalog/lambda
make build
```

Upload the zip to an S3 bucket in the consumer account (or a shared artifacts account):

```bash
aws s3 cp build/s3-hydration-lambda.zip \
  s3://my-artifacts-bucket/s3-hydration/lambda/s3-hydration-lambda.zip
```

### Step 2: Upload Templates to S3

Service Catalog requires templates to be accessible via S3 URLs.

```bash
aws s3 cp service-catalog/producer/template.yaml \
  s3://my-artifacts-bucket/s3-hydration/templates/producer/template.yaml

aws s3 cp service-catalog/consumer/template.yaml \
  s3://my-artifacts-bucket/s3-hydration/templates/consumer/template.yaml
```

### Step 3: Create a Portfolio

Create a portfolio in the account that will manage the Service Catalog products (typically a shared-services or platform account):

```bash
aws servicecatalog create-portfolio \
  --display-name "S3 Hydration" \
  --provider-name "Platform Team" \
  --description "Cross-account S3 data transfer with re-encryption"
```

Note the `Id` from the response:

```json
{
  "PortfolioDetail": {
    "Id": "port-abc123def456",
    ...
  }
}
```

Export it for use in subsequent commands:

```bash
export PORTFOLIO_ID=port-abc123def456
```

### Step 4: Create the Producer Product

```bash
aws servicecatalog create-product \
  --name "S3 Hydration - Producer" \
  --owner "Platform Team" \
  --description "Creates the source S3 bucket, KMS key, and cross-account read role in the producer account." \
  --product-type CLOUD_FORMATION_TEMPLATE \
  --provisioning-artifact-parameters '{
    "Name": "v1.0.0",
    "Description": "Initial release",
    "Info": {
      "LoadTemplateFromURL": "https://my-artifacts-bucket.s3.amazonaws.com/s3-hydration/templates/producer/template.yaml"
    },
    "Type": "CLOUD_FORMATION_TEMPLATE"
  }'
```

Note the `ProductId` and `ProvisioningArtifactDetail.Id` from the response:

```bash
export PRODUCER_PRODUCT_ID=prod-abc123
export PRODUCER_PA_ID=pa-abc123
```

### Step 5: Create the Consumer Product

```bash
aws servicecatalog create-product \
  --name "S3 Hydration - Consumer" \
  --owner "Platform Team" \
  --description "Creates the destination S3 bucket, transfer Lambda, VPC endpoint, scheduling, and monitoring in the consumer account." \
  --product-type CLOUD_FORMATION_TEMPLATE \
  --provisioning-artifact-parameters '{
    "Name": "v1.0.0",
    "Description": "Initial release",
    "Info": {
      "LoadTemplateFromURL": "https://my-artifacts-bucket.s3.amazonaws.com/s3-hydration/templates/consumer/template.yaml"
    },
    "Type": "CLOUD_FORMATION_TEMPLATE"
  }'
```

```bash
export CONSUMER_PRODUCT_ID=prod-def456
export CONSUMER_PA_ID=pa-def456
```

### Step 6: Associate Products with the Portfolio

```bash
aws servicecatalog associate-product-with-portfolio \
  --product-id "$PRODUCER_PRODUCT_ID" \
  --portfolio-id "$PORTFOLIO_ID"

aws servicecatalog associate-product-with-portfolio \
  --product-id "$CONSUMER_PRODUCT_ID" \
  --portfolio-id "$PORTFOLIO_ID"
```

### Step 7: Create Launch Constraints

Launch constraints define the IAM role that Service Catalog assumes when provisioning the product. This lets end users launch products without needing broad IAM permissions themselves.

Create a launch role in each target account with permissions to create the resources defined in the templates (S3, KMS, IAM, Lambda, EventBridge, CloudWatch, SQS, EC2 VPC Endpoints, SNS). The role must trust the Service Catalog service principal:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "servicecatalog.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

Then associate the constraint with each product:

```bash
aws servicecatalog create-constraint \
  --portfolio-id "$PORTFOLIO_ID" \
  --product-id "$PRODUCER_PRODUCT_ID" \
  --type LAUNCH \
  --parameters '{"RoleArn": "arn:aws:iam::111111111111:role/ServiceCatalogLaunchRole"}'

aws servicecatalog create-constraint \
  --portfolio-id "$PORTFOLIO_ID" \
  --product-id "$CONSUMER_PRODUCT_ID" \
  --type LAUNCH \
  --parameters '{"RoleArn": "arn:aws:iam::222222222222:role/ServiceCatalogLaunchRole"}'
```

### Step 8: Share the Portfolio

Share the portfolio with the AWS Organisations OUs or specific accounts that should be able to provision the products.

**Share with an Organisational Unit:**

```bash
aws servicecatalog create-portfolio-share \
  --portfolio-id "$PORTFOLIO_ID" \
  --organization-node "Type=ORGANIZATIONAL_UNIT,Value=ou-abc1-23456789"
```

**Share with a specific account:**

```bash
aws servicecatalog create-portfolio-share \
  --portfolio-id "$PORTFOLIO_ID" \
  --account-id "333333333333"
```

### Step 9: Grant Access to End Users

In each account that receives the portfolio share, grant IAM principals (users, groups, or roles) access to the portfolio:

```bash
aws servicecatalog associate-principal-with-portfolio \
  --portfolio-id "$PORTFOLIO_ID" \
  --principal-arn "arn:aws:iam::222222222222:role/DeveloperRole" \
  --principal-type IAM_PATTERN
```

### Step 10: Provision the Products

End users (or automation) can now provision each product. **The producer must be provisioned first** since the consumer stack depends on its outputs.

#### Generate an ExternalId

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Record this value securely. The same ExternalId must be used for both stacks.

#### Provision the Producer

```bash
aws servicecatalog provision-product \
  --product-id "$PRODUCER_PRODUCT_ID" \
  --provisioning-artifact-id "$PRODUCER_PA_ID" \
  --provisioned-product-name "s3-hydration-producer" \
  --provisioning-parameters \
    '[
      {"Key": "ConsumerAccountId", "Value": "222222222222"},
      {"Key": "OrganisationId",    "Value": "o-abc123"},
      {"Key": "ExternalId",        "Value": "<generated-external-id>"}
    ]'
```

Wait for provisioning to complete:

```bash
aws servicecatalog describe-provisioned-product \
  --name "s3-hydration-producer" \
  --query "ProvisionedProductDetail.Status"
```

Retrieve the producer stack outputs:

```bash
aws servicecatalog get-provisioned-product-outputs \
  --provisioned-product-name "s3-hydration-producer"
```

Note the values for `BucketArn`, `BucketName`, `RoleArn`, and `KmsKeyArn`.

#### Provision the Consumer

```bash
aws servicecatalog provision-product \
  --product-id "$CONSUMER_PRODUCT_ID" \
  --provisioning-artifact-id "$CONSUMER_PA_ID" \
  --provisioned-product-name "s3-hydration-consumer" \
  --provisioning-parameters \
    '[
      {"Key": "ProducerAccountId",    "Value": "111111111111"},
      {"Key": "ProducerBucketName",   "Value": "<from-producer-outputs>"},
      {"Key": "ProducerBucketArn",    "Value": "<from-producer-outputs>"},
      {"Key": "CrossAccountRoleArn",  "Value": "<from-producer-outputs>"},
      {"Key": "ProducerKmsKeyArn",    "Value": "<from-producer-outputs>"},
      {"Key": "OrganisationId",       "Value": "o-abc123"},
      {"Key": "ExternalId",           "Value": "<generated-external-id>"},
      {"Key": "VpcId",                "Value": "vpc-xxx"},
      {"Key": "SubnetIds",            "Value": "subnet-aaa,subnet-bbb"},
      {"Key": "RouteTableIds",        "Value": "rtb-xxx,rtb-yyy"},
      {"Key": "LambdaS3Bucket",       "Value": "my-artifacts-bucket"},
      {"Key": "LambdaS3Key",          "Value": "s3-hydration/lambda/s3-hydration-lambda.zip"},
      {"Key": "AlarmEmail",           "Value": "ops@example.com"}
    ]'
```

If `AlarmEmail` is provided, confirm the SNS subscription via the email that arrives.

## Updating a Product Version

When templates or Lambda code change, create a new provisioning artifact (version) on the product:

```bash
aws servicecatalog create-provisioning-artifact \
  --product-id "$CONSUMER_PRODUCT_ID" \
  --parameters '{
    "Name": "v1.1.0",
    "Description": "Description of changes",
    "Info": {
      "LoadTemplateFromURL": "https://my-artifacts-bucket.s3.amazonaws.com/s3-hydration/templates/consumer/template.yaml"
    },
    "Type": "CLOUD_FORMATION_TEMPLATE"
  }'
```

Then update existing provisioned products to the new version:

```bash
aws servicecatalog update-provisioned-product \
  --provisioned-product-name "s3-hydration-consumer" \
  --product-id "$CONSUMER_PRODUCT_ID" \
  --provisioning-artifact-id "$NEW_PA_ID" \
  --provisioning-parameters \
    '[
      {"Key": "LambdaS3Key", "Value": "s3-hydration/lambda/v1.1.0/s3-hydration-lambda.zip", "UsePreviousValue": false}
    ]'
```

Parameters not listed are retained from the previous provisioning.

## Verifying a Deployment

After provisioning both products:

```bash
# Upload a test object to the producer bucket
echo "test" > /tmp/test.txt
aws s3 cp /tmp/test.txt s3://<producer-bucket-name>/test/test.txt

# Trigger the Lambda manually
aws lambda invoke \
  --function-name s3-hydration-transfer \
  --payload '{}' \
  /tmp/invoke-response.json

cat /tmp/invoke-response.json

# Confirm the object arrived in the consumer bucket
aws s3 ls s3://<consumer-bucket-name>/test/

# Verify it was re-encrypted with the consumer KMS key
aws s3api head-object \
  --bucket <consumer-bucket-name> \
  --key test/test.txt \
  --query "{Encryption: ServerSideEncryption, KmsKey: SSEKMSKeyId}"
```

## Terminating a Provisioned Product

```bash
aws servicecatalog terminate-provisioned-product \
  --provisioned-product-name "s3-hydration-consumer"
```

Terminate the consumer first, then the producer -- the consumer stack references producer resources.

## Further Reading

- [Architecture and security analysis](service-catalog/docs/architecture.md)
- [Direct CloudFormation deployment guide](service-catalog/docs/deployment-guide.md)
- [Operational runbook](service-catalog/docs/runbook.md)
