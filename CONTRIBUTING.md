# Contributing to Prism

Thank you for your interest in contributing to Prism! We welcome contributions from the community.

## Getting Started

1. **Fork the repository** on GitHub
2. **Clone your fork** locally:
   ```bash
   git clone https://github.com/YOUR_USERNAME/prism.git
   cd prism
   ```
3. **Create a branch** for your changes:
   ```bash
   git checkout -b feature/your-feature-name
   ```

## Development Setup

### Prerequisites

- Python 3.12+
- Rust 1.70+
- Node.js 18+
- Docker and Docker Compose

### Local Development

```bash
# Backend (Management Plane)
cd management_plane
pip install -e ".[dev]"
pytest tests/ -v

# Data Plane (Rust)
cd data_plane/tupl_dp/bridge
cargo build
cargo test

# Frontend (UI)
cd ui
npm install
npm run dev
```

## Making Changes

### Code Style

- **Python**: Follow PEP 8, use `ruff` for linting
- **Rust**: Use `rustfmt` and `clippy`
- **TypeScript**: Follow project `.eslintrc` config

### Testing

- Add tests for new features
- Ensure all tests pass before submitting PR
- Aim for >80% code coverage for new code

### Commit Messages

Use conventional commits format:

```
type(scope): brief description

Longer explanation if needed.

Fixes #123
```

**Types**: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`

**Examples**:
- `feat(sdk): add hard-block mode option`
- `fix(data-plane): resolve memory leak in rule storage`
- `docs(readme): update quick start guide`

## Submitting Changes

1. **Push your branch** to your fork
2. **Create a Pull Request** on GitHub
3. **Describe your changes**:
   - What problem does this solve?
   - How did you test it?
   - Any breaking changes?
4. **Wait for review** - maintainers will review your PR

## Pull Request Guidelines

- Keep PRs focused - one feature/fix per PR
- Update documentation if needed
- Add tests for new functionality
- Ensure CI passes (tests, linting)
- Be responsive to feedback

## Code Review Process

1. Automated CI checks run (tests, linting, security scans)
2. Maintainer reviews code
3. Requested changes (if any)
4. Approval and merge

## Community Guidelines

- Be respectful and constructive
- Help others learn and grow
- Follow our Code of Conduct
- Ask questions in Discussions or Issues

## Reporting Issues

Found a bug? Have a feature request?

1. **Check existing issues** first
2. **Create a new issue** with:
   - Clear title and description
   - Steps to reproduce (for bugs)
   - Expected vs actual behavior
   - Environment details (OS, versions, etc.)

## Security Issues

**DO NOT** open public issues for security vulnerabilities.

See [SECURITY.md](SECURITY.md) for responsible disclosure process.

## Questions?

- GitHub Discussions: Ask questions and share ideas
- Documentation: [docs/](docs/)
- Project README: [README.md](README.md)

## License

By contributing, you agree that your contributions will be licensed under the MIT License.

---

Thank you for contributing to Prism! 🎉
