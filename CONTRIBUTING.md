# Contributing to `skippy-cov`

Contributions are welcome, and they are greatly appreciated!
Every little bit helps, and credit will always be given.

You can contribute in many ways:

# Types of Contributions

## Report Bugs

Report bugs at https://github.com/mgaitan/skippy-cov/issues

If you are reporting a bug, please include:

- Your operating system name and version.
- Any details about your local setup that might be helpful in troubleshooting.
- Detailed steps to reproduce the bug.

## Fix Bugs

Look through the GitHub issues for bugs.
Anything tagged with "bug" and "help wanted" is open to whoever wants to implement a fix for it.

## Implement Features

Look through the GitHub issues for features.
Anything tagged with "enhancement" and "help wanted" is open to whoever wants to implement it.

## Write Documentation

skippy-cov could always use more documentation, whether as part of the official docs, in docstrings, or even on the web in blog posts, articles, and such.

## Submit Feedback

The best way to send feedback is to file an issue at https://github.com/mgaitan/skippy-cov/issues.

If you are proposing a new feature:

- Explain in detail how it would work.
- Keep the scope as narrow as possible, to make it easier to implement.
- Remember that this is a volunteer-driven project, and that contributions
  are welcome :)

# Get Started!

Ready to contribute? Here's how to set up `skippy-cov` for local development.
Please note this documentation assumes you already have `uv` and `Git` installed and ready to go.

1. Fork the `skippy-cov` repo on GitHub.

2. Clone your fork locally:

```bash
cd <directory_in_which_repo_should_be_created>
git clone git@github.com:YOUR_NAME/skippy-cov.git
```

3. Now we need to install the environment. Navigate into the directory

```bash
cd skippy-cov
```

Then install the development environment, including test, lint and type-checking groups:

```bash
uv sync
```

4. Install prek as an external tool and enable the hooks from `prek.toml`:

```bash
uv tool install prek
prek install
```

5. Create a branch for local development:

```bash
git checkout -b name-of-your-bugfix-or-feature
```

Now you can make your changes locally.

6. Don't forget to add test cases for your added functionality to the `tests` directory.

7. When you're done making changes, check that your changes pass the formatting tests.

```bash
make check
```

Now, validate that all unit tests are passing:

```bash
make test
```

`make test` and `make test-cov` require 100% line and branch coverage of
`skippy_cov`, including CLI and pytest integration subprocesses. The threshold
is set by `fail_under = 100` in `pyproject.toml` and also applies in CI. Subprocess
measurement uses coverage.py's native `patch = ["subprocess"]` support; it keeps
the tool's coverage separate from the baseline databases created by the tests.
There are no project-specific coverage exclusions.

9. The supported Python versions are 3.12 through 3.15. CI tests all four versions.
   To reproduce the matrix locally:

```bash
for version in 3.12 3.13 3.14 3.15; do
    uv run --locked --isolated --no-default-groups --group test --python "$version" pytest --cov --cov-context=test --cov-report=term-missing
done
```

uv can install missing Python interpreters automatically. Run `make check` for
Ruff, ty and lockfile validation, or `prek run --all-files` for all git hooks.

10. Commit your changes and push your branch to GitHub:

```bash
git add .
git commit -m "Your detailed description of your changes."
git push origin name-of-your-bugfix-or-feature
```

11. Submit a pull request through the GitHub website.

## Releases

The package version lives in `pyproject.toml`; use `uv version --bump patch` to
prepare a version change. After the change is reviewed and merged, `make release`
creates the corresponding GitHub release in `mgaitan/skippy-cov`.

The release workflow builds the package and publishes through PyPI Trusted
Publishing. Configure the PyPI publisher for owner `mgaitan`, repository
`skippy-cov`, and workflow `release.yml` before publishing a release.

# Pull Request Guidelines

Before you submit a pull request, check that it meets these guidelines:

1. The pull request should include tests.

2. If the pull request adds functionality, the docs should be updated.
   Put your new functionality into a function with a docstring, and add the feature to the list in `README.md`.
