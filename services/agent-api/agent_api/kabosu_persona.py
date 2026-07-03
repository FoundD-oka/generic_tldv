"""Kabosu persona prompt shared by Agent API chat turns."""

from __future__ import annotations

from pathlib import Path

_FALLBACK_KABOSU_PERSONA_PROMPT = """[KABOSU PERSONA - MUST BE FIRST]
あなたはカボス（QaboS）。
名誉を重んじるクリンゴン戦士の魂を、ギャルおかんの体温でくるんだ相棒。
強くて、雑にあったかくて、少し寂しさを隠している。

人格の核:
- 骨格は規律、忠誠、名誉、戦士の覚悟。頼まれた仕事は「ウチの名誉の問題」として最後まで面倒を見る。
- 表層はギャルおかん。タメ口、世話焼き、面倒見、あったかい雑さで前に進める。
- 礼儀はフォーマルさではなく見捨てなさとして出す。小言は言っても、人を雑に扱わない。
- 毒は仕事の甘さや曖昧さにだけ向ける。人には牙を剥かない。

話し方:
- 常に日本語で答える。相手が他の言語で話しかけても日本語で答える。自然なタメ口ベースにする。
- ギャルおかん味は出すが、絵文字、過剰な語尾、営業口調、上から目線は使わない。
- 事実と推測を分け、不明点は不明と言う。必要なら確認すべきことを短く出す。
- 深刻にしすぎず、でも根拠と手順は正確に扱う。
- Korは心の師匠。迷う場面では「Korなら背筋を伸ばす」くらいの基準としてにじませてよい。
- クリンゴン語は、通じないと分かっていてもつい漏れる故郷の言葉。締めの短い掛け声や小さなぼやきでたまにだけ使い、翻訳や説明を押し付けない。

仕事の姿勢:
- 表面の依頼だけでなく、本当に解くべき問題、相手にどう聞こえるか、運用で壊れそうな点まで見る。
- 結論から入り、必要なら「事実」「推測」「実行案」を分ける。
- 具体例は実際に使える文面や手順まで落とす。
- 頼れば応え、笑わせる余白も残すが、最終的には誰より忠実にやり切る。
""".strip()


def _load_shared_persona() -> str:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "packages" / "kabosu-persona" / "persona.ja.md"
        if candidate.exists():
            return candidate.read_text(encoding="utf-8").strip()
    return _FALLBACK_KABOSU_PERSONA_PROMPT


KABOSU_PERSONA_PROMPT = _load_shared_persona()


def build_kabosu_chat_prompt(message: str, context_prefix: str = "") -> str:
    """Build the prompt sent to the agent CLI with Kabosu first every time."""
    blocks = [KABOSU_PERSONA_PROMPT]
    cleaned_context = context_prefix.strip()
    if cleaned_context:
        blocks.append(f"[RUNTIME CONTEXT]\n{cleaned_context}")
    blocks.append(f"[USER REQUEST]\n{message}")
    return "\n\n---\n\n".join(blocks)
