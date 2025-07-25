<div align="center">

<pre>
 ██████╗██╗      █████╗ ██████╗ ███████╗
██╔════╝██║     ██╔══██╗██╔══██╗██╔════╝
██║     ██║     ███████║██████╔╝█████╗  
██║     ██║     ██╔══██║██╔══██╗██╔══╝  
╚██████╗███████╗██║  ██║██████╔╝███████╗
 ╚═════╝╚══════╝╚═╝  ╚═╝╚═════╝ ╚══════╝

Command-line-interface Launcher for AIND Behavior Experiments
</pre>
</div>


[![Documentation](https://camo.githubusercontent.com/d7111793c727ad754311b21d4f083d842f5071163a74f406822a63eb0fb2af69/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f446f63756d656e746174696f6e2d626c75653f636f6c6f723d344138424431266c696e6b3d6874747073253341253246253246616c6c656e6e657572616c64796e616d6963732e6769746875622e696f253246426f6e7361692e416c6c656e4e657572616c44796e616d696373253246)](https://allenneuraldynamics.github.io/clabe/)
![CI](https://github.com/AllenNeuralDynamics/Aind.Behavior.ExperimentLauncher/actions/workflows/ci.yml/badge.svg)
[![PyPI - Version](https://img.shields.io/pypi/v/aind-clabe)](https://pypi.org/project/aind-clabe/)
[![License](https://img.shields.io/badge/license-MIT-brightgreen)](LICENSE)
[![ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)

# clabe

A library for building workflows for behavior experiments.

> ⚠️ **Caution:**  
> This repository is currently under active development and is subject to frequent changes. Features and APIs may evolve without prior notice.

## Installing and Upgrading

If you choose to clone the repository, you can install the package by running the following command from the root directory of the repository:

```bash
pip install .
```

Otherwise, you can use pip:

```bash
pip install aind-clabe
```

## Getting started and API usage

The library provides a main class "Launcher" that can be used to create a linear workflow for behavior experiments. These workflows rely on modular interfaces that can be used to interact with various components of the experiment and other services.
Some of these services are specific for AIND:

- [aind-data-schema](https://github.com/AllenNeuralDynamics/aind-data-schema)
- [aind-data-schema-models](https://github.com/AllenNeuralDynamics/aind-data-schema-models)
- [aind-watchdog-service](https://github.com/AllenNeuralDynamics/aind-watchdog-service)
- [aind-data-mapper](https://github.com/AllenNeuralDynamics/aind-metadata-mapper)

We will also try to scope all dependencies of the related to AIND Services to its own optional dependency list in the `./pyproject.toml` file of this repository. Therefore, in order to use this module, you will need to install these optional dependencies by running:

```uv sync --extra aind-services```

A basic example of how to use the Launcher class can be found in the `examples` directory of this repository.

## Contributors

Contributions to this repository are welcome! However, please ensure that your code adheres to the recommended DevOps practices below:

### Linting

We use [ruff](https://docs.astral.sh/ruff/) as our primary linting tool.

### Testing

Attempt to add tests when new features are added.
To run the currently available tests, run `uv run -m unittest` from the root of the repository.

### Lock files

We use [uv](https://docs.astral.sh/uv/) to manage our lock files and therefore encourage everyone to use uv as a package manager as well.
