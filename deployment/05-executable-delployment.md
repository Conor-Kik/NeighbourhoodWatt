# Guide to using the Executable

This guide covers building and running the Windows executable.

## 5.1 Building the Executable

We use `pyinstaller` to build an executable from the GUI which was developed using
`PyQt6`. `pyinstaller` has been added to the `neighbourhoodwatt` conda environment so please see
[using conda](./02-using-conda.md) for more details on how to get the correct python
environment.

All of the files required to build the `.exe` have been put in the `./build` directory.
To make the executable run the following command:

```
python -m PyInstaller --noconfirm --clean --workpath .cache/pyinstaller-work build/NeighbourhoodWatt.spec
```

Then the final `.exe` file can be found at `./dist/NeighbourhoodWatt.exe`. This can now
be run like any other normal executable.

The builder creates a clean default database from the schema and seed SQL in
`.cache/executable-seed/`. It never packages your working database or saved runs.
Schema, seed data and offline postcode data are included in the executable.
LightGBM libraries are collected by PyInstaller's installed LightGBM hook.

The executable opens the GUI without a console window. Rebuild it after pulling
code changes; an older executable does not pick up changes from the repository.

## 5.2 Running the executable

- Python and the repository are not required on the destination computer.
- The writable database is stored at `%LOCALAPPDATA%\GridSync\app_database.db`.
  Existing data is preserved when launching a newer executable.
- Settings → Storage → Reset database closes the app after confirmation. Opening
  the executable again recreates the database from bundled defaults.
- Internet access is required to download missing weather and AEMO data.
- The standard build uses HiGHS and excludes Gurobi. See the root third-party notices before changing solver packaging.
- The current build configuration targets Windows. macOS/Linux builds require
  separate platform validation.
