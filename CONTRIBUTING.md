# Contributing

Thanks for your interest in improving the watsonx Platform MCP server.

By participating you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Before you start

- **The spec is the contract.** [`spec/`](spec/) is the source of truth for
  every tool's inputs, outputs, and behavior. If a change alters tool
  behavior, update the relevant spec file in the same pull request.
- **Stay product-agnostic.** This server covers the generic Common Core
  Services layer (projects, assets, connections, jobs). Product-specific
  asset types or job configurations belong in a layer built on top, not as
  special cases here — see "Extension model" in
  [`spec/00-overview.md`](spec/00-overview.md).
- For larger changes, open an issue first so the approach can be agreed
  before you write code.

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Pull requests

- Add or update unit tests for every behavior change. Tests mock HTTP with
  `respx`; never call a real cluster or tenant from the unit test suite.
- **Never commit real credentials, tokens, cluster hostnames, account IDs,
  or project/asset IDs** from a real environment — including in test
  fixtures, spec examples, and captured payloads. Use placeholders such as
  `cpd.example.com` and obviously fake UUIDs.
- If you verified a behavior against a live SaaS tenant or CPD cluster, say
  so in the pull request and update the tool's verification level in the
  README ("Tool confidence tiers") and spec.
- Keep commits focused, with messages that describe what changed and why.

## Reporting security issues

Do not open a public issue for security problems. See
[SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contributions are licensed under the
[Apache License 2.0](LICENSE).
