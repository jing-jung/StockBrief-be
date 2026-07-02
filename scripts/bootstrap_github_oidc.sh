#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Bootstrap StockBrief backend deployment prerequisites for a new AWS account.

Creates or updates:
  - S3 remote Terraform state bucket
  - DynamoDB Terraform lock table
  - GitHub Actions OIDC provider
  - GitHub Actions deploy IAM role
  - GitHub Environment variables used by backend-dev-deploy.yml

Example:
  scripts/bootstrap_github_oidc.sh \
    --environment dev \
    --region ap-northeast-2 \
    --github-owner 80-hours-a-week \
    --github-repo StockBrief-be \
    --alarm-emails-json '["ops@example.com"]'

Options:
  --environment VALUE       Environment name. Default: dev
  --region VALUE            AWS region. Default: ap-northeast-2
  --github-owner VALUE      GitHub organization or owner. Default: 80-hours-a-week
  --github-repo VALUE       GitHub repository name. Default: StockBrief-be
  --github-branch VALUE     GitHub branch allowed to assume the role. Default: main
  --state-bucket VALUE      Terraform state bucket. Default: stockbrief-terraform-state-<account>-<region>
  --lock-table VALUE        Terraform lock table. Default: stockbrief-terraform-locks
  --role-name VALUE         IAM deploy role name. Default: stockbrief-<environment>-github-actions-deploy
  --alarm-emails-json VALUE JSON array for OPERATIONAL_ALARM_EMAILS_JSON. Default: []
  --write-deploy-profile-vars
                            Also write TF_BACKEND_CONFIG_HCL and TFVARS_JSON to the
                            selected GitHub Environment. Default: false
  --vpc-id VALUE            Optional VPC ID for generated TFVARS_JSON.
  --db-subnet-ids VALUE     Optional comma-separated DB subnet IDs for generated TFVARS_JSON.
  --lambda-subnet-ids VALUE Optional comma-separated Lambda subnet IDs for generated TFVARS_JSON.
  --vpc-endpoint-route-table-ids VALUE
                            Optional comma-separated route table IDs for generated TFVARS_JSON.
  --cognito-domain-prefix VALUE
                            Optional Cognito Hosted UI domain prefix for generated TFVARS_JSON.
  --dry-run                 Print planned write actions without changing AWS or GitHub resources.
  -h, --help                Show this help.
USAGE
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

run_change() {
  if [ "$dry_run" = "true" ]; then
    printf 'DRY RUN: %s\n' "$*"
    return 0
  fi

  "$@" >/dev/null
}

set_github_variable() {
  variable_name="$1"
  variable_value="$2"

  if [ "$dry_run" = "true" ]; then
    printf 'DRY RUN: gh variable set %s --repo %s --env %s --body <value>\n' \
      "$variable_name" "$repo_full_name" "$environment"
    return 0
  fi

  gh variable set "$variable_name" --repo "$repo_full_name" --env "$environment" --body "$variable_value" >/dev/null
}

environment="dev"
region="ap-northeast-2"
github_owner="80-hours-a-week"
github_repo="StockBrief-be"
github_branch="main"
state_bucket=""
lock_table="stockbrief-terraform-locks"
role_name=""
alarm_emails_json="[]"
write_deploy_profile_vars="false"
vpc_id=""
db_subnet_ids_csv=""
lambda_subnet_ids_csv=""
vpc_endpoint_route_table_ids_csv=""
cognito_domain_prefix=""
dry_run="false"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --environment)
      environment="$2"
      shift 2
      ;;
    --region)
      region="$2"
      shift 2
      ;;
    --github-owner)
      github_owner="$2"
      shift 2
      ;;
    --github-repo)
      github_repo="$2"
      shift 2
      ;;
    --github-branch)
      github_branch="$2"
      shift 2
      ;;
    --state-bucket)
      state_bucket="$2"
      shift 2
      ;;
    --lock-table)
      lock_table="$2"
      shift 2
      ;;
    --role-name)
      role_name="$2"
      shift 2
      ;;
    --alarm-emails-json)
      alarm_emails_json="$2"
      shift 2
      ;;
    --write-deploy-profile-vars)
      write_deploy_profile_vars="true"
      shift
      ;;
    --vpc-id)
      vpc_id="$2"
      shift 2
      ;;
    --db-subnet-ids)
      db_subnet_ids_csv="$2"
      shift 2
      ;;
    --lambda-subnet-ids)
      lambda_subnet_ids_csv="$2"
      shift 2
      ;;
    --vpc-endpoint-route-table-ids)
      vpc_endpoint_route_table_ids_csv="$2"
      shift 2
      ;;
    --cognito-domain-prefix)
      cognito_domain_prefix="$2"
      shift 2
      ;;
    --dry-run)
      dry_run="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

