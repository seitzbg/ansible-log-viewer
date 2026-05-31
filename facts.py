#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["rich", "redis"]
# ///
"""Ansible fact store CLI — reads the ansible jsonfile cache, optionally mirrors
it into redis, writes curated YAML snapshots, and answers queries.

Read path for queries: redis first (if configured), automatic fallback to the
jsonfile cache. Ansible itself never depends on redis (jsonfile is the system
of record), and redis is entirely optional here too.

Configuration: ~/.config/ansible-log-viewer/config.toml (override with
$ALV_CONFIG). Relevant keys: ansible_home, cache_dir, snapshot_dir,
facts_playbook, and an optional [redis] table (host/port/password). The
ALV_HOME / ALV_CACHE_DIR / ALV_SNAPSHOT_DIR env vars override the file.
"""
import argparse, json, os, re, sys, subprocess, tomllib
from pathlib import Path

_CONFIG_PATH = (
    Path(os.environ["ALV_CONFIG"]).expanduser()
    if os.environ.get("ALV_CONFIG")
    else Path.home() / ".config" / "ansible-log-viewer" / "config.toml"
)


def _load_config() -> dict:
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    return {}


_cfg = _load_config()


def _resolve_path(env_var: str, cfg_key: str, default: Path) -> Path:
    if os.environ.get(env_var):
        return Path(os.environ[env_var]).expanduser()
    if cfg_key in _cfg:
        return Path(_cfg[cfg_key]).expanduser()
    return default


# Root of your Ansible repo (defaults to the current directory).
ANSIBLE_HOME = _resolve_path("ALV_HOME", "ansible_home", Path.cwd())
# Ansible's jsonfile fact cache — set `fact_caching = jsonfile` and
# `fact_caching_connection = .ansible-facts` in your ansible.cfg.
DEFAULT_CACHE = _resolve_path("ALV_CACHE_DIR", "cache_dir", ANSIBLE_HOME / ".ansible-facts")
# Where curated per-host YAML snapshots are written (the log viewer reads these
# to enrich its Hosts/Facts tabs).
SNAPSHOT_DIR = _resolve_path("ALV_SNAPSHOT_DIR", "snapshot_dir", ANSIBLE_HOME / "facts-snapshot")
# Playbook that `facts.py refresh` runs to (re)gather facts into the cache.
FACTS_PLAYBOOK = _cfg.get("facts_playbook", "playbooks/gather-all-facts.yml")

# Curated, stable facts only — volatile facts are excluded so snapshot git
# diffs reflect real infra drift.
CURATED = [
    "ansible_distribution", "ansible_distribution_version",
    "ansible_distribution_release", "ansible_kernel", "ansible_architecture",
    "ansible_virtualization_type", "ansible_virtualization_role",
    "ansible_processor_count", "ansible_processor_vcpus",
    "ansible_memtotal_mb", "ansible_pkg_mgr",
]

def _facts_of(blob: dict) -> dict:
    # mitogen/jsonfile cache wraps facts as a JSON string under __payload__.
    if isinstance(blob, dict) and "__payload__" in blob:
        try:
            return json.loads(blob["__payload__"])
        except (ValueError, TypeError):
            return {}
    return blob.get("ansible_facts", blob)

# ansible's cache key may carry a session prefix (e.g. mitogen's "s1_").
_PREFIX_RE = re.compile(r"^s\d+_")

def _norm_host(name: str) -> str:
    return _PREFIX_RE.sub("", name)

def _canonical(name: str, facts: dict) -> str:
    """Collapse a host's short inventory alias to its FQDN.

    Some dynamic inventories (e.g. the Proxmox source) register a host under a
    bare short name *in addition to* its FQDN, so the jsonfile cache holds two
    entries (e.g. `web01` and `web01.example.net`) for one machine — which
    produced byte-identical duplicate snapshots. We only merge when the host's
    own ansible_fqdn is `name` itself or `name.<domain>`, so two genuinely
    different hosts that merely share a short prefix are never conflated."""
    fqdn = facts.get("ansible_fqdn")
    if fqdn and (fqdn == name or fqdn.startswith(name + ".")):
        return fqdn
    return name

def load_jsonfile(cache_dir: Path) -> dict:
    """Newest write wins per normalized host (drops the cache prefix and
    de-dupes stale legacy entries against current ones)."""
    hosts = {}
    if not cache_dir.is_dir():
        return hosts
    files = [f for f in cache_dir.iterdir() if f.is_file()]
    for f in sorted(files, key=lambda p: p.stat().st_mtime):  # old → new
        try:
            facts = _facts_of(json.loads(f.read_text()))
        except (ValueError, OSError):
            continue
        hosts[_norm_host(f.name)] = facts
    return hosts

