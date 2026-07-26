#!/usr/bin/env python3
"""Offline contract test for dispatch, status, idempotency, and same-task harness."""

import base64, hashlib, json, os, signal, socket, struct, subprocess, tempfile, threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def send_frame(connection, payload: dict) -> None:
    body = json.dumps(payload).encode()
    header = bytes([0x81])
    if len(body) < 126:
        header += bytes([len(body)])
    else:
        header += bytes([126]) + struct.pack("!H", len(body))
    connection.sendall(header + body)


def receive_frame(connection) -> dict | None:
    header = connection.recv(2)
    if not header:
        return None
    opcode, second = header[0] & 0x0F, header[1]
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", connection.recv(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", connection.recv(8))[0]
    mask = connection.recv(4) if second & 0x80 else b""
    body = b""
    while len(body) < length:
        body += connection.recv(length - len(body))
    if mask:
        body = bytes(byte ^ mask[index % 4] for index, byte in enumerate(body))
    if opcode == 8:
        return None
    return json.loads(body)

def fake_app_server(
    path: Path,
    ready: threading.Event,
    visible: bool = True,
    stall_ready: threading.Event | None = None,
) -> None:
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)
    ready.set()
    connection, _ = server.accept()
    request = b""
    while b"\r\n\r\n" not in request:
        request += connection.recv(4096)
    key_line = next(line for line in request.decode().split("\r\n") if line.lower().startswith("sec-websocket-key:"))
    key = key_line.split(":", 1)[1].strip()
    accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
    connection.sendall((
        "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
    ).encode())
    threads, listed, turns = {}, {}, 0
    while True:
        message = receive_frame(connection)
        if message is None:
            break
        method = message.get("method")
        if method == "initialize":
            send_frame(connection, {"id": message["id"], "result": {"userAgent": "fake"}})
        elif method == "thread/start":
            cwd = message["params"]["cwd"]
            thread = "00000000-0000-4000-8000-000000000001" if cwd.endswith("darkexec") else "00000000-0000-4000-8000-000000000002"
            threads[thread] = 0
            listed[thread] = {"id": thread, "source": "vscode", "cwd": cwd}
            send_frame(connection, {"id": message["id"], "result": {"thread": {"id": thread, "source": "vscode", "cwd": cwd}}})
        elif method == "thread/list":
            cwd = message["params"]["cwd"]
            data = [thread for thread in listed.values() if visible and thread["cwd"] == cwd]
            send_frame(connection, {"id": message["id"], "result": {"data": data, "nextCursor": None}})
        elif method == "turn/start":
            turns += 1
            thread = message["params"]["threadId"]
            prompt = message["params"]["input"][0]["text"]
            turn = f"turn-{turns}"
            send_frame(connection, {"id": message["id"], "result": {"turn": {"id": turn}}})
            if prompt == "WAIT_FOR_SIGNAL":
                if stall_ready:
                    stall_ready.set()
                continue
            text = "HARNESS_OK" if "harness" in prompt.lower() else (f"TARGET_OK:{prompt}" if thread.endswith("2") else "EXECUTIVE_OK")
            send_frame(connection, {"method": "item/completed", "params": {"threadId": thread, "turnId": turn, "item": {"type": "agentMessage", "text": text}}})
            threads[thread] += 12
            multiplier = threads[thread] // 12
            send_frame(connection, {"method": "thread/tokenUsage/updated", "params": {
                "threadId": thread, "turnId": turn, "tokenUsage": {"total": {
                    "inputTokens": 10 * multiplier, "cachedInputTokens": 4 * multiplier,
                    "outputTokens": 2 * multiplier, "reasoningOutputTokens": multiplier,
                    "totalTokens": threads[thread],
                }},
            }})
            send_frame(connection, {"method": "turn/completed", "params": {"threadId": thread, "turn": {"id": turn, "status": "completed", "error": None}}})
        elif method == "turn/interrupt":
            send_frame(connection, {"id": message["id"], "result": {}})
    connection.close()
    server.close()