require_command aws
require_command gh
require_command python3
require_command sed

if ! python3 - "$alarm_emails_json" <<'PY'
import json
import sys

try:
    value = json.loads(sys.argv[1])
except json.JSONDecodeError as exc:
    print(f"--alarm-emails-json must be valid JSON: {exc}", file=sys.stderr)
    sys.exit(1)

if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
    print(
        "--alarm-emails-json must be a JSON array of strings, "
        "for example '[\"ops@example.com\"]'.",
        file=sys.stderr,
    )
    sys.exit(1)
PY
then
  exit 1
fi

account_id="$(aws sts get-caller-identity --query Account --output text)"
if [ -z "$account_id" ] || [ "$account_id" = "None" ]; then
  echo "Could not determine AWS account id." >&2
  exit 1
fi

if [ -z "$state_bucket" ]; then
  state_bucket="stockbrief-terraform-state-${account_id}-${region}"
fi

if [ -z "$role_name" ]; then
  role_name="stockbrief-${environment}-github-actions-deploy"
fi

repo_full_name="${github_owner}/${github_repo}"
oidc_provider_url="token.actions.githubusercontent.com"
oidc_provider_arn="arn:aws:iam::${account_id}:oidc-provider/${oidc_provider_url}"
role_arn="arn:aws:iam::${account_id}:role/${role_name}"
resource_name_prefix="stockbrief-${environment}"
env_upper="$(printf '%s' "$environment" | tr '[:lower:]' '[:upper:]' | tr '-' '_')"
deploy_role_var="AWS_${env_upper}_DEPLOY_ROLE_ARN"
state_key="stockbrief/${environment}/terraform.tfstate"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

echo "Bootstrapping StockBrief ${environment} deployment in AWS account ${account_id} (${region})"

if [ "$dry_run" = "true" ]; then
  echo "Dry-run mode enabled. AWS and GitHub write actions will be logged only."
fi

if aws s3api head-bucket --bucket "$state_bucket" >/dev/null 2>&1; then
  echo "S3 state bucket already exists: ${state_bucket}"
else
  echo "Creating S3 state bucket: ${state_bucket}"
  if [ "$region" = "us-east-1" ]; then
    run_change aws s3api create-bucket --bucket "$state_bucket"
  else
    run_change aws s3api create-bucket \
      --bucket "$state_bucket" \
      --region "$region" \
      --create-bucket-configuration "LocationConstraint=${region}"
  fi
fi

run_change aws s3api put-bucket-versioning \
  --bucket "$state_bucket" \
  --versioning-configuration Status=Enabled

run_change aws s3api put-bucket-encryption \
  --bucket "$state_bucket" \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

run_change aws s3api put-public-access-block \
  --bucket "$state_bucket" \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

if aws dynamodb describe-table --table-name "$lock_table" --region "$region" >/dev/null 2>&1; then
  echo "DynamoDB lock table already exists: ${lock_table}"
else
  echo "Creating DynamoDB lock table: ${lock_table}"
  run_change aws dynamodb create-table \
    --table-name "$lock_table" \
    --attribute-definitions AttributeName=LockID,AttributeType=S \
    --key-schema AttributeName=LockID,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST \
    --region "$region"
  if [ "$dry_run" != "true" ]; then
    aws dynamodb wait table-exists --table-name "$lock_table" --region "$region"
  fi
fi

if aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$oidc_provider_arn" >/dev/null 2>&1; then
  echo "GitHub OIDC provider already exists: ${oidc_provider_arn}"
else
  echo "Creating GitHub OIDC provider: ${oidc_provider_url}"
  run_change aws iam create-open-id-connect-provider \
    --url "https://${oidc_provider_url}" \
    --client-id-list sts.amazonaws.com \
    --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