def _redis_conf():
    """Optional redis connection for the fact cache. Reads the [redis] table in
    config.toml (host / port / password) or the REDIS_HOST / REDIS_PORT /
    REDIS_PASSWORD env vars (env wins). Returns None when no host is configured,
    so every caller falls back to the jsonfile cache — redis is never required.
    """
    rc = _cfg.get("redis", {}) if isinstance(_cfg.get("redis"), dict) else {}
    host = os.environ.get("REDIS_HOST") or rc.get("host")
    if not host:
        return None
    port = os.environ.get("REDIS_PORT") or rc.get("port") or 6379
    pw = os.environ.get("REDIS_PASSWORD") or rc.get("password") or None
    try:
        return host, int(port), pw
    except (TypeError, ValueError):
        return None

def load_redis():
    conf = _redis_conf()
    if not conf:
        return None
    host, port, pw = conf
    try:
        import redis
        r = redis.Redis(host=host, port=port, password=pw,
                         socket_connect_timeout=3, decode_responses=True)
        r.ping()
        hosts = {}
        for key in r.scan_iter("facts:*"):
            raw = json.loads(r.get(key))
            hosts[_norm_host(key.split(":", 1)[1])] = _facts_of(raw)
        return hosts or None
    except Exception:
        return None

def get_hosts(args):
    if not args.no_redis:
        h = load_redis()
        if h:
            return h, "redis"
    return load_jsonfile(Path(args.cache_dir)), "jsonfile"

def _dig(facts: dict, key: str):
    return facts.get(key)

def _real_only(hosts: dict) -> dict:
    """Drop cache entries with no real gathered facts (no ansible_distribution
    — stale/empty 'ghost' rows). cmd_host bypasses this so any raw key is
    still inspectable by exact name."""
    return {n: f for n, f in hosts.items() if f.get("ansible_distribution")}

def cmd_list(args):
    from rich.console import Console
    from rich.table import Table
    hosts, src = get_hosts(args)
    hosts = _real_only(hosts)
    t = Table(title=f"hosts ({len(hosts)}) — source: {src}")
    for c in ("host", "distro", "ver", "kernel", "vCPU", "RAM(MB)", "IP"):
        t.add_column(c)
    for name in sorted(hosts):
        f = hosts[name]
        ip = (f.get("ansible_default_ipv4") or {}).get("address", "-")
        t.add_row(name, str(f.get("ansible_distribution", "-")),
                  str(f.get("ansible_distribution_version", "-")),
                  str(f.get("ansible_kernel", "-")),
                  str(f.get("ansible_processor_vcpus", "-")),
                  str(f.get("ansible_memtotal_mb", "-")), str(ip))
    c = Console()
    c.print(t)
    c.print()

def cmd_host(args):
    hosts, _ = get_hosts(args)
    f = hosts.get(args.name)
    if f is None:
        print(f"no facts for {args.name}", file=sys.stderr); sys.exit(1)
    print(json.dumps(f, indent=2, sort_keys=True))

def cmd_query(args):
    from rich.console import Console
    from rich.table import Table
    m = re.match(r"^\s*([\w.]+)\s*(==|!=|~=)\s*(.+?)\s*$", args.where)
    if not m:
        print("bad --where (use fact==val, fact!=val, fact~=regex)", file=sys.stderr)
        sys.exit(2)
    key, op, val = m.group(1), m.group(2), m.group(3)
    hosts, src = get_hosts(args)
    hosts = _real_only(hosts)
    rows = []
    for name in sorted(hosts):
        actual = _dig(hosts[name], key)
        s = "" if actual is None else str(actual)
        ok = (op == "==" and s == val) or (op == "!=" and s != val) or \
             (op == "~=" and re.search(val, s) is not None)
        if ok:
            rows.append((name, s))
    t = Table(title=f"{key} {op} {val} — {len(rows)} match (source: {src})")
    t.add_column("host"); t.add_column(key)
    for n, s in rows:
        t.add_row(n, s)
    c = Console()
    c.print(t)
    c.print()

