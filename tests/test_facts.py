# facts.py CLI tests (jsonfile path) — migrated from the munro-ansible repo.
import json, subprocess, sys, pathlib, os

FACTS = pathlib.Path(__file__).resolve().parent.parent / "facts.py"

def _run(args, env=None):
    return subprocess.run([sys.executable, str(FACTS), *args],
                           capture_output=True, text=True, env={**os.environ, **(env or {})})

def test_list_from_jsonfile(tmp_path):
    cache = tmp_path / ".ansible-facts"
    cache.mkdir()
    (cache / "hostA").write_text(json.dumps({
        "ansible_facts": {"ansible_distribution": "Debian",
                          "ansible_distribution_version": "12",
                          "ansible_kernel": "6.1.0",
                          "ansible_architecture": "x86_64",
                          "ansible_processor_vcpus": 4,
                          "ansible_memtotal_mb": 8000,
                          "ansible_default_ipv4": {"address": "10.0.0.5"},
                          "ansible_pkg_mgr": "apt"}}))
    r = _run(["list", "--cache-dir", str(cache), "--no-redis"])
    assert r.returncode == 0, r.stderr
    assert "hostA" in r.stdout and "Debian" in r.stdout

def test_query_filter(tmp_path):
    cache = tmp_path / ".ansible-facts"
    cache.mkdir()
    for h, distro in (("a", "Debian"), ("b", "Fedora")):
        (cache / h).write_text(json.dumps({"ansible_facts": {
            "ansible_distribution": distro, "ansible_kernel": "6.1",
            "ansible_distribution_version": "1", "ansible_architecture": "x86_64",
            "ansible_processor_vcpus": 1, "ansible_memtotal_mb": 1024,
            "ansible_default_ipv4": {"address": "1.2.3.4"}, "ansible_pkg_mgr": "dnf"}}))
    r = _run(["query", "--where", "ansible_distribution==Fedora",
              "--cache-dir", str(cache), "--no-redis"])
    assert r.returncode == 0, r.stderr
    assert "b" in r.stdout and "Fedora" in r.stdout
    assert "Debian" not in r.stdout