def main() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        target, workspace = root / "target", root / "darkexec"
        target.mkdir()
        workspace.mkdir()
        config = root / "config.toml"
        config.write_text(f'[projects."{target}"]\ntrust_level = "trusted"\n')
        socket_path = root / "app.sock"
        ready = threading.Event()
        server = threading.Thread(target=fake_app_server, args=(socket_path, ready), daemon=True)
        server.start()
        assert ready.wait(timeout=2)
        env = {
            **os.environ, "DARKEXEC_STATE_ROOT": str(root / "state"),
            "DARKEXEC_WORKSPACE": str(workspace), "DARKEXEC_CONFIG": str(config),
            "DARKEXEC_APP_SERVER_SOCKET": str(socket_path),
        }
        command = [
            str(ROOT / "bin/darkexec"), "dispatch", "--target", str(target),
            "--job-id", "incident-1", "--prompt-stdin", "--read-only-harness", "--json",
        ]
        first = subprocess.run(command, input="Natural request.", capture_output=True, text=True, env=env, check=False)
        assert first.returncode == 0, first.stderr or first.stdout
        result = json.loads(first.stdout)
        assert result["status"] == "completed", result
        assert result["transport"] == "codex-app-server-control-socket", result
        assert result["executive"]["threadId"].endswith("1"), result
        assert result["executive"]["appVisible"] is True, result
        assert result["target"]["threadId"].endswith("2"), result
        assert result["target"]["appVisible"] is True, result
        assert (result["executive"]["listedCwd"], result["target"]["listedCwd"]) == (str(workspace), str(target)), result
        assert result["target"]["harness"]["status"] == "completed", result
        assert result["target"]["resultText"] == "TARGET_OK:Natural request.", result
        assert result["cumulativeUsage"]["total"] == 48, result
        second = subprocess.run(command, input="Natural request.", capture_output=True, text=True, env=env, check=False)
        assert second.returncode == 0
        assert json.loads(second.stdout)["createdAt"] == result["createdAt"]
        status = subprocess.run(
            [str(ROOT / "bin/darkexec"), "status", "--thread", result["target"]["threadId"], "--json"],
            capture_output=True, text=True, env=env, check=False,
        )
        assert status.returncode == 0
        assert json.loads(status.stdout)["jobId"] == "incident-1"
        conflict = subprocess.run(command, input="Different request.", capture_output=True, text=True, env=env, check=False)
        assert conflict.returncode != 0
        server.join(timeout=2)
        assert not server.is_alive()
        hidden_socket, hidden_ready = root / "hidden.sock", threading.Event()
        hidden_server = threading.Thread(target=fake_app_server, args=(hidden_socket, hidden_ready, False), daemon=True)
        hidden_server.start(); assert hidden_ready.wait(timeout=2)
        hidden_env, hidden_command = {**env, "DARKEXEC_APP_SERVER_SOCKET": str(hidden_socket)}, [*command]
        hidden_command[5] = "incident-hidden"
        hidden = subprocess.run(hidden_command, input="Natural request.", capture_output=True, text=True, env=hidden_env, check=False)
        hidden_result = json.loads(hidden.stdout)
        assert hidden.returncode != 0 and hidden_result["status"] == "failed", hidden
        assert "not listed by the running Codex App" in hidden_result["error"], hidden_result
        hidden_server.join(timeout=2)
        assert not hidden_server.is_alive()
        signal_socket, signal_server_ready, stalled = root / "signal.sock", threading.Event(), threading.Event()
        signal_server = threading.Thread(
            target=fake_app_server,
            args=(signal_socket, signal_server_ready, True, stalled),
            daemon=True,
        )
        signal_server.start()
        assert signal_server_ready.wait(timeout=2)
        signal_env = {**env, "DARKEXEC_APP_SERVER_SOCKET": str(signal_socket)}
        signal_command = [*command]
        signal_command[5] = "incident-signal"
        process = subprocess.Popen(
            signal_command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=signal_env,
        )
        assert process.stdin
        process.stdin.write("WAIT_FOR_SIGNAL")
        process.stdin.close()
        assert stalled.wait(timeout=3)
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=5)
        assert process.returncode == 128 + signal.SIGTERM, process.returncode
        interrupted = json.loads((root / "state" / f"{hashlib.sha256(b'incident-signal').hexdigest()}.json").read_text())
        assert interrupted["status"] == "interrupted", interrupted
        assert interrupted["error"] == f"interrupted by signal {signal.SIGTERM}", interrupted
        signal_server.join(timeout=2)
        assert not signal_server.is_alive()
    print(json.dumps({"status": "passed", "contracts": [
        "saved-target", "running-app-list-proof", "one-executive", "one-target", "same-task-harness",
        "separate-usage", "idempotent-job", "thread-status", "conflict-closed", "signal-terminalized",
    ]}))


if __name__ == "__main__":
    main()
