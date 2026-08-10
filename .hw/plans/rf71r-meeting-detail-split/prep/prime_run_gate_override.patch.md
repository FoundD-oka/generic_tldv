# prime_run.py 停止条件上書きパッチ(コーディネータが適用して commit する)

`.hw/prime_run.py` の `run_prime()` 内、現行:

```python
    context = load_run_context(root, task_id)
    gate = f"bash .hw/hooks/pr-ready-gate.sh {task_id}"
    prompt = build_prompt(task_id, context, gate)
```

を次に置き換える:

```python
    context = load_run_context(root, task_id)
    default_gate = f"bash .hw/hooks/pr-ready-gate.sh {task_id}"
    # M/L の prime タスクでは、pr-ready-gate が要求する Fable READY を実行役が
    # 作れない(レビューは read-only の別工程で、READY は差分hashに束縛される)。
    # 停止条件だけを HW_PRIME_GATE で差し替え可能にする。既定は現行のまま
    # fail-closed。上書きした値は prime-run.json の gate_command に記録され、
    # 最終権威は変わらず pr-ready-gate と CI(PR前に必ず全ゲートを通す)。
    gate = os.environ.get("HW_PRIME_GATE", "").strip() or default_gate
    if gate != default_gate:
        print(f"[hw][prime] 停止条件を上書き: {gate}")
    prompt = build_prompt(task_id, context, gate)
```

適用後の確認: `python3 -c "import ast;ast.parse(open('.hw/prime_run.py').read())"` が無言で通ること。
