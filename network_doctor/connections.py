from __future__ import annotations

import shutil
import socket
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from .client import MikroTikClient

CONNECTION_LABELS = {
    "API": "api",
    "API-SSL": "api_ssl",
    "SSH-туннель": "ssh",
}
CONNECTION_NAMES = {value: key for key, value in CONNECTION_LABELS.items()}
DEFAULT_PORTS = {"api": 8728, "api_ssl": 8729, "ssh": 22}


@dataclass(frozen=True)
class ConnectionConfig:
    connection_type: str = "api"
    host: str = ""
    port: int = 8728
    username: str = "admin"
    password: str = ""
    ssl_verify: bool = False
    ssh_host: str = ""
    ssh_port: int = 22
    ssh_user: str = ""
    ssh_key: str = ""
    ssh_remote_host: str = ""
    ssh_remote_port: int = 8728
    timeout: int = 8

    @property
    def label(self) -> str:
        return CONNECTION_NAMES.get(self.connection_type, self.connection_type)

    def validate(self) -> None:
        if not self.host.strip():
            raise ValueError("Укажите IP-адрес роутера или SSH-сервера.")
        if not self.username.strip():
            raise ValueError("Укажите логин администратора.")
        if not 1 <= int(self.port) <= 65535:
            raise ValueError("Порт должен быть числом от 1 до 65535.")
        if self.connection_type == "ssh":
            if not self.ssh_remote_host.strip():
                raise ValueError("Укажите IP-адрес роутера за SSH-туннелем.")
            if not self.ssh_user.strip():
                raise ValueError("Укажите SSH-пользователя.")
            if not 1 <= int(self.ssh_remote_port) <= 65535:
                raise ValueError("Порт API за туннелем должен быть от 1 до 65535.")


class SshTunnel:
    """Manage a local OpenSSH port-forward for a RouterOS API connection."""

    def __init__(self, config: ConnectionConfig) -> None:
        self.config = config
        self.local_port = _free_port()
        self.process: subprocess.Popen[str] | None = None

    def __enter__(self) -> SshTunnel:
        self.open()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def open(self) -> None:
        ssh = shutil.which("ssh")
        if not ssh:
            raise RuntimeError(
                "SSH-клиент не найден. Установите OpenSSH или используйте API."
            )
        command = [
            ssh,
            "-N",
            "-o",
            "BatchMode=yes",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "ConnectTimeout=8",
            "-o",
            "ServerAliveInterval=15",
            "-p",
            str(self.config.ssh_port),
            "-L",
            (
                f"127.0.0.1:{self.local_port}:"
                f"{self.config.ssh_remote_host}:{self.config.ssh_remote_port}"
            ),
        ]
        if self.config.ssh_key:
            command.extend(["-i", self.config.ssh_key])
        destination = (
            f"{self.config.ssh_user}@{self.config.ssh_host or self.config.host}"
        )
        command.append(destination)
        try:
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            raise RuntimeError(f"Не удалось запустить SSH: {exc}") from exc

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                error = (self.process.stderr.read() if self.process.stderr else "").strip()
                self.close()
                raise RuntimeError(
                    "SSH-туннель не поднялся"
                    + (f": {error}" if error else ". Проверьте ключ и доступ по SSH.")
                )
            try:
                with socket.create_connection(("127.0.0.1", self.local_port), timeout=0.3):
                    return
            except OSError:
                time.sleep(0.1)
        self.close()
        raise RuntimeError("SSH-туннель не открыл локальный порт за 10 секунд.")

    def close(self) -> None:
        process = self.process
        self.process = None
        if process is None:
            return
        try:
            process.terminate()
            process.wait(timeout=2)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass


@contextmanager
def open_client(config: ConnectionConfig) -> Iterator[MikroTikClient]:
    """Open a MikroTik client, optionally through an SSH tunnel."""
    config.validate()
    if config.connection_type == "ssh":
        with SshTunnel(config) as tunnel:
            client = MikroTikClient(
                "127.0.0.1",
                config.username,
                config.password,
                port=tunnel.local_port,
                use_ssl=False,
                timeout=config.timeout,
            )
            try:
                client.connect()
                yield client
            finally:
                client.close()
        return

    client = MikroTikClient(
        config.host,
        config.username,
        config.password,
        port=config.port,
        use_ssl=config.connection_type == "api_ssl",
        ssl_verify=config.ssl_verify,
        timeout=config.timeout,
    )
    try:
        client.connect()
        yield client
    finally:
        client.close()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