fi

owner_escaped="$(json_escape "$github_owner")"
repo_escaped="$(json_escape "$github_repo")"
branch_escaped="$(json_escape "$github_branch")"
environment_escaped="$(json_escape "$environment")"

cat >"${tmpdir}/trust-policy.json" <<POLICY
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "${oidc_provider_arn}"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "${oidc_provider_url}:aud": "sts.amazonaws.com",
          "${oidc_provider_url}:sub": "repo:${owner_escaped}/${repo_escaped}:environment:${environment_escaped}"
        }
      }
    }
  ]
}
POLICY

cat >"${tmpdir}/deploy-policy.json" <<POLICY
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "TerraformStateBucket",
      "Effect": "Allow",
      "Action": [
        "s3:CreateBucket",
        "s3:GetBucketLocation",
        "s3:GetBucketVersioning",
        "s3:GetEncryptionConfiguration",
        "s3:GetObject",
        "s3:ListBucket",
        "s3:PutEncryptionConfiguration",
        "s3:PutBucketPublicAccessBlock",
        "s3:PutBucketVersioning",
        "s3:PutObject",
        "s3:DeleteObject"
      ],
      "Resource": [
        "arn:aws:s3:::${state_bucket}",
        "arn:aws:s3:::${state_bucket}/*"
      ]
    },
    {
      "Sid": "TerraformLockTable",
      "Effect": "Allow",
      "Action": [
        "dynamodb:CreateTable",
        "dynamodb:DescribeTable",
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:DeleteItem"
      ],
      "Resource": "arn:aws:dynamodb:${region}:${account_id}:table/${lock_table}"
    },
    {
      "Sid": "DeployIamRolesByPrefix",
      "Effect": "Allow",
      "Action": [
        "iam:AttachRolePolicy",
        "iam:CreateRole",
        "iam:DeleteRole",
        "iam:DeleteRolePolicy",
        "iam:DetachRolePolicy",
        "iam:GetRole",
        "iam:GetRolePolicy",
        "iam:ListAttachedRolePolicies",
        "iam:ListRolePolicies",
        "iam:ListRoleTags",
        "iam:PutRolePolicy",
        "iam:TagRole",
        "iam:UntagRole",
        "iam:UpdateAssumeRolePolicy"
      ],
      "Resource": "arn:aws:iam::${account_id}:role/${resource_name_prefix}-*"
    },
    {
      "Sid": "DeployPassRolesByPrefix",
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": "arn:aws:iam::${account_id}:role/${resource_name_prefix}-*",
      "Condition": {
        "StringEquals": {
          "iam:PassedToService": [
            "bedrock-agentcore.amazonaws.com",
            "lambda.amazonaws.com",
            "rds.amazonaws.com",
            "scheduler.amazonaws.com"
          ]
        }
      }
    },
    {
      "Sid": "DeployRdsServiceLinkedRole",
      "Effect": "Allow",
      "Action": "iam:CreateServiceLinkedRole",
      "Resource": "arn:aws:iam::*:role/aws-service-role/rds.amazonaws.com/AWSServiceRoleForRDS",
      "Condition": {
        "StringLike": {
          "iam:AWSServiceName": "rds.amazonaws.com"
        }
      }
    },
    {
      "Sid": "DeployAwsManagedPolicyRead",
      "Effect": "Allow",
      "Action": "iam:GetPolicy",
      "Resource": [
        "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
      ]
    },
    {
      "Sid": "DeployLambdaFunctionByPrefix",
      "Effect": "Allow",
      "Action": [
        "lambda:AddPermission",
        "lambda:CreateFunction",
        "lambda:DeleteFunction",
        "lambda:GetFunction",
        "lambda:GetFunctionCodeSigningConfig",
        "lambda:GetPolicy",
        "lambda:ListTags",
        "lambda:ListVersionsByFunction",
        "lambda:RemovePermission",
        "lambda:TagResource",
        "lambda:UntagResource",
        "lambda:UpdateFunctionCode",
        "lambda:UpdateFunctionConfiguration"
      ],
      "Resource": "arn:aws:lambda:${region}:${account_id}:function:${resource_name_prefix}-*"
    },
    {
      "Sid": "DeployLogGroupsByPrefix",
      "Effect": "Allow",
      "Action": [
        "logs:DeleteLogGroup",
        "logs:ListTagsForResource",
        "logs:PutRetentionPolicy",
        "logs:TagResource",
        "logs:UntagResource"
      ],
      "Resource": [
        "arn:aws:logs:${region}:${account_id}:log-group:/aws/amplify/${resource_name_prefix}-web",
        "arn:aws:logs:${region}:${account_id}:log-group:/aws/amplify/${resource_name_prefix}-web:*",
        "arn:aws:logs:${region}:${account_id}:log-group:/aws/apigateway/${resource_name_prefix}-http-api",
        "arn:aws:logs:${region}:${account_id}:log-group:/aws/apigateway/${resource_name_prefix}-http-api:*",
        "arn:aws:logs:${region}:${account_id}:log-group:/aws/bedrock-agentcore/${resource_name_prefix//-/_}_agent",
        "arn:aws:logs:${region}:${account_id}:log-group:/aws/bedrock-agentcore/${resource_name_prefix//-/_}_agent:*",
        "arn:aws:logs:${region}:${account_id}:log-group:/aws/lambda/${resource_name_prefix}-api",
        "arn:aws:logs:${region}:${account_id}:log-group:/aws/lambda/${resource_name_prefix}-api:*",
        "arn:aws:logs:${region}:${account_id}:log-group:/aws/rds/${resource_name_prefix}-postgres",
        "arn:aws:logs:${region}:${account_id}:log-group:/aws/rds/${resource_name_prefix}-postgres:*"
      ]
    },
    {
      "Sid": "DeployCloudWatchAlarmsByPrefix",
      "Effect": "Allow",
      "Action": [
        "cloudwatch:DeleteAlarms",
        "cloudwatch:ListTagsForResource",
        "cloudwatch:PutMetricAlarm",
        "cloudwatch:TagResource",
        "cloudwatch:UntagResource"
      ],
      "Resource": "arn:aws:cloudwatch:${region}:${account_id}:alarm:${resource_name_prefix}-*"
    },
    {
      "Sid": "DeployIngestionRawBucketByPrefix",
      "Effect": "Allow",
      "Action": [
        "s3:CreateBucket",
        "s3:DeleteBucket",
        "s3:DeleteBucketWebsite",
        "s3:GetAccelerateConfiguration",
        "s3:GetBucketAcl",
        "s3:GetBucketCors",
        "s3:GetBucketLocation",
        "s3:GetBucketLogging",
        "s3:GetBucketObjectLockConfiguration",
        "s3:GetBucketOwnershipControls",
        "s3:GetBucketPolicy",
        "s3:GetBucketPolicyStatus",
        "s3:GetBucketPublicAccessBlock",
        "s3:GetBucketRequestPayment",
        "s3:GetBucketTagging",
        "s3:GetBucketVersioning",
        "s3:GetBucketWebsite",
        "s3:GetEncryptionConfiguration",
        "s3:GetLifecycleConfiguration",
        "s3:GetReplicationConfiguration",
        "s3:ListBucket",
        "s3:PutBucketPublicAccessBlock",
        "s3:PutBucketTagging",
        "s3:PutBucketVersioning",
        "s3:PutEncryptionConfiguration",
        "s3:PutLifecycleConfiguration"
      ],
      "Resource": [
        "arn:aws:s3:::${resource_name_prefix}-raw-${account_id}-${region}",
        "arn:aws:s3:::${resource_name_prefix}-raw-${account_id}-${region}/*"
      ]
    },
    {
      "Sid": "DeploySchedulesByPrefix",
      "Effect": "Allow",
      "Action": [
        "scheduler:CreateSchedule",
        "scheduler:DeleteSchedule",
        "scheduler:GetSchedule",
        "scheduler:ListTagsForResource",
        "scheduler:TagResource",
        "scheduler:UntagResource",
        "scheduler:UpdateSchedule"
      ],
      "Resource": "arn:aws:scheduler:${region}:${account_id}:schedule/default/${resource_name_prefix}-*"
    },
    {
      "Sid": "DeploySecretsByPrefix",
      "Effect": "Allow",
      "Action": [
        "secretsmanager:CreateSecret",
        "secretsmanager:DeleteSecret",
        "secretsmanager:DescribeSecret",
        "secretsmanager:GetResourcePolicy",
        "secretsmanager:GetSecretValue",
        "secretsmanager:ListSecretVersionIds",
        "secretsmanager:PutSecretValue",
        "secretsmanager:TagResource",
        "secretsmanager:UntagResource",
        "secretsmanager:UpdateSecret"
      ],
      "Resource": "arn:aws:secretsmanager:${region}:${account_id}:secret:${resource_name_prefix}/*"
    },
    {
      "Sid": "DeployRdsManagedMasterUserSecret",
      "Effect": "Allow",
      "Action": [
        "secretsmanager:CreateSecret",
        "secretsmanager:DescribeSecret",
        "secretsmanager:TagResource"
      ],
      "Resource": "arn:aws:secretsmanager:${region}:${account_id}:secret:rds!db-*"
    },
    {
      "Sid": "DeploySnsTopicsByPrefix",
      "Effect": "Allow",
      "Action": [
        "sns:CreateTopic",
        "sns:DeleteTopic",
        "sns:GetTopicAttributes",
        "sns:ListSubscriptionsByTopic",
        "sns:ListTagsForResource",
        "sns:SetTopicAttributes",
        "sns:Subscribe",
        "sns:TagResource",
        "sns:UntagResource"
      ],
      "Resource": "arn:aws:sns:${region}:${account_id}:${resource_name_prefix}-*"
    },
    {
      "Sid": "DeploySqsQueuesByPrefix",
      "Effect": "Allow",
      "Action": [
        "sqs:CreateQueue",
        "sqs:DeleteQueue",
        "sqs:GetQueueAttributes",
        "sqs:ListQueueTags",
        "sqs:SetQueueAttributes",
        "sqs:TagQueue",
        "sqs:UntagQueue"
      ],
      "Resource": "arn:aws:sqs:${region}:${account_id}:${resource_name_prefix}-*"
    },
    {
      "Sid": "DevBackendDeploymentWildcardFallback",
      "Effect": "Allow",
      "Action": [
        "apigateway:*",
        "amplify:CreateApp",
        "amplify:CreateBranch",
        "amplify:DeleteApp",
        "amplify:DeleteBranch",
        "amplify:GetApp",
        "amplify:GetBranch",
        "amplify:ListTagsForResource",
        "amplify:TagResource",
        "amplify:UntagResource",
        "amplify:UpdateApp",
        "amplify:UpdateBranch",
        "cloudformation:CreateStack",
        "cloudformation:DeleteStack",
        "cloudformation:DescribeStackEvents",
        "cloudformation:DescribeStacks",
        "cloudformation:GetTemplate",
        "cloudformation:ListStackResources",
        "cloudformation:UpdateStack",
        "cloudformation:ValidateTemplate",
        "cloudwatch:DescribeAlarms",
        "cognito-idp:CreateUserPool",
        "cognito-idp:CreateUserPoolClient",
        "cognito-idp:CreateUserPoolDomain",
        "cognito-idp:DeleteUserPool",
        "cognito-idp:DeleteUserPoolClient",
        "cognito-idp:DeleteUserPoolDomain",
        "cognito-idp:DescribeUserPool",
        "cognito-idp:DescribeUserPoolClient",
        "cognito-idp:DescribeUserPoolDomain",
        "cognito-idp:GetUserPoolMfaConfig",
        "cognito-idp:ListTagsForResource",
        "cognito-idp:TagResource",
        "cognito-idp:UntagResource",
        "cognito-idp:UpdateUserPool",
        "cognito-idp:UpdateUserPoolClient",
        "ec2:AuthorizeSecurityGroupEgress",
        "ec2:AuthorizeSecurityGroupIngress",
        "ec2:CreateSecurityGroup",
        "ec2:CreateNatGateway",
        "ec2:CreateRoute",
        "ec2:CreateRouteTable",
        "ec2:CreateInternetGateway",
        "ec2:CreateSubnet",
        "ec2:CreateTags",
        "ec2:CreateVpcEndpoint",
        "ec2:AllocateAddress",
        "ec2:AttachInternetGateway",
        "ec2:AssociateRouteTable",
        "ec2:DeleteInternetGateway",
        "ec2:DeleteNatGateway",
        "ec2:DeleteRoute",
        "ec2:DeleteRouteTable",
        "ec2:DeleteSecurityGroup",
        "ec2:DeleteSubnet",
        "ec2:DeleteTags",
        "ec2:DeleteVpcEndpoints",
        "ec2:DescribeAddresses",
        "ec2:DescribeAddressesAttribute",
        "ec2:DescribeAvailabilityZones",
        "ec2:DescribeInternetGateways",
        "ec2:DescribeNatGateways",
        "ec2:DescribeNetworkInterfaces",
        "ec2:DescribePrefixLists",
        "ec2:DescribeRouteTables",
        "ec2:DescribeSecurityGroups",
        "ec2:DescribeSecurityGroupRules",
        "ec2:DescribeSubnets",
        "ec2:DescribeVpcEndpointServices",
        "ec2:DescribeVpcEndpoints",
        "ec2:DescribeVpcs",
        "ec2:DetachInternetGateway",
        "ec2:DisassociateRouteTable",
        "ec2:ModifySecurityGroupRules",
        "ec2:ModifyVpcEndpoint",
        "ec2:ReleaseAddress",
        "ec2:ReplaceRoute",
        "ec2:RevokeSecurityGroupEgress",
        "ec2:RevokeSecurityGroupIngress",
        "kms:CreateAlias",
        "kms:CreateKey",
        "kms:DeleteAlias",
        "kms:DescribeKey",
        "kms:EnableKeyRotation",
        "kms:GetKeyPolicy",
        "kms:GetKeyRotationStatus",
        "kms:ListAliases",
        "kms:ListResourceTags",
        "kms:PutKeyPolicy",
        "kms:ScheduleKeyDeletion",
        "kms:TagResource",
        "kms:UntagResource",
        "kms:UpdateAlias",
        "logs:CreateLogGroup",
        "logs:CreateLogDelivery",
        "logs:DeleteLogDelivery",
        "logs:DescribeLogGroups",
        "logs:DescribeResourcePolicies",
        "logs:GetLogDelivery",
        "logs:ListLogDeliveries",
        "logs:PutResourcePolicy",
        "logs:TagResource",
        "logs:UpdateLogDelivery",
        "rds:AddTagsToResource",
        "rds:CreateDBInstance",
        "rds:CreateDBProxy",
        "rds:CreateDBSubnetGroup",
        "rds:DeleteDBInstance",
        "rds:DeleteDBProxy",
        "rds:DeleteDBSubnetGroup",
        "rds:DeregisterDBProxyTargets",
        "rds:DescribeDBInstances",
        "rds:DescribeDBProxies",
        "rds:DescribeDBProxyTargetGroups",
        "rds:DescribeDBProxyTargets",
        "rds:DescribeDBSubnetGroups",
        "rds:ListTagsForResource",
        "rds:ModifyDBInstance",
        "rds:ModifyDBProxy",
        "rds:ModifyDBProxyTargetGroup",
        "rds:RegisterDBProxyTargets",
        "rds:RemoveTagsFromResource",
        "sns:GetSubscriptionAttributes",
        "sns:Unsubscribe",
        "sts:GetCallerIdentity"
      ],
      "Resource": "*"
    }
  ]
}
POLICY

