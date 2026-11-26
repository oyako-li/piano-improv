"""言葉の指示 → スタイルの変化量。

普段はキーワード辞書で読む。環境変数 PIANO_LLM に Ollama のモデル名を入れると、
辞書で読めない言い回しもローカル LLM に読ませる（GPU を使うので既定では使わない）。
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

from .style import PARAMS

RULES: list[tuple[str, dict[str, float]]] = [
    (r"静か|しずか|穏やか|落ち着", {"softness": .2, "density": -.1, "tempo": -.05}),
    (r"優しく|やさし|柔らか|やわらか", {"softness": .15, "pedal": .05}),
    (r"強く|力強|はっきり|明瞭", {"softness": -.2}),
    (r"ゆっくり|遅く|のんびり", {"tempo": -.15}),
    (r"速く|はやく|テンポ.?上", {"tempo": .15}),
    (r"明るく|明るい|晴れ|爽やか|さわやか", {"darkness": -.25}),
    (r"暗く|暗い|切な|悲し|哀|寂し|さみし|メランコリ", {"darkness": .25}),
    (r"雨", {"darkness": .15, "impressionism": .1, "flow": .15, "pedal": .1, "space": .1}),
    (r"夜|眠|寝", {"softness": .1, "tempo": -.1, "darkness": .1, "space": .05}),
    (r"朝|目覚め", {"darkness": -.2, "register": .1, "flow": .1}),
    (r"印象派|ドビュッシー|サティ|ラヴェル|浮遊|ふわ|幻想|水", {"impressionism": .25, "color": .1}),
    (r"映画|ジブリ|久石|坂本|王道|ポップ|わかりやす", {"impressionism": -.25}),
    (r"シンプル|素朴|素直|すっきり", {"color": -.2, "density": -.05}),
    (r"おしゃれ|ジャズ|複雑|色彩|カラフル|テンション", {"color": .2}),
    (r"高く|高い|高め|高音|きらきら|キラキラ", {"register": .15}),
    (r"低く|低い|低め|低音|重厚", {"register": -.15}),
    (r"間を|間が|余白|休符|ぽつぽつ", {"space": .2, "density": -.1}),
    (r"詰め|途切れ|歌う|よく動", {"space": -.15, "density": .1}),
    (r"流れ|アルペジオ|流れる|さらさら", {"flow": .2}),
    (r"止ま|静止|和音.?置", {"flow": -.2}),
    (r"細か|16分|十六分|きらめ|せせらぎ|水面", {"ripple": .25, "flow": .1}),
    (r"ゆったり.{0,4}伴奏|伴奏.{0,4}(ゆったり|シンプル|少な)", {"ripple": -.2}),
    (r"和風|日本的|ヨナ抜き|五音", {"pentatonic": .25}),
    (r"揺れ|ルバート|情緒|歌うように", {"rubato": .15}),
    (r"淡々|一定|揺らさ", {"rubato": -.2}),
    (r"3拍子|三拍子|ワルツ", {"triple": .4}),
    (r"4拍子|四拍子", {"triple": -.4}),
    (r"響き|ペダル|残響", {"pedal": .15}),
    (r"ドライ|乾", {"pedal": -.2}),
    (r"跳躍|跳ね|大胆", {"leapiness": .15}),
    (r"なめらか|滑らか", {"leapiness": -.15}),
    (r"盛り上(?!げ(ない|なく|すぎ))|ドラマ|劇的|壮大|エモ|山場|サビ|起承転結", {"drama": .2}),
    (r"平ら|起伏.{0,3}(少な|なく|無)|盛り上げ(ない|なく|すぎ)|作業用|ずっと同じ", {"drama": -.2}),
]
POSITIVE = re.compile(r"いい|良い|好き|最高|素敵|すてき|綺麗|きれい|美しい")
NEGATIVE = re.compile(r"微妙|いまいち|イマイチ|嫌|変|違う")


def parse_keywords(text: str) -> dict[str, float]:
    gain = 1.4 if re.search(r"もっと|かなり|すごく|とても", text) else \
        0.5 if re.search(r"少し|ちょっと|やや|若干", text) else 1.0
    out: dict[str, float] = {}
    for pat, d in RULES:
        if re.search(pat, text):
            for k, v in d.items():
                out[k] = out.get(k, 0) + v * gain
    return {k: max(-.25, min(.25, v)) for k, v in out.items()}   # 1 回の指示で動かしすぎない


def parse_llm(text: str, mu: dict, model: str) -> dict[str, float]:
    desc = "\n".join(f"- {k}: {d}（0={a} / 1={b}）現在 {mu[k]:.2f}" for k, (d, a, b, _) in PARAMS.items())
    prompt = (
        "ピアノ即興演奏のスタイルを、聴き手の言葉に合わせて調整します。\n"
        f"パラメータ（すべて 0..1）:\n{desc}\n\n"
        f"聴き手の言葉: 「{text}」\n\n"
        '変えるべきパラメータだけを、変化量 (-0.4..0.4) で JSON にしてください。例: {"tempo": -0.1, "darkness": 0.2}'
    )
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                       "format": "json", "stream": False, "think": False,
                       "options": {"temperature": 0.2}}).encode()
    req = urllib.request.Request(os.environ.get("OLLAMA_HOST", "http://localhost:11434") + "/api/chat",
                                 data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        content = json.loads(r.read())["message"]["content"]
    d = json.loads(content)
    return {k: max(-.4, min(.4, float(v))) for k, v in d.items() if k in PARAMS and isinstance(v, (int, float))}


def interpret(text: str, mu: dict) -> tuple[dict[str, float], float, str]:
    """(変化量, 評価, どう読んだか)。評価は 'いいね' 系だけの言葉のとき ±1。"""
    deltas = parse_keywords(text)
    how = "辞書"
    model = os.environ.get("PIANO_LLM")
    if model and not deltas:
        try:
            deltas = parse_llm(text, mu, model)
            how = f"LLM({model})"
        except Exception as e:  # noqa: BLE001
            how = f"LLM失敗: {e}"
    rating = 0.0
    if not deltas:
        if POSITIVE.search(text):
            rating = 1.0
        elif NEGATIVE.search(text):
            rating = -1.0
    return deltas, rating, how
