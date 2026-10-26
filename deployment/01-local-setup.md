# 1 Local Setup Instructions

## 1.1 Clone the Repository
Please see the tutorial [here](https://docs.github.com/en/repositories/creating-and-managing-repositories/cloning-a-repository).

## 1.2 Install Miniforge (Mamba/Conda)
We use Miniforge to manage our Python environments to automatically fetch required C-libraries.

### Windows
```curl.exe -L -O "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Windows-x86_64.exe"```
```start Miniforge3-Windows-x86_64.exe```
Follow the installer steps, then open Miniforge Prompt. To use PowerShell, run
`conda init powershell` in Miniforge Prompt and reopen PowerShell.

### Linux (x86_64)
The project environment requires glibc 2.28 or newer.

```curl -L -O "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh"```
```bash Miniforge3-Linux-x86_64.sh```

### Intel Macs
```curl -L -O "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-MacOSX-x86_64.sh"```
```bash Miniforge3-MacOSX-x86_64.sh```

### Apple Silicon Macs
```curl -L -O "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-MacOSX-arm64.sh"```
```bash Miniforge3-MacOSX-arm64.sh```

Allow the Unix installer to initialize your shell, then reopen the terminal.

## 1.3 Install Project Dependencies
Create the environment from the repository root. This also installs the local
project and its pip dependencies.

### Create the environment (only needed on first-time setup)
```conda env create -f environment.yml```

### Activate the project environment
```conda activate neighbourhoodwatt```

Note: Whenever dependencies change in environment.yml, refresh your setup by running
```conda env update -f environment.yml```


## 1.4 Initialise the Database
Create the database before running the application or simulations:
```gridsync reset-db```

Run this again after upgrading to the model-only forecast schema. It clears
existing cached data and simulation results.

## 1.5 Set Up Pre-Commit Hooks
Register the pre-commit checks for your local Git.
```pre-commit install```


## 1.6 Running Development Tools
Always make sure your workspace environment is active (`conda activate neighbourhoodwatt`) before calling development binaries.
There is no need to prefix commands with an engine runner.

### Running Tests
Run the automated suite from the repository root:
```pytest --ignore=tests/integration```

For full GUI simulations and explicit coverage options, see [the testing guide](../tests/README.md).

### Type Checking
Run mypy static type checking across source directories and test files:
```mypy --explicit-package-bases --config-file=pyproject.toml tests src/backend_v2 src/database_v2```

### Formatting & Linting
Run ruff manually to check and auto-fix code style issues:
```ruff check --fix```
```ruff format```