if [ "$write_deploy_profile_vars" = "true" ]; then
  cat >"${tmpdir}/backend.hcl" <<BACKEND
bucket         = "${state_bucket}"
key            = "${state_key}"
region         = "${region}"
dynamodb_table = "${lock_table}"
encrypt        = true
BACKEND

  if ! python3 - \
    "$environment" \
    "$region" \
    "$account_id" \
    "$vpc_id" \
    "$db_subnet_ids_csv" \
    "$lambda_subnet_ids_csv" \
    "$vpc_endpoint_route_table_ids_csv" \
    "$cognito_domain_prefix" >"${tmpdir}/tfvars.json" <<'PY'
import json
import sys


def csv_list(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


environment = sys.argv[1]
region = sys.argv[2]
account_id = sys.argv[3]
vpc_id = sys.argv[4].strip()
db_subnet_ids = csv_list(sys.argv[5])
lambda_subnet_ids = csv_list(sys.argv[6])
route_table_ids = csv_list(sys.argv[7])
cognito_domain_prefix = sys.argv[8].strip() or f"stockbrief-{environment}-{account_id}"

network_inputs_present = any(
    [vpc_id, db_subnet_ids, lambda_subnet_ids, route_table_ids]
)
if network_inputs_present:
    errors = []
    if not vpc_id:
        errors.append("--vpc-id is required when generating a networked TFVARS_JSON.")
    if len(db_subnet_ids) < 2:
        errors.append("--db-subnet-ids must include at least two subnet IDs.")
    if not lambda_subnet_ids:
        errors.append("--lambda-subnet-ids must include at least one subnet ID.")
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        sys.exit(1)

profile = {
    "environment": environment,
    "aws_region": region,
    "api_lambda_package_path": "../../dist/stockbrief-api-lambda.zip",
    "cors_allowed_origins": (
        "http://localhost:3000,http://127.0.0.1:3000,"
        "http://localhost:3001,http://127.0.0.1:3001"
    ),
    "chat_provider": "mock",
    "enable_amplify": False,
    "amplify_repository_url": "https://github.com/80-hours-a-week/StockBrief-fe",
    "amplify_branch_name": "main",
    "amplify_cognito_redirect_uri": "",
    "cognito_callback_urls": [
        "http://localhost:3000/auth/callback",
        "http://localhost:3001/auth/callback",
        "http://127.0.0.1:3000/auth/callback",
        "http://127.0.0.1:3001/auth/callback",
    ],
    "cognito_logout_urls": [
        "http://localhost:3000/account",
        "http://localhost:3001/account",
        "http://127.0.0.1:3000/account",
        "http://127.0.0.1:3001/account",
    ],
    "cognito_hosted_ui_domain_prefix": cognito_domain_prefix,
    "db_instance_class": "db.t4g.micro",
    "db_allocated_storage_gb": 20,
    "db_deletion_protection": False,
    "db_skip_final_snapshot": True,
    "db_backup_retention_period": 1,
    "vpc_id": vpc_id,
    "db_subnet_ids": db_subnet_ids,
    "db_security_group_ids": [],
    "rds_proxy_security_group_ids": [],
    "enable_rds_proxy": False,
    "lambda_subnet_ids": lambda_subnet_ids,
    "lambda_security_group_ids": [],
    "vpc_endpoint_route_table_ids": route_table_ids,
    "enable_lambda_nat_egress": False,
    "lambda_nat_create_public_subnet": False,
    "lambda_nat_public_subnet_id": "",
    "lambda_nat_public_subnet_cidr_block": "",
    "lambda_nat_public_subnet_availability_zone": "",
    "lambda_nat_internet_gateway_id": "",
    "lambda_nat_create_internet_gateway": False,
    "lambda_nat_route_subnet_ids": [],
    "agentcore_runtime_enabled": False,
    "agentcore_runtime_container_uri": "",
    "agentcore_network_mode": "PUBLIC",
    "enable_ingestion_scheduler": False,
    "ingestion_schedule_jobs": [],
}

print(json.dumps(profile, ensure_ascii=False, indent=2))
PY
  then
    exit 1
  fi
fi

if aws iam get-role --role-name "$role_name" >/dev/null 2>&1; then
  echo "Updating IAM role trust policy: ${role_name}"
  run_change aws iam update-assume-role-policy \
    --role-name "$role_name" \
    --policy-document "file://${tmpdir}/trust-policy.json"
else
  echo "Creating IAM role: ${role_name}"
  run_change aws iam create-role \
    --role-name "$role_name" \
    --assume-role-policy-document "file://${tmpdir}/trust-policy.json"
fi

legacy_policy_name="stockbrief-${environment}-backend-deploy"
new_policy_name="stockbrief-${environment}-deploy-access"

if [ "$legacy_policy_name" != "$new_policy_name" ]; then
  legacy_delete_error="${tmpdir}/delete-legacy-policy.err"
  if [ "$dry_run" = "true" ]; then
    run_change aws iam delete-role-policy \
      --role-name "$role_name" \
      --policy-name "$legacy_policy_name"
  else
    if ! aws iam delete-role-policy \
      --role-name "$role_name" \
      --policy-name "$legacy_policy_name" 2>"$legacy_delete_error"; then
      legacy_delete_message="$(cat "$legacy_delete_error")"
      case "$legacy_delete_message" in
        *NoSuchEntity*)
          ;;
        *)
          printf '%s\n' "$legacy_delete_message" >&2
          exit 1
          ;;
      esac
    fi
  fi
