"""Les paquets proposés au PC : seulement ceux qui ont le bon nom, sous leur nom exact."""

import hashlib
import os

from mydock_dock import clients
from mydock_dock.clients import ClientRegistry, version_key


def write(directory, name, content=b"deb-content"):
    path = directory / name
    path.write_bytes(content)
    return path


def test_a_missing_or_empty_folder_offers_nothing(tmp_path):
    assert ClientRegistry(tmp_path / "absent").scan() == []
    assert ClientRegistry(tmp_path).latest() is None
    assert ClientRegistry(tmp_path).get("mydock-agent_0.7.0_all.deb") is None


def test_the_agent_package_is_found_with_its_version_size_and_checksum(tmp_path):
    content = b"\x00\x01paquet" * 1000
    write(tmp_path, "mydock-agent_0.7.0_all.deb", content)
    [client] = ClientRegistry(tmp_path).scan()
    assert client.file == "mydock-agent_0.7.0_all.deb"
    assert client.version == "0.7.0"
    assert client.size == len(content)
    assert client.sha256 == hashlib.sha256(content).hexdigest()
    assert client.path == tmp_path / "mydock-agent_0.7.0_all.deb"
    assert client.public() == {
        "id": "deb",
        "label": "Ubuntu, Debian et dérivés",
        "file": "mydock-agent_0.7.0_all.deb",
        "version": "0.7.0",
        "size": len(content),
        "sha256": client.sha256,
    }
    assert "path" not in client.public()  # le chemin sur le Pi ne sort jamais


def test_anything_that_is_not_exactly_the_agent_package_is_ignored(tmp_path):
    for name in (
        "mydock-agent_0.7.0_all.deb.bak",
        "mydock-agent_0.7_arm64.deb",
        "mydock-agent_x.y_all.deb",
        "mydock-agent_0.7.0_all.deb ",
        "Mydock-agent_0.7.0_all.deb",
        "evil.deb",
        "mydock-agent_0.7.0_all.DEB",
        ".mydock-agent_0.7.0_all.deb",
        "léger.deb",
    ):
        write(tmp_path, name)
    assert ClientRegistry(tmp_path).scan() == []


def test_a_symbolic_link_a_folder_or_an_empty_file_is_not_a_package(tmp_path):
    target = write(tmp_path, "autre.bin")
    (tmp_path / "mydock-agent_0.7.0_all.deb").symlink_to(target)
    (tmp_path / "mydock-agent_0.8.0_all.deb").mkdir()
    write(tmp_path, "mydock-agent_0.9.0_all.deb", b"")
    assert ClientRegistry(tmp_path).scan() == []


def test_a_file_far_bigger_than_any_package_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(clients, "MAX_CLIENT_BYTES", 100)
    write(tmp_path, "mydock-agent_0.7.0_all.deb", b"x" * 101)
    write(tmp_path, "mydock-agent_0.6.0_all.deb", b"x" * 100)
    assert [c.version for c in ClientRegistry(tmp_path).scan()] == ["0.6.0"]


def test_versions_are_compared_as_numbers_not_as_text(tmp_path):
    for version in ("0.9.0", "0.10.0", "0.2.5", "1.0"):
        write(tmp_path, f"mydock-agent_{version}_all.deb", version.encode())
    registry = ClientRegistry(tmp_path)
    assert [c.version for c in registry.scan()] == ["1.0", "0.10.0", "0.9.0", "0.2.5"]
    assert registry.latest().version == "1.0"
    assert version_key("0.10.0") > version_key("0.9.9")


def test_a_package_is_found_by_its_exact_name_only(tmp_path):
    write(tmp_path, "mydock-agent_0.7.0_all.deb")
    registry = ClientRegistry(tmp_path)
    assert registry.get("mydock-agent_0.7.0_all.deb") is not None
    for name in (
        "../mydock-agent_0.7.0_all.deb",
        "/etc/passwd",
        "mydock-agent_0.7.0_all.deb/",
        "mydock-agent_0.7.0_all.deb\x00.txt",
        "MYDOCK-AGENT_0.7.0_ALL.DEB",
        "",
    ):
        assert registry.get(name) is None, repr(name)


def test_the_checksum_is_computed_once_and_again_when_the_file_changes(tmp_path, monkeypatch):
    path = write(tmp_path, "mydock-agent_0.7.0_all.deb", b"premier")
    registry = ClientRegistry(tmp_path)
    reads = []
    original = registry._sha256

    def counting(*args):
        reads.append(1)
        return original(*args)

    monkeypatch.setattr(registry, "_sha256", counting)
    first = registry.scan()[0].sha256
    registry.scan()
    registry.scan()
    assert len(reads) == 1  # la liste n'est pas relue tant que le dossier ne change pas
    path.write_bytes(b"second contenu, plus long")
    os.utime(path, ns=(1, 2))
    second = registry.scan()[0].sha256
    assert second == hashlib.sha256(b"second contenu, plus long").hexdigest() != first
    assert len(reads) == 2


def test_a_package_that_disappears_leaves_the_list(tmp_path):
    path = write(tmp_path, "mydock-agent_0.7.0_all.deb")
    registry = ClientRegistry(tmp_path)
    assert registry.latest() is not None
    path.unlink()
    assert registry.scan() == []
    assert registry.get("mydock-agent_0.7.0_all.deb") is None
