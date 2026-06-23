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
  - GitHub repository variables used by backend-dev-deploy.yml

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

environment="dev"
region="ap-northeast-2"
github_owner="80-hours-a-week"
github_repo="StockBrief-be"
github_branch="main"
state_bucket=""
lock_table="stockbrief-terraform-locks"
role_name=""
alarm_emails_json="[]"

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
env_upper="$(printf '%s' "$environment" | tr '[:lower:]' '[:upper:]' | tr '-' '_')"
deploy_role_var="AWS_${env_upper}_DEPLOY_ROLE_ARN"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

echo "Bootstrapping StockBrief ${environment} deployment in AWS account ${account_id} (${region})"

if aws s3api head-bucket --bucket "$state_bucket" >/dev/null 2>&1; then
  echo "S3 state bucket already exists: ${state_bucket}"
else
  echo "Creating S3 state bucket: ${state_bucket}"
  if [ "$region" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$state_bucket" >/dev/null
  else
    aws s3api create-bucket \
      --bucket "$state_bucket" \
      --region "$region" \
      --create-bucket-configuration "LocationConstraint=${region}" >/dev/null
  fi
fi

aws s3api put-bucket-versioning \
  --bucket "$state_bucket" \
  --versioning-configuration Status=Enabled >/dev/null

aws s3api put-bucket-encryption \
  --bucket "$state_bucket" \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}' >/dev/null

aws s3api put-public-access-block \
  --bucket "$state_bucket" \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true >/dev/null

if aws dynamodb describe-table --table-name "$lock_table" --region "$region" >/dev/null 2>&1; then
  echo "DynamoDB lock table already exists: ${lock_table}"
else
  echo "Creating DynamoDB lock table: ${lock_table}"
  aws dynamodb create-table \
    --table-name "$lock_table" \
    --attribute-definitions AttributeName=LockID,AttributeType=S \
    --key-schema AttributeName=LockID,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST \
    --region "$region" >/dev/null
  aws dynamodb wait table-exists --table-name "$lock_table" --region "$region"
fi

if aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$oidc_provider_arn" >/dev/null 2>&1; then
  echo "GitHub OIDC provider already exists: ${oidc_provider_arn}"
else
  echo "Creating GitHub OIDC provider: ${oidc_provider_url}"
  aws iam create-open-id-connect-provider \
    --url "https://${oidc_provider_url}" \
    --client-id-list sts.amazonaws.com \
    --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1 >/dev/null
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
        "s3:PutBucketEncryption",
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
      "Sid": "DevBackendDeployment",
      "Effect": "Allow",
      "Action": [
        "apigateway:DELETE",
        "apigateway:GET",
        "apigateway:PATCH",
        "apigateway:POST",
        "apigateway:PUT",
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
        "cloudwatch:DeleteAlarms",
        "cloudwatch:DescribeAlarms",
        "cloudwatch:ListTagsForResource",
        "cloudwatch:PutMetricAlarm",
        "cloudwatch:TagResource",
        "cloudwatch:UntagResource",
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
        "ec2:CreateTags",
        "ec2:CreateVpcEndpoint",
        "ec2:AllocateAddress",
        "ec2:AssociateRouteTable",
        "ec2:DeleteNatGateway",
        "ec2:DeleteRoute",
        "ec2:DeleteRouteTable",
        "ec2:DeleteSecurityGroup",
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
        "ec2:DisassociateRouteTable",
        "ec2:ModifySecurityGroupRules",
        "ec2:ModifyVpcEndpoint",
        "ec2:ReleaseAddress",
        "ec2:ReplaceRoute",
        "ec2:RevokeSecurityGroupEgress",
        "ec2:RevokeSecurityGroupIngress",
        "iam:AttachRolePolicy",
        "iam:CreateRole",
        "iam:DeleteRole",
        "iam:DeleteRolePolicy",
        "iam:DetachRolePolicy",
        "iam:GetPolicy",
        "iam:GetRole",
        "iam:GetRolePolicy",
        "iam:ListAttachedRolePolicies",
        "iam:ListRolePolicies",
        "iam:ListRoleTags",
        "iam:PassRole",
        "iam:PutRolePolicy",
        "iam:TagRole",
        "iam:UntagRole",
        "iam:UpdateAssumeRolePolicy",
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
        "lambda:UpdateFunctionConfiguration",
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
        "logs:DeleteLogGroup",
        "logs:DescribeLogGroups",
        "logs:ListTagsForResource",
        "logs:PutRetentionPolicy",
        "logs:TagResource",
        "logs:UntagResource",
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
        "s3:CreateBucket",
        "s3:DeleteBucket",
        "s3:DeleteBucketEncryption",
        "s3:DeleteBucketPublicAccessBlock",
        "s3:DeleteBucketTagging",
        "s3:DeleteBucketWebsite",
        "s3:DeleteLifecycleConfiguration",
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
        "s3:PutLifecycleConfiguration",
        "scheduler:CreateSchedule",
        "scheduler:DeleteSchedule",
        "scheduler:GetSchedule",
        "scheduler:ListTagsForResource",
        "scheduler:TagResource",
        "scheduler:UntagResource",
        "scheduler:UpdateSchedule",
        "secretsmanager:CreateSecret",
        "secretsmanager:DeleteSecret",
        "secretsmanager:DescribeSecret",
        "secretsmanager:GetResourcePolicy",
        "secretsmanager:GetSecretValue",
        "secretsmanager:ListSecretVersionIds",
        "secretsmanager:PutSecretValue",
        "secretsmanager:TagResource",
        "secretsmanager:UntagResource",
        "secretsmanager:UpdateSecret",
        "sns:CreateTopic",
        "sns:DeleteTopic",
        "sns:GetSubscriptionAttributes",
        "sns:GetTopicAttributes",
        "sns:ListSubscriptionsByTopic",
        "sns:ListTagsForResource",
        "sns:SetTopicAttributes",
        "sns:Subscribe",
        "sns:TagResource",
        "sns:Unsubscribe",
        "sns:UntagResource",
        "sqs:CreateQueue",
        "sqs:DeleteQueue",
        "sqs:GetQueueAttributes",
        "sqs:ListQueueTags",
        "sqs:SetQueueAttributes",
        "sqs:TagQueue",
        "sqs:UntagQueue",
        "sts:GetCallerIdentity"
      ],
      "Resource": "*"
    }
  ]
}
POLICY

