# Contributing to PAE

Thank you for contributing to PAE. Every contribution must maintain the non-advisory architectural guardrails that keep PAE within regulatory safe harbor.

## Non-Advisory Guardrails (Mandatory)

Before submitting any PR, verify your changes do not:

1. **Recommend specific securities.** No function should output "buy X" or "sell Y."
2. **Generate personalized investment advice.** No output should say "you should" regarding investment actions.
3. **Create automated alerts that imply advice.** Alerts must be factual observations on user-defined conditions, not action recommendations.
4. **Set default parameters that imply advice.** All inputs must start blank or neutral.
5. **Label any output as "optimal" or "recommended."** The efficient frontier shows all points equally.

If you are unsure whether a feature crosses the line, open an issue for discussion before implementing.

## Development Setup

```bash
# Rust engine
cd engine && cargo build && cargo test

# Python analytics
cd analytics && pip install -e ".[dev]" && pytest tests/ -v

# UI
cd ui && npx tsc

# Full build
make build && make test
```

## Code Standards

- **Rust**: Follow `cargo clippy` recommendations. No warnings allowed.
- **Python**: Ruff for linting, mypy for type checking. 100-char line limit.
- **TypeScript**: Strict mode. No `any` types.
- **Documentation**: Every public function must have a docstring explaining what it calculates and citing the methodology.

## Pull Request Process

1. Fork the repo and create a feature branch.
2. Write tests for new functionality.
3. Ensure all CI checks pass.
4. Include a note on regulatory compliance if the feature produces user-facing analytical output.
5. Submit PR with clear description of changes.

## License

By contributing, you agree that your contributions will be licensed under AGPL-3.0.
