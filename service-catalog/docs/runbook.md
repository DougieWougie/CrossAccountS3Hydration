# S3 Hydration Operational Runbook

## Overview

The S3 Hydration solution performs cross-account S3 data transfer using a Consumer Pull model. A Lambda function runs on a daily EventBridge schedule (default 06:00 UTC), assumes a cross-account IAM role in the producer account, lists and copies objects from the producer bucket to the consumer bucket, and re-encrypts them under the consumer's KMS key.

Key components:

- **AWS Lambda** -- performs the object transfer, with built-in retry and idempotency logic.
- **Amazon EventBridge** -- triggers the Lambda on a daily cron schedule.
- **Amazon SQS Dead Letter Queue (DLQ)** -- captures failed Lambda invocation payloads for later inspection.
- **Amazon CloudWatch Alarms** -- monitors Lambda errors, duration, DLQ depth, and custom transfer metrics.

A sync marker object (`_s3_hydration_last_sync`) is written to the consumer bucket after each successful run to enable incremental transfers on subsequent invocations.

## Key Resources

| Resource | Name / Identifier | Description |
|---|---|---|
| Lambda Function | `s3-hydration-transfer` | Performs the cross-account S3 object transfer |
| EventBridge Rule | `s3-hydration-schedule` | Daily cron trigger (default 06:00 UTC) |
| SQS DLQ | `s3-hydration-dlq` | Dead letter queue for failed Lambda invocations |
| CloudWatch Log Group | `/aws/lambda/s3-hydration-transfer` | Lambda execution logs |
| SNS Topic | `s3-hydration-alarms` | Alarm notification target |
| Custom Metrics Namespace | `S3Hydration` | Custom CloudWatch metrics (ObjectsTransferred, ObjectsFailed, etc.) |

## Alarm Response Procedures

### LambdaErrorAlarm

**What it means:** The Lambda invocation itself failed -- it either threw an unhandled exception or was throttled.

**Response steps:**

1. Check the CloudWatch log group `/aws/lambda/s3-hydration-transfer` for error messages and stack traces from the failing invocation.
2. Check the DLQ (`s3-hydration-dlq`) for the failed event payload.
3. Verify that the producer bucket and cross-account role still exist and have not been deleted or renamed.
4. Verify the cross-account role trust policy in the producer account has not changed. It must still trust the consumer Lambda execution role and include the correct ExternalId and OrganisationId conditions.
5. If the failure was transient (for example, a temporary network issue or throttling), re-trigger the Lambda manually (see Common Operations below).

### LambdaDurationAlarm

**What it means:** The Lambda execution time exceeded 13 minutes, approaching the hard 15-minute Lambda timeout.

**Response steps:**

1. Check whether the volume of data in the producer bucket has increased significantly since the last successful run.
2. Consider increasing the Lambda memory allocation (which also increases proportional CPU).
3. Check for network issues, especially if the Lambda runs inside a VPC.
4. Determine whether individual objects are very large. Extremely large objects take longer to copy and may push overall duration past the limit.

### DlqDepthAlarm

**What it means:** One or more failed Lambda invocations have been sent to the dead letter queue rather than being retried successfully.

**Response steps:**

1. Inspect the DLQ messages to identify the failing event payload (see Common Operations below).
2. Correlate the message timestamp with CloudWatch logs to find the corresponding invocation and its error output.
3. Identify and fix the root cause (permission change, resource deletion, etc.).
4. Re-trigger the Lambda manually after the fix is in place.

### ObjectsFailedAlarm

**What it means:** The Lambda invocation itself succeeded, but one or more individual objects failed to transfer during execution.

**Response steps:**

1. Search the CloudWatch logs for `ObjectTransferError` entries. These entries include the failing object key and error details.
2. Identify the specific keys that failed.
3. Check whether those objects are corrupted, excessively large, or have unusual permissions in the producer bucket.
4. Re-trigger the Lambda manually. The idempotency logic will skip objects that already transferred successfully and retry only the ones that are missing or incomplete.