if aws iam get-role --role-name "$role_name" >/dev/null 2>&1; then
  echo "Updating IAM role trust policy: ${role_name}"
  aws iam update-assume-role-policy \
    --role-name "$role_name" \
    --policy-document "file://${tmpdir}/trust-policy.json" >/dev/null
else
  echo "Creating IAM role: ${role_name}"
  aws iam create-role \
    --role-name "$role_name" \
    --assume-role-policy-document "file://${tmpdir}/trust-policy.json" >/dev/null
fi

legacy_policy_name="stockbrief-${environment}-backend-deploy"
new_policy_name="stockbrief-${environment}-deploy-access"

if [ "$legacy_policy_name" != "$new_policy_name" ]; then
  legacy_delete_error="${tmpdir}/delete-legacy-policy.err"
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

aws iam put-role-policy \
  --role-name "$role_name" \
  --policy-name "$new_policy_name" \
  --policy-document "file://${tmpdir}/deploy-policy.json" >/dev/null

echo "Configuring GitHub Environment branch policy: ${repo_full_name}/${environment}"
gh api --method PUT "repos/${repo_full_name}/environments/${environment}" \
  -F wait_timer=0 \
  -F 'deployment_branch_policy[protected_branches]=false' \
  -F 'deployment_branch_policy[custom_branch_policies]=true' >/dev/null

existing_branch_policy_id="$(
  gh api "repos/${repo_full_name}/environments/${environment}/deployment-branch-policies" \
    --jq ".branch_policies[] | select(.name == \"${branch_escaped}\" and .type == \"branch\") | .id"
)"

if [ -z "$existing_branch_policy_id" ]; then
  gh api --method POST "repos/${repo_full_name}/environments/${environment}/deployment-branch-policies" \
    -f name="$github_branch" \
    -f type=branch >/dev/null
fi

obsolete_branch_policy_ids="$(
  gh api "repos/${repo_full_name}/environments/${environment}/deployment-branch-policies" \
    --jq ".branch_policies[] | select(.type == \"branch\" and .name != \"${branch_escaped}\") | .id"
)"

if [ -n "$obsolete_branch_policy_ids" ]; then
  echo "Removing obsolete GitHub Environment branch policies for ${repo_full_name}/${environment}"
  while IFS= read -r obsolete_branch_policy_id; do
    [ -n "$obsolete_branch_policy_id" ] || continue
    gh api --method DELETE \
      "repos/${repo_full_name}/environments/${environment}/deployment-branch-policies/${obsolete_branch_policy_id}" >/dev/null
  done <<EOF
${obsolete_branch_policy_ids}
EOF
fi

echo "Setting GitHub repository variables on ${repo_full_name}"
gh variable set "$deploy_role_var" --repo "$repo_full_name" --body "$role_arn" >/dev/null

if [ "$deploy_role_var" != "AWS_DEV_DEPLOY_ROLE_ARN" ] && [ "$environment" = "dev" ]; then
  gh variable set AWS_DEV_DEPLOY_ROLE_ARN --repo "$repo_full_name" --body "$role_arn" >/dev/null
fi

gh variable set OPERATIONAL_ALARM_EMAILS_JSON --repo "$repo_full_name" --body "$alarm_emails_json" >/dev/null

cat <<SUMMARY

Bootstrap complete.

Terraform backend:
  bucket         = "${state_bucket}"
  key            = "stockbrief/${environment}/terraform.tfstate"
  region         = "${region}"
  dynamodb_table = "${lock_table}"

GitHub variables:
  ${deploy_role_var}=${role_arn}
  OPERATIONAL_ALARM_EMAILS_JSON=<configured JSON array>

Next:
  1. Make sure infra/terraform/backend.tf matches the backend above.
  2. Make sure infra/terraform/envs/dev/deploy.auto.tfvars.json matches this AWS account and network.
  3. Merge the backend deployment workflow PR.
SUMMARY
