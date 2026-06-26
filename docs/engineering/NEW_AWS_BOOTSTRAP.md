# 새 AWS 계정 부트스트랩 가이드

이 문서는 기존 dev AWS 계정을 사용할 수 없거나, 팀원별 AWS 계정을
돌아가며 사용해야 할 때 새 계정에 StockBrief BE 리소스를 생성하고 FE
Amplify를 연결하는 절차를 정리한다.

팀원 계정마다 `dev-junwoo`, `dev-minsu` 같은 배포 프로필 이름을 하나씩
사용한다. 하나의 프로필은 아래 두 파일로 구성된다.

- `infra/terraform/backends/<target_env>.hcl`
- `infra/terraform/envs/<target_env>/deploy.auto.tfvars.json`

기존 공용 dev 계정은 `dev` 프로필로 유지한다. 새 팀원 계정을 추가할 때는
아래 템플릿을 복사해서 사용한다.

- `infra/terraform/backends/dev-template.hcl.example`
- `infra/terraform/envs/dev-template/deploy.auto.tfvars.json.example`

이 저장소에는 기본 `dev` profile과 template만 둔다. 팀원별 실제 AWS
account ID, VPC ID, subnet ID, route table ID, Amplify domain이 들어간
profile 파일은 팀 정책상 공개 저장소 커밋이 허용될 때만 별도 PR로 추가한다.
허용되지 않는 경우에는 내부 handoff 문서나 제한된 운영 저장소에서 관리한다.

## 생성 후 공유 템플릿

새 dev 계정 리소스 생성이 끝나면 이 섹션을 팀 내부 공유용으로 복사해서
쓴다. 실제 계정 ID, 리소스 ID, API ID, Cognito ID, 도메인은 PR 본문이나
공개 문서에 그대로 남기지 말고 내부 배포 핸드오프, GitHub Environment
메모, Terraform output 기록에만 남긴다.

생성되어야 하는 리소스:

- Terraform remote state S3 bucket
- Terraform lock용 DynamoDB table
- GitHub Actions OIDC deploy role
- API Gateway HTTP API
- Lambda backend runtime
- Cognito User Pool과 web app client
- Cognito Hosted UI domain
- Private RDS PostgreSQL 16
- Secrets Manager VPC endpoint
- CloudWatch log group과 기본 운영 alarm

내부 핸드오프에 기록할 런타임 output:

- API base URL: `<api-gateway-base-url>`
- Cognito issuer: `<cognito-issuer-url>`
- Cognito User Pool ID: `<cognito-user-pool-id>`
- Cognito App Client ID: `<cognito-app-client-id>`
- Cognito Hosted UI: `<cognito-hosted-ui-domain>`
- RDS endpoint는 Terraform output으로만 관리하고 앱 코드에는 복사하지 않는다.

내부 핸드오프에 기록할 bootstrap 식별자:

- Terraform state bucket: `<terraform-state-bucket>`
- Terraform lock table: `<terraform-lock-table>`
- GitHub Actions deploy role ARN: `<github-actions-deploy-role-arn>`

생성 후 확인해야 하는 항목:

- Lambda maintenance operation `migrate_and_seed`
- `GET /v1/health`
- `GET /v1/stocks/candidates?limit=3`
- `GET /v1/recommendations/candidates?limit=3`
- `POST /v1/chat`
- 로컬 FE에서 Cognito Hosted UI 로그인 확인: `http://localhost:3001`
- 인증 토큰으로 `GET /v1/me`, `GET /v1/me/preferences`,
  `GET /v1/me/chat-sessions` 확인

의도적으로 나중에 처리해도 되는 항목:

- Amplify Hosting 생성
- RDS Proxy 사용 여부
- AgentCore Runtime
- Bedrock direct provider
- 실제 외부 API secret 값과 provider ingestion job
- Amplify 도메인에서 Cognito callback smoke test

## 사전 준비

