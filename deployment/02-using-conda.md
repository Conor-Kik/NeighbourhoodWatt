# 2 Dependency Management with Conda (via Miniforge)

To solve our cross-platform C-library dependencies (such as X11 systems for PyQt6 or openmp for LightGBM), this project uses a unified Mamba/Conda workflow.
Miniforge bundles both conda and mamba (a fast, C++ alternative to the classic conda solver). It pulls directly from the conda-forge index, downloading pre-compiled binaries for Windows, macOS, and Linux so no local compilation is needed.

## 2.1 Modifying Dependencies
Because we are managing our environment state through a clean declarative system config layout, we modify our packages manually using a text editor instead of using automated CLI flags.
Open your root environment.yml file to add, pin, or adjust packages.

Packages requiring heavy compiled C-libraries, graphics bindings, or core data science tools go under the main dependencies: block list.
Packages that only exist on standard PyPI or require local editable source linkage go under the nested - pip: block array.

```yaml
dependencies:
  - lightgbm >=4.6,<5      # Heavy binary/C-library tools go here
  - pip
  - pip:
      - ckanapi >=4.11     # Pure-python pip dependencies go here
```

## 2.2 Managing & Updating the Environment
Always ensure your terminal session is pointed inside your active workspace container before working on the project:
```conda activate neighbourhoodwatt```

### Syncing & Applying Configuration Updates
If you modify environment.yml, pull new code changes from GitHub, or switch features branches, refresh your active local system environment by running:
```conda env update -f environment.yml```

## 2.3 Best Practices & Things to Avoid
Declare dependencies in `environment.yml`, then run `conda env update -f environment.yml` instead of installing packages individually.
Adding Type Stubs: For libraries without inline type annotations, add their types-* or -stubs packages to the Conda dependencies when available, or the pip list otherwise.
Running Application Commands: Because Conda explicitly links binaries straight into your active terminal path variables, you can drop runner middleware prefixes like `uv run` entirely. Run your tools directly:
```gridsync run```
```pytest```
