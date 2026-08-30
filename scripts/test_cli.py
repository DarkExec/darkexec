#!/usr/bin/env python3
"""Offline contract test for dispatch, status, idempotency, and same-task harness."""

import base64, fcntl, hashlib, json, os, runpy, signal, socket, struct, subprocess, sys, tempfile, threading, time
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
    stall_harness: bool = False,
    fail_harness: bool = False,
    route_unresolved: bool = False,
    remove_routed_target: bool = False,
    observed_options: list[dict] | None = None,
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
            if cwd.endswith("darkexec"):
                thread = "00000000-0000-4000-8000-000000000001"
            else:
                suffix = 2
                while f"00000000-0000-4000-8000-{suffix:012d}" in threads:
                    suffix += 1
                thread = f"00000000-0000-4000-8000-{suffix:012d}"
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
        elif method == "thread/turns/list":
            thread = message["params"]["threadId"]
            if thread not in loaded:
                send_frame(connection, {
                    "id": message["id"],
                    "error": {"code": -32600, "message": f"thread not found: {thread}"},
                })
                continue
            ordered = list(reversed(histories[thread]))
            start = int(message["params"].get("cursor") or 0)
            limit = int(message["params"].get("limit") or 16)
            data = ordered[start:start + limit]
            next_cursor = str(start + limit) if start + limit < len(ordered) else None
            send_frame(connection, {"id": message["id"], "result": {
                "data": data, "nextCursor": next_cursor,
            }})
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
            if observed_options is not None:
                observed_options.append({key: message["params"][key] for key in ("effort", "serviceTier") if key in message["params"]})
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
            if stall_harness and "harness pass" in prompt.lower():
                if stall_ready:
                    stall_ready.set()
                continue
            if fail_harness and "harness pass" in prompt.lower():
                history["status"] = "failed"
                send_frame(connection, {"method": "turn/completed", "params": {
                    "threadId": thread, "turn": {
                        "id": turn, "status": "failed", "error": {"message": "fixture harness failure"},
                    },
                }})
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
            if prompt.startswith("DARKEXEC GLOBAL ROUTE TASK."):
                allowed = json.loads(
                    prompt.split("Allowed candidates: ", 1)[1].split(
                        ". Natural request:", 1
                    )[0]
                )
                selected = next(item for item in allowed if item["hostLabel"] == "DROIDFI")
                job_id = prompt.split("owns job ", 1)[1].split(".", 1)[0]
                text = (
                    f"DARKEXEC_GLOBAL_ROUTE_READY {job_id} "
                    + json.dumps({
                        "hostId": selected["hostId"],
                        "targetPath": selected["targetPath"],
                    }, separators=(",", ":"))
                )
            elif prompt.startswith("DARKEXEC ROUTE TASK."):
                allowed = json.loads(
                    prompt.split("Allowed projects: ", 1)[1].split(
                        ". Natural request:", 1
                    )[0]
                )
                job_id = prompt.split("owns job ", 1)[1].split(".", 1)[0]
                if route_unresolved:
                    text = f"DARKEXEC_ROUTE_UNRESOLVED {job_id}"
                else:
                    selected = next(path for path in allowed if not path.endswith("darkexec"))
                    text = f"DARKEXEC_ROUTE_READY {job_id} {selected}"
                    if remove_routed_target:
                        Path(selected).rmdir()
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
                "threadId": thread, "turnId": turn, "tokenUsage": {"last": {
                    "inputTokens": 10, "cachedInputTokens": 4,
                    "outputTokens": 2, "reasoningOutputTokens": 1, "totalTokens": 12,
                }, "total": {
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
                "threadId": thread, "turnId": turn, "tokenUsage": {"last": {
                    "inputTokens": 10, "cachedInputTokens": 4, "outputTokens": 2,
                    "reasoningOutputTokens": 1, "totalTokens": 12,
                }, "total": {
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
        os.environ["DARKEXEC_DOCTRINE_REFRESH"] = ""
        runtime = runpy.run_path(str(ROOT / "bin/darkexec"))
        refresh_doctrine = runtime["refresh_harness_doctrine"]
        incident_prompt = (
            "you can go ahead and make all the changes to toolburn you wanted, and also move passes "
            "into DarkExec/harness-ops, then change the harness prompt because efficiency passes can "
            "help toolburn make the harness passes more economical"
        )
        assert runtime["harness_prompt_mode"](incident_prompt) is None
        assert runtime["harness_prompt_mode"](runtime["standard_harness_prompt"]()) == "standard"
        assert runtime["harness_prompt_mode"](runtime["READ_ONLY_HARNESS_PROMPT"]) == "read-only"
        pinned_refresh = refresh_doctrine()
        assert pinned_refresh["status"] == "pinned", pinned_refresh
        refresh_fixture = root / "refresh-fixture"
        refresh_fixture.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' '{\"status\":\"current\","
            "\"revision\":\"1111111111111111111111111111111111111111\","
            "\"harnessOpsSha256\":\"2222222222222222222222222222222222222222222222222222222222222222\"}'\n"
        )
        refresh_fixture.chmod(0o700)
        refresh_doctrine.__globals__["DOCTRINE_REFRESH"] = str(refresh_fixture)
        managed_refresh = refresh_doctrine()
        assert managed_refresh == {
            "status": "current",
            "revision": "1" * 40,
            "harnessOpsSha256": "2" * 64,
        }, managed_refresh
        refresh_fixture.write_text("#!/usr/bin/env bash\nprintf '%s\\n' '{}'")
        refresh_fixture.chmod(0o700)
        try:
            refresh_doctrine()
            raise AssertionError("invalid doctrine refresh receipt was accepted")
        except RuntimeError as exc:
            assert "invalid receipt" in str(exc), exc
        refresh_doctrine.__globals__["DOCTRINE_REFRESH"] = ""
        prompt_path = root / "config" / "harness-prompt.txt"
        efficiency_prompt_path = root / "config" / "efficiency-prompt.txt"
        harness_project_prompt_root = root / "config" / "harness-prompts"
        efficiency_project_prompt_root = root / "config" / "efficiency-prompts"
        prompt_project = root / "prompt-project"
        prompt_project.mkdir()
        prompt_config = root / "prompt-config.toml"
        prompt_config.write_text(f'[projects."{prompt_project}"]\ntrust_level = "trusted"\n')
        prompt_env = {
            **os.environ,
            "DARKEXEC_HARNESS_PROMPT_PATH": str(prompt_path),
            "DARKEXEC_EFFICIENCY_PROMPT_PATH": str(efficiency_prompt_path),
            "DARKEXEC_HARNESS_PROJECT_PROMPT_ROOT": str(harness_project_prompt_root),
            "DARKEXEC_EFFICIENCY_PROJECT_PROMPT_ROOT": str(efficiency_project_prompt_root),
            "DARKEXEC_CONFIG": str(prompt_config),
        }
        default_prompt = json.loads(subprocess.run(
            [str(ROOT / "bin/darkexec"), "harness-prompt", "--json"],
            capture_output=True, text=True, env=prompt_env, check=True,
        ).stdout)
        assert default_prompt["source"] == "default" and default_prompt["isDefault"] is True
        assert "docs/passes/harness/AGENTS.md" in default_prompt["prompt"]
        custom_text = "Review this completed session and make one durable harness improvement."
        saved_prompt = json.loads(subprocess.run(
            [str(ROOT / "bin/darkexec"), "harness-prompt", "--set-stdin", "--json"],
            input=custom_text, capture_output=True, text=True, env=prompt_env, check=True,
        ).stdout)
        assert saved_prompt == {
            "schemaVersion": 1, "prompt": custom_text,
            "source": "custom", "isDefault": False, "targetPath": None,
        }, saved_prompt
        assert prompt_path.read_text() == custom_text + "\n"
        assert prompt_path.stat().st_mode & 0o777 == 0o600
        inherited_prompt = json.loads(subprocess.run(
            [str(ROOT / "bin/darkexec"), "harness-prompt", "--target", str(prompt_project), "--json"],
            capture_output=True, text=True, env=prompt_env, check=True,
        ).stdout)
        assert inherited_prompt["prompt"] == custom_text
        assert inherited_prompt["source"] == "inherited" and inherited_prompt["isDefault"] is True
        project_text = "Use the project-specific harness workflow."
        project_prompt = json.loads(subprocess.run(
            [
                str(ROOT / "bin/darkexec"), "harness-prompt", "--target", str(prompt_project),
                "--set-stdin", "--json",
            ],
            input=project_text, capture_output=True, text=True, env=prompt_env, check=True,
        ).stdout)
        assert project_prompt["prompt"] == project_text and project_prompt["source"] == "project"
        assert project_prompt["targetPath"] == str(prompt_project)
        assert prompt_path.read_text() == custom_text + "\n"
        reset_project_prompt = json.loads(subprocess.run(
            [
                str(ROOT / "bin/darkexec"), "harness-prompt", "--target", str(prompt_project),
                "--reset", "--json",
            ],
            capture_output=True, text=True, env=prompt_env, check=True,
        ).stdout)
        assert reset_project_prompt["prompt"] == custom_text
        assert reset_project_prompt["source"] == "inherited"
        empty_prompt = subprocess.run(
            [str(ROOT / "bin/darkexec"), "harness-prompt", "--set-stdin", "--json"],
            input="  ", capture_output=True, text=True, env=prompt_env, check=False,
        )
        assert empty_prompt.returncode != 0 and "must not be empty" in empty_prompt.stderr
        reset_prompt = json.loads(subprocess.run(
            [str(ROOT / "bin/darkexec"), "harness-prompt", "--reset", "--json"],
            capture_output=True, text=True, env=prompt_env, check=True,
        ).stdout)
        assert reset_prompt["source"] == "default" and not prompt_path.exists()
        default_efficiency_prompt = json.loads(subprocess.run(
            [str(ROOT / "bin/darkexec"), "efficiency-prompt", "--json"],
            capture_output=True, text=True, env=prompt_env, check=True,
        ).stdout)
        assert default_efficiency_prompt["source"] == "default"
        assert default_efficiency_prompt["prompt"].startswith(
            "Now briefly review the harness pass you just performed"
        )
        assert "docs/passes/efficiency/AGENTS.md" in default_efficiency_prompt["prompt"]
        custom_efficiency_text = "Find and fix the single largest avoidable harness cost."
        saved_efficiency_prompt = json.loads(subprocess.run(
            [str(ROOT / "bin/darkexec"), "efficiency-prompt", "--set-stdin", "--json"],
            input=custom_efficiency_text, capture_output=True, text=True, env=prompt_env, check=True,
        ).stdout)
        assert saved_efficiency_prompt["prompt"] == custom_efficiency_text
        assert efficiency_prompt_path.read_text() == custom_efficiency_text + "\n"
        assert efficiency_prompt_path.stat().st_mode & 0o777 == 0o600
        project_efficiency_text = "Review this project's largest avoidable harness cost."
        subprocess.run(
            [
                str(ROOT / "bin/darkexec"), "efficiency-prompt", "--target", str(prompt_project),
                "--set-stdin", "--json",
            ],
            input=project_efficiency_text, capture_output=True, text=True, env=prompt_env, check=True,
        )
        resolved_efficiency_prompt = json.loads(subprocess.run(
            [
                str(ROOT / "bin/darkexec"), "efficiency-prompt", "--target", str(prompt_project),
                "--json",
            ],
            capture_output=True, text=True, env=prompt_env, check=True,
        ).stdout)
        assert resolved_efficiency_prompt["prompt"] == project_efficiency_text
        assert resolved_efficiency_prompt["source"] == "project"
        normalize_input_items = runtime["normalize_input_items"]
        source_rollout = root / "source-rollout.jsonl"
        source_rollout.write_text("\n".join(json.dumps(item) for item in [
            {"type": "session_meta", "payload": {"id": "source-thread"}},
            {"type": "session_meta", "payload": {"id": "forked-from-thread"}},
            {"timestamp": "2026-08-05T00:00:00Z", "type": "event_msg", "payload": {
                "type": "task_started", "turn_id": "product-turn",
            }},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call", "call_id": "private-call", "name": "exec",
                "input": 'tools.exec_command({"cmd":"rg secret-token private-file"})',
            }},
            {"type": "response_item", "payload": {
                "type": "custom_tool_call_output", "call_id": "private-call",
                "success": True, "output": "private output",
            }},
            {"timestamp": "2026-08-05T00:00:01Z", "type": "event_msg", "payload": {
                "type": "token_count", "info": {"last_token_usage": {
                    "input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 5,
                    "reasoning_output_tokens": 2, "total_tokens": 105,
                }},
            }},
            {"timestamp": "2026-08-05T00:00:02Z", "type": "event_msg", "payload": {
                "type": "task_complete", "turn_id": "product-turn", "duration_ms": 2000,
            }},
        ]) + "\n")
        source_evidence = runtime["rollout_closeout_evidence"](
            str(source_rollout), "source-thread", ["product-turn"]
        )
        assert source_evidence["evidenceComplete"] is True, source_evidence
        assert source_evidence["totals"]["modelCalls"] == 1, source_evidence
        assert source_evidence["totals"]["toolCalls"] == 1, source_evidence
        assert source_evidence["totals"]["usage"]["total"] == 105, source_evidence
        activity = source_evidence["turns"][0]["activity"]
        assert activity["totals"][0]["kind"] == "inspect", activity
        assert activity["totals"][0]["modelCallsAfter"] == 1, activity
        assert activity["spans"] == [activity["totals"][0]], activity
        assert source_evidence["activityTotals"] == activity["totals"], source_evidence
        rendered_evidence = json.dumps(source_evidence)
        assert "secret-token" not in rendered_evidence, source_evidence
        assert "private-file" not in rendered_evidence, source_evidence
        assert "private output" not in rendered_evidence, source_evidence
        attachment_input = [
            {"type": "text", "text": 'Approve release.\n\nAttached image: "logo.png"'},
            {"type": "localImage", "path": "/tmp/logo.png"},
        ]
        assert normalize_input_items(
            attachment_input, "Approve release.", "input manifest"
        ) == attachment_input
        for invalid_content, error in (
            ([42], "input manifest input items must be objects"),
            (
                [{"type": "text", "text": "Do not approve release."}],
                "stdin prompt does not match the input manifest text",
            ),
            (
                [{"type": "text", "text": "Approve release.\nActually, do not approve."}],
                "stdin prompt does not match the input manifest text",
            ),
        ):
            try:
                normalize_input_items(invalid_content, "approve release.", "input manifest")
            except RuntimeError as exc:
                assert str(exc) == error, exc
            else:
                raise AssertionError(f"accepted invalid structured input: {invalid_content}")
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
        stale_target = root / "stale-target"
        config.write_text(
            f'[projects."{target}"]\ntrust_level = "trusted"\n'
            f'[projects."{stale_target}"]\ntrust_level = "trusted"\n'
        )
        route_candidates = root / "route-candidates.json"
        route_candidates.write_text(json.dumps([
            {"hostId": "host-agentfsd", "hostLabel": "AgentFSD", "targetPath": str(target)},
            {"hostId": "host-droidfi", "hostLabel": "DROIDFI", "targetPath": "/root/openclaw-maintenance"},
        ]))
        route_socket = root / "route-app.sock"
        route_ready = threading.Event()
        route_server = threading.Thread(
            target=fake_app_server, args=(route_socket, route_ready), daemon=True
        )
        route_server.start()
        assert route_ready.wait(timeout=2)
        route_env = {
            **os.environ, "DARKEXEC_STATE_ROOT": str(root / "state"),
            "DARKEXEC_WORKSPACE": str(workspace), "DARKEXEC_CONFIG": str(config),
            "DARKEXEC_APP_SERVER_SOCKET": str(route_socket),
        }
        route = subprocess.run([
            str(ROOT / "bin/darkexec"), "resolve-global", "--job-id", "route-1",
            "--candidates-json", str(route_candidates), "--prompt-stdin", "--json",
        ], input="Fix the OpenClaw issue on DROIDFI.", capture_output=True, text=True,
            env=route_env, check=False)
        assert route.returncode == 0, route.stderr or route.stdout
        route_result = json.loads(route.stdout)
        assert route_result["status"] == "completed", route_result
        assert route_result["hostId"] == "host-droidfi", route_result
        assert route_result["targetPath"] == "/root/openclaw-maintenance", route_result
        assert route_result["executive"]["appVisible"] is True, route_result
        repeated_route = subprocess.run([
            str(ROOT / "bin/darkexec"), "resolve-global", "--job-id", "route-1",
            "--candidates-json", str(route_candidates), "--prompt-stdin", "--json",
        ], input="Fix the OpenClaw issue on DROIDFI.", capture_output=True, text=True,
            env=route_env, check=False)
        assert repeated_route.returncode == 0, repeated_route.stderr
        assert json.loads(repeated_route.stdout)["createdAt"] == route_result["createdAt"]

        socket_path = root / "app.sock"
        ready = threading.Event()
        observed_options = []
        server = threading.Thread(target=fake_app_server, args=(socket_path, ready), kwargs={"observed_options": observed_options}, daemon=True)
        server.start()
        assert ready.wait(timeout=2)
        env = {
            **os.environ, "DARKEXEC_STATE_ROOT": str(root / "state"),
            "DARKEXEC_EXECUTION_ROOT": str(root / "executions"),
            "DARKEXEC_CONTROL_ROOT": str(root / "controls"),
            "DARKEXEC_SESSION_ROOT": str(root / "sessions"),
            "DARKEXEC_HARNESS_EPISODE_ROOT": str(root / "harness-episodes"),
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
            "--thinking-level", "max", "--speed", "fast",
        ]
        first = subprocess.run(command, input="Natural request.", capture_output=True, text=True, env=env, check=False)
        assert first.returncode == 0, first.stderr or first.stdout
        result = json.loads(first.stdout)
        assert observed_options[0] == {}, observed_options
        assert observed_options[1] == {"effort": "max", "serviceTier": "fast"}, observed_options
        conflict_options = subprocess.run([*command[:-1], "standard"], input="Natural request.", capture_output=True, text=True, env=env, check=False)
        assert conflict_options.returncode != 0 and "different request" in conflict_options.stderr
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
        episode_path = Path(result["harnessEpisode"]["path"])
        episode = json.loads(episode_path.read_text())
        assert episode["schema"] == "darkexec.harness-episode/v1", episode
        assert episode["harnessMode"] == "read-only", episode
        assert episode["episodePurpose"] == "control", episode
        assert episode["target"]["turnId"] == result["target"]["turnId"], episode
        assert episode["target"]["harness"]["turnId"] == result["target"]["harness"]["turnId"], episode
        assert episode["runtimeRevision"] == subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
        ).stdout.strip(), episode
        assert episode_path.stat().st_mode & 0o777 == 0o600
        assert episode_path.parent.stat().st_mode & 0o777 == 0o700
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
            f'[projects."{stale_target}"]\ntrust_level = "trusted"\n'
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
        assert str(stale_target) not in routed_inputs[0][0]["text"], routed_inputs
        assert "DARKEXEC_ROUTE_UNRESOLVED" in routed_inputs[0][0]["text"], routed_inputs
        assert routed_inputs[1] == [
            {"type": "text", "text": "Choose the owner and inspect it."}
        ], routed_inputs
        assert "Same-task harness: deferred" in routed_inputs[2][0]["text"], routed_inputs
        routed_server.join(timeout=2)
        assert not routed_server.is_alive()

        unresolved_socket, unresolved_ready = root / "unresolved.sock", threading.Event()
        unresolved_server = threading.Thread(
            target=fake_app_server,
            args=(unresolved_socket, unresolved_ready),
            kwargs={"route_unresolved": True}, daemon=True,
        )
        unresolved_server.start(); assert unresolved_ready.wait(timeout=2)
        unresolved_command = list(routed_command)
        unresolved_command[unresolved_command.index("incident-routed-deferred")] = "incident-route-unresolved"
        unresolved = subprocess.run(
            unresolved_command, input="Choose between two equally plausible owners.",
            capture_output=True, text=True,
            env={
                **env, "DARKEXEC_CONFIG": str(routed_config),
                "DARKEXEC_APP_SERVER_SOCKET": str(unresolved_socket),
            },
            check=False,
        )
        unresolved_result = json.loads(unresolved.stdout)
        assert unresolved.returncode != 0, unresolved_result
        assert unresolved_result["status"] == "failed", unresolved_result
        assert "could not identify one exact saved project" in unresolved_result["error"], unresolved_result
        assert unresolved_result["target"] == {}, unresolved_result
        unresolved_server.join(timeout=2)
        assert not unresolved_server.is_alive()

        disappearing_target = root / "disappearing-target"
        disappearing_target.mkdir()
        disappearing_config = root / "disappearing-config.toml"
        disappearing_config.write_text(
            f'[projects."{workspace}"]\ntrust_level = "trusted"\n'
            f'[projects."{disappearing_target}"]\ntrust_level = "trusted"\n'
        )
        disappearing_socket, disappearing_ready = root / "disappearing.sock", threading.Event()
        disappearing_server = threading.Thread(
            target=fake_app_server,
            args=(disappearing_socket, disappearing_ready),
            kwargs={"remove_routed_target": True}, daemon=True,
        )
        disappearing_server.start(); assert disappearing_ready.wait(timeout=2)
        disappearing_command = list(routed_command)
        disappearing_command[disappearing_command.index("incident-routed-deferred")] = "incident-route-disappeared"
        disappeared = subprocess.run(
            disappearing_command, input="Choose the disappearing owner.",
            capture_output=True, text=True,
            env={
                **env, "DARKEXEC_CONFIG": str(disappearing_config),
                "DARKEXEC_APP_SERVER_SOCKET": str(disappearing_socket),
            },
            check=False,
        )
        disappeared_result = json.loads(disappeared.stdout)
        assert disappeared.returncode != 0, disappeared_result
        assert disappeared_result["status"] == "failed", disappeared_result
        assert disappeared_result["error"] == "Resolved target is no longer an available saved project", disappeared_result
        assert disappeared_result["target"] == {}, disappeared_result
        disappearing_server.join(timeout=2)
        assert not disappearing_server.is_alive()
        dispatch_image = root / "dispatch-image.png"
        dispatch_image.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        dispatch_input = [
            {"type": "text", "text": 'Attachment dispatch.\n\nAttached image: "logo.png"'},
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
        interrupted_harness_socket = root / "interrupted-harness.sock"
        interrupted_harness_ready, interrupted_harness_started = threading.Event(), threading.Event()
        interrupted_harness_server = threading.Thread(
            target=fake_app_server,
            args=(interrupted_harness_socket, interrupted_harness_ready, True, interrupted_harness_started, {}, None, True),
            daemon=True,
        )
        interrupted_harness_server.start(); assert interrupted_harness_ready.wait(timeout=2)
        interrupted_harness_process = subprocess.Popen(
            [
                str(ROOT / "bin/darkexec"), "run", "--target", str(target),
                "--prompt-stdin", "--standard-harness", "--json",
            ],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env={
                **{key: value for key, value in env.items() if key != "CODEX_THREAD_ID"},
                "DARKEXEC_APP_SERVER_SOCKET": str(interrupted_harness_socket),
            },
        )
        assert interrupted_harness_process.stdin
        interrupted_harness_process.stdin.write("Complete product before interrupted harness.")
        interrupted_harness_process.stdin.close()
        assert interrupted_harness_started.wait(timeout=3)
        interrupted_harness_process.send_signal(signal.SIGTERM)
        interrupted_harness_process.wait(timeout=5)
        interrupted_harness_result = json.loads(interrupted_harness_process.stdout.read())
        assert interrupted_harness_process.returncode == 128 + signal.SIGTERM
        assert interrupted_harness_result["status"] == "interrupted", interrupted_harness_result
        assert interrupted_harness_result["harness"]["status"] == "interrupted", interrupted_harness_result
        assert interrupted_harness_result["harness"]["turnId"], interrupted_harness_result
        interrupted_episode = json.loads(Path(interrupted_harness_result["harnessEpisode"]["path"]).read_text())
        assert interrupted_episode["status"] == "interrupted", interrupted_episode
        assert interrupted_episode["target"]["harness"]["turnId"], interrupted_episode
        interrupted_harness_server.join(timeout=2)
        failed_harness_socket = root / "failed-harness.sock"
        failed_harness_ready = threading.Event()
        failed_target = root / "failed-target"
        failed_target.mkdir()
        with config.open("a") as handle:
            handle.write(f'[projects."{failed_target}"]\ntrust_level = "trusted"\n')
        failed_harness_server = threading.Thread(
            target=fake_app_server,
            args=(failed_harness_socket, failed_harness_ready),
            kwargs={"fail_harness": True}, daemon=True,
        )
        failed_harness_server.start(); assert failed_harness_ready.wait(timeout=2)
        failed_harness = subprocess.run(
            [
                str(ROOT / "bin/darkexec"), "run", "--target", str(failed_target),
                "--prompt-stdin", "--standard-harness", "--json",
            ], input="Complete product before failed harness.", capture_output=True, text=True,
            env={
                **{key: value for key, value in env.items() if key != "CODEX_THREAD_ID"},
                "DARKEXEC_APP_SERVER_SOCKET": str(failed_harness_socket),
            }, check=False,
        )
        failed_harness_result = json.loads(failed_harness.stdout)
        assert failed_harness.returncode == 1 and failed_harness_result["status"] == "failed"
        assert failed_harness_result["harness"]["status"] == "failed", failed_harness_result
        failed_episode = json.loads(Path(failed_harness_result["harnessEpisode"]["path"]).read_text())
        assert failed_episode["status"] == "failed" and failed_episode["target"]["harness"]["turnId"]
        failed_harness_server.join(timeout=2)
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
            # A missing/broken optional Gym journal must not affect ordinary completion.
            "DARKEXEC_HARNESS_EPISODE_ROOT": str(config),
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
        assert interactive_result["harnessEpisode"]["path"] is None, interactive_result
        assert interactive_result["harnessEpisode"]["error"], interactive_result
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
        status_target_thread = interactive_result["target"]["threadId"]
        target_status_socket, target_status_ready = root / "target-status.sock", threading.Event()
        target_status_server = threading.Thread(
            target=fake_app_server,
            args=(target_status_socket, target_status_ready, True, None, {
                status_target_thread: {"cwd": str(target), "turns": [
                    {"id": "baseline-turn", "status": "completed", "items": [
                        {"id": "baseline-user", "type": "userMessage", "content": [
                            {"type": "text", "text": "Original product work"},
                        ]},
                    ]},
                    {"id": "01a01cf5-9a0e-73f2-ae20-b892e9381cff", "status": "inProgress", "items": [
                        {"id": "direct-user", "type": "userMessage", "content": [
                            {"type": "text", "text": "PRIVATE DIRECT FOLLOW-UP"},
                        ]},
                        {"id": "direct-agent", "type": "agentMessage",
                         "text": "PRIVATE DIRECT RESULT"},
                    ]},
                ]},
            }),
            daemon=True,
        )
        target_status_server.start(); assert target_status_ready.wait(timeout=2)
        target_status_result = subprocess.run(
            [
                str(ROOT / "bin/darkexec"), "target-status", "--target", str(target),
                "--thread", status_target_thread, "--after-turn", "baseline-turn", "--json",
            ],
            capture_output=True, text=True,
            env={**run_env, "DARKEXEC_APP_SERVER_SOCKET": str(target_status_socket)},
            check=False,
        )
        target_status_payload = json.loads(target_status_result.stdout)
        assert target_status_result.returncode == 0, target_status_result.stderr
        assert target_status_payload["status"] == "newer_turn", target_status_payload
        assert target_status_payload["newerTurn"] == {
            "turnId": "01a01cf5-9a0e-73f2-ae20-b892e9381cff",
            "turnStatus": "inProgress", "turnKind": "product",
            "createdAt": "2026-08-20T02:17:32.942000Z",
        }, target_status_payload
        assert "PRIVATE DIRECT FOLLOW-UP" not in target_status_result.stdout
        assert "PRIVATE DIRECT RESULT" not in target_status_result.stdout
        assert "PRIVATE BASELINE STEERING" not in target_status_result.stdout
        assert "PRIVATE DIRECT STEERING" not in target_status_result.stdout
        target_status_server.join(timeout=2)
        assert not target_status_server.is_alive()
        target_input_socket, target_input_ready = root / "target-input.sock", threading.Event()
        target_input_server = threading.Thread(
            target=fake_app_server,
            args=(target_input_socket, target_input_ready, True, None, {
                status_target_thread: {"cwd": str(target), "turns": [
                    {"id": "baseline-turn", "status": "completed", "items": [
                        {"id": "baseline-user", "type": "userMessage", "content": [
                            {"type": "text", "text": "Original product work"},
                        ]},
                        {"id": "baseline-steer", "type": "userMessage", "content": [
                            {"type": "text", "text": "PRIVATE BASELINE STEERING"},
                        ]},
                    ]},
                    {"id": "direct-turn", "status": "completed", "items": [
                        {"id": "direct-user", "type": "userMessage", "content": [
                            {"type": "text", "text": "PRIVATE DIRECT FOLLOW-UP"},
                        ]},
                        {"id": "direct-steer", "type": "userMessage", "content": [
                            {"type": "text", "text": "PRIVATE DIRECT STEERING"},
                        ], "clientId": "intent-direct-steer"},
                        {"id": "direct-agent", "type": "agentMessage",
                         "text": "PRIVATE DIRECT RESULT"},
                    ]},
                ]},
            }),
            daemon=True,
        )
        target_input_server.start(); assert target_input_ready.wait(timeout=2)
        target_input_result = subprocess.run(
            [
                str(ROOT / "bin/darkexec"), "target-status", "--target", str(target),
                "--thread", status_target_thread, "--after-turn", "baseline-turn",
                "--include-input", "--include-result", "--json",
            ],
            capture_output=True, text=True,
            env={**run_env, "DARKEXEC_APP_SERVER_SOCKET": str(target_input_socket)},
            check=False,
        )
        target_input_payload = json.loads(target_input_result.stdout)
        assert target_input_result.returncode == 0, target_input_result.stderr
        assert (
            target_input_payload["newerTurn"]["inputText"] == "PRIVATE DIRECT FOLLOW-UP"
        ), target_input_payload
        assert (
            target_input_payload["newerTurn"]["resultText"] == "PRIVATE DIRECT RESULT"
        ), target_input_payload
        assert target_input_payload["newerTurns"] == [target_input_payload["newerTurn"]]
        assert [
            {
                "messageId": item["messageId"], "turnId": item["turnId"],
                "inputText": item["inputText"], "ordinal": item["ordinal"],
                **(
                    {"clientIntentId": item["clientIntentId"]}
                    if item.get("clientIntentId") else {}
                ),
            }
            for item in target_input_payload["steeringMessages"]
        ] == [
            {
                "messageId": "baseline-steer", "turnId": "baseline-turn",
                "inputText": "PRIVATE BASELINE STEERING", "ordinal": 1,
            },
            {
                "messageId": "direct-steer", "turnId": "direct-turn",
                "inputText": "PRIVATE DIRECT STEERING", "ordinal": 1,
                "clientIntentId": "intent-direct-steer",
            },
        ], target_input_payload
        target_input_server.join(timeout=2)
        assert not target_input_server.is_alive()
        identity_harness_turn = "01a02e78-238e-7383-85c9-4b60760e1681"
        session_path = (
            Path(run_env["DARKEXEC_SESSION_ROOT"])
            / f"{hashlib.sha256(status_target_thread.encode()).hexdigest()}.json"
        )
        session_path.parent.mkdir(parents=True, exist_ok=True)
        session_path.write_text(json.dumps({
            "threadId": status_target_thread,
            "status": "pending",
            "harnessTurnId": identity_harness_turn,
        }))
        identity_socket, identity_ready = root / "target-harness-identity.sock", threading.Event()
        identity_server = threading.Thread(
            target=fake_app_server,
            args=(identity_socket, identity_ready, True, None, {
                status_target_thread: {"cwd": str(target), "turns": [
                    {"id": "baseline-turn", "status": "completed", "items": [
                        {"id": "baseline-user", "type": "userMessage", "content": [
                            {"type": "text", "text": "Original product work"},
                        ]},
                    ]},
                    {"id": identity_harness_turn, "status": "inProgress", "items": [
                        {"id": "changed-harness-user", "type": "userMessage", "content": [
                            {"type": "text", "text": "A newly versioned harness prompt"},
                        ]},
                    ]},
                ]},
            }),
            daemon=True,
        )
        identity_server.start(); assert identity_ready.wait(timeout=2)
        identity_status = subprocess.run(
            [
                str(ROOT / "bin/darkexec"), "target-status", "--target", str(target),
                "--thread", status_target_thread, "--after-turn", "baseline-turn",
                "--include-input", "--json",
            ],
            capture_output=True, text=True,
            env={**run_env, "DARKEXEC_APP_SERVER_SOCKET": str(identity_socket)},
            check=False,
        )
        identity_payload = json.loads(identity_status.stdout)
        assert identity_status.returncode == 0, identity_status.stderr
        assert identity_payload["newerTurn"]["turnKind"] == "harness", identity_payload
        assert identity_payload["newerTurn"]["turnId"] == identity_harness_turn, identity_payload
        assert identity_payload["steeringMessages"] == [], identity_payload
        identity_server.join(timeout=2)
        assert not identity_server.is_alive()
        direct_image = root / "direct-image.png"
        direct_image.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        direct_manifest = root / "direct-input.json"
        direct_input = [
            {"type": "text", "text": 'Direct attachment request.\n\nAttached image: "logo.png"'},
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
        continue_options = []
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
            kwargs={"observed_options": continue_options},
            daemon=True,
        )
        continue_server.start(); assert continue_ready.wait(timeout=2)
        continued = subprocess.run(
            [
                str(ROOT / "bin/darkexec"), "continue", "--target", str(target),
                "--thread", interactive_state["target"]["threadId"], "--prompt-stdin",
                "--source-executive-turn", "--thinking-level", "low", "--speed", "standard", "--json",
            ],
            input="Dependent follow-up.", capture_output=True, text=True,
            env={**run_env, "DARKEXEC_APP_SERVER_SOCKET": str(continue_socket)}, check=False,
        )
        continued_result = json.loads(continued.stdout)
        assert continue_options == [{"effort": "low", "serviceTier": None}], continue_options
        assert continued.returncode == 0 and continued_result["status"] == "completed", continued_result
        assert continued_result["target"]["resultText"] == "TARGET_OK:Dependent follow-up.", continued_result
        assert continue_inputs[0] == follow_up_input, continue_inputs[0]
        continue_server.join(timeout=2)
        assert not continue_server.is_alive()
        # Persist choices without starting a turn, then use them from a caller
        # that predates execution flags (including a pinned App worker).
        saved_thread = interactive_state["target"]["threadId"]
        options_socket, options_ready = root / "options.sock", threading.Event()
        options_turns = []
        options_server = threading.Thread(target=fake_app_server,
            args=(options_socket, options_ready),
            kwargs={"seeded_threads": {saved_thread: {"cwd": str(target), "turns": []}}, "observed_options": options_turns}, daemon=True)
        options_server.start(); assert options_ready.wait(timeout=2)
        saved = subprocess.run([str(ROOT / "bin/darkexec"), "set-execution-options",
            "--target", str(target), "--thread", saved_thread,
            "--thinking-level", "high", "--speed", "fast", "--json"],
            capture_output=True, text=True,
            env={**run_env, "DARKEXEC_APP_SERVER_SOCKET": str(options_socket)}, check=False)
        assert saved.returncode == 0, saved.stderr or saved.stdout
        assert json.loads(saved.stdout)["status"] == "saved"
        assert options_turns == []
        options_server.join(timeout=2)
        inherited_socket, inherited_ready = root / "inherited.sock", threading.Event()
        inherited_options = []
        inherited_server = threading.Thread(target=fake_app_server,
            args=(inherited_socket, inherited_ready),
            kwargs={"seeded_threads": {saved_thread: {"cwd": str(target), "turns": []}}, "observed_options": inherited_options}, daemon=True)
        inherited_server.start(); assert inherited_ready.wait(timeout=2)
        inherited = subprocess.run([str(ROOT / "bin/darkexec"), "continue",
            "--target", str(target), "--thread", saved_thread,
            "--product", "--prompt-stdin", "--json"], input="Use saved options.",
            capture_output=True, text=True,
            env={**run_env, "DARKEXEC_APP_SERVER_SOCKET": str(inherited_socket)}, check=False)
        assert inherited.returncode == 0, inherited.stderr or inherited.stdout
        assert inherited_options == [{"effort": "high", "serviceTier": "fast"}], inherited_options
        inherited_server.join(timeout=2)
        product_socket, product_ready, product_inputs = (
            root / "product.sock", threading.Event(), []
        )
        product_server = threading.Thread(
            target=fake_app_server,
            args=(product_socket, product_ready, True, None, {
                interactive_state["target"]["threadId"]: {"cwd": str(target), "turns": []},
            }, product_inputs),
            daemon=True,
        )
        product_server.start(); assert product_ready.wait(timeout=2)
        product = subprocess.run(
            [
                str(ROOT / "bin/darkexec"), "continue", "--target", str(target),
                "--thread", interactive_state["target"]["threadId"],
                "--executive-thread", interactive_executive, "--prompt-stdin",
                "--product", "--json",
            ],
            input=incident_prompt, capture_output=True, text=True,
            env={**run_env, "DARKEXEC_APP_SERVER_SOCKET": str(product_socket)}, check=False,
        )
        product_result = json.loads(product.stdout)
        assert product.returncode == 0 and product_result["status"] == "completed", product_result
        assert product_result["turnKind"] == "product", product_result
        assert product_result["nativeTurnStarted"] is True, product_result
        assert product_inputs[0][0]["text"] == incident_prompt, product_inputs
        product_server.join(timeout=2)
        assert not product_server.is_alive()
        efficiency_socket, efficiency_ready = root / "efficiency.sock", threading.Event()
        efficiency_prompt = "Find and fix the single largest avoidable harness cost."
        efficiency_server = threading.Thread(
            target=fake_app_server,
            args=(efficiency_socket, efficiency_ready, True, None, {
                interactive_state["target"]["threadId"]: {"cwd": str(target), "turns": [{
                    "id": "standard-harness-turn", "status": "completed", "items": [
                        {"id": "standard-harness-user", "type": "userMessage", "content": [{
                            "type": "text", "text": "Let's do a harness pass following harness-ops doctrine."
                        }]},
                    ],
                }]},
            }),
            daemon=True,
        )
        efficiency_server.start(); assert efficiency_ready.wait(timeout=2)
        efficiency = subprocess.run(
            [
                str(ROOT / "bin/darkexec"), "continue", "--target", str(target),
                "--thread", interactive_state["target"]["threadId"],
                "--executive-thread", interactive_executive, "--prompt-stdin",
                "--harness-efficiency", "--json",
            ],
            input=efficiency_prompt, capture_output=True, text=True,
            env={
                **run_env,
                "DARKEXEC_APP_SERVER_SOCKET": str(efficiency_socket),
                "DARKEXEC_HARNESS_EPISODE_ROOT": str(root / "harness-episodes"),
            }, check=False,
        )
        efficiency_result = json.loads(efficiency.stdout)
        assert efficiency.returncode == 0 and efficiency_result["status"] == "completed", efficiency_result
        efficiency_episode = json.loads(Path(efficiency_result["harnessEpisode"]["path"]).read_text())
        assert efficiency_episode["harnessMode"] == "efficiency", efficiency_episode
        assert efficiency_episode["terminalIdentity"]["kind"] == "interactive_manual", efficiency_episode
        efficiency_server.join(timeout=2)
        assert not efficiency_server.is_alive()
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
            "DARKEXEC_HARNESS_PROMPT_PATH": str(root / "timer-harness-prompt.txt"),
            "DARKEXEC_HARNESS_PROJECT_PROMPT_ROOT": str(root / "timer-project-prompts"),
            "DARKEXEC_EFFICIENCY_PROMPT_PATH": str(root / "timer-efficiency-prompt.txt"),
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
        detached_closeout_thread = "00000000-0000-4000-8000-000000000012"
        detached_arm = [
            str(ROOT / "bin/darkexec"), "debounce", "--target", str(target),
            "--thread", detached_closeout_thread, "--turn", "product-detached",
            "--seconds", "1800", "--harness-mode", "standard", "--json",
        ]
        detached_armed = subprocess.run(
            detached_arm, capture_output=True, text=True, env=timer_env, check=False,
        )
        assert detached_armed.returncode == 0, detached_armed.stderr or detached_armed.stdout
        detached_request = "00000000-0000-4000-8000-000000000013"
        detached_command = [
            str(ROOT / "bin/darkexec"), "debounce-now", "--thread",
            detached_closeout_thread, "--detach", "--request-id", detached_request,
            "--note-stdin", "--json",
        ]
        detached_closeout = subprocess.run(
            detached_command, input="Keep this note once", capture_output=True, text=True,
            env=timer_env, check=False,
        )
        detached_closeout_result = json.loads(detached_closeout.stdout)
        assert detached_closeout.returncode == 0, detached_closeout.stderr or detached_closeout.stdout
        assert detached_closeout_result == {
            "status": "pending", "threadId": detached_closeout_thread,
            "generation": 1, "requestId": detached_request, "accepted": True, "harnessMode": "standard",
        }, detached_closeout_result
        duplicate_closeout = subprocess.run(
            [
                str(ROOT / "bin/darkexec"), "debounce-now", "--thread",
                detached_closeout_thread, "--detach", "--request-id",
                "00000000-0000-4000-8000-000000000014", "--note-stdin", "--json",
            ],
            input="Do not append this duplicate note", capture_output=True, text=True,
            env=timer_env, check=False,
        )
        duplicate_result = json.loads(duplicate_closeout.stdout)
        assert duplicate_closeout.returncode == 0 and duplicate_result["accepted"] is True
        assert duplicate_result["requestId"] == detached_request, duplicate_result
        detached_state_path = (
            Path(timer_env["DARKEXEC_SESSION_ROOT"])
            / f"{hashlib.sha256(detached_closeout_thread.encode()).hexdigest()}.json"
        )
        detached_state = json.loads(detached_state_path.read_text())
        assert detached_state["closeoutRequestId"] == detached_request, detached_state
        assert detached_state["harnessStatus"] == "queued", detached_state
        assert detached_state["harnessNote"] == "Keep this note once", detached_state
        assert "harnessPrompt" not in detached_state, detached_state
        manual_schedules = [
            line for line in scheduler_log.read_text().splitlines()
            if f"--thread {detached_closeout_thread}" in line and "-manual" in line
        ]
        assert len(manual_schedules) == 1, manual_schedules
        assert f"--request-id {detached_request}" in manual_schedules[0], manual_schedules[0]
        wrong_detached_fire = subprocess.run(
            [
                str(ROOT / "bin/darkexec"), "_debounce-fire", "--thread",
                detached_closeout_thread, "--generation", "1", "--request-id",
                "00000000-0000-4000-8000-000000000099",
            ],
            capture_output=True, text=True, env=timer_env, check=False,
        )
        assert wrong_detached_fire.returncode == 0
        assert json.loads(wrong_detached_fire.stdout)["status"] == "stale"
        # Explicit standard selection must survive legacy mode, paused state, duplicate
        # clicks, and a timer/manual race. Automatic callers retain the drill.
        for index, (paused, explicit_standard) in enumerate(((False, True), (True, True), (False, False)), start=40):
            selection_thread = f"00000000-0000-4000-8000-0000000000{index}"
            selection_turn = f"selection-product-{index}"
            selection_path = Path(timer_env["DARKEXEC_SESSION_ROOT"]) / f"{hashlib.sha256(selection_thread.encode()).hexdigest()}.json"
            selection_arm = subprocess.run([
                str(ROOT / "bin/darkexec"), "debounce", "--target", str(target),
                "--thread", selection_thread, "--turn", selection_turn,
                "--seconds", "1800", "--harness-mode", "read-only", "--json",
            ], capture_output=True, text=True, env=timer_env)
            assert selection_arm.returncode == 0, selection_arm.stderr
            if paused:
                pause_selection = subprocess.run([
                    str(ROOT / "bin/darkexec"), "debounce-pause", "--thread", selection_thread, "--json",
                ], capture_output=True, text=True, env=timer_env)
                assert pause_selection.returncode == 0, pause_selection.stderr
            selection_request = f"selection-request-{index}"
            selection_command = [
                str(ROOT / "bin/darkexec"), "debounce-now", "--thread", selection_thread,
                "--detach", "--request-id", selection_request, "--json",
            ] + (["--harness-standard"] if explicit_standard else [])
            for _ in range(2):
                accepted = subprocess.run(selection_command, capture_output=True, text=True, env=timer_env)
                assert accepted.returncode == 0, accepted.stderr or accepted.stdout
                acceptance = json.loads(accepted.stdout)
                assert acceptance["accepted"] is True, acceptance
                assert acceptance["harnessMode"] == ("standard" if explicit_standard else "read-only"), acceptance
            if not explicit_standard:
                before_conflict = selection_path.read_bytes()
                conflict = subprocess.run(selection_command + ["--harness-standard"], capture_output=True, text=True, env=timer_env)
                assert conflict.returncode == 1, conflict.stdout
                assert json.loads(conflict.stdout)["status"] == "rejected", conflict.stdout
                assert selection_path.read_bytes() == before_conflict
            selection_schedules = [line for line in scheduler_log.read_text().splitlines()
                                   if f"--thread {selection_thread}" in line and "-manual" in line]
            assert len(selection_schedules) == 1, selection_schedules
            # Prompt settings are resolved when execution starts, not when queued.
            project_setting = Path(timer_env["DARKEXEC_HARNESS_PROJECT_PROMPT_ROOT"]) / f"{hashlib.sha256(str(target).encode()).hexdigest()}.txt"
            project_setting.parent.mkdir(parents=True, exist_ok=True)
            expected_prompt = "CURRENT PROJECT HARNESS PROMPT"
            project_setting.write_text(expected_prompt)
            selection_inputs = []
            selection_socket, selection_ready = root / f"selection-{index}.sock", threading.Event()
            selection_server = threading.Thread(target=fake_app_server, args=(
                selection_socket, selection_ready, True, None, {
                    selection_thread: {"cwd": str(target), "turns": [{
                        "id": selection_turn, "status": "completed", "items": [{
                            "id": "selection-user", "type": "userMessage",
                            "content": [{"type": "text", "text": "Completed product work"}],
                        }],
                    }]},
                }, selection_inputs,
            ), daemon=True)
            selection_server.start(); assert selection_ready.wait(timeout=2)
            fire_command = [str(ROOT / "bin/darkexec"), "_debounce-fire", "--thread", selection_thread, "--generation", "1"]
            fire_env = {**timer_env, "DARKEXEC_APP_SERVER_SOCKET": str(selection_socket)}
            # Both paths race for the existing lock; precisely one may dispatch.
            racers = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=fire_env)
                      for command in (fire_command, fire_command + ["--request-id", selection_request])]
            race_results = []
            for racer in racers:
                stdout, stderr = racer.communicate(timeout=10)
                assert racer.returncode == 0, stderr or stdout
                race_results.append(json.loads(stdout)["status"])
            assert sorted(race_results) == ["completed", "stale"], race_results
            selection_server.join(timeout=2)
            assert not selection_server.is_alive()
            assert len(selection_inputs) == 1, selection_inputs
            assert selection_inputs[0] == [{"type": "text", "text": expected_prompt if explicit_standard else runtime["READ_ONLY_HARNESS_PROMPT"]}], selection_inputs
            selection_state = json.loads(selection_path.read_text())
            assert selection_state["harnessThreadId"] == selection_thread, selection_state
            if not explicit_standard:
                conflict = subprocess.run(selection_command + ["--harness-standard"], capture_output=True, text=True, env=timer_env)
                assert conflict.returncode == 1 and json.loads(conflict.stdout)["status"] == "rejected", conflict.stdout
            project_setting.unlink()
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
            {"id": "failed-command", "type": "commandExecution", "status": "failed", "exitCode": 2,
             "command": "private command", "aggregatedOutput": "private output"},
            {"id": "a-product-3", "type": "agentMessage", "text": "Completed after one retry."},
        ]}]
        fallback_rollout = root / "fallback-target.jsonl"
        fallback_rollout.write_text("\n".join(json.dumps(item) for item in [
            {"type": "session_meta", "payload": {"id": timer_thread}},
            {"timestamp": "2026-08-05T00:00:00Z", "type": "event_msg", "payload": {
                "type": "task_started", "turn_id": "product-3",
            }},
            {"timestamp": "2026-08-05T00:00:01Z", "type": "event_msg", "payload": {
                "type": "token_count", "info": {
                    "last_token_usage": {
                        "input_tokens": 90, "cached_input_tokens": 50, "output_tokens": 10,
                        "reasoning_output_tokens": 3, "total_tokens": 100,
                    },
                    "total_token_usage": {
                        "input_tokens": 90, "cached_input_tokens": 50, "output_tokens": 10,
                        "reasoning_output_tokens": 3, "total_tokens": 100,
                    },
                },
            }},
            {"timestamp": "2026-08-05T00:00:02Z", "type": "event_msg", "payload": {
                "type": "task_complete", "turn_id": "product-3", "duration_ms": 2000,
            }},
        ]) + "\n")
        fallback_inputs = []
        fallback_server = threading.Thread(
            target=fake_app_server,
            args=(fallback_socket, fallback_ready, True, None, {
                timer_thread: {
                    "cwd": str(target), "turns": fallback_turns,
                    "path": str(fallback_rollout), "requirePath": True,
                },
            }, fallback_inputs),
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
        assert fallback_result["harnessThreadId"] == timer_thread, fallback_result
        submitted_prompt = next(
            item["text"] for item in fallback_inputs[-1]
            if item.get("type") == "text"
        )
        fallback_state = json.loads(manual_state_path.read_text())
        assert submitted_prompt == fallback_state["harnessPrompt"], submitted_prompt
        assert "DARKEXEC BOUNDED TRAILING CLOSEOUT" not in submitted_prompt, submitted_prompt
        assert "CAPSULE" not in submitted_prompt, submitted_prompt
        assert "harnessCapsuleSha256" not in fallback_state, fallback_state
        fallback_server.join(timeout=2)
        assert not fallback_server.is_alive()
        for index, (mode, setting_path) in enumerate((
            ("standard", Path(timer_env["DARKEXEC_HARNESS_PROMPT_PATH"])),
            ("efficiency", Path(timer_env["DARKEXEC_EFFICIENCY_PROMPT_PATH"])),
        ), start=20):
            current_thread = f"00000000-0000-4000-8000-0000000000{index}"
            product_turn = f"product-current-{mode}"
            setting_path.write_text(f"OLD {mode.upper()} PROMPT\n")
            current_arm = subprocess.run(
                [
                    str(ROOT / "bin/darkexec"), "debounce", "--target", str(target),
                    "--thread", current_thread, "--turn", product_turn,
                    "--seconds", "1800", "--harness-mode", mode, "--json",
                ],
                capture_output=True, text=True, env=timer_env, check=False,
            )
            assert current_arm.returncode == 0, current_arm.stderr or current_arm.stdout
            current_state_path = (
                Path(timer_env["DARKEXEC_SESSION_ROOT"])
                / f"{hashlib.sha256(current_thread.encode()).hexdigest()}.json"
            )
            assert "harnessPrompt" not in json.loads(current_state_path.read_text())
            current_prompt = f"CURRENT {mode.upper()} PROMPT"
            setting_path.write_text(current_prompt + "\n")
            current_inputs = []
            current_socket, current_ready = root / f"current-{mode}.sock", threading.Event()
            current_server = threading.Thread(
                target=fake_app_server,
                args=(current_socket, current_ready, True, None, {
                    current_thread: {"cwd": str(target), "turns": [{
                        "id": product_turn, "status": "completed", "items": [{
                            "id": f"u-{mode}", "type": "userMessage",
                            "content": [{"type": "text", "text": "Completed product work"}],
                        }],
                    }]},
                }, current_inputs),
                daemon=True,
            )
            current_server.start(); assert current_ready.wait(timeout=2)
            current_fire = subprocess.run(
                [
                    str(ROOT / "bin/darkexec"), "_debounce-fire", "--thread",
                    current_thread, "--generation", "1",
                ],
                capture_output=True, text=True,
                env={**timer_env, "DARKEXEC_APP_SERVER_SOCKET": str(current_socket)},
                check=False,
            )
            current_result = json.loads(current_fire.stdout)
            assert current_fire.returncode == 0 and current_result["status"] == "completed", current_result
            submitted_current_prompt = next(
                item["text"] for item in current_inputs[-1] if item.get("type") == "text"
            )
            assert submitted_current_prompt == current_prompt, submitted_current_prompt
            assert f"OLD {mode.upper()} PROMPT" not in submitted_current_prompt
            current_server.join(timeout=2)
            assert not current_server.is_alive()
        Path(timer_env["DARKEXEC_HARNESS_PROMPT_PATH"]).unlink()
        Path(timer_env["DARKEXEC_EFFICIENCY_PROMPT_PATH"]).unlink()
        journal = [
            json.loads(path.read_text())
            for path in (root / "harness-episodes").glob("*.json")
        ]
        manual_episode = next(
            item for item in journal
            if item["harnessLifecycle"] == "manual"
            and item["target"]["harness"]["turnId"] == "manual-harness"
        )
        assert manual_episode["episodePurpose"] == "ordinary", manual_episode
        assert manual_episode["generation"] == 2 and manual_episode["status"] == "completed"
        abandoned_generation = next(
            item for item in journal
            if item["harnessLifecycle"] == "deferred"
            and item["generation"] == 1
            and item["target"]["threadId"] == timer_thread
        )
        assert abandoned_generation["status"] == "abandoned", abandoned_generation
        fallback_episode = next(
            item for item in journal
            if item["harnessLifecycle"] == "deferred"
            and item["target"]["turnId"] == "product-3"
        )
        assert fallback_episode["status"] == "completed", fallback_episode
        assert fallback_episode["target"]["harness"]["turnId"], fallback_episode
        assert fallback_episode["target"]["harness"]["threadId"] == timer_thread
        assert fallback_episode["target"]["harness"]["usage"]["input"] == 10, fallback_episode
        assert fallback_episode["target"]["usage"]["total"] == 100, fallback_episode
        assert fallback_episode["target"]["modelCallCount"] == 1, fallback_episode
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
        now_inputs = []
        now_server = threading.Thread(
            target=fake_app_server,
            args=(now_socket, now_ready, True, None, {
                now_thread: {"cwd": str(target), "turns": [{
                    "id": now_turn, "status": "completed", "items": [{
                        "id": "now-user", "type": "userMessage",
                        "content": [{"type": "text", "text": "Latest product work"}],
                    }],
                }]},
            }, now_inputs),
            daemon=True,
        )
        now_server.start(); assert now_ready.wait(timeout=2)
        now_result = subprocess.run(
            [str(ROOT / "bin/darkexec"), "debounce-now", "--thread", now_thread,
             "--note-stdin", "--json"],
            input="Prioritize the refresh interruption reported by the operator",
            capture_output=True, text=True,
            env={**timer_env, "DARKEXEC_APP_SERVER_SOCKET": str(now_socket)}, check=False,
        )
        now_status = json.loads(now_result.stdout)
        assert now_result.returncode == 0 and now_status["status"] == "completed", now_status
        now_server.join(timeout=2)
        assert not now_server.is_alive()
        assert now_inputs[0][0]["text"].startswith(
            "Prioritize the refresh interruption reported by the operator. Let's do a harness pass"
        ), now_inputs
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
        expected_update = subprocess.run(
            [str(ROOT / "bin/darkexec"), "update", "--expected-commit", "0" * 40, "--json"],
            capture_output=True, text=True,
            env={**env, "DARKEXEC_UPDATE_REPOSITORY": str(update_source), "DARKEXEC_UPDATE_REF": "main"},
            check=False,
        )
        assert expected_update.returncode != 0, expected_update.stdout
        assert "DarkExec update moved" in expected_update.stderr, expected_update.stderr
        identity_result = subprocess.run(
            [str(ROOT / "bin/darkexec"), "identity", "--json"],
            capture_output=True, text=True,
            env={**env, "DARKEXEC_APP_SERVER_SOCKET": str(root / "unavailable.sock")},
            check=False,
        )
        identity_payload = json.loads(identity_result.stdout)
        assert identity_result.returncode == 1, identity_result.stderr or identity_payload
        assert identity_payload["protocolVersion"] == 1, identity_payload
        assert identity_payload["appServerReady"] is False, identity_payload
        assert str(target) in identity_payload["projects"], identity_payload
    print(json.dumps({"status": "passed", "contracts": [
        "saved-project-list", "saved-target", "running-app-list-proof", "post-first-turn-app-list-proof",
        "one-executive", "one-target", "same-task-harness",
        "interactive-harness-mode-required", "interactive-target-run", "private-execution-state",
        "interactive-execution-status", "privacy-safe-target-status",
        "bounded-native-input-reconciliation", "direct-structured-input",
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
        "stale-route-candidate-filtering", "ambiguous-route-fail-closed",
        "disappearing-route-target-fail-closed",
        "manual-harness-suppression",
            "schedule-failure-immediate-closeout", "same-session-trailing-closeout",
        "per-call-closeout-usage", "cold-task-resume",
        "persisted-rollout-path-resume", "debounce-status",
        "debounce-pause-resume", "debounce-cancel", "debounce-now",
        "unbounded-turn-wait", "lost-completion-reconciliation",
        "append-only-harness-episode", "manual-deferred-episode-identity",
        "started-harness-interruption-preserved", "journal-failure-isolated",
        "install-default-verification", "self-update", "pinned-self-update", "runtime-identity",
    ]}))


if __name__ == "__main__":
    main()
