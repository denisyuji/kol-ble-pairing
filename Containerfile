# Build image for the Linux executable. The binary needs a glibc at least as
# new as the one it was built with, so build on an old one: Debian bullseye
# has glibc 2.31 (runs on Ubuntu 20.04+, Debian 11+, RHEL 9+).
# Used by build-container.sh; it runs build.sh inside.
FROM docker.io/library/python:3.12-slim-bullseye

# PyInstaller needs objdump (binutils) to collect shared libraries.
RUN apt-get update \
 && apt-get install -y --no-install-recommends binutils \
 && rm -rf /var/lib/apt/lists/*
