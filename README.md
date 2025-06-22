# clabe

![CI](https://github.com/AllenNeuralDynamics/Aind.Behavior.ExperimentLauncher/actions/workflows/ci.yml/badge.svg)
[![PyPI - Version](https://img.shields.io/pypi/v/aind-clabe)](https://pypi.org/project/aind-clabe/)
[![License](https://img.shields.io/badge/license-MIT-brightgreen)](LICENSE)
[![ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)

Source for for a minimal framework that can be used to build experimental interfaces

---

## Introduction


The launcher module of this library provides a frontend interface to launch, monitor and manage behavior tasks using this library. It is also designed to interface with the AIND Services.

### AIND Services Module

While we will try to keep this library up to date, it is recommended to check the original repositories for the most recent updates.

A list of services relevant for this library include:

- [aind-data-schema](https://github.com/AllenNeuralDynamics/aind-data-schema)
- [aind-data-schema-models](https://github.com/AllenNeuralDynamics/aind-data-schema-models)
- [aind-watchdog-service](https://github.com/AllenNeuralDynamics/aind-watchdog-service)
- [aind-slims-api](https://github.com/AllenNeuralDynamics/aind-slims-api)
- [aind-data-mapper](https://github.com/AllenNeuralDynamics/aind-metadata-mapper)

We will generally try to wrap the services provided by these repositories into a more user-friendly and opinionated interface that can be used by the Aind Behavior Services library and other repositories.

We will also try to scope all dependencies of the related to AIND Services to its own optional dependency list in the `./pyproject.toml` file of this repository. Therefore, in order to use this module, you will need to install these optional dependencies by running:

```pip install .[aind-services]```

Feedback and contributions are welcome!

## General instructions

This repository follows the project structure laid out in the [Aind.Behavior.Services repository](https://github.com/AllenNeuralDynamics/Aind.Behavior.Services).