- AWS CLI가 새 AWS 계정으로 로그인되어 있어야 한다.
- 리전은 기본적으로 `ap-northeast-2`를 사용한다.
- GitHub Environment variable을 설정할 수 있는 권한이 있어야 한다.
  팀원별 AWS 계정 전환에서는 Repository variable을 사용하지 않는다.
- 비용 관리를 위해 계정별 AWS Budget을 먼저 설정하는 것을 권장한다.

## 팀원이 직접 준비할 값

팀원이 자기 AWS 계정에 StockBrief dev 환경을 만들 때는 아래 값을 직접
확인해서 profile 파일에 넣는다. 이 값들은 secret이 아니지만, 실제 계정 ID와
리소스 ID는 공개 PR 본문에 길게 노출하지 말고 팀이 허용한 설정 파일 또는
내부 핸드오프에만 남긴다.

- 배포 프로필 이름: 예시 `dev-junwoo`, `dev-minsu`
- AWS 계정 ID
- AWS 리전: 보통 `ap-northeast-2`
- 운영 알림을 받을 이메일 주소
- VPC ID
- RDS가 들어갈 subnet ID 2개
- Lambda가 들어갈 subnet ID 목록: 보통 RDS subnet과 동일하게 사용
- VPC endpoint용 route table ID
- Cognito Hosted UI domain prefix: 예시
  `stockbrief-dev-minsu-<account-id>`
- 사용할 리소스 옵션: RDS, NAT Gateway, EventBridge Scheduler, RDS Proxy,
  Amplify, provider ingestion
- Amplify 생성 후 default domain

아래 값은 팀원이 직접 보관하고, 채팅, PR 본문, 커밋 파일에 남기지 않는다.

- AWS access key ID 또는 secret access key
- RDS password 또는 credential이 들어간 `DATABASE_URL`
- OpenDART, NAVER, KRX, GitHub, Amplify token
- AWS Secrets Manager에서 생성된 secret value

## 새 AWS 계정 생성 절차

1. 현재 AWS CLI가 바라보는 계정을 확인한다.

   ```bash
   aws sts get-caller-identity
   aws configure list
   ```

2. BE repo 루트에서 bootstrap script를 실행한다. `--environment`에는 팀원
   계정용 프로필 이름을 넣는다.

   ```bash
   scripts/bootstrap_github_oidc.sh \
     --environment dev-junwoo \
     --region ap-northeast-2 \
     --github-owner 80-hours-a-week \
     --github-repo StockBrief-be \
     --alarm-emails-json '["REPLACE_WITH_ALERT_EMAIL"]'
   ```

3. 출력된 state bucket, lock table, deploy role ARN을 내부 핸드오프에
   기록한다. credential이나 secret value는 기록하지 않는다.

4. backend profile 파일을 만든다.

   ```bash
   cp infra/terraform/backends/dev-template.hcl.example \
     infra/terraform/backends/dev-junwoo.hcl
   ```

   파일 안의 account ID, target environment 이름, lock table 이름을 실제
   값으로 바꾼다.

5. tfvars profile 파일을 만든다.

   ```bash
   mkdir -p infra/terraform/envs/dev-junwoo
   cp infra/terraform/envs/dev-template/deploy.auto.tfvars.json.example \
     infra/terraform/envs/dev-junwoo/deploy.auto.tfvars.json
   ```

   VPC, subnet, route table, Cognito domain prefix, 사용할 AWS 리소스 옵션을
   실제 값으로 채운다.

   실제 account/resource ID가 들어간 profile 파일은 공개 저장소에 바로
   올리지 않는다. 팀 정책상 허용되는지 확인한 뒤 PR을 만들고, 허용되지 않으면
   template만 repo에 두고 profile은 제한된 운영 문서나 별도 private handoff로
   관리한다.

6. 새 backend로 Terraform을 초기화한다.

   ```bash
   cd infra/terraform
   terraform init -reconfigure -backend-config=backends/dev-junwoo.hcl
   terraform state list
   ```

