#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_DIR="${ROOT_DIR}"
BUILD_DIR="${ROOT_DIR}/dist/lambda-api"
ZIP_PATH="${ROOT_DIR}/dist/stockbrief-api-lambda.zip"
PYTHON_BIN="${PYTHON_BIN:-python3.13}"
LAMBDA_PLATFORM="${LAMBDA_PLATFORM:-manylinux2014_x86_64}"
LAMBDA_PYTHON_VERSION="${LAMBDA_PYTHON_VERSION:-3.13}"

rm -rf "${BUILD_DIR}" "${ZIP_PATH}"
mkdir -p "${BUILD_DIR}" "${ROOT_DIR}/dist"
REQUIREMENTS_FILE="$(mktemp)"
trap 'rm -f "${REQUIREMENTS_FILE}"' EXIT

"${PYTHON_BIN}" -c 'import pathlib, re, sys, tomllib
dependencies = tomllib.loads(pathlib.Path(sys.argv[1]).read_text())["project"]["dependencies"]
excluded_runtime_dependencies = {"boto3", "botocore", "uvicorn"}

def dependency_name(dependency):
    return re.split(r"[<>=!~;\[]", dependency, maxsplit=1)[0].strip().lower()

lambda_dependencies = [
    dep
    for dep in dependencies
    if dependency_name(dep) not in excluded_runtime_dependencies
]
pathlib.Path(sys.argv[2]).write_text("\n".join(lambda_dependencies) + "\n")' \
  "${API_DIR}/pyproject.toml" \
  "${REQUIREMENTS_FILE}"

"${PYTHON_BIN}" -m pip install \
  --target "${BUILD_DIR}" \
  --platform "${LAMBDA_PLATFORM}" \
  --implementation cp \
  --python-version "${LAMBDA_PYTHON_VERSION}" \
  --only-binary=:all: \
  --requirement "${REQUIREMENTS_FILE}"

cp -R "${API_DIR}/app" "${BUILD_DIR}/app"

find "${BUILD_DIR}" -type d -name "__pycache__" -prune -exec rm -rf {} +

(
  cd "${BUILD_DIR}"
  zip -qr "${ZIP_PATH}" .
)

echo "Packaged ${ZIP_PATH}"
