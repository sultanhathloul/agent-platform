"""The sandbox memory budget (timeout budget PR, I7; ADR-0252): workers × the child limit + the
service's reserve must fit the container's memory limit, or the service refuses to start."""

import pytest

from app.rag.config import load_docs_config
from app.rag.ingest.sandbox import MemoryBudgetExceeded, check_memory_budget, container_memory_mib

GIB = 1024**3


def _cgroup(tmp_path, text: str):
    path = tmp_path / "memory.max"
    path.write_text(text + "\n")
    return (path,)


def _config(config_dir, **sandbox):
    config = load_docs_config(config_dir)
    limits = config.ingest.sandbox.model_copy(update=sandbox)
    return config.model_copy(update={"ingest": config.ingest.model_copy(update={"sandbox": limits})})


def test_the_shipped_limits_fit_the_shipped_container(config_dir, tmp_path):
    """config/docs.yaml in a 2 GiB container (docker-compose.yml mem_limit 2g): 2 × 640 + 512."""
    config = load_docs_config(config_dir)
    assert (config.ingest.sandbox.memory_mb, config.ingest.sandbox.parent_reserve_mb) == (640, 512)
    line = check_memory_budget(config.ingest.workers, config, _cgroup(tmp_path, str(2 * GIB)))
    assert line.endswith("= 1792 MiB; container limit 2048 MiB")


def test_the_limits_before_this_change_are_refused(config_dir, tmp_path):
    """The finding: two children of 1 GiB beside the service in 2 GiB."""
    with pytest.raises(MemoryBudgetExceeded) as refused:
        check_memory_budget(2, _config(config_dir, memory_mb=1024), _cgroup(tmp_path, str(2 * GIB)))
    assert "2560 MiB; container limit 2048 MiB: refusing to start" in str(refused.value)


def test_more_workers_than_the_container_holds_are_refused(config_dir, tmp_path):
    with pytest.raises(MemoryBudgetExceeded):
        check_memory_budget(3, load_docs_config(config_dir), _cgroup(tmp_path, str(2 * GIB)))


@pytest.mark.parametrize("text", ["max", str(2**63 - 4096)])  # v2 and v1 without a limit
def test_no_container_limit_is_no_refusal(config_dir, tmp_path, text):
    files = _cgroup(tmp_path, text)
    assert container_memory_mib(files) is None
    assert check_memory_budget(8, load_docs_config(config_dir), files).endswith("container limit none")


def test_no_cgroup_file_is_no_limit(tmp_path):
    assert container_memory_mib((tmp_path / "absent",)) is None