fi

run_change aws iam put-role-policy \
  --role-name "$role_name" \
  --policy-name "$new_policy_name" \
  --policy-document "file://${tmpdir}/deploy-policy.json"

echo "Configuring GitHub Environment branch policy: ${repo_full_name}/${environment}"
run_change gh api --method PUT "repos/${repo_full_name}/environments/${environment}" \
  -F wait_timer=0 \
  -F 'deployment_branch_policy[protected_branches]=false' \
  -F 'deployment_branch_policy[custom_branch_policies]=true'

existing_branch_policy_id="$(
  gh api "repos/${repo_full_name}/environments/${environment}/deployment-branch-policies" \
    --jq ".branch_policies[] | select(.name == \"${branch_escaped}\" and .type == \"branch\") | .id"
)"

if [ -z "$existing_branch_policy_id" ]; then
  run_change gh api --method POST "repos/${repo_full_name}/environments/${environment}/deployment-branch-policies" \
    -f name="$github_branch" \
    -f type=branch
fi

obsolete_branch_policies="$(
  gh api "repos/${repo_full_name}/environments/${environment}/deployment-branch-policies" \
    --jq ".branch_policies[] | select(.type == \"branch\" and .name != \"${branch_escaped}\") | [.name, .id] | @tsv"
)"

