# Contributing

We welcome contributions to Metascan! Here's how to get started.

## Setting Up Development Environment

1. **Fork and clone the repository:**
   ```bash
   git clone https://github.com/yourusername/metascan.git
   cd metascan
   ```

2. **Set up both backend and frontend:**
   ```bash
   # Backend
   python3.11 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   pip install -r requirements-dev.txt
   python setup_models.py

   # Frontend
   cd frontend && npm install && cd ..
   ```

See [docs/installation.md](docs/installation.md) for a full breakdown.

## Development Guidelines

**Code Standards:**
- Follow PEP 8 style guidelines for Python
- Use TypeScript for all frontend code
- Use `black` for Python formatting, `vue-tsc` for frontend type checking
- Add type hints where appropriate
- Write docstrings for public functions and classes

**Testing:**
- Write tests for new features using `pytest`
- Maintain or improve test coverage
- Verify frontend builds cleanly (`npm run build`)
- Test both backend API endpoints and frontend components

**Commit Guidelines:**
- Use clear, descriptive commit messages with conventional prefixes (`feat:`, `fix:`, `refactor:`)
- Reference issues in commits when applicable
- Keep commits atomic and focused on single changes

See [docs/developer-guidelines.md](docs/developer-guidelines.md) for build commands, the project rule set, and the CI matrix. The canonical, exhaustive rule list lives in [`CLAUDE.md`](CLAUDE.md).

## Submitting Changes

1. **Create a feature branch:**
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes and verify locally:**
   ```bash
   make quality test
   cd frontend && npm run build
   ```

3. **Commit and push:**
   ```bash
   git add .
   git commit -m "feat: add your descriptive commit message"
   git push origin feature/your-feature-name
   ```

4. **Open a pull request** with a clear description, related issues, and screenshots for UI changes.