## Common Operations

### Manual Trigger

Invoke the Lambda function directly:

```bash
aws lambda invoke \
  --function-name s3-hydration-transfer \
  --payload '{}' \
  --cli-binary-format raw-in-base64-out \
  response.json
cat response.json
```

### Check Last Sync Time

Read the sync marker from the consumer bucket:

```bash
aws s3 cp s3://<consumer-bucket>/_s3_hydration_last_sync -
```

### Reset Sync (Full Re-transfer)

Delete the sync marker to force a full re-transfer on the next invocation:

```bash
aws s3 rm s3://<consumer-bucket>/_s3_hydration_last_sync
```

Then trigger the Lambda manually. This will re-transfer all objects, but idempotency checks will skip objects that already exist with the same size.

### View Recent Logs

Tail the last hour of Lambda logs:

```bash
aws logs tail /aws/lambda/s3-hydration-transfer --since 1h --format short
```

### Check DLQ Messages

Receive messages from the dead letter queue without removing them (visibility timeout set to zero):

```bash
aws sqs receive-message \
  --queue-url <dlq-url> \
  --max-number-of-messages 10 \
  --visibility-timeout 0
```

### View Custom Metrics

Retrieve the ObjectsTransferred metric for the last 24 hours:

```bash
aws cloudwatch get-metric-statistics \
  --namespace S3Hydration \
  --metric-name ObjectsTransferred \
  --start-time $(date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 86400 \
  --statistics Sum
```

## Troubleshooting

### "Failed to assume producer role"

- Verify the cross-account role ARN is correct in the consumer stack parameters.
- Check the trust policy in the producer account and confirm it includes the consumer Lambda execution role as a trusted principal.
- Verify the ExternalId matches between the producer and consumer stacks.
- Check that the OrganisationId condition in the trust policy matches the actual AWS Organizations ID.

### "Access Denied" on GetObject

- Verify the producer bucket policy allows the cross-account role to perform `s3:GetObject`.
- Check that the producer KMS key policy grants `kms:Decrypt` to the cross-account role.
- Verify the objects actually exist in the producer bucket at the expected key prefixes.

### "Access Denied" on PutObject

- Verify the Lambda execution role has `s3:PutObject` permission on the consumer bucket.
- Check the consumer KMS key policy grants `kms:Encrypt` and `kms:GenerateDataKey` to the Lambda execution role.

### Lambda Timeout

- Check the total number and sizes of objects being transferred.
- Consider increasing the Lambda memory allocation (which also increases proportional CPU and network throughput).
- If the Lambda is deployed inside a VPC, check for network connectivity issues.
- Verify the S3 Gateway Endpoint is correctly configured in the VPC route tables.

### No Objects Transferred

- Check whether the sync marker timestamp is ahead of all object `LastModified` timestamps in the producer bucket. If so, no objects qualify for incremental transfer.
- Reset the sync marker (see Common Operations above) and re-trigger.
- Verify the transfer prefix configured in the stack parameters matches the actual object key prefixes in the producer bucket.

## Maintenance

### Rotating the External ID

1. Generate a new ExternalId value.
2. Update the producer stack with the new ExternalId parameter.
3. Update the consumer stack with the same new ExternalId parameter.
4. Trigger a manual test invocation to verify the cross-account assume-role call still succeeds.

### Updating Lambda Code

1. Build the new deployment package:
   ```bash
   cd service-catalog/lambda && make build
   ```
2. Upload the new zip artifact to the S3 bucket used for Lambda code storage.
3. Update the consumer stack, or apply the change directly:
   ```bash
   aws lambda update-function-code \
     --function-name s3-hydration-transfer \
     --s3-bucket <code-bucket> \
     --s3-key <new-zip-key>
   ```

### KMS Key Rotation

Both the producer and consumer KMS keys have automatic annual rotation enabled. No manual action is required. AWS manages the rotation of the backing key material transparently.
