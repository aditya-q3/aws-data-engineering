terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "us-east-2"
}

resource "aws_s3_bucket" "demo_bucket" {
  bucket = "sentrias-dev-trial-bucket"

  tags = {
    Environment = "dev"
    ManagedBy   = "Terraform"
  }
}