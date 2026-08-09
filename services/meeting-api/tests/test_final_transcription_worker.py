"""ST-6: final-transcription を start_sweeps から独立ワーカーループへ分離した検証。

観点:
  - start_sweeps のイテレーションから final-transcription が消えていること
  - 新ワーカーが周期実行・env ガード・例外耐性・停止を満たすこと
  - 新ワーカーが長時間ブロックしても他 sweep が並行して進むこと(本タスクの本質)
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meeting_api import sweeps


OTHER_SWEEPS = (
    "_sweep_stale_stopping",
    "_sweep_aggregation_retry",
    "_sweep_unfinalized_recordings",
    "_sweep_drive_export_jobs",
    "_sweep_voiceprint_retention",
)


# ---------------------------------------------------------------------------
# AT-001 / FP-005: start_sweeps から final-transcription が除去されている
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_sweeps_no_longer_runs_final_transcription(monkeypatch):
    monkeypatch.setenv("MEETING_API_SWEEPS_ENABLED", "true")
    factory = MagicMock()
    final_transcription = AsyncMock(return_value=0)

    async def stop_after_one_iteration():
        # 1周した時点でループを終了させる(container-stops は各周の最後)。
        await sweeps.stop_sweeps()
        return {}

    patches = {name: AsyncMock(return_value=0) for name in OTHER_SWEEPS}
    patches["_sweep_container_stops"] = AsyncMock(side_effect=stop_after_one_iteration)
    patches["_sweep_final_transcription_jobs"] = final_transcription

    with patch.multiple(sweeps, **patches):
        await asyncio.wait_for(sweeps.start_sweeps(factory), timeout=5.0)

    assert final_transcription.call_count == 0

    # FP-005: 他の sweep は start_sweeps に残っている
    for name in OTHER_SWEEPS:
        assert patches[name].call_count == 1, name
        patches[name].assert_awaited_with(factory)
    assert patches["_sweep_container_stops"].call_count == 1


# ---------------------------------------------------------------------------
# AT-002: 新ワーカーが db_session_factory 付きで sweep を実行する
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_worker_runs_final_transcription_sweep(monkeypatch):
    monkeypatch.setenv("MEETING_API_SWEEPS_ENABLED", "true")
    factory = MagicMock()

    async def one_shot(db_session_factory):
        await sweeps.stop_final_transcription_worker()
        return 0

    sweep_mock = AsyncMock(side_effect=one_shot)

    with patch.object(sweeps, "_sweep_final_transcription_jobs", new=sweep_mock):
        await asyncio.wait_for(
            sweeps.start_final_transcription_worker(factory), timeout=5.0
        )

    assert sweep_mock.call_count == 1
    sweep_mock.assert_awaited_with(factory)


# ---------------------------------------------------------------------------
# AT-003: MEETING_API_SWEEPS_ENABLED=false で即 return
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_worker_disabled_by_env_returns_immediately(monkeypatch):
    monkeypatch.setenv("MEETING_API_SWEEPS_ENABLED", "false")
    factory = MagicMock()
    sweep_mock = AsyncMock(return_value=0)

    with patch.object(sweeps, "_sweep_final_transcription_jobs", new=sweep_mock):
        await asyncio.wait_for(
            sweeps.start_final_transcription_worker(factory), timeout=1.0
        )

    assert sweep_mock.call_count == 0
    factory.assert_not_called()


# ---------------------------------------------------------------------------
# AT-004: イテレーション内の例外でループが止まらない
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_worker_survives_sweep_exception(monkeypatch):
    monkeypatch.setenv("MEETING_API_SWEEPS_ENABLED", "true")
    monkeypatch.setattr(sweeps, "FINAL_TRANSCRIPTION_POLL_INTERVAL", 0.01)
    factory = MagicMock()
    calls: list[object] = []

    async def flaky(db_session_factory):
        calls.append(db_session_factory)
        if len(calls) == 1:
            raise RuntimeError("boom")
        await sweeps.stop_final_transcription_worker()
        return 0

    sweep_mock = AsyncMock(side_effect=flaky)

    with patch.object(sweeps, "_sweep_final_transcription_jobs", new=sweep_mock):
        await asyncio.wait_for(
            sweeps.start_final_transcription_worker(factory), timeout=5.0
        )

    assert sweep_mock.call_count == 2


# ---------------------------------------------------------------------------
# AT-005: 並行性 — ワーカーが長時間 await 中でも他 sweep が進む
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_other_sweeps_progress_while_final_transcription_blocks(monkeypatch):
    monkeypatch.setenv("MEETING_API_SWEEPS_ENABLED", "true")
    factory = MagicMock()
    release = asyncio.Event()
    stale_called = asyncio.Event()

    async def blocking_final_transcription(db_session_factory):
        # 長時間 STT を待つ実挙動の代役。テスト終了まで解放されない。
        await release.wait()
        return 0

    async def stale_stopping(db_session_factory):
        stale_called.set()
        return 0

    async def stop_after_one_iteration():
        await sweeps.stop_sweeps()
        return {}

    patches = {name: AsyncMock(return_value=0) for name in OTHER_SWEEPS}
    patches["_sweep_stale_stopping"] = AsyncMock(side_effect=stale_stopping)
    patches["_sweep_container_stops"] = AsyncMock(side_effect=stop_after_one_iteration)
    patches["_sweep_final_transcription_jobs"] = AsyncMock(
        side_effect=blocking_final_transcription
    )

    with patch.multiple(sweeps, **patches):
        worker_task = asyncio.create_task(
            sweeps.start_final_transcription_worker(factory)
        )
        # ワーカーを先にブロック状態へ入れる
        await asyncio.sleep(0)
        sweeps_task = asyncio.create_task(sweeps.start_sweeps(factory))
        try:
            await asyncio.wait_for(stale_called.wait(), timeout=5.0)
            await asyncio.wait_for(sweeps_task, timeout=5.0)
            assert not worker_task.done(), "worker はまだ final-transcription 待機中のはず"
        finally:
            release.set()
            await sweeps.stop_final_transcription_worker()
            worker_task.cancel()
            await asyncio.gather(worker_task, sweeps_task, return_exceptions=True)

    assert patches["_sweep_final_transcription_jobs"].call_count == 1


# ---------------------------------------------------------------------------
# AT-007: stop_final_transcription_worker でループが終了する
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stop_final_transcription_worker_ends_loop(monkeypatch):
    monkeypatch.setenv("MEETING_API_SWEEPS_ENABLED", "true")
    monkeypatch.setattr(sweeps, "FINAL_TRANSCRIPTION_POLL_INTERVAL", 0.01)
    factory = MagicMock()
    entered = asyncio.Event()

    async def noop(db_session_factory):
        entered.set()
        return 0

    with patch.object(
        sweeps, "_sweep_final_transcription_jobs", new=AsyncMock(side_effect=noop)
    ):
        task = asyncio.create_task(sweeps.start_final_transcription_worker(factory))
        await asyncio.wait_for(entered.wait(), timeout=5.0)
        await sweeps.stop_final_transcription_worker()
        await asyncio.wait_for(task, timeout=5.0)

    assert task.done() and task.exception() is None


# ---------------------------------------------------------------------------
# NFT-002: 停止イベント・カウンタが start_sweeps 側と独立
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_worker_stop_event_is_independent_from_sweeps(monkeypatch):
    monkeypatch.setenv("MEETING_API_SWEEPS_ENABLED", "true")
    factory = MagicMock()

    async def one_shot(db_session_factory):
        await sweeps.stop_final_transcription_worker()
        return 0

    sweeps._stop_event = asyncio.Event()
    sweeps_event = sweeps._stop_event

    with patch.object(
        sweeps, "_sweep_final_transcription_jobs", new=AsyncMock(side_effect=one_shot)
    ):
        await asyncio.wait_for(
            sweeps.start_final_transcription_worker(factory), timeout=5.0
        )

    # ワーカー停止が start_sweeps 側の stop イベントへ波及していない
    assert sweeps._stop_event is sweeps_event
    assert not sweeps_event.is_set()
    assert sweeps._ft_stop_event is not sweeps_event
    assert sweeps._ft_stop_event.is_set()