7. GitHub Environment variable을 등록한다. 실제 account/resource ID가 들어간
   profile 파일을 repo에 커밋하지 않는 기본 운영에서는 Actions가 이 값을
   읽어서 runner 안에 임시 profile 파일을 만든다.

   Repository variables에는 아래 값을 등록하지 않는다. 전역 변수에 특정
   팀원의 role ARN이나 tfvars를 넣으면 다른 팀원이 `target_env`를 잘못
   선택했을 때 다른 AWS 계정으로 배포될 수 있다.

   ```text
   AWS_DEV_JUNWOO_DEPLOY_ROLE_ARN
   OPERATIONAL_ALARM_EMAILS_JSON
   TF_BACKEND_CONFIG_HCL
   TFVARS_JSON
   ```

8. GitHub Actions의 `backend-dev-deploy` workflow를 수동 실행한다.

   ```text
   target_env=dev-junwoo
   ```

   workflow는 `AWS_DEV_JUNWOO_DEPLOY_ROLE_ARN`을 읽고,
   `TF_BACKEND_CONFIG_HCL`과 `TFVARS_JSON`으로 runner 안에
   `backends/dev-junwoo.hcl`,
   `envs/dev-junwoo/deploy.auto.tfvars.json`을 생성한 뒤 plan/apply를 실행한다.

9. VPC, subnet, Cognito URL, Amplify URL이 확정되기 전에는 `TFVARS_JSON`을
   안전한 기본값으로 둔다.

## GitHub Actions 설정 방법

팀원은 자기 AWS 계정 bootstrap을 끝낸 뒤 GitHub에서 아래 항목을 직접
확인한다. 이 설정이 맞아야 `backend-dev-deploy`가 장기 access key 없이
OIDC로 팀원 AWS 계정에 배포할 수 있다.

중요: 팀원별 계정 전환 구조에서는 Repository variables를 사용하지 않는다.
아래 값은 반드시 `StockBrief-be > Settings > Environments > <target_env> >
Environment variables`에만 등록한다.

### 1. GitHub Environment 확인

`scripts/bootstrap_github_oidc.sh`는 `--environment` 값과 같은 이름의
GitHub Environment를 만든다.

예를 들어 `--environment dev-minsu`로 실행했다면 GitHub에서 아래 경로를
확인한다.

```text
StockBrief-be > Settings > Environments > dev-minsu
```

확인할 값:

- Environment 이름이 `target_env`와 같은지 확인한다.
- Deployment branches and tags가 `main` branch만 허용하는지 확인한다.
- 필요한 경우 Required reviewers를 추가한다. dev 자동 배포를 바로 쓰려면
  reviewer 없이 둔다.

### 2. GitHub Actions deploy role variable 등록

변수 이름은 `target_env`를 대문자로 바꾸고 dash를 underscore로 바꾼 형태를
사용한다.

예시:

```text
target_env=dev-minsu
variable name=AWS_DEV_MINSU_DEPLOY_ROLE_ARN
```

GitHub에서 아래 경로에 값을 등록한다.

```text
StockBrief-be > Settings > Environments > dev-minsu > Environment variables
```

Repository variables에는 등록하지 않는다. 여러 팀원 계정을 동시에 운영할 때
각 Environment 안에 자기 role ARN을 두어야 계정 전환 실수를 줄일 수 있다.

Key:

```text
AWS_DEV_MINSU_DEPLOY_ROLE_ARN
```

Value:

```text
arn:aws:iam::<account-id>:role/stockbrief-dev-minsu-github-actions-deploy
```

### 3. 운영 알림 이메일 variable 확인

운영 알림 이메일은 JSON 배열 문자열로 저장한다.

Key:

```text
OPERATIONAL_ALARM_EMAILS_JSON
```

Value:

```text
["name@example.com"]
```

여러 명이면 아래처럼 넣는다.

