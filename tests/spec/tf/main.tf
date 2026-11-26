# Empty root so `terraform test` can run the spec files in ./tests against modules that
# are referenced by path from each run block. Run: terraform -chdir=tests/spec/tf test
terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}
