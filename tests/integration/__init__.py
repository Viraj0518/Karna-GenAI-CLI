"""End-to-end integration tests for Karna.

These tests exercise full multi-module pipelines (agent loop + tools +
sessions + skills + hooks + memory) using a ``MockProvider`` so they
never hit the network. They complement the narrower unit tests in the
parent ``tests/`` package.
"""