```text
OPERATIONAL_ALARM_EMAILS_JSON=["name1@example.com","name2@example.com"]
```

### 4. Terraform backend variable 등록

GitHub Environment `dev-minsu`에 아래 variable을 등록한다.

Key:

```text
TF_BACKEND_CONFIG_HCL
```

Value:

```hcl
bucket         = "stockbrief-terraform-state-<account-id>-ap-northeast-2"
key            = "stockbrief/dev-minsu/terraform.tfstate"
region         = "ap-northeast-2"
dynamodb_table = "stockbrief-terraform-locks"
encrypt        = true
```

### 5. Terraform tfvars variable 등록

GitHub Environment `dev-minsu`에 아래 variable을 등록한다.

Key:

```text
TFVARS_JSON
```

Value:

```json
{
  "environment": "dev-minsu",
  "aws_region": "ap-northeast-2",
  "api_lambda_package_path": "../../dist/stockbrief-api-lambda.zip",
  "cors_allowed_origins": "http://localhost:3000,http://127.0.0.1:3000",
  "enable_amplify": false,
  "amplify_repository_url": "https://github.com/80-hours-a-week/StockBrief-fe",
  "amplify_branch_name": "main",
  "amplify_cognito_redirect_uri": "",
  "cognito_callback_urls": [
    "http://localhost:3000/auth/callback"
  ],
  "cognito_logout_urls": [
    "http://localhost:3000/account"
  ],
  "cognito_hosted_ui_domain_prefix": "stockbrief-dev-minsu-<account-id>",
  "db_instance_class": "db.t4g.micro",
  "db_allocated_storage_gb": 20,
  "db_deletion_protection": false,
  "db_skip_final_snapshot": true,
  "db_backup_retention_period": 1,
  "vpc_id": "<vpc-id>",
  "db_subnet_ids": [
    "<private-subnet-id-1>",
    "<private-subnet-id-2>"
  ],
  "db_security_group_ids": [],
  "rds_proxy_security_group_ids": [],
  "enable_rds_proxy": false,
  "lambda_subnet_ids": [
    "<private-subnet-id-1>",
    "<private-subnet-id-2>"
  ],
  "lambda_security_group_ids": [],
  "vpc_endpoint_route_table_ids": [
    "<private-route-table-id>"
  ],
  "enable_lambda_nat_egress": false,
  "lambda_nat_public_subnet_id": "",
  "lambda_nat_route_subnet_ids": [],
  "agentcore_runtime_enabled": false,
  "agentcore_runtime_container_uri": "",
  "agentcore_network_mode": "PUBLIC",
  "enable_ingestion_scheduler": false,
  "ingestion_schedule_jobs": []
}
```

`TFVARS_JSON`의 `environment` 값은 workflow의 `target_env`와 같아야 한다.
다르면 배포가 중단된다.

`amplify_cognito_redirect_uri`는 `enable_amplify=false`인 콘솔 관리 방식에서는
빈 문자열로 둔다. 이 경우 Terraform은 `cognito_callback_urls`의 첫 번째 값을
Amplify module 기본 redirect URI로 사용한다. Terraform으로 Amplify를 직접
생성하는 별도 환경에서만 해당 환경의 redirect URI로 채운다.

`agentcore_runtime_container_uri`는 `agentcore_runtime_enabled=false`이면 빈
문자열로 둔다. AgentCore Runtime을 켜는 환경에서만 ECR image URI를 입력한다.

### 6. profile 파일 생성 방식 확인

GitHub Actions가 최종적으로 읽는 파일 이름은 `target_env`와 정확히 맞아야
한다.

```text
infra/terraform/backends/dev-minsu.hcl
infra/terraform/envs/dev-minsu/deploy.auto.tfvars.json
```

다만 팀원별 실제 profile 파일은 git에 커밋하지 않는다. workflow가
`TF_BACKEND_CONFIG_HCL`과 `TFVARS_JSON`을 읽어서 runner 안에 위 파일을
임시 생성한다.

