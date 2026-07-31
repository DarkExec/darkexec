#!/usr/bin/env python3
"""Offline contract test for dispatch, status, idempotency, and same-task harness."""

import base64, fcntl, hashlib, json, os, signal, socket, struct, subprocess, sys, tempfile, threading, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def process_start_ticks(pid: int) -> int:
    raw = Path(f"/proc/{pid}/stat").read_text()
    return int(raw[raw.rfind(")") + 2:].split()[19])

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
    seeded_threads: dict[str, dict] | None = None,
    observed_inputs: list[list[dict]] | None = None,
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
    threads, listed, histories, materialized, loaded, required_resume_paths, turns = (
        {}, {}, {}, set(), set(), {}, 0
    )
    for thread_id, seed in (seeded_threads or {}).items():
        threads[thread_id] = 0
        listed[thread_id] = {"id": thread_id, "source": "vscode", "cwd": seed["cwd"]}
        if seed.get("path"):
            listed[thread_id]["path"] = seed["path"]
        if seed.get("requirePath"):
            required_resume_paths[thread_id] = seed["path"]
        histories[thread_id] = list(seed.get("turns") or [])
        materialized.add(thread_id)
        if seed.get("loaded", True):
            loaded.add(thread_id)
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
            histories[thread] = []
            loaded.add(thread)
            send_frame(connection, {"id": message["id"], "result": {"thread": {"id": thread, "source": "vscode", "cwd": cwd}}})
        elif method == "thread/list":
            cwd = message["params"]["cwd"]
            data = [
                thread for thread in listed.values()
                if visible and thread["cwd"] == cwd and thread["id"] in materialized
            ]
            send_frame(connection, {"id": message["id"], "result": {"data": data, "nextCursor": None}})
        elif method == "thread/read":
            thread = message["params"]["threadId"]
            if thread not in loaded:
                send_frame(connection, {
                    "id": message["id"],
                    "error": {"code": -32600, "message": f"thread not found: {thread}"},
                })
                continue
            metadata = listed[thread]
            send_frame(connection, {"id": message["id"], "result": {"thread": {
                **metadata, "turns": histories[thread], "status": {"type": "idle"},
            }}})
        elif method == "thread/resume":
            thread = message["params"]["threadId"]
            if (
                thread in required_resume_paths
                and message["params"].get("path") != required_resume_paths[thread]
            ):
                send_frame(connection, {
                    "id": message["id"],
                    "error": {"code": -32600, "message": f"thread not found: {thread}"},
                })
                continue
            loaded.add(thread)
            metadata = listed[thread]
            send_frame(connection, {"id": message["id"], "result": {"thread": {
                **metadata, "turns": histories[thread], "status": {"type": "idle"},
            }}})
        elif method == "turn/start":
            turns += 1
            thread = message["params"]["threadId"]
            turn_input = message["params"]["input"]
            if observed_inputs is not None:
                observed_inputs.append(turn_input)
            prompt = next(
                item["text"] for item in turn_input
                if item.get("type") == "text" and item.get("text")
            )
            turn = f"turn-{turns}"
            send_frame(connection, {"id": message["id"], "result": {"turn": {"id": turn}}})
            history = {
                "id": turn, "status": "inProgress",
                "items": [{"id": f"user-{turn}", "type": "userMessage", "content": turn_input}],
            }
            histories[thread].append(history)
            materialized.add(thread)
            if prompt == "WAIT_FOR_SIGNAL":
                if stall_ready:
                    stall_ready.set()
                continue
            if prompt == "WAIT_FOR_STEER":
                if stall_ready:
                    stall_ready.set()
                continue
            if prompt == "WAIT_THEN_COMPLETE":
                time.sleep(1.2)
            if prompt == "WAIT_WITH_CONTROL_FOLLOWUP":
                control = next(item for item in histories if item.endswith("1"))
                histories[control].append({
                    "id": "queued-user-follow-up", "status": "inProgress",
                    "items": [{"id": "queued-user", "type": "userMessage", "content": [
                        {"type": "text", "text": "Dependent follow-up while Background is running."},
                    ]}],
                })
            if prompt.startswith("DARKEXEC ROUTE TASK."):
                allowed = json.loads(
                    prompt.split("Allowed projects: ", 1)[1].split(
                        ". Natural request:", 1
                    )[0]
                )
                selected = next(path for path in allowed if not path.endswith("darkexec"))
                job_id = prompt.split("owns job ", 1)[1].split(".", 1)[0]
                text = f"DARKEXEC_ROUTE_READY {job_id} {selected}"
            else:
                text = "HARNESS_OK" if "harness" in prompt.lower() else (f"TARGET_OK:{prompt}" if thread.endswith("2") else "EXECUTIVE_OK")
            history["items"].append({"id": f"agent-{turn}", "type": "agentMessage", "text": text})
            history["status"] = "completed"
            if prompt == "COMPLETE_WITHOUT_NOTIFICATION":
                continue
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
            thread = message["params"]["threadId"]
            turn = message["params"]["turnId"]
            for history in histories.get(thread, []):
                if history.get("id") == turn:
                    history["status"] = "interrupted"
            send_frame(connection, {"id": message["id"], "result": {}})
        elif method == "turn/steer":
            thread = message["params"]["threadId"]
            turn = message["params"]["expectedTurnId"]
            active = next(
                item for item in histories.get(thread, [])
                if item.get("id") == turn and item.get("status") == "inProgress"
            )
            steer_input = message["params"]["input"]
            steer_text = next(item["text"] for item in steer_input if item.get("type") == "text")
            active["items"].append({
                "id": f"steer-{turn}", "type": "userMessage", "content": steer_input,
            })
            text = f"TARGET_OK:WAIT_FOR_STEER:{steer_text}"
            active["items"].append({"id": f"agent-{turn}", "type": "agentMessage", "text": text})
            send_frame(connection, {"id": message["id"], "result": {"turnId": turn}})
            send_frame(connection, {"method": "item/completed", "params": {
                "threadId": thread, "turnId": turn,
                "item": {"type": "agentMessage", "text": text},
            }})
            threads[thread] += 12
            send_frame(connection, {"method": "thread/tokenUsage/updated", "params": {
                "threadId": thread, "turnId": turn, "tokenUsage": {"total": {
                    "inputTokens": 10, "cachedInputTokens": 4, "outputTokens": 2,
                    "reasoningOutputTokens": 1, "totalTokens": threads[thread],
                }},
            }})
            send_frame(connection, {"method": "turn/completed", "params": {
                "threadId": thread,
                "turn": {"id": turn, "status": "completed", "error": None},
            }})
            active["status"] = "completed"
    connection.close()
    server.close()


