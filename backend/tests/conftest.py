"""Shared fixtures."""

import pytest

from cfo.usage import recorder


class _NullSink:
    def write(self, record) -> None:  # noqa: ANN001
        pass


@pytest.fixture(autouse=True)
def no_llm_usage_database_writes():
    """FakeLLM calls in unrelated tests must not write `llm_usage` rows into the shared database (T032).
    Tests of the recording itself install their own sink with `recorder.set_sink`."""
    recorder.set_sink(_NullSink())
    yield
    recorder.set_sink(None)
