---
description: "Use when writing or modifying Python code that integrates with AWS services, boto3, Amazon Textract, CLI flows, or JSON extraction logic. Enforces secure AWS usage and project conventions."
name: "Python AWS Best Practices"
applyTo: "**/*.py"
---
# Python AWS Best Practices

## Security and AWS access
- Never hardcode AWS credentials, tokens, or secrets in source code, tests, or examples.
- Prefer the default AWS credential chain, named profiles, environment variables, or IAM roles.
- Read the AWS region from configuration such as `AWS_DEFAULT_REGION`; only use a safe default when the project already expects one.
- Do not log sensitive values, raw credentials, full personal documents, or unnecessary PII.

## boto3 usage
- Keep AWS calls isolated in small functions so parsing and business logic remain testable.
- Catch `botocore.exceptions.ClientError` for service failures and return actionable error messages.
- Prefer explicit client construction with clear service names and region configuration.
- Validate request constraints before the AWS call, such as file size and required input fields.

## Project conventions
- Use English consistently for code comments, docstrings, variable names, log messages, CLI help text, documentation, and user-facing output.
- Keep the JSON output structure stable and predictable, following the existing `header`, `items`, and `consumer` sections.
- Use `logging` instead of `print` for runtime information and errors.
- Preserve CLI behavior with clear options, helpful messages, and safe defaults.
- Add type hints and short docstrings for non-trivial functions.
- Keep parsing logic separate from CLI entrypoint logic.

## Code quality
- Prefer small, single-purpose helpers over large monolithic functions.
- Handle edge cases explicitly, especially OCR inconsistencies, missing fields, and malformed document numbers.
- Favor readable, defensive code over clever shortcuts.
- When changing extraction logic, avoid breaking the current output schema unless the request explicitly asks for it.

## Testing guidance
- Test parsing behavior with representative invoice payloads and fixtures.
- Mock the boto3 client boundary instead of calling live AWS services in tests.
- Verify error handling for missing credentials, invalid files, and AWS service exceptions.