def main() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        install_contract = subprocess.run(
            [str(ROOT / "scripts/verify_install_contract.py"), str(ROOT / "bin/darkexec")],
            capture_output=True, text=True, check=False,
        )
        assert install_contract.returncode == 0, install_contract.stderr
        install_result = json.loads(install_contract.stdout)
        assert install_result["turnTimeoutDefault"] == 0
        assert install_result["stopControl"] is True
        assert install_result["executionRootDefault"] == "/var/lib/darkexec/executives"
        bounded_runtime = root / "bounded-darkexec"
        bounded_runtime.write_text(
            (ROOT / "bin/darkexec").read_text().replace(
                'DARKEXEC_TURN_TIMEOUT", "0"',
                'DARKEXEC_TURN_TIMEOUT", "630"',
                1,
            )
        )
        rejected_contract = subprocess.run(
            [str(ROOT / "scripts/verify_install_contract.py"), str(bounded_runtime)],
            capture_output=True, text=True, check=False,
        )
        assert rejected_contract.returncode == 1
        assert 'expected 0' in rejected_contract.stderr
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
            "DARKEXEC_EXECUTION_ROOT": str(root / "executions"),
            "DARKEXEC_CONTROL_ROOT": str(root / "controls"),
            "DARKEXEC_SESSION_ROOT": str(root / "sessions"),
            "DARKEXEC_WORKSPACE": str(workspace), "DARKEXEC_CONFIG": str(config),
            "DARKEXEC_APP_SERVER_SOCKET": str(socket_path),
        }
        projects = subprocess.run(
            [str(ROOT / "bin/darkexec"), "projects", "--json"],
            capture_output=True, text=True, env=env, check=False,
        )
        assert projects.returncode == 0, projects.stderr
        assert json.loads(projects.stdout) == {
            "schemaVersion": 1,
            "projects": [str(target)],
        }, projects.stdout
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
        waited_status = subprocess.run(
            [
                str(ROOT / "bin/darkexec"), "status", "--thread",
                result["executive"]["threadId"], "--wait", "--json",
            ],
            capture_output=True, text=True, env=env, check=False,
        )
        assert waited_status.returncode == 0
        assert json.loads(waited_status.stdout)["status"] == "completed", waited_status.stdout
        abandoned_job = "incident-abandoned"
        abandoned_path = root / "state" / f"{hashlib.sha256(abandoned_job.encode()).hexdigest()}.json"
        abandoned_path.write_text(json.dumps({
            "schemaVersion": 1, "jobId": abandoned_job, "targetPath": str(target),
            "status": "target_running",
            "executive": {"threadId": "abandoned-executive"},
            "target": {"threadId": "abandoned-target"},
        }))
        abandoned = subprocess.run(
            [
                str(ROOT / "bin/darkexec"), "status", "--job-id",
                abandoned_job, "--wait", "--json",
            ],
            capture_output=True, text=True, env=env, check=False, timeout=2,
        )
        abandoned_result = json.loads(abandoned.stdout)
        assert abandoned.returncode == 1, abandoned_result
        assert abandoned_result["status"] == "abandoned", abandoned_result
        assert abandoned_result["receiptStatus"] == "target_running", abandoned_result
        live_job = "incident-live-wait"
        live_path = root / "state" / f"{hashlib.sha256(live_job.encode()).hexdigest()}.json"
        live_receipt = {
            "schemaVersion": 1, "jobId": live_job, "targetPath": str(target),
            "status": "target_running",
            "executive": {"threadId": "live-wait-executive"},
            "target": {"threadId": "live-wait-target"},
        }
        live_path.write_text(json.dumps(live_receipt))
        live_lock = live_path.with_suffix(".lock").open("a+")
        fcntl.flock(live_lock.fileno(), fcntl.LOCK_EX)
        def finish_live_receipt() -> None:
            time.sleep(0.3)
            completed_receipt = {**live_receipt, "status": "completed"}
            temporary = live_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(completed_receipt))
            os.replace(temporary, live_path)
            fcntl.flock(live_lock.fileno(), fcntl.LOCK_UN)
            live_lock.close()
        finisher = threading.Thread(target=finish_live_receipt, daemon=True)
        finisher.start()
        live_wait = subprocess.run(
            [
                str(ROOT / "bin/darkexec"), "status", "--thread",
                "live-wait-executive", "--wait", "--json",
            ],
            capture_output=True, text=True, env=env, check=False, timeout=2,
        )
        finisher.join(timeout=1)
        assert live_wait.returncode == 0, live_wait.stderr or live_wait.stdout
        assert json.loads(live_wait.stdout)["status"] == "completed", live_wait.stdout
        abandoned_stop = subprocess.run(
            [
                str(ROOT / "bin/darkexec"), "stop", "--executive-thread",
                "abandoned-executive", "--json",
            ],
            capture_output=True, text=True, env=env, check=False,
        )
        abandoned_stop_result = json.loads(abandoned_stop.stdout)
        assert abandoned_stop.returncode == 0, abandoned_stop_result
        assert abandoned_stop_result["source"] == "background_job", abandoned_stop_result
        assert abandoned_stop_result["status"] == "already_stopped", abandoned_stop_result
        conflict = subprocess.run(command, input="Different request.", capture_output=True, text=True, env=env, check=False)
        assert conflict.returncode != 0
        server.join(timeout=2)
        assert not server.is_alive()
        deferred_socket, deferred_ready, deferred_inputs = (
            root / "deferred-initial.sock", threading.Event(), []
        )
        deferred_server = threading.Thread(
            target=fake_app_server,
            args=(deferred_socket, deferred_ready, True, None, {}, deferred_inputs),
            daemon=True,
        )
        deferred_server.start(); assert deferred_ready.wait(timeout=2)
        deferred_command = [
            str(ROOT / "bin/darkexec"), "dispatch", "--target", str(target),
            "--job-id", "incident-deferred-initial", "--prompt-stdin",
            "--read-only-harness", "--defer-initial-harness", "--json",
        ]
        deferred = subprocess.run(
            deferred_command, input="Conversational request.",
            capture_output=True, text=True,
            env={**env, "DARKEXEC_APP_SERVER_SOCKET": str(deferred_socket)}, check=False,
        )
        deferred_result = json.loads(deferred.stdout)
        assert deferred.returncode == 0 and deferred_result["status"] == "completed", deferred_result
        assert deferred_result["harnessMode"] == "read-only", deferred_result
        assert deferred_result["initialHarnessMode"] == "debounce", deferred_result
        assert deferred_result["target"]["harness"]["status"] == "deferred", deferred_result
        assert deferred_result["target"]["turnId"], deferred_result
        deferred_text = [
            item.get("text", "")
            for turn_input in deferred_inputs
            for item in turn_input
            if item.get("type") == "text"
        ]
        assert not any("Let's do a harness pass" in text for text in deferred_text), deferred_text
        deferred_server.join(timeout=2)
        assert not deferred_server.is_alive()
        mode_conflict = subprocess.run(
            [item for item in deferred_command if item != "--defer-initial-harness"],
            input="Conversational request.", capture_output=True, text=True, env=env, check=False,
        )
        assert mode_conflict.returncode != 0
        assert "different harness mode" in mode_conflict.stderr, mode_conflict.stderr
        routed_config = root / "routed-config.toml"
        routed_config.write_text(
            f'[projects."{workspace}"]\ntrust_level = "trusted"\n'
            f'[projects."{target}"]\ntrust_level = "trusted"\n'
        )
        routed_socket, routed_ready, routed_inputs = (
            root / "routed.sock", threading.Event(), []
        )
        routed_server = threading.Thread(
            target=fake_app_server,
            args=(routed_socket, routed_ready, True, None, {}, routed_inputs),
            daemon=True,
        )
        routed_server.start(); assert routed_ready.wait(timeout=2)
        routed_command = [
            str(ROOT / "bin/darkexec"), "dispatch", "--target", str(workspace),
            "--job-id", "incident-routed-deferred", "--prompt-stdin",
            "--read-only-harness", "--defer-initial-harness", "--resolve-target", "--json",
        ]
        routed = subprocess.run(
            routed_command, input="Choose the owner and inspect it.",
            capture_output=True, text=True,
            env={
                **env, "DARKEXEC_CONFIG": str(routed_config),
                "DARKEXEC_APP_SERVER_SOCKET": str(routed_socket),
            },
            check=False,
        )
        routed_result = json.loads(routed.stdout)
        assert routed.returncode == 0, routed.stderr or routed_result
        assert routed_result["requestedTargetPath"] == str(workspace), routed_result
        assert routed_result["targetResolution"] == "executive", routed_result
        assert routed_result["targetPath"] == str(target), routed_result
        assert routed_result["resolvedTargetPath"] == str(target), routed_result
        assert routed_result["executive"]["listedCwd"] == str(workspace), routed_result
        assert routed_result["target"]["listedCwd"] == str(target), routed_result
        assert (
            routed_result["target"]["resultText"]
            == "TARGET_OK:Choose the owner and inspect it."
        ), routed_result
        assert routed_result["target"]["harness"]["status"] == "deferred", routed_result
        assert len(routed_inputs) == 3, routed_inputs
        assert routed_inputs[0][0]["text"].startswith("DARKEXEC ROUTE TASK."), routed_inputs
        assert routed_inputs[1] == [
            {"type": "text", "text": "Choose the owner and inspect it."}
        ], routed_inputs
        assert "Same-task harness: deferred" in routed_inputs[2][0]["text"], routed_inputs
        routed_server.join(timeout=2)
        assert not routed_server.is_alive()
        dispatch_image = root / "dispatch-image.png"
        dispatch_image.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        dispatch_input = [
            {"type": "text", "text": "Attachment dispatch.\n\nAttached image: logo.png"},
            {"type": "localImage", "path": str(dispatch_image)},
        ]
        dispatch_manifest = root / "dispatch-input.json"
        dispatch_manifest.write_text(json.dumps({
            "schemaVersion": 1, "input": dispatch_input,
        }))
        attachment_socket, attachment_ready, attachment_inputs = (
            root / "attachment-dispatch.sock", threading.Event(), []
        )
        attachment_server = threading.Thread(
            target=fake_app_server,
            args=(attachment_socket, attachment_ready, True, None, {}, attachment_inputs),
            daemon=True,
        )
        attachment_server.start(); assert attachment_ready.wait(timeout=2)
        attachment_command = [
            str(ROOT / "bin/darkexec"), "dispatch", "--target", str(target),
            "--job-id", "incident-attachment", "--prompt-stdin",
            "--input-json", str(dispatch_manifest), "--read-only-harness", "--json",
        ]
        attachment_dispatch = subprocess.run(
            attachment_command, input="Attachment dispatch.", capture_output=True, text=True,
            env={**env, "DARKEXEC_APP_SERVER_SOCKET": str(attachment_socket)}, check=False,
        )
        attachment_result = json.loads(attachment_dispatch.stdout)
        assert attachment_dispatch.returncode == 0 and attachment_result["status"] == "completed", attachment_result
        assert dispatch_input in attachment_inputs, attachment_inputs
        attachment_server.join(timeout=2)
        assert not attachment_server.is_alive()
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
        queued_socket, queued_ready = root / "queued.sock", threading.Event()
        queued_server = threading.Thread(
            target=fake_app_server, args=(queued_socket, queued_ready), daemon=True,
        )
        queued_server.start(); assert queued_ready.wait(timeout=2)
        queued_command = [*command]
        queued_command[queued_command.index("incident-1")] = "incident-queued-follow-up"
        queued = subprocess.run(
            queued_command, input="WAIT_WITH_CONTROL_FOLLOWUP",
            capture_output=True, text=True,
            env={**env, "DARKEXEC_APP_SERVER_SOCKET": str(queued_socket)}, check=False,
        )
        queued_result = json.loads(queued.stdout)
        assert queued.returncode == 0 and queued_result["status"] == "completed", queued_result
        assert queued_result["executive"]["closeoutStatus"] == "suppressed_active_user_turn", queued_result
        queued_server.join(timeout=2)
        assert not queued_server.is_alive()
        missing_mode = subprocess.run(
            [
                str(ROOT / "bin/darkexec"), "run", "--target", str(target),
                "--prompt-stdin", "--json",
            ],
            input="Must not create a target task.", capture_output=True, text=True,
            env=env, check=False,
        )
        assert missing_mode.returncode != 0, missing_mode
        assert "one of the arguments --read-only-harness --standard-harness is required" in missing_mode.stderr, missing_mode.stderr
        interactive_executive = "10000000-0000-4000-8000-000000000001"
        executive_input = [
            {"type": "text", "text": "Exact response request."},
            {"type": "image", "url": "data:image/png;base64,ZmFrZQ=="},
        ]
        executive_turn = {
            "id": "executive-turn-1", "status": "inProgress",
            "items": [{"id": "executive-user-1", "type": "userMessage", "content": executive_input}],
        }
        run_socket, run_ready, run_inputs = root / "run.sock", threading.Event(), []
        run_server = threading.Thread(
            target=fake_app_server,
            args=(run_socket, run_ready, True, None, {
                interactive_executive: {"cwd": str(workspace), "turns": [executive_turn]},
            }, run_inputs),
            daemon=True,
        )
        run_server.start(); assert run_ready.wait(timeout=2)
        run_env = {
            **env, "DARKEXEC_APP_SERVER_SOCKET": str(run_socket),
            "CODEX_THREAD_ID": interactive_executive,
        }
        run_command = [
            str(ROOT / "bin/darkexec"), "run", "--target", str(target),
            "--prompt-stdin", "--read-only-harness", "--json",
        ]
        sourced_run_command = [*run_command[:5], "--source-executive-turn", *run_command[5:]]
        interactive = subprocess.run(
            sourced_run_command, input="Exact response request.", capture_output=True, text=True,
            env=run_env, check=False,
        )
        interactive_result = json.loads(interactive.stdout)
        assert interactive.returncode == 0 and interactive_result["status"] == "completed", interactive_result
        assert interactive_result["target"]["resultText"] == "TARGET_OK:Exact response request.", interactive_result
        assert interactive_result["target"]["appVisible"] is True, interactive_result
        assert interactive_result["harness"]["status"] == "completed", interactive_result
        assert run_inputs[0] == executive_input, run_inputs[0]
        interactive_state = json.loads(
            (root / "executions" / f"{hashlib.sha256(interactive_executive.encode()).hexdigest()}.json").read_text()
        )
        assert interactive_state["status"] == "completed", interactive_state
        assert interactive_state["target"]["threadId"].endswith("2"), interactive_state
        interactive_path = root / "executions" / f"{hashlib.sha256(interactive_executive.encode()).hexdigest()}.json"
        assert interactive_path.stat().st_mode & 0o777 == 0o600
        assert interactive_path.with_suffix(".lock").stat().st_mode & 0o777 == 0o600
        assert (root / "executions").stat().st_mode & 0o777 == 0o700
        completed_status = subprocess.run(
            [
                str(ROOT / "bin/darkexec"), "execution-status",
                "--executive-thread", interactive_executive, "--json",
            ],
            capture_output=True, text=True, env=run_env, check=False,
        )
        completed_status_result = json.loads(completed_status.stdout)
        assert completed_status.returncode == 0, completed_status.stderr
        assert completed_status_result["status"] == "completed", completed_status_result
        assert completed_status_result["phase"] == "idle", completed_status_result
        assert completed_status_result["runnerActive"] is False, completed_status_result
        assert completed_status_result["target"]["threadId"].endswith("2"), completed_status_result
        unknown_status = subprocess.run(
            [
                str(ROOT / "bin/darkexec"), "execution-status",
                "--executive-thread", "10000000-0000-4000-8000-000000000099", "--json",
            ],
            capture_output=True, text=True, env=run_env, check=False,
        )
        unknown_status_result = json.loads(unknown_status.stdout)
        assert unknown_status.returncode == 1, unknown_status
        assert unknown_status_result["status"] == "not_found", unknown_status_result
        assert unknown_status_result["runnerActive"] is False, unknown_status_result
        run_server.join(timeout=2)
        assert not run_server.is_alive()
        direct_image = root / "direct-image.png"
        direct_image.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        direct_manifest = root / "direct-input.json"
        direct_input = [
            {"type": "text", "text": "Direct attachment request.\n\nAttached image: logo.png"},
            {"type": "localImage", "path": str(direct_image)},
        ]
        direct_manifest.write_text(json.dumps({"schemaVersion": 1, "input": direct_input}))
        direct_socket, direct_ready, direct_inputs = (
            root / "direct-input.sock", threading.Event(), []
        )
        direct_server = threading.Thread(
            target=fake_app_server,
            args=(direct_socket, direct_ready, True, None, {}, direct_inputs),
            daemon=True,
        )
        direct_server.start(); assert direct_ready.wait(timeout=2)
        direct_env = {
            key: value for key, value in run_env.items() if key != "CODEX_THREAD_ID"
        }
        direct_env["DARKEXEC_APP_SERVER_SOCKET"] = str(direct_socket)
        direct = subprocess.run(
            [
                str(ROOT / "bin/darkexec"), "run", "--target", str(target),
                "--prompt-stdin", "--input-json", str(direct_manifest),
                "--read-only-harness", "--json",
            ],
            input="Direct attachment request.", capture_output=True, text=True,
            env=direct_env, check=False,
        )
        direct_result = json.loads(direct.stdout)
        assert direct.returncode == 0 and direct_result["status"] == "completed", direct_result
        assert direct_inputs[0] == direct_input, direct_inputs[0]
        direct_server.join(timeout=2)
        assert not direct_server.is_alive()
        continue_socket, continue_ready = root / "continue.sock", threading.Event()
        follow_up_input = [
            {"type": "text", "text": "Dependent follow-up."},
            {"type": "localImage", "path": "/tmp/follow-up.png"},
        ]
        continue_inputs = []
        continue_server = threading.Thread(
            target=fake_app_server,
            args=(continue_socket, continue_ready, True, None, {
                interactive_executive: {"cwd": str(workspace), "turns": [{
                    "id": "executive-turn-2", "status": "inProgress",
                    "items": [{
                        "id": "executive-user-2", "type": "userMessage",
                        "content": follow_up_input,
                    }],
                }]},
                interactive_state["target"]["threadId"]: {"cwd": str(target), "turns": []},
            }, continue_inputs),
            daemon=True,
        )
        continue_server.start(); assert continue_ready.wait(timeout=2)
        continued = subprocess.run(
            [
                str(ROOT / "bin/darkexec"), "continue", "--target", str(target),
                "--thread", interactive_state["target"]["threadId"], "--prompt-stdin",
                "--source-executive-turn", "--json",
            ],
            input="Dependent follow-up.", capture_output=True, text=True,
            env={**run_env, "DARKEXEC_APP_SERVER_SOCKET": str(continue_socket)}, check=False,
        )
        continued_result = json.loads(continued.stdout)
        assert continued.returncode == 0 and continued_result["status"] == "completed", continued_result
        assert continued_result["target"]["resultText"] == "TARGET_OK:Dependent follow-up.", continued_result
        assert continue_inputs[0] == follow_up_input, continue_inputs[0]
        continue_server.join(timeout=2)
        assert not continue_server.is_alive()
        steer_socket, steer_ready, steer_turn_ready = (
            root / "steer.sock", threading.Event(), threading.Event()
        )
        steer_server = threading.Thread(
            target=fake_app_server,
            args=(steer_socket, steer_ready, True, steer_turn_ready, {
                interactive_state["target"]["threadId"]: {"cwd": str(target), "turns": []},
            }),
            daemon=True,
        )
        steer_server.start(); assert steer_ready.wait(timeout=2)
        steer_env = {**run_env, "DARKEXEC_APP_SERVER_SOCKET": str(steer_socket)}
        steer_run = subprocess.Popen(
            [
                str(ROOT / "bin/darkexec"), "continue", "--target", str(target),
                "--thread", interactive_state["target"]["threadId"],
                "--executive-thread", interactive_executive,
                "--prompt-stdin", "--json",
            ],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=steer_env,
        )
        assert steer_run.stdin is not None
        steer_run.stdin.write("WAIT_FOR_STEER")
        steer_run.stdin.close()
        assert steer_turn_ready.wait(timeout=2)
        execution_file = (
            root / "executions" / f"{hashlib.sha256(interactive_executive.encode()).hexdigest()}.json"
        )
        for _ in range(40):
            active_steer = json.loads(execution_file.read_text())
            if (active_steer.get("target") or {}).get("turnId"):
                break
            time.sleep(0.05)
        target_turn = active_steer["target"]["turnId"]
        steered = subprocess.run(
            [
                str(ROOT / "bin/darkexec"), "steer",
                "--executive-thread", interactive_executive,
                "--thread", interactive_state["target"]["threadId"],
                "--turn", target_turn, "--intent-id", "intent-steer-1",
                "--prompt-stdin", "--json",
            ],
            input="STEER_OK", capture_output=True, text=True, env=steer_env, check=False,
        )
        steered_result = json.loads(steered.stdout)
        assert steered.returncode == 0, steered.stderr
        assert steered_result["status"] == "acknowledged", steered_result
        assert steered_result["turnId"] == target_turn, steered_result
        assert steer_run.stdout is not None and steer_run.stderr is not None
        steer_stdout = steer_run.stdout.read()
        steer_stderr = steer_run.stderr.read()
        assert steer_run.wait(timeout=3) == 0, steer_stderr
        steer_result = json.loads(steer_stdout)
        assert steer_result["status"] == "completed", steer_result
        assert steer_result["target"]["resultText"] == "TARGET_OK:WAIT_FOR_STEER:STEER_OK", steer_result
        steer_server.join(timeout=2)
        assert not steer_server.is_alive()
        rejected_steer = subprocess.run(
            [
                str(ROOT / "bin/darkexec"), "steer",
                "--executive-thread", interactive_executive,
                "--thread", interactive_state["target"]["threadId"],
                "--turn", target_turn, "--intent-id", "intent-steer-2",
                "--prompt-stdin", "--json",
            ],
            input="TOO_LATE", capture_output=True, text=True, env=steer_env, check=False,
        )
        assert rejected_steer.returncode != 0
        assert "not accepting same-turn steering" in rejected_steer.stderr
        replacement = subprocess.run(
            run_command, input="Must not replace the bound target.",
            capture_output=True, text=True, env=run_env, check=False,
        )
        assert replacement.returncode != 0
        assert "is already bound to target" in replacement.stderr, replacement.stderr
        long_socket, long_ready = root / "long.sock", threading.Event()
        long_server = threading.Thread(target=fake_app_server, args=(long_socket, long_ready), daemon=True)
        long_server.start(); assert long_ready.wait(timeout=2)
        long_env = {
            **env, "DARKEXEC_APP_SERVER_SOCKET": str(long_socket),
            "DARKEXEC_TURN_TIMEOUT": "0",
        }
        long_result = subprocess.run(
            run_command, input="WAIT_THEN_COMPLETE", capture_output=True, text=True,
            env=long_env, check=False, timeout=5,
        )
        assert long_result.returncode == 0, long_result.stderr or long_result.stdout
        assert json.loads(long_result.stdout)["status"] == "completed", long_result.stdout
        long_server.join(timeout=2)
        assert not long_server.is_alive()
        reconciled_socket, reconciled_ready = root / "reconciled.sock", threading.Event()
        reconciled_server = threading.Thread(
            target=fake_app_server, args=(reconciled_socket, reconciled_ready), daemon=True
        )
        reconciled_server.start(); assert reconciled_ready.wait(timeout=2)
        reconciled = subprocess.run(
            run_command, input="COMPLETE_WITHOUT_NOTIFICATION",
            capture_output=True, text=True,
            env={
                **long_env,
                "DARKEXEC_APP_SERVER_SOCKET": str(reconciled_socket),
                "CODEX_THREAD_ID": "10000000-0000-4000-8000-000000000006",
            },
            check=False, timeout=8,
        )
        assert reconciled.stdout, reconciled.stderr
        reconciled_result = json.loads(reconciled.stdout)
        assert reconciled.returncode == 0, reconciled.stderr or reconciled.stdout
        assert reconciled_result["status"] == "completed", reconciled_result
        assert (
            reconciled_result["target"]["resultText"]
            == "TARGET_OK:COMPLETE_WITHOUT_NOTIFICATION"
        ), reconciled_result
        reconciled_server.join(timeout=2)
        assert not reconciled_server.is_alive()
        signal_socket, signal_server_ready, stalled = root / "signal.sock", threading.Event(), threading.Event()
        signal_server = threading.Thread(
            target=fake_app_server,
            args=(signal_socket, signal_server_ready, True, stalled),
            daemon=True,
        )
        signal_server.start()
        assert signal_server_ready.wait(timeout=2)
        stop_executive = "10000000-0000-4000-8000-000000000002"
        signal_env = {
            **env, "DARKEXEC_APP_SERVER_SOCKET": str(signal_socket),
            "CODEX_THREAD_ID": stop_executive,
        }
        process = subprocess.Popen(
            run_command,
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
        active_status = subprocess.run(
            [
                str(ROOT / "bin/darkexec"), "execution-status",
                "--executive-thread", stop_executive, "--json",
            ],
            capture_output=True, text=True, env=signal_env, check=False,
        )
        active_status_result = json.loads(active_status.stdout)
        assert active_status.returncode == 0, active_status.stderr
        assert active_status_result["status"] == "active", active_status_result
        assert active_status_result["phase"] == "target_running", active_status_result
        assert active_status_result["runnerActive"] is True, active_status_result
        assert active_status_result["target"]["turnId"], active_status_result
        stop_target = "00000000-0000-4000-8000-000000000002"
        stop_session = root / "sessions" / f"{hashlib.sha256(stop_target.encode()).hexdigest()}.json"
        stop_session.parent.mkdir(parents=True, exist_ok=True)
        stop_session.write_text(json.dumps({
            "schemaVersion": 1, "threadId": stop_target, "status": "pending",
            "timerUnit": None, "generation": 1,
        }))
        stopped = subprocess.run(
            [
                str(ROOT / "bin/darkexec"), "stop",
                "--executive-thread", stop_executive, "--json",
            ],
            capture_output=True, text=True, env=signal_env, check=False,
        )
        stopped_result = json.loads(stopped.stdout)
        assert stopped.returncode == 0 and stopped_result["status"] == "stopped", stopped_result
        assert stopped_result["targetState"] == "interrupt_acknowledged", stopped_result
        assert stopped_result["termSent"] is True and stopped_result["killSent"] is False, stopped_result
        assert stopped_result["closeout"]["status"] == "cancelled", stopped_result
        stop_session.unlink()
        process.wait(timeout=5)
        assert process.returncode == 128 + signal.SIGTERM, process.returncode
        interrupted = json.loads(
            (root / "executions" / f"{hashlib.sha256(stop_executive.encode()).hexdigest()}.json").read_text()
        )
        assert interrupted["status"] == "interrupted", interrupted
        assert interrupted["error"] == f"interrupted by signal {signal.SIGTERM}", interrupted
        interrupted_status = subprocess.run(
            [
                str(ROOT / "bin/darkexec"), "execution-status",
                "--executive-thread", stop_executive, "--json",
            ],
            capture_output=True, text=True, env=signal_env, check=False,
        )
        interrupted_status_result = json.loads(interrupted_status.stdout)
        assert interrupted_status.returncode == 0, interrupted_status.stderr
        assert interrupted_status_result["status"] == "interrupted", interrupted_status_result
        assert interrupted_status_result["phase"] == "idle", interrupted_status_result
        assert interrupted_status_result["runnerActive"] is False, interrupted_status_result
        repeated = subprocess.run(
            [
                str(ROOT / "bin/darkexec"), "stop",
                "--executive-thread", stop_executive, "--json",
            ],
            capture_output=True, text=True, env=signal_env, check=False,
        )
        assert repeated.returncode == 0
        assert json.loads(repeated.stdout)["status"] == "already_stopped", repeated.stdout
        interrupted_continue = subprocess.run(
            [
                str(ROOT / "bin/darkexec"), "continue", "--target", str(target),
                "--thread", stopped_result["targetThreadId"], "--prompt-stdin", "--json",
            ],
            input="Must not resume.", capture_output=True, text=True,
            env=signal_env, check=False,
        )
        assert interrupted_continue.returncode != 0
        assert "interrupted target lineage" in interrupted_continue.stderr, interrupted_continue.stderr
        signal_server.join(timeout=2)
        assert not signal_server.is_alive()
        hard_executive = "10000000-0000-4000-8000-000000000003"
        stubborn = subprocess.Popen([
            sys.executable, "-c",
            "import signal,time; signal.signal(signal.SIGTERM, lambda *_: None); time.sleep(30)",
        ])
        time.sleep(0.1)
        hard_path = root / "executions" / f"{hashlib.sha256(hard_executive.encode()).hexdigest()}.json"
        hard_path.write_text(json.dumps({
            "schemaVersion": 1, "executiveThreadId": hard_executive,
            "targetPath": str(target), "mode": "initial", "status": "active",
            "phase": "starting", "runner": {
                "pid": stubborn.pid, "startTicks": process_start_ticks(stubborn.pid),
            },
            "target": {},
        }))
        hard = subprocess.run(
            [
                str(ROOT / "bin/darkexec"), "stop",
                "--executive-thread", hard_executive, "--hard", "--json",
            ],
            capture_output=True, text=True, env=signal_env, check=False, timeout=8,
        )
        hard_result = json.loads(hard.stdout)
        assert hard.returncode == 0 and hard_result["status"] == "stopped", hard_result
        assert hard_result["killSent"] is True and hard_result["targetState"] == "not_created", hard_result
        stubborn.wait(timeout=3)
        assert stubborn.returncode == -signal.SIGKILL, stubborn.returncode
        detached_executive = "10000000-0000-4000-8000-000000000004"
        detached_thread = "20000000-0000-4000-8000-000000000001"
        detached_path = root / "executions" / f"{hashlib.sha256(detached_executive.encode()).hexdigest()}.json"
        detached_path.write_text(json.dumps({
            "schemaVersion": 1, "executiveThreadId": detached_executive,
            "targetPath": "/not/currently/saved", "mode": "continue", "status": "active",
            "phase": "target_running", "runner": {"pid": None, "startTicks": None},
            "target": {"threadId": detached_thread, "turnId": "detached-turn"},
        }))
        detached_socket, detached_ready = root / "detached.sock", threading.Event()
        detached_server = threading.Thread(
            target=fake_app_server,
            args=(detached_socket, detached_ready, False, None, {
                detached_thread: {"cwd": "/not/currently/saved", "turns": [{
                    "id": "detached-turn", "status": "inProgress", "items": [],
                }]},
            }),
            daemon=True,
        )
        detached_server.start(); assert detached_ready.wait(timeout=2)
        detached = subprocess.run(
            [
                str(ROOT / "bin/darkexec"), "stop", "--executive-thread",
                detached_executive, "--hard", "--json",
            ],
            capture_output=True, text=True,
            env={**signal_env, "DARKEXEC_APP_SERVER_SOCKET": str(detached_socket)},
            check=False, timeout=8,
        )
        detached_result = json.loads(detached.stdout)
        assert detached.returncode == 0 and detached_result["status"] == "stopped", detached_result
        assert detached_result["targetState"] == "interrupt_sent", detached_result
        assert detached_result["targetThreadId"] == detached_thread, detached_result
        detached_server.join(timeout=2)
        assert not detached_server.is_alive()
        stale_executive = "10000000-0000-4000-8000-000000000005"
        unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        stale_path = root / "executions" / f"{hashlib.sha256(stale_executive.encode()).hexdigest()}.json"
        stale_path.write_text(json.dumps({
            "schemaVersion": 1, "executiveThreadId": stale_executive,
            "targetPath": str(target), "mode": "initial", "status": "active",
            "phase": "starting", "runner": {
                "pid": unrelated.pid, "startTicks": process_start_ticks(unrelated.pid) + 1,
            },
            "target": {},
        }))
        stale_stop = subprocess.run(
            [
                str(ROOT / "bin/darkexec"), "stop",
                "--executive-thread", stale_executive, "--hard", "--json",
            ],
            capture_output=True, text=True, env=signal_env, check=False,
        )
        stale_result = json.loads(stale_stop.stdout)
        assert stale_stop.returncode == 0 and stale_result["status"] == "stopped", stale_result
        assert stale_result["termSent"] is False and stale_result["killSent"] is False, stale_result
        assert unrelated.poll() is None
        unrelated.terminate()
        unrelated.wait(timeout=3)
        timer_thread = "00000000-0000-4000-8000-000000000002"
        scheduler_log = root / "scheduler.log"
        fake_systemd_run = root / "systemd-run"
        fake_systemd_run.write_text(
            "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >>\"$DARKEXEC_SCHEDULER_LOG\"\nexit \"${DARKEXEC_SCHEDULER_EXIT:-0}\"\n"
        )
        fake_systemctl = root / "systemctl"
        fake_systemctl.write_text(
            "#!/usr/bin/env bash\nprintf 'stop %s\\n' \"$*\" >>\"$DARKEXEC_SCHEDULER_LOG\"\n"
        )
        fake_systemd_run.chmod(0o755)
        fake_systemctl.chmod(0o755)
        timer_env = {
            **env,
            "DARKEXEC_SESSION_ROOT": str(root / "sessions"),
            "DARKEXEC_SYSTEMD_RUN": str(fake_systemd_run),
            "DARKEXEC_SYSTEMCTL": str(fake_systemctl),
            "DARKEXEC_SCHEDULER_LOG": str(scheduler_log),
            "DARKEXEC_DEBOUNCE_SKIP_PREFLIGHT": "1",
        }
        arm = [
            str(ROOT / "bin/darkexec"), "debounce", "--target", str(target),
            "--thread", timer_thread, "--turn", "product-1", "--seconds", "1800",
            "--harness-mode", "standard", "--json",
        ]
        armed = subprocess.run(arm, capture_output=True, text=True, env=timer_env, check=False)
        armed_result = json.loads(armed.stdout)
        assert armed.returncode == 0 and armed_result["status"] == "pending", armed_result
        assert armed_result["generation"] == 1 and "-g1-a1" in armed_result["timerUnit"], armed_result
        assert (
            "--on-calendar=" + armed_result["dueAt"].replace("T", " ").removesuffix("Z") + " UTC"
            in scheduler_log.read_text()
        ), scheduler_log.read_text()
        reset = [*arm]
        reset[reset.index("product-1")] = "product-2"
        reset_result = json.loads(subprocess.run(
            reset, capture_output=True, text=True, env=timer_env, check=False,
        ).stdout)
        assert reset_result["generation"] == 2 and "-g2-a1" in reset_result["timerUnit"], reset_result
        assert "stop darkexec-closeout-" in scheduler_log.read_text()
        pending_status = subprocess.run(
            [str(ROOT / "bin/darkexec"), "debounce-status", "--thread", timer_thread, "--json"],
            capture_output=True, text=True, env=timer_env, check=False,
        )
        assert pending_status.returncode == 0
        assert json.loads(pending_status.stdout)["status"] == "pending", pending_status.stdout
        stale = subprocess.run(
            [str(ROOT / "bin/darkexec"), "_debounce-fire", "--thread", timer_thread, "--generation", "1"],
            capture_output=True, text=True, env=timer_env, check=False,
        )
        assert stale.returncode == 0 and json.loads(stale.stdout)["status"] == "stale", stale.stdout
        manual_socket, manual_ready = root / "manual.sock", threading.Event()
        manual_turns = [
            {"id": "product-2", "status": "completed", "items": [
                {"id": "u-product", "type": "userMessage", "content": [{"type": "text", "text": "Follow-up work"}]},
            ]},
            {"id": "manual-harness", "status": "completed", "items": [
                {"id": "u-harness", "type": "userMessage", "content": [{"type": "text", "text": (
                    "Let's do a harness pass where we take a look at this session and turn trial and error "
                    "into fast, reliable, and durable execution. Make sure we are following "
                    "/srv/darkexec/harness-ops.md doctrine."
                )}]},
                {"id": "a-harness", "type": "agentMessage", "text": "SECOND HARNESS CLOSEOUT"},
            ]},
        ]
        manual_server = threading.Thread(
            target=fake_app_server,
            args=(manual_socket, manual_ready, True, None, {
                timer_thread: {
                    "cwd": str(target), "turns": manual_turns, "loaded": False,
                    "path": str(root / "persisted-target.jsonl"), "requirePath": True,
                },
            }),
            daemon=True,
        )
        manual_server.start(); assert manual_ready.wait(timeout=2)
        manual_env = {**timer_env, "DARKEXEC_APP_SERVER_SOCKET": str(manual_socket)}
        manual = subprocess.run(
            [str(ROOT / "bin/darkexec"), "_debounce-fire", "--thread", timer_thread, "--generation", "2"],
            capture_output=True, text=True, env=manual_env, check=False,
        )
        manual_result = json.loads(manual.stdout)
        assert manual.returncode == 0 and manual_result["status"] == "manual_harness_seen", manual.stdout
        assert manual_result["harnessTurnId"] == "manual-harness", manual_result
        assert manual_result["harnessResult"] == "SECOND HARNESS CLOSEOUT", manual_result
        manual_server.join(timeout=2)
        assert not manual_server.is_alive()
        manual_state_path = (
            Path(timer_env["DARKEXEC_SESSION_ROOT"])
            / f"{hashlib.sha256(timer_thread.encode()).hexdigest()}.json"
        )
        manual_state = json.loads(manual_state_path.read_text())
        manual_state["harnessTurnId"] = None
        manual_state["harnessResult"] = None
        manual_state_path.write_text(json.dumps(manual_state))
        repair_socket, repair_ready = root / "repair.sock", threading.Event()
        repair_server = threading.Thread(
            target=fake_app_server,
            args=(repair_socket, repair_ready, True, None, {
                timer_thread: {"cwd": str(target), "turns": manual_turns},
            }),
            daemon=True,
        )
        repair_server.start(); assert repair_ready.wait(timeout=2)
        repair_env = {**timer_env, "DARKEXEC_APP_SERVER_SOCKET": str(repair_socket)}
        repaired = subprocess.run(
            [str(ROOT / "bin/darkexec"), "debounce-status", "--thread", timer_thread, "--json"],
            capture_output=True, text=True, env=repair_env, check=False,
        )
        repaired_result = json.loads(repaired.stdout)
        assert repaired.returncode == 0, repaired.stderr
        assert repaired_result["harnessTurnId"] == "manual-harness", repaired_result
        assert repaired_result["harnessResult"] == "SECOND HARNESS CLOSEOUT", repaired_result
        repair_server.join(timeout=2)
        assert not repair_server.is_alive()
        fallback_socket, fallback_ready = root / "fallback.sock", threading.Event()
        fallback_turns = [{"id": "product-3", "status": "completed", "items": [
            {"id": "u-product-3", "type": "userMessage", "content": [{"type": "text", "text": "More follow-up work"}]},
        ]}]
        fallback_server = threading.Thread(
            target=fake_app_server,
            args=(fallback_socket, fallback_ready, True, None, {
                timer_thread: {"cwd": str(target), "turns": fallback_turns},
            }),
            daemon=True,
        )
        fallback_server.start(); assert fallback_ready.wait(timeout=2)
        fallback_env = {
            **timer_env, "DARKEXEC_APP_SERVER_SOCKET": str(fallback_socket),
            "DARKEXEC_SCHEDULER_EXIT": "1",
        }
        fallback_arm = [*arm]
        fallback_arm[fallback_arm.index("product-1")] = "product-3"
        fallback = subprocess.run(
            fallback_arm, capture_output=True, text=True, env=fallback_env, check=False,
        )
        fallback_result = json.loads(fallback.stdout)
        assert fallback.returncode == 0 and fallback_result["status"] == "completed", fallback_result
        assert fallback_result["scheduleError"], fallback_result
        fallback_server.join(timeout=2)
        assert not fallback_server.is_alive()
        cancelled = subprocess.run(
            [str(ROOT / "bin/darkexec"), "debounce-cancel", "--thread", timer_thread, "--json"],
            capture_output=True, text=True, env=timer_env, check=False,
        )
        assert cancelled.returncode == 0 and json.loads(cancelled.stdout)["status"] == "cancelled", cancelled.stdout
        paused_thread = "00000000-0000-4000-8000-000000000003"
        paused_arm = [*arm]
        paused_arm[paused_arm.index(timer_thread)] = paused_thread
        paused_arm[paused_arm.index("product-1")] = "paused-product-1"
        paused_result = json.loads(subprocess.run(
            paused_arm, capture_output=True, text=True, env=timer_env, check=False,
        ).stdout)
        assert paused_result["status"] == "pending", paused_result
        paused = subprocess.run(
            [str(ROOT / "bin/darkexec"), "debounce-pause", "--thread", paused_thread, "--json"],
            capture_output=True, text=True, env=timer_env, check=False,
        )
        paused_status = json.loads(paused.stdout)
        assert paused.returncode == 0 and paused_status["status"] == "paused", paused_status
        assert 0 < paused_status["remainingSeconds"] <= 1800, paused_status
        paused_reset = [*paused_arm]
        paused_reset[paused_reset.index("paused-product-1")] = "paused-product-2"
        paused_reset_status = json.loads(subprocess.run(
            paused_reset, capture_output=True, text=True, env=timer_env, check=False,
        ).stdout)
        assert paused_reset_status["status"] == "paused", paused_reset_status
        assert paused_reset_status["generation"] == 2, paused_reset_status
        assert paused_reset_status["remainingSeconds"] == 1800, paused_reset_status
        resumed = subprocess.run(
            [str(ROOT / "bin/darkexec"), "debounce-resume", "--thread", paused_thread, "--json"],
            capture_output=True, text=True, env=timer_env, check=False,
        )
        resumed_status = json.loads(resumed.stdout)
        assert resumed.returncode == 0 and resumed_status["status"] == "pending", resumed_status
        paused_cancelled = subprocess.run(
            [str(ROOT / "bin/darkexec"), "debounce-cancel", "--thread", paused_thread, "--json"],
            capture_output=True, text=True, env=timer_env, check=False,
        )
        assert json.loads(paused_cancelled.stdout)["status"] == "cancelled", paused_cancelled.stdout
        restarted_status = json.loads(subprocess.run(
            paused_reset, capture_output=True, text=True, env=timer_env, check=False,
        ).stdout)
        assert restarted_status["status"] == "pending", restarted_status
        assert restarted_status["generation"] == 3, restarted_status
        subprocess.run(
            [str(ROOT / "bin/darkexec"), "debounce-cancel", "--thread", paused_thread, "--json"],
            capture_output=True, text=True, env=timer_env, check=False,
        )
        now_thread = "00000000-0000-4000-8000-000000000004"
        now_turn = "closeout-now-product"
        now_arm = [*arm]
        now_arm[now_arm.index(timer_thread)] = now_thread
        now_arm[now_arm.index("product-1")] = now_turn
        now_armed = json.loads(subprocess.run(
            now_arm, capture_output=True, text=True, env=timer_env, check=False,
        ).stdout)
        assert now_armed["status"] == "pending", now_armed
        now_socket, now_ready = root / "closeout-now.sock", threading.Event()
        now_server = threading.Thread(
            target=fake_app_server,
            args=(now_socket, now_ready, True, None, {
                now_thread: {"cwd": str(target), "turns": [{
                    "id": now_turn, "status": "completed", "items": [{
                        "id": "now-user", "type": "userMessage",
                        "content": [{"type": "text", "text": "Latest product work"}],
                    }],
                }]},
            }),
            daemon=True,
        )
        now_server.start(); assert now_ready.wait(timeout=2)
        now_result = subprocess.run(
            [str(ROOT / "bin/darkexec"), "debounce-now", "--thread", now_thread, "--json"],
            capture_output=True, text=True,
            env={**timer_env, "DARKEXEC_APP_SERVER_SOCKET": str(now_socket)}, check=False,
        )
        now_status = json.loads(now_result.stdout)
        assert now_result.returncode == 0 and now_status["status"] == "completed", now_status
        now_server.join(timeout=2)
        assert not now_server.is_alive()
        update_source = root / "update-source"
        (update_source / "scripts").mkdir(parents=True)
        fake_install = update_source / "scripts" / "install.sh"
        fake_install.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' '{\"commit\":\"update-test\",\"workspace\":\"/srv/darkexec\","
            "\"nextAction\":\"Open /srv/darkexec in Codex App.\"}'\n"
        )
        fake_install.chmod(0o755)
        subprocess.run(["git", "init", "--quiet", "--initial-branch=main"], cwd=update_source, check=True)
        subprocess.run(["git", "add", "scripts/install.sh"], cwd=update_source, check=True)
        subprocess.run(
            ["git", "-c", "user.name=DarkExec Test", "-c", "user.email=test@darkexec.invalid",
             "commit", "--quiet", "-m", "fixture"],
            cwd=update_source, check=True,
        )
        updated = subprocess.run(
            [str(ROOT / "bin/darkexec"), "update", "--json"],
            capture_output=True, text=True,
            env={**env, "DARKEXEC_UPDATE_REPOSITORY": str(update_source), "DARKEXEC_UPDATE_REF": "main"},
            check=False,
        )
        updated_result = json.loads(updated.stdout)
        assert updated.returncode == 0 and updated_result["status"] == "updated", updated.stderr or updated.stdout
        assert updated_result["commit"] == "update-test", updated_result
    print(json.dumps({"status": "passed", "contracts": [
        "saved-project-list", "saved-target", "running-app-list-proof", "post-first-turn-app-list-proof",
        "one-executive", "one-target", "same-task-harness",
        "interactive-harness-mode-required", "interactive-target-run", "private-execution-state",
        "interactive-execution-status", "direct-structured-input",
        "attached-same-turn-steer",
        "runtime-owned-follow-up",
        "bound-target-no-replacement", "executive-scoped-clean-stop", "idempotent-stop",
        "stop-cancels-closeout", "no-interrupted-resume",
        "verified-hard-stop", "recorded-unsaved-target-stop", "stale-pid-safe",
        "separate-usage", "idempotent-job", "thread-status", "receipt-attached-wait",
        "abandoned-receipt-fail-closed", "background-stop-receipt-resolution",
        "background-closeout-user-turn-suppression",
        "conflict-closed", "signal-terminalized", "follow-up-debounce-reset", "stale-generation-noop",
        "deferred-initial-harness", "executive-target-resolution",
        "manual-harness-suppression",
        "schedule-failure-immediate-closeout", "cold-task-resume",
        "persisted-rollout-path-resume", "debounce-status",
        "debounce-pause-resume", "debounce-cancel", "debounce-now",
        "unbounded-turn-wait", "lost-completion-reconciliation",
        "install-default-verification", "self-update",
    ]}))


if __name__ == "__main__":
    main()
