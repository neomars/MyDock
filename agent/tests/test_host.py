import asyncio
import json
from pathlib import Path

from mydock_agent.collectors.host import (
    HostProvider,
    default_route_interface,
    list_interfaces,
    read_pretty_os_name,
)
from mydock_agent.demo import DemoHostProvider

ROUTES = (
    "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
    "wlp0s20f3\t00000000\t0102000A\t0003\t0\t0\t600\t00000000\t0\t0\t0\n"
    "wlp0s20f3\t0002000A\t00000000\t0001\t0\t0\t600\t00FFFFFF\t0\t0\t0\n"
    "enp0s31f6\t00000000\t0102000A\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"
)


def build_sys(root: Path) -> Path:
    """Arbre /sys factice : un portable avec Ethernet débranché, Wi-Fi actif et Docker."""
    sys_root = root / "sys"
    (sys_root / "class" / "net").mkdir(parents=True)

    def add(name, mac, *, virtual=False, wifi=False, state="up", carrier="1", type_="1"):
        base = "devices/virtual/net" if virtual else "devices/pci0000:00/0000:00:1f.6/net"
        directory = sys_root / base / name
        directory.mkdir(parents=True)
        (directory / "address").write_text(mac + "\n")
        (directory / "operstate").write_text(state + "\n")
        (directory / "carrier").write_text(carrier + "\n")
        (directory / "type").write_text(type_ + "\n")
        if wifi:
            (directory / "wireless").mkdir()
        (sys_root / "class" / "net" / name).symlink_to(directory)

    add("lo", "00:00:00:00:00:00", virtual=True, state="unknown", type_="772")
    add("enp0s31f6", "02:00:5E:10:00:01", state="down", carrier="0")
    add("wlp0s20f3", "02:00:5e:10:00:02", wifi=True)
    add("docker0", "02:42:00:00:00:03", virtual=True, state="down", carrier="0")
    add("tun0", "00:00:00:00:00:00", virtual=True, state="unknown", type_="65534")
    add("enx001122", "00:00:00:00:00:00", state="up")  # MAC nulle : ignorée
    return sys_root


def build_proc(root: Path) -> Path:
    proc = root / "proc"
    (proc / "net").mkdir(parents=True)
    (proc / "net" / "route").write_text(ROUTES)
    return proc


def test_default_route_picks_the_lowest_metric(tmp_path):
    proc = build_proc(tmp_path)
    assert default_route_interface(proc / "net" / "route") == "enp0s31f6"


def test_default_route_absent_or_garbage(tmp_path):
    assert default_route_interface(tmp_path / "nope") is None
    route = tmp_path / "route"
    route.write_text("Iface\tDestination\n\nbizarre\nwlp\t00000000\t0\tZZ\t0\t0\tx\n")
    assert default_route_interface(route) is None


def test_only_physical_interfaces_are_listed(tmp_path):
    interfaces = list_interfaces(
        sys_root=build_sys(tmp_path),
        proc_root=build_proc(tmp_path),
        addresses={"wlp0s20f3": ["192.0.2.20"], "docker0": ["172.17.0.1"]},
    )
    assert [i["name"] for i in interfaces] == ["enp0s31f6", "wlp0s20f3"]


def test_ethernet_unplugged_and_wifi_up(tmp_path):
    wired, wifi = list_interfaces(
        sys_root=build_sys(tmp_path),
        proc_root=build_proc(tmp_path),
        addresses={"wlp0s20f3": ["192.0.2.20"]},
    )
    assert wired == {
        "name": "enp0s31f6",
        "mac": "02:00:5e:10:00:01",
        "kind": "ethernet",
        "up": False,
        "ipv4": [],
        "default": True,
    }
    assert wifi["kind"] == "wifi"
    assert wifi["mac"] == "02:00:5e:10:00:02"  # toujours en minuscules
    assert wifi["up"] is True
    assert wifi["ipv4"] == ["192.0.2.20"]
    assert wifi["default"] is False


def test_missing_sysfs_gives_an_empty_list(tmp_path):
    assert list_interfaces(sys_root=tmp_path / "absent", proc_root=tmp_path, addresses={}) == []


def test_os_release(tmp_path):
    path = tmp_path / "os-release"
    path.write_text('NAME="Ubuntu"\nPRETTY_NAME="Ubuntu 26.04.1 LTS"\nVERSION_ID="26.04"\n')
    assert read_pretty_os_name(path) == "Ubuntu 26.04.1 LTS"
    assert read_pretty_os_name(tmp_path / "absent") == "Linux"
    path.write_text("PRETTY_NAME='Debian GNU/Linux 13 (trixie)'\n")
    assert read_pretty_os_name(path) == "Debian GNU/Linux 13 (trixie)"


def test_host_snapshot_shape():
    provider = HostProvider(
        collect_interfaces=lambda: [{"name": "x"}],
        env={"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "ubuntu:GNOME"},
    )
    data = provider.collect()
    assert json.loads(json.dumps(data)) == data  # sérialisable
    assert data["session"] == {"type": "wayland", "desktop": "GNOME"}
    assert data["interfaces"] == [{"name": "x"}]
    assert data["agent"]["demo"] is False
    assert data["hostname"]
    assert data["boot_time"] > 1_600_000_000


async def test_host_provider_republishes_periodically():
    provider = HostProvider(refresh=0.02, collect_interfaces=list, env={})
    published: list[dict] = []

    async def publish(data):
        published.append(data)
        return True

    task = asyncio.create_task(provider.run(publish))
    await asyncio.sleep(0.12)
    task.cancel()
    assert len(published) >= 3


def test_demo_data_is_obviously_fake():
    data = DemoHostProvider().collect()
    assert data["hostname"] == "pc-demo"
    assert data["agent"]["demo"] is True
    for itf in data["interfaces"]:
        assert itf["mac"].startswith("02:")  # adresse administrée localement
        assert all(ip.startswith("192.0.2.") for ip in itf["ipv4"])  # RFC 5737