`backends/dev-minsu.hcl`의 state bucket 계정 ID와
`AWS_DEV_MINSU_DEPLOY_ROLE_ARN`의 계정 ID가 다르면 workflow가 즉시 실패해야
정상이다. 이 실패는 잘못된 계정 배포를 막는 보호 장치다.

`backend-dev-deploy`는 dev 전용 workflow라서 `target_env=dev` 또는
`target_env=dev-*`만 허용한다. `staging`, `prod` 같은 환경은 별도 workflow와
별도 approval 정책으로 만든다.

### 7. workflow 수동 실행

GitHub에서 아래 경로로 이동한다.

```text
StockBrief-be > Actions > Backend dev deploy > Run workflow
```

입력값:

```text
Branch: main
target_env: dev-minsu
```

workflow가 하는 일:

- `AWS_DEV_MINSU_DEPLOY_ROLE_ARN`을 찾아 OIDC로 AWS role을 assume한다.
- `TF_BACKEND_CONFIG_HCL`로 `backends/dev-minsu.hcl`을 임시 생성한다.
- `TFVARS_JSON`으로 `envs/dev-minsu/deploy.auto.tfvars.json`을 임시 생성한다.
- 생성된 profile 파일로 Terraform init/plan/apply를 실행한다.
- Lambda package를 만들고 최신 BE를 배포한다.

### 8. 실패했을 때 먼저 볼 것

- `Could not resolve deploy role variable`:
  `AWS_<TARGET_ENV>_DEPLOY_ROLE_ARN` 이름이 틀렸거나 값이 없다.
- `TF_BACKEND_CONFIG_HCL/TFVARS_JSON are not both set`:
  GitHub Environment variable 둘 중 하나가 비어 있다.
- `TFVARS_JSON is not valid JSON`:
  JSON 문법이 깨졌거나 trailing comma가 있다.
- `TFVARS_JSON environment must match target_env`:
  `TFVARS_JSON` 안의 `environment`와 workflow 입력 `target_env`가 다르다.
- `AssumeRoleWithWebIdentity`:
  GitHub Environment 이름과 IAM trust policy의 `environment:<target_env>`가
  맞지 않거나, Environment branch rule이 `main`을 허용하지 않는다.
- `backend account mismatch`:
  deploy role ARN의 계정과 Terraform backend state bucket의 계정이 다르다.
- `No such file or directory`:
  `backends/<target_env>.hcl` 또는
  `envs/<target_env>/deploy.auto.tfvars.json` 파일명이 틀렸다.

## dev 기본 권장값

- `enable_amplify = false`
- `agentcore_runtime_enabled = false`
- `enable_rds_proxy = false`
- `db_deletion_protection = false`
- `db_skip_final_snapshot = true`
- `db_backup_retention_period = 1`
- VPC와 subnet이 확정되기 전에는 subnet list를 비워둔다.
- Cognito Hosted UI domain prefix는 중복 여부를 확인한 뒤 채운다.

## Apply 전 확인

`terraform apply`는 아래 조건이 모두 맞을 때만 실행한다.

- 현재 AWS CLI 계정이 새 target 계정이다.
- `backends/<target_env>.hcl`이 새 state bucket을 가리킨다.
- `terraform state list`에 이전 계정 리소스가 보이지 않는다.
- `terraform plan`에 예상한 리소스만 나온다.
- 비용이 큰 리소스는 팀에서 명시적으로 사용하기로 했다.
- RDS dev 설정이 삭제 가능, final snapshot 생략, 짧은 backup retention으로 되어 있다.
- secret value는 git 밖에서 채울 예정이다.

## 배포 후 Smoke Test

- `GET /v1/health`
- `GET /v1/stocks/candidates`
- `GET /v1/recommendations/candidates`
- `POST /v1/chat`
- Amplify `/recommendations`
- Amplify `/stocks/[ticker]`
- Cognito callback
- 유효한 token으로 `GET /v1/me/watchlist`