if [ -n "$obsolete_branch_policies" ]; then
  echo "Obsolete GitHub Environment branch policies for ${repo_full_name}/${environment}:"
  while IFS="$(printf '\t')" read -r obsolete_branch_policy_name obsolete_branch_policy_id; do
    [ -n "$obsolete_branch_policy_id" ] || continue
    echo "  - name=${obsolete_branch_policy_name} id=${obsolete_branch_policy_id}"
  done <<EOF
${obsolete_branch_policies}
EOF

  echo "Removing obsolete GitHub Environment branch policies for ${repo_full_name}/${environment}"
  while IFS="$(printf '\t')" read -r _obsolete_branch_policy_name obsolete_branch_policy_id; do
    [ -n "$obsolete_branch_policy_id" ] || continue
    run_change gh api --method DELETE \
      "repos/${repo_full_name}/environments/${environment}/deployment-branch-policies/${obsolete_branch_policy_id}"
  done <<EOF
${obsolete_branch_policies}
EOF
fi

echo "Setting GitHub Environment variables on ${repo_full_name}/${environment}"
set_github_variable "$deploy_role_var" "$role_arn"
set_github_variable OPERATIONAL_ALARM_EMAILS_JSON "$alarm_emails_json"

if [ "$write_deploy_profile_vars" = "true" ]; then
  set_github_variable TF_BACKEND_CONFIG_HCL "$(cat "${tmpdir}/backend.hcl")"
  set_github_variable TFVARS_JSON "$(cat "${tmpdir}/tfvars.json")"
fi

cat <<SUMMARY

Bootstrap complete.

Terraform backend:
  bucket         = "${state_bucket}"
  key            = "${state_key}"
  region         = "${region}"
  dynamodb_table = "${lock_table}"

GitHub Environment variables (${environment}):
  ${deploy_role_var}=${role_arn}
  OPERATIONAL_ALARM_EMAILS_JSON=<configured JSON array>
  TF_BACKEND_CONFIG_HCL=$([ "$write_deploy_profile_vars" = "true" ] && printf '<generated>' || printf '<not configured>')
  TFVARS_JSON=$([ "$write_deploy_profile_vars" = "true" ] && printf '<generated>' || printf '<not configured>')

Next:
  1. If TF_BACKEND_CONFIG_HCL/TFVARS_JSON are not configured, add a profile file or rerun with --write-deploy-profile-vars.
  2. Review the selected Terraform backend and generated profile before apply=true.
  3. Merge the backend deployment workflow PR.
SUMMARY
