# Security Policy

## Supported versions

Only the latest release on the default branch receives security fixes.

## Reporting a vulnerability

**Please do not report security vulnerabilities through public GitHub
issues, discussions, or pull requests.**

Report them privately through GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
("Report a vulnerability" under this repository's **Security** tab).

Please include:

- a description of the issue and its impact
- steps to reproduce, or a proof of concept
- the affected version or commit

Do not include real credentials, tokens, or data from a production
environment in your report.

We aim to acknowledge reports within 5 business days and will keep you
informed as the fix progresses.

## Scope

This policy covers the code in this repository. Vulnerabilities in IBM
watsonx, Cloud Pak for Data, or IBM Cloud services themselves should be
reported to IBM's Product Security Incident Response Team (PSIRT):
https://www.ibm.com/trust/security-psirt

## Handling credentials

This server keeps credentials in memory for the session only and redacts
known secret fields from every tool's output (see
[`spec/03-conventions.md`](spec/03-conventions.md)). If you find a case
where a credential, token, or secret reaches tool output, logs, or error
messages, please report it as a vulnerability.
