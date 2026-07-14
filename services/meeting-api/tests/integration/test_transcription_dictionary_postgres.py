import asyncio
import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func
from sqlalchemy.future import select

from meeting_api.database import async_session_local
from meeting_api.models import TranscriptionDictionaryTerm
from meeting_api.transcription_dictionary import DictionaryTermCreate, create_dictionary_term


@pytest.mark.asyncio
async def test_real_postgres_advisory_lock_enforces_200_term_cap():
    async with async_session_local() as db:
        await db.execute(delete(TranscriptionDictionaryTerm).where(TranscriptionDictionaryTerm.user_id == 777))
        db.add_all([
            TranscriptionDictionaryTerm(
                user_id=777, term=f"term-{i}", normalized_term=f"term-{i}", enabled=True,
            )
            for i in range(199)
        ])
        await db.commit()

    async def create(value: str):
        async with async_session_local() as db:
            try:
                await create_dictionary_term(
                    DictionaryTermCreate(term=value),
                    auth_data=(None, SimpleNamespace(id=777)),
                    db=db,
                )
                return 201
            except HTTPException as exc:
                return exc.status_code

    statuses = await asyncio.gather(create("term-a"), create("term-b"))
    assert sorted(statuses) == [201, 409]
    async with async_session_local() as db:
        count = (await db.execute(select(func.count(TranscriptionDictionaryTerm.id)).where(
            TranscriptionDictionaryTerm.user_id == 777
        ))).scalar_one()
        assert count == 200
