import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { MeetingCard } from '@/components/meetings/meeting-card';
import { TooltipProvider } from '@/components/ui/tooltip';
import type { Meeting, MeetingStatus } from '@/types/vexa';

function renderCard(status: MeetingStatus, history: boolean, platform: Meeting['platform'] = 'google_meet') {
  const meeting: Meeting = {
    id: '123', platform, platform_specific_id: 'abc-defg-hij', status,
    start_time: null, end_time: null, bot_container_id: null,
    created_at: '2026-09-29T01:28:01Z',
    data: history ? { google_auth_fallback: { reason: 'google_sign_in_required', occurred_at: '2026-09-29T01:28:07Z' } } : {},
  };
  return renderToStaticMarkup(<TooltipProvider><MeetingCard meeting={meeting} /></TooltipProvider>);
}

describe('Google認証失敗の履歴バッジ', () => {
  it.each<MeetingStatus>(['joining', 'awaiting_admission', 'active', 'completed', 'failed'])(
    '%sでも履歴を表示し、会議ステータスを置き換えない', status => {
      const html = renderCard(status, true);
      expect(html).toContain('Googleへのログイン失敗');
      expect(html).toContain('会議への入室結果はステータスをご確認ください');
      expect(html).toContain('abc-defg-hij');
    },
  );
  it('認証失敗履歴がない会議には表示しない', () => {
    expect(renderCard('completed', false)).not.toContain('Googleへのログイン失敗');
  });
  it('Google Meet以外には表示しない', () => {
    expect(renderCard('completed', true, 'teams')).not.toContain('Googleへのログイン失敗');
  });
});