## 팀 AWS 계정 전환 방법

팀에서 현재 사용할 AWS 계정을 바꿀 때는 프로필 이름 하나만 바꿔서
전환한다.

BE 배포 전환 절차:

1. 아래 두 파일이 있는지 확인한다.

   ```text
   infra/terraform/backends/<target_env>.hcl
   infra/terraform/envs/<target_env>/deploy.auto.tfvars.json
   ```

2. GitHub Environment variable이 있는지 확인한다.

   ```text
   AWS_<TARGET_ENV_WITH_DASHES_REPLACED_BY_UNDERSCORES>_DEPLOY_ROLE_ARN
   ```

   예를 들어 `dev-junwoo`는 `AWS_DEV_JUNWOO_DEPLOY_ROLE_ARN`을 사용한다.
   이 값은 Repository variables가 아니라
   `Settings > Environments > dev-junwoo > Environment variables`에 있어야 한다.

3. `backend-dev-deploy` workflow를 아래 값으로 실행한다.

   ```text
   target_env=<target_env>
   ```

4. Terraform output으로 나온 BE 값을 같은 계정의 FE Amplify 환경 변수에
   반영한다.

한 계정의 BE output을 다른 계정의 Cognito나 API Gateway 값과 섞어 쓰면
안 된다. API Gateway, Cognito User Pool, Cognito App Client, Hosted UI domain,
Amplify environment variable은 모두 같은 target profile에서 나온 값이어야 한다.

## FE Amplify 콘솔 수동 생성 방법

Amplify Hosting은 콘솔에서 관리한다. Terraform은 BE 리소스만 관리한다.
관리 대상은 RDS, Lambda, API Gateway, Cognito, Secrets Manager, alarm,
backend deployment IAM이다.

현재 활성 AWS 계정마다 Amplify app을 하나 만든다.

1. target BE profile과 같은 AWS 계정으로 AWS Console에 들어간다.
2. Amplify Hosting으로 이동해서 GitHub 연결 방식으로 새 app을 만든다.
3. `80-hours-a-week/StockBrief-fe` repo를 연결한다.
4. `main` branch를 선택한다.
5. repo의 `amplify.yml` build setting을 사용한다.
6. Terraform output을 보고 Amplify 환경 변수를 설정한다.

   ```text
   NEXT_PUBLIC_API_BASE_URL=<api_base_url>/v1
   NEXT_PUBLIC_APP_NAME=StockBrief
   NEXT_PUBLIC_COGNITO_REGION=ap-northeast-2
   NEXT_PUBLIC_COGNITO_USER_POOL_ID=<cognito_user_pool_id>
   NEXT_PUBLIC_COGNITO_APP_CLIENT_ID=<cognito_app_client_id>
   NEXT_PUBLIC_COGNITO_HOSTED_UI_DOMAIN=<cognito_hosted_ui_domain에서 https:// 제거>
   NEXT_PUBLIC_COGNITO_REDIRECT_URI=https://main.<amplify-default-domain>/auth/callback
   ```

7. Amplify를 한 번 배포하고 default domain을 기록한다.
8. target profile의 `cognito_callback_urls`와 `cognito_logout_urls`에 Amplify
   callback/logout URL을 추가한다.
9. 같은 `target_env`로 BE Terraform 배포를 다시 실행해서 Cognito가 Amplify
   도메인을 허용하게 한다.
10. Cognito callback 변경 후 Amplify를 다시 배포한다.

Cognito URL 패턴:

```text
callback: https://main.<amplify-default-domain>/auth/callback
logout:   https://main.<amplify-default-domain>/account
```

콘솔의 GitHub App 연결 방식을 쓰는 경우 팀원에게 Amplify access token이나
GitHub personal access token을 받을 필요가 없다. 팀원은 Amplify app 생성 후
Amplify default domain만 공유하면 된다.
