#!/usr/bin/env bash
set -euo pipefail

tf_dir="${TF_DIR:-infra/terraform}"
backend_file="${TF_BACKEND_FILE:-${tf_dir}/backend.tf}"
backend_config="${TF_BACKEND_CONFIG:-}"
deploy_role_arn="${DEPLOY_ROLE_ARN:-${AWS_DEV_DEPLOY_ROLE_ARN:-}}"

role_account="$(printf '%s' "$deploy_role_arn" | sed -n 's/^arn:aws:iam::\([0-9]\{12\}\):role\/.*$/\1/p')"
if [ -z "$role_account" ]; then
  echo "::error::Could not parse AWS account id from AWS_DEV_DEPLOY_ROLE_ARN."
  exit 1
fi

if [ -n "$backend_config" ]; then
  case "$backend_config" in
    /*)
      backend_source="$backend_config"
      ;;
    *)
      backend_source="${tf_dir}/${backend_config}"
      ;;
  esac
else
  backend_source="$backend_file"
fi

if [ ! -f "$backend_source" ]; then
  echo "::error::Terraform backend configuration not found: ${backend_source}"
  exit 1
fi

backend_bucket="$(awk -F'"' '/bucket[[:space:]]*=/{print $2; exit}' "$backend_source")"
backend_account="$(printf '%s' "$backend_bucket" | sed -n 's/^stockbrief-terraform-state-\([0-9]\{12\}\)-.*$/\1/p')"
if [ -z "$backend_account" ]; then
  echo "::error::Could not parse AWS account id from Terraform backend bucket: ${backend_bucket}"
  exit 1
fi

if [ -n "${ASSUMED_AWS_ACCOUNT_ID:-}" ]; then
  actual_account="$ASSUMED_AWS_ACCOUNT_ID"
else
  actual_account="$(aws sts get-caller-identity --query Account --output text)"
fi

if [ "$actual_account" != "$role_account" ]; then
  echo "::error::Assumed AWS account ${actual_account} does not match deploy role account ${role_account}."
  exit 1
fi

if [ "$actual_account" != "$backend_account" ]; then
  echo "::error::Assumed AWS account ${actual_account} does not match Terraform backend account ${backend_account} from ${backend_bucket}."
  exit 1
fi

echo "Verified deploy account ${actual_account} matches deploy role and Terraform backend ${backend_source}."
