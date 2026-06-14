#!/bin/bash

# All configuration (account, region, image name, tag, Dockerfile) is supplied
# as flags by ci/scripts/ecs-deploy; require_config below enforces that they are set.

# --- Dependency Verification ---
dependency_verification() {
  MISSING_DEPENDENCIES=false
  # If you have any dependencies for your script, add the checks for them here:
  which aws > /dev/null
  if [[ $? -ne 0 ]]; then
    echo "AWS CLI ('aws') is required to run this script. Install it and run 'aws configure'."
    MISSING_DEPENDENCIES=true
  fi
  which docker > /dev/null
  if [[ $? -ne 0 ]]; then
    echo "Docker is required to run this script."
    MISSING_DEPENDENCIES=true
  fi
  if [[ $MISSING_DEPENDENCIES == true ]]; then
    return 1
  fi
}

dependency_verification
MISSING_DEPENDENCIES=$?

# Set exit on error flag after dependency verification
set -e

if [[ $MISSING_DEPENDENCIES -ne 0 ]]; then
  exit 1;
fi

# --- Summary ---
summary() {
  # Display Summary
  # This string replacement is the name of the file/script
  echo "${0##*/}"
  echo "    Build the Thiup web image from the given Dockerfile and push it to ECR."
  echo "    Local equivalent of the CodeBuild buildspec (login -> build -> push)."
  echo ""
  echo "    Not meant to be run directly: all configuration (account, region, image"
  echo "    name, tag, Dockerfile) is owned by ci/scripts/ecs-deploy and passed in as"
  echo "    flags. Use 'bash ci/scripts/ecs-deploy build' instead."
  echo ""
  echo "build and push (driven by ecs-deploy):"
  echo "    bash ci/scripts/ecs-deploy build --tag my-tag"
}

# --- Help ---
help() {
   # Display Help
   summary
   echo ""
   echo "options (all required, normally supplied by ci/scripts/ecs-deploy):"
   echo "-h, --help           Print this help."
   echo "-s, --summary        Print a summary of the script."
   echo "-t, --tag            Image tag to build and push."
   echo "-a, --account        AWS account id."
   echo "-r, --region         AWS region."
   echo "-i, --image-name     ECR repository / image name."
   echo "-f, --dockerfile     Dockerfile to build from."
   echo ""
   echo "Requires AWS credentials configured locally (aws configure) and a running Docker daemon."
}

# --- Configuration validation ---
require_config() {
  local missing=false
  local var
  for var in AWS_ACCOUNT_ID REGION IMAGE_NAME IMAGE_TAG DOCKERFILE; do
    if [[ -z "${!var}" ]]; then
      echo "Missing required value: ${var}. Run 'bash ci/scripts/ecs-deploy build' or see -h." >&2
      missing=true
    fi
  done
  if [[ $missing == true ]]; then
    exit 1
  fi
}

# --- Build & push ---
build_and_push() {
  require_config

  REGISTRY_HOST="${AWS_ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
  ECR_URL="${REGISTRY_HOST}/${IMAGE_NAME}"
  GIT_SHA=$(git rev-parse HEAD 2>/dev/null || echo "unknown")

  echo "Logging in to ECR: ${REGISTRY_HOST}"
  aws ecr get-login-password --region "$REGION" \
    | docker login --username AWS --password-stdin "$REGISTRY_HOST"

  echo "Building ${ECR_URL}:${IMAGE_TAG} (GIT_SHA=${GIT_SHA})"
  DOCKER_BUILDKIT=1 docker build . \
    --build-arg BUILDKIT_INLINE_CACHE=1 \
    --build-arg GIT_SHA="$GIT_SHA" \
    --cache-from "${ECR_URL}:${IMAGE_TAG}" \
    --file "$DOCKERFILE" \
    --tag "${ECR_URL}:${IMAGE_TAG}"

  echo "Pushing ${ECR_URL}:${IMAGE_TAG}"
  docker push "${ECR_URL}:${IMAGE_TAG}"

  echo ""
  echo "Pushed ${ECR_URL}:${IMAGE_TAG}"
  echo "Next: run 'bash ci/scripts/ecs-deploy' to make ECS pull the new image."
}

# --- Main program ---
# Place your arg/flag related variables here at the top
HELP=false
SUMMARY=false

# For looping for access arg/flag input and making changes based off of them
while [[ $# -gt 0 ]]
do
  case $1 in
  -h|--help) HELP=true; shift ;;
  -s|--summary) SUMMARY=true; shift ;;
  -t|--tag) IMAGE_TAG=$2; shift 2 ;;
  -a|--account) AWS_ACCOUNT_ID=$2; shift 2 ;;
  -r|--region) REGION=$2; shift 2 ;;
  -i|--image-name) IMAGE_NAME=$2; shift 2 ;;
  -f|--dockerfile) DOCKERFILE=$2; shift 2 ;;
  *) echo "Unknown option: $1"; help; exit 1 ;;
  esac
done

if [[ $HELP == true ]]; then
  help
elif [[ $SUMMARY == true ]]; then
  summary
else
  build_and_push
fi
