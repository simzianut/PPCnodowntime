# 0063: Training image fixes from the first server build: no bytecode precompile, no container network

- Date: 2026-11-08
- Who: vlad + vlad-claude
- Status: accepted
- Supersedes: — (amends 0058 and 0062)
- Affects: docker/train.Dockerfile, docker-compose.train.yml, docker/train_forecasters.sh

## Context
The first build on the GPU server (BENGEE-PROD-NVIDIA01) failed twice:
1. `uv python install 3.12` panicked with `No file descriptors available (os error 24)`. `UV_COMPILE_BYTECODE=1` made uv bytecode-compile the whole standard library on many threads, which ran past the build container's open-file limit.
2. `docker compose run` failed with `all predefined address pools have been fully subnetted`. The server's Docker has no free subnet left for a new compose network.

## Decision
- **Drop `UV_COMPILE_BYTECODE`** and cap uv at `UV_CONCURRENT_INSTALLS=4`. Python compiles bytecode lazily on first import, which costs a few seconds once per container.
- **Training containers run with `network_mode: none`.** They read mounted data and write mounted folders, and need no network. The setting stops compose from creating a network and isolates the job on a production host. The image build keeps its own network access for downloads.
- **`train_forecasters.sh` stops** every stage that runs a container when the `ppc-train:latest` image is missing. Before this fix, the later stages ran after a failed build.

## Alternatives considered
- Raise the file limit (`--ulimit nofile`): BuildKit doesn't take it per build, and we shouldn't change daemon settings on a production server.
- Reuse an existing Docker network: couples our job to someone else's network.

## Consequences
- Nothing in a training container can reach the internet. That is intended, since runs must not download anything.