def cmd_mirror(args):
    """Read jsonfile cache → redis + curated YAML snapshots. Run from the
    gather-all-facts.yml playbook. Never fatal: redis errors are warnings.
    Cache entries without real gathered facts (no ansible_distribution) are
    skipped so the committed snapshot audit trail stays meaningful."""
    hosts = load_jsonfile(Path(args.cache_dir))
    if not hosts:
        print("no jsonfile cache to mirror", file=sys.stderr); return
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        import yaml
    except ModuleNotFoundError:
        print(
            f"FATAL: PyYAML not importable by this interpreter ({sys.executable}). "
            "Run facts.py with the ansible interpreter — the playbook uses "
            "ansible_playbook_python; ansible-core hard-depends on PyYAML.",
            file=sys.stderr,
        )
        sys.exit(2)
    real = {}
    skipped = 0
    for name, f in hosts.items():
        if not f.get("ansible_distribution"):
            skipped += 1
            continue
        real[_canonical(name, f)] = f  # short alias collapses into the FQDN
    for name, f in real.items():
        curated = {k: f.get(k) for k in CURATED if f.get(k) is not None}
        # Inside an LXC, ansible_processor_vcpus is derived from
        # /proc/cpuinfo "cpu cores"/"siblings", which lxcfs leaves at the
        # *host* node's topology — so it reads 64/80 instead of the
        # container's Proxmox `cores` limit. ansible_processor_nproc is
        # `nproc`, which respects the container cpuset and equals the
        # configured cores. Prefer it for LXC (memory needs no such fix:
        # lxcfs virtualizes /proc/meminfo so ansible_memtotal_mb is right).
        if f.get("ansible_virtualization_type") == "lxc" \
                and f.get("ansible_processor_nproc") is not None:
            curated["ansible_processor_vcpus"] = f["ansible_processor_nproc"]
        ipv4 = f.get("ansible_default_ipv4") or {}
        curated["primary_ipv4"] = ipv4.get("address")
        curated["zfs_present"] = bool(f.get("ansible_zfs_datasets")) or \
            "zfs" in (f.get("ansible_kernel_modules") or [])
        (SNAPSHOT_DIR / f"{name}.yml").write_text(
            yaml.safe_dump(curated, default_flow_style=False, sort_keys=True))
    # Drop superseded short-name snapshots: if we wrote `<host>.<domain>.yml`
    # this run, the bare `<host>.yml` is a stale duplicate alias of it.
    removed = 0
    for name in real:
        short = name.split(".", 1)[0]
        if short == name:
            continue
        dup = SNAPSHOT_DIR / f"{short}.yml"
        if dup.is_file():
            dup.unlink()
            removed += 1
    if skipped:
        print(f"skipped {skipped} cache entries without ansible_distribution")
    if removed:
        print(f"pruned {removed} superseded short-name snapshot duplicates")
    conf = _redis_conf()
    if not conf:
        print("WARN: redis creds unavailable; snapshots written, redis skipped")
        return
    host, port, pw = conf
    try:
        import redis
        r = redis.Redis(host=host, port=port, password=pw,
                         socket_connect_timeout=3, decode_responses=True)
        # Full resync: prune any facts:* key not in the current real set
        # (clears stale/prefixed zombie keys from earlier runs).
        wanted = {f"facts:{name}" for name in real}
        stale = [k for k in r.scan_iter("facts:*") if k not in wanted]
        if stale:
            r.delete(*stale)
        for name, f in real.items():
            r.set(f"facts:{name}", json.dumps(f))
        msg = f"mirrored {len(real)} hosts to redis + {SNAPSHOT_DIR}"
        if stale:
            msg += f"; pruned {len(stale)} stale redis keys"
        print(msg)
    except Exception as e:
        print(f"WARN: redis mirror failed ({e}); snapshots still written")

def cmd_refresh(args):
    cmd = ["ansible-playbook", FACTS_PLAYBOOK]
    if args.limit:
        cmd += ["--limit", args.limit]
    sys.exit(subprocess.call(cmd, cwd=ANSIBLE_HOME))

def main():
    # common flags accepted both before and after the subcommand
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--cache-dir", default=str(DEFAULT_CACHE))
    common.add_argument("--no-redis", action="store_true", help="skip redis, use jsonfile")

    p = argparse.ArgumentParser(description="Ansible fact store", parents=[common])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", parents=[common]).set_defaults(fn=cmd_list)
    hp = sub.add_parser("host", parents=[common]); hp.add_argument("name"); hp.set_defaults(fn=cmd_host)
    qp = sub.add_parser("query", parents=[common]); qp.add_argument("--where", required=True); qp.set_defaults(fn=cmd_query)
    sub.add_parser("mirror", parents=[common]).set_defaults(fn=cmd_mirror)
    rp = sub.add_parser("refresh", parents=[common]); rp.add_argument("--limit", default=""); rp.set_defaults(fn=cmd_refresh)
    args = p.parse_args()
    args.fn(args)

if __name__ == "__main__":
    main()
