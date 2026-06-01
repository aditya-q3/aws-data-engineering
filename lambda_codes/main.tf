terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region = "us-east-2"
}

####################################################
# ZIP LAMBDA
####################################################

data "archive_file" "lambda_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda/lambda_function.py"
  output_path = "${path.module}/lambda.zip"
}

####################################################
# EXISTING LAYER
####################################################

data "aws_lambda_layer_version" "common_layer" {
  layer_name = "lambda-layer-dev"
}

####################################################
# IAM ROLE
####################################################

resource "aws_iam_role" "lambda_role" {

  name = "ninjaone-api-terraform-dev-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })
}

####################################################
# CLOUDWATCH LOGS
####################################################

resource "aws_iam_role_policy_attachment" "logs" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

####################################################
# SECRETS MANAGER
####################################################

resource "aws_iam_role_policy" "secrets_policy" {

  name = "ninjaone-secrets-policy"
  role = aws_iam_role.lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "secretsmanager:GetSecretValue"
      ]
      Resource = "*"
    }]
  })
}

####################################################
# S3 ACCESS
####################################################

resource "aws_iam_role_policy" "s3_policy" {

  name = "ninjaone-s3-policy"
  role = aws_iam_role.lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "s3:GetObject",
        "s3:PutObject",
        "s3:ListBucket"
      ]
      Resource = "*"
    }]
  })
}

####################################################
# LAMBDA
####################################################

resource "aws_lambda_function" "ninjaone" {

  function_name = "ninjaone-api-terraform-dev"
  role          = aws_iam_role.lambda_role.arn

  runtime = "python3.12"
  handler = "lambda_function.lambda_handler"

  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256

  timeout     = 900
  memory_size = 512

  layers = [
    data.aws_lambda_layer_version.common_layer.arn
  ]
}