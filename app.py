import streamlit as st
import json
import os
from datetime import datetime, timedelta
import anthropic
import requests

# ページ設定
st.set_page_config(
    page_title="家族の献立アプリ",
    page_icon="🍽️",
    layout="wide"
)

# ファイルパス
SETTINGS_FILE = "settings.json"
HISTORY_FILE = "history.json"
STOCK_FILE = "stock.json"
FAVORITES_FILE = "favorites.json"
CARRYOVER_FILE = "carryover.json"

# ===== データ管理関数 =====

def load_json(filepath, default):
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    return default

def save_json(filepath, data):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_settings():   return load_json(SETTINGS_FILE, None)
def save_settings(d):  save_json(SETTINGS_FILE, d)
def load_history():    return load_json(HISTORY_FILE, [])
def save_history(d):   save_json(HISTORY_FILE, d)
def load_stock():      return load_json(STOCK_FILE, {"ingredients": [], "retort": []})
def save_stock(d):     save_json(STOCK_FILE, d)
def load_favorites():  return load_json(FAVORITES_FILE, [])
def save_favorites(d): save_json(FAVORITES_FILE, d)
def load_carryover():  return load_json(CARRYOVER_FILE, [])   # [{"dish": str}, ...]
def save_carryover(d): save_json(CARRYOVER_FILE, d)

# ===== Notion連携 =====

def fetch_notion_menu(notion_token, database_id):
    url = f"https://api.notion.com/v1/databases/{database_id}/query"
    headers = {
        "Authorization": f"Bearer {notion_token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }
    try:
        res = requests.post(url, headers=headers, json={})
        if res.status_code == 200:
            results = res.json().get("results", [])
            dishes = []
            for page in results:
                props = page.get("properties", {})
                for key in ["名前", "Name", "料理名", "dish"]:
                    if key in props:
                        title_list = props[key].get("title", [])
                        if title_list:
                            dishes.append(title_list[0]["text"]["content"])
                        break
            return dishes
        else:
            return None
    except Exception:
        return None


# ===== NotionのmdファイルをパースしてNotionの料理名一覧を取得 =====

WEEKDAY_CHARS = ["月", "火", "水", "木", "金", "土", "日"]

# 献立として扱わないキーワード（部分一致で判定）
SKIP_WORDS_EXACT = {
    # 食事以外・非料理
    "弁当", "お外", "外食", "外", "なし", "残り物", "残り", "適当", "ひとり",
    "もっちゃんだけ", "一人", "ひとりごはん",
    # 飲み物・菓子類
    "ビール", "シャンパン", "炭酸水", "カフェオレ", "お菓子", "ケーキ",
    # 雑貨・買い物
    "ゴミ袋", "割り箸", "トイレットペーパー", "ナプキン", "クレンジング",
    # 副菜・付け合わせ（単体では献立でない）
    "カット野菜", "サラダ", "味噌汁", "バゲット",
    # 食材名（料理名でないもの）
    "もやし", "ほうれん草",
}

# 部分一致で除外するキーワード
SKIP_WORDS_PARTIAL = ["残り", "適当", "ひとり", "一人", "弁当", "外"]

def is_skip(word: str) -> bool:
    """除外すべき単語かどうか判定"""
    w = word.strip()
    if not w:
        return True
    if w in SKIP_WORDS_EXACT:
        return True
    for partial in SKIP_WORDS_PARTIAL:
        if partial in w:
            return True
    return False

def extract_dishes_from_cell(cell_text: str) -> list:
    """セルのテキストから料理名リストを抽出（カンマ・読点で分割しスキップ判定）"""
    if not cell_text.strip():
        return []
    items = [d.strip() for d in cell_text.replace("、", ",").split(",") if d.strip()]
    return [d for d in items if not is_skip(d)]

def parse_notion_md_table(content: str) -> list:
    """
    テーブル形式のmdから献立を抽出する。
    列数・昼夜を問わず、曜日行のすべてのセルから料理名を取り出す。
    例:
      | 月 | サバ、味噌汁 |              → サバ
      | 月 | 弁当 | 麻婆茄子、サラダ |   → 麻婆茄子
      | 月 | ネギトロ | 鍋 |             → ネギトロ、鍋（両方）
    """
    rows = []
    for line in content.splitlines():
        line = line.strip()
        if not line.startswith("|") or "---" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells:
            continue
        day = cells[0].strip()
        if day not in WEEKDAY_CHARS:
            continue

        # 曜日以降のすべてのセルから料理を収集
        all_dishes = []
        for cell in cells[1:]:
            all_dishes.extend(extract_dishes_from_cell(cell))

        if not all_dishes:
            continue

        # 全セルの元テキストを結合して表示用に使う
        raw_text = "、".join(c for c in cells[1:] if c.strip())

        rows.append({
            "day": day,
            "dinner": all_dishes[0],       # メイン料理名（最初に見つかったもの）
            "dinner_all": all_dishes,      # すべての料理
            "dinner_raw": raw_text,        # 元テキスト（表示用）
        })
    return rows

def parse_notion_md_legacy(content: str) -> list:
    """旧形式（全角スペース区切り）のmdから料理名を抽出"""
    rows = []
    for line in content.splitlines():
        line = line.strip().lstrip("#").strip()
        if not line:
            continue
        parts = line.replace("\u3000", " ").split()
        if len(parts) >= 2 and parts[0] in WEEKDAY_CHARS:
            dishes = extract_dishes_from_cell(" ".join(parts[1:]))
            if dishes:
                rows.append({
                    "day": parts[0],
                    "dinner": dishes[0],
                    "dinner_all": dishes,
                    "dinner_raw": " ".join(parts[1:]),
                })
    return rows

def parse_notion_md(content: str) -> list:
    """テーブル形式・旧形式の両方に対応。テーブルがあればそちらを優先"""
    rows = parse_notion_md_table(content)
    if rows:
        return rows
    return parse_notion_md_legacy(content)

# notion_history.json:
# [{"dish": str, "count": int, "dates": [str], "recipe": str}, ...]
# count: 登場回数（多いほど人気）、dates: 登場した日付リスト

def load_notion_history() -> list:
    return load_json("notion_history.json", [])

def save_notion_history(records: list):
    save_json("notion_history.json", records)

def get_notion_dish_names() -> list:
    """献立生成プロンプト用に料理名だけのリストを返す"""
    return [r["dish"] for r in load_notion_history()]

def merge_into_notion_history(existing: list, new_records: list) -> tuple:
    """
    新しいレコードを既存データにマージする。
    同名料理はcountを加算・datesをマージ。
    Returns: (merged_list, added_count, updated_count)
    """
    by_dish = {r["dish"]: r for r in existing}
    added, updated = 0, 0
    for rec in new_records:
        dish = rec["dish"]
        date = rec.get("date", "")
        if dish in by_dish:
            by_dish[dish]["count"] = by_dish[dish].get("count", 1) + rec.get("count", 1)
            dates = by_dish[dish].get("dates", [])
            if date and date not in dates:
                dates.append(date)
            by_dish[dish]["dates"] = sorted(dates)
            if rec.get("recipe"):
                by_dish[dish]["recipe"] = rec["recipe"]
            updated += 1
        else:
            by_dish[dish] = {
                "dish": dish,
                "count": rec.get("count", 1),
                "dates": [date] if date else [],
                "recipe": rec.get("recipe", ""),
            }
            added += 1
    # count降順でソート（人気順）
    merged = sorted(by_dish.values(), key=lambda r: -r.get("count", 1))
    return merged, added, updated

# ===== Claude API呼び出し =====

def generate_menu(settings, stock, favorites, notion_dishes, selected_notion_dishes=None, fixed_days=None, carryover_dishes=None):
    # notion_historyもマージ
    notion_dishes = list(set((notion_dishes or []) + get_notion_dish_names()))
    client = anthropic.Anthropic(api_key=st.session_state.api_key)

    cooking_time_text = "\n".join([
        f"  - {day}: {time}" for day, time in settings["cooking_times"].items()
    ])

    favorite_hint = ""
    if favorites:
        import random
        picks = random.sample(favorites, min(2, len(favorites)))
        favorite_hint = "・".join([f["dish"] for f in picks])

    selected_hint = ""
    if selected_notion_dishes:
        selected_hint = "・".join(selected_notion_dishes)

    notion_text = ""
    if notion_dishes:
        notion_text = "、".join(notion_dishes[:30])

    fixed_text = ""
    if fixed_days:
        fixed_text = "\n".join([f"  - {d['day']}: {d['dish']}（この曜日はこの料理で固定）" for d in fixed_days])

    carryover_text = ""
    if carryover_dishes:
        carryover_text = "・".join(carryover_dishes)

    prompt = f"""あなたは家族の食事をサポートするプロの栄養士です。
以下の条件で、月曜日から日曜日までの夕食7日分の献立を考えてください。

【家族情報】
- 人数: {settings['family_size']}人
- 好き嫌い・アレルギー: {settings['allergies'] or "特になし"}

【調理時間の目安】
{cooking_time_text}

【今週の手持ち食材】
{", ".join(stock["ingredients"]) if stock["ingredients"] else "特になし"}

【レトルト・保存食】
{", ".join(stock["retort"]) if stock["retort"] else "特になし"}

【固定献立（この曜日と料理は変更しないでください）】
{fixed_text if fixed_text else "特になし"}

【前週から持ち越す献立（必ず今週の献立に含めてください）】
{carryover_text if carryover_text else "特になし"}

【ユーザーが今週食べたい献立（必ず献立に含めてください）】
{selected_hint if selected_hint else "特になし"}

【お気に入り献立（2〜3週に1回程度、献立に組み込んでください）】
{favorite_hint if favorite_hint else "特になし"}

【過去の献立履歴（なるべく重複を避けてください）】
{notion_text if notion_text else "特になし"}

【条件】
- 栄養バランスを考慮し、主食・主菜・副菜が揃うように提案してください
- 手持ち食材やレトルトをなるべく活用してください
- 同じ料理が連続しないようにしてください
- 季節感や家族が喜びそうな献立にしてください
- お気に入り献立が指定されている場合は、7日のうち1〜2日に組み込んでください

【出力形式】
以下のJSON形式のみで出力してください。前後に説明文は不要です。

{{
  "menu": [
    {{
      "day": "月曜日",
      "dish": "料理名",
      "ingredients": ["食材1", "食材2", "食材3"],
      "recipe": "簡単な作り方を3〜5ステップで説明",
      "uses_stock": true,
      "is_favorite": false
    }}
  ],
  "shopping_list": {{
    "野菜・果物": ["食材1", "食材2"],
    "肉・魚": ["食材1", "食材2"],
    "乳製品・卵": ["食材1"],
    "調味料・その他": ["食材1", "食材2"]
  }}
}}"""

    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw)

def generate_single_day(settings, stock, day_name, other_dishes):
    """指定した1曜日の献立だけを再生成する"""
    client = anthropic.Anthropic(api_key=st.session_state.api_key)
    cooking_time = settings.get("cooking_times", {}).get(day_name, "30分以内")
    other_text = "、".join(other_dishes) if other_dishes else "特になし"

    prompt = f"""あなたは家族の食事をサポートするプロの栄養士です。
{day_name}の夕食1日分の献立を1つ考えてください。

【家族情報】
- 人数: {settings['family_size']}人
- 好き嫌い・アレルギー: {settings['allergies'] or "特になし"}

【{day_name}の調理時間の目安】
{cooking_time}

【今週の手持ち食材】
{", ".join(stock["ingredients"]) if stock["ingredients"] else "特になし"}

【今週の他の献立（重複しないようにしてください）】
{other_text}

【出力形式】
以下のJSON形式のみで出力してください。

{{
  "day": "{day_name}",
  "dish": "料理名",
  "ingredients": ["食材1", "食材2"],
  "recipe": "簡単な作り方を3〜5ステップで説明",
  "uses_stock": false,
  "is_favorite": false
}}"""

    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = response.content[0].text.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw)

# ===== 画面: 食材ストック管理 =====

def show_stock_page():
    st.title("🧊 食材ストック管理")
    st.caption("ここで管理した食材が献立生成に自動で使われます。")

    stock = load_stock()

    # --- 冷蔵庫の食材 ---
    st.subheader("🥦 冷蔵庫の食材")

    if stock["ingredients"]:
        st.markdown("**現在の食材一覧**（使い切ったものはチェックして削除）")
        to_delete_ing = []
        cols = st.columns(3)
        for i, item in enumerate(stock["ingredients"]):
            with cols[i % 3]:
                if st.checkbox(item, key=f"ing_{i}"):
                    to_delete_ing.append(item)

        if to_delete_ing:
            if st.button(f"🗑️ チェックした食材を削除（{len(to_delete_ing)}件）", key="del_ing"):
                stock["ingredients"] = [x for x in stock["ingredients"] if x not in to_delete_ing]
                save_stock(stock)
                st.success("削除しました")
                st.rerun()
    else:
        st.info("食材が登録されていません")

    st.markdown("---")
    st.markdown("**食材を追加**")
    st.markdown("入力例：鶏もも肉、豚バラ、玉ねぎ、にんじん、じゃがいも、キャベツ、豆腐、卵")
    new_ing = st.text_input("追加する食材（カンマ区切りや読点で複数入力可）", key="new_ing_input")
    if st.button("➕ 食材を追加", key="add_ing"):
        if new_ing:
            items = [x.strip() for x in new_ing.replace("、", ",").split(",") if x.strip()]
            stock["ingredients"] = list(set(stock["ingredients"] + items))
            save_stock(stock)
            st.success(f"{len(items)}件追加しました")
            st.rerun()

    st.divider()

    # --- レトルト・保存食 ---
    st.subheader("🍛 レトルト・保存食")

    if stock["retort"]:
        st.markdown("**現在のストック一覧**（使い切ったものはチェックして削除）")
        to_delete_ret = []
        cols = st.columns(3)
        for i, item in enumerate(stock["retort"]):
            with cols[i % 3]:
                if st.checkbox(item, key=f"ret_{i}"):
                    to_delete_ret.append(item)

        if to_delete_ret:
            if st.button(f"🗑️ チェックしたものを削除（{len(to_delete_ret)}件）", key="del_ret"):
                stock["retort"] = [x for x in stock["retort"] if x not in to_delete_ret]
                save_stock(stock)
                st.success("削除しました")
                st.rerun()
    else:
        st.info("レトルト・保存食が登録されていません")

    st.markdown("---")
    st.markdown("**レトルト・保存食を追加**")
    st.markdown("入力例：カレーの素、麻婆豆腐の素、パスタソース（トマト）、鍋の素、ミートソース缶")
    new_ret = st.text_input("追加するレトルト・保存食（カンマ区切りや読点で複数入力可）", key="new_ret_input")
    if st.button("➕ レトルト・保存食を追加", key="add_ret"):
        if new_ret:
            items = [x.strip() for x in new_ret.replace("、", ",").split(",") if x.strip()]
            stock["retort"] = list(set(stock["retort"] + items))
            save_stock(stock)
            st.success(f"{len(items)}件追加しました")
            st.rerun()

# ===== 画面: お気に入り管理 =====

def show_favorites_page():
    st.title("⭐ お気に入り献立")
    st.caption("登録した献立は2〜3週に1回程度、献立案として提案されます。")

    favorites = load_favorites()

    if favorites:
        st.subheader(f"登録済み: {len(favorites)}件")
        for i, fav in enumerate(favorites):
            col1, col2 = st.columns([5, 1])
            with col1:
                st.markdown(f"**{fav['dish']}**")
                if fav.get("memo"):
                    st.caption(fav["memo"])
            with col2:
                if st.button("削除", key=f"delfav_{i}"):
                    favorites.pop(i)
                    save_favorites(favorites)
                    st.rerun()
    else:
        st.info("まだお気に入りが登録されていません。献立生成後に⭐ボタンで登録できます。")

    st.divider()
    st.subheader("手動で追加")
    col1, col2 = st.columns([3, 2])
    with col1:
        dish_name = st.text_input("料理名")
    with col2:
        memo = st.text_input("メモ（任意）", placeholder="家族みんなが好き など")
    if st.button("⭐ お気に入りに追加"):
        if dish_name:
            favorites.append({"dish": dish_name, "memo": memo})
            save_favorites(favorites)
            st.success(f"「{dish_name}」を追加しました")
            st.rerun()

# ===== 画面: 献立生成 =====

def show_generate_page():
    st.title("🍽️ 今週の献立を作成")

    settings = load_settings()
    if not settings:
        st.warning("まず設定を完了してください。")
        return

    col1, col2 = st.columns(2)
    with col1:
        st.info(f"👨‍👩‍👧‍👦 家族: {settings['family_size']}人")
    with col2:
        if settings.get("allergies"):
            st.warning(f"⚠️ {settings['allergies']}")

    stock = load_stock()
    favorites = load_favorites()

    # 食材ストック確認
    st.subheader("🧊 現在の食材ストック")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**冷蔵庫の食材**")
        if stock["ingredients"]:
            st.markdown("、".join(stock["ingredients"]))
        else:
            st.caption("未登録（食材ストック画面で追加してください）")
    with col2:
        st.markdown("**レトルト・保存食**")
        if stock["retort"]:
            st.markdown("、".join(stock["retort"]))
        else:
            st.caption("未登録（食材ストック画面で追加してください）")

    if favorites:
        st.caption(f"⭐ お気に入り {len(favorites)}件 を参考に献立を提案します")

    # Notion連携
    notion_dishes = []
    if settings.get("notion_token") and settings.get("notion_database_id"):
        with st.spinner("Notionから献立履歴を取得中..."):
            notion_dishes = fetch_notion_menu(
                settings["notion_token"],
                settings["notion_database_id"]
            ) or []
        if notion_dishes:
            st.caption(f"📓 Notionから{len(notion_dishes)}件の献立履歴を参照します")
        else:
            st.caption("⚠️ Notionからデータを取得できませんでした")

    # ===== Notion履歴から人気献立をランダム提示 =====
    notion_history = load_notion_history()
    selected_notion_dishes = []
    if notion_history:
        import random

        def pick_random_dishes(history):
            weights = [r.get("count", 1) for r in history]
            n_pick = min(5, len(history))
            candidates = random.choices(history, weights=weights, k=n_pick * 3)
            seen = set()
            result = []
            for p in candidates:
                if p["dish"] not in seen:
                    seen.add(p["dish"])
                    result.append(p)
                if len(result) >= n_pick:
                    break
            return result

        # 初回のみピックアップを生成。ボタンが押されたときだけ更新
        if "notion_picks" not in st.session_state:
            st.session_state.notion_picks = pick_random_dishes(notion_history)

        st.divider()
        st.subheader("🌟 今週の献立に含めますか？")
        st.caption("過去の献立から人気のものをピックアップしました。チェックした料理を今週の献立に組み込みます。")

        col_refresh, _ = st.columns([2, 5])
        with col_refresh:
            if st.button("🔀 別の候補を見る", key="refresh_picks"):
                st.session_state.notion_picks = pick_random_dishes(notion_history)
                for i in range(5):
                    st.session_state.pop(f"notion_pick_{i}", None)
                st.rerun()

        unique_picks = st.session_state.notion_picks
        cols = st.columns(min(len(unique_picks), 5))
        for i, pick in enumerate(unique_picks):
            with cols[i % len(cols)]:
                count = pick.get("count", 1)
                dates = pick.get("dates", [])
                checked = st.checkbox(
                    f"**{pick['dish']}**",
                    key=f"notion_pick_{i}"
                )
                st.caption(f"🌟 {count}回登場")
                if dates:
                    st.caption(f"最終: {dates[-1]}")
                if checked:
                    selected_notion_dishes.append(pick["dish"])

        if selected_notion_dishes:
            st.success(f"✅ 選択中: {' / '.join(selected_notion_dishes)}")

    # ===== 前週から持ち越す献立 =====
    carryover_list = load_carryover()
    carryover_dishes = []
    if carryover_list:
        st.divider()
        st.subheader("⏭️ 持ち越し献立")
        st.caption("チェックしたものを今週の献立に組み込みます。「持ち越し献立」メニューで追加・削除できます。")
        cols = st.columns(min(4, len(carryover_list)))
        for i, item in enumerate(carryover_list):
            with cols[i % 4]:
                checked = st.checkbox(f"**{item['dish']}**", key=f"carryover_{i}")
                if checked:
                    carryover_dishes.append(item["dish"])
        if carryover_dishes:
            st.success(f"✅ 持ち越し: {' / '.join(carryover_dishes)}")

    st.divider()

    if st.button("✨ 献立を生成する", use_container_width=True, type="primary"):
        if not st.session_state.get("api_key"):
            st.error("設定画面でAPIキーを入力してください")
            return

        with st.spinner("AIが献立を考えています...少々お待ちください🤔"):
            try:
                result = generate_menu(settings, stock, favorites, notion_dishes, selected_notion_dishes, carryover_dishes=carryover_dishes)
                st.session_state.current_menu = result
                st.success("献立が完成しました！")
                st.rerun()
            except Exception as e:
                st.error(f"エラーが発生しました: {e}")

    if "current_menu" in st.session_state:
        show_menu_result(st.session_state.current_menu)

# ===== 献立表示 =====

def show_menu_result(result, readonly=False):
    st.divider()
    st.subheader("📅 今週の夕食献立")

    favorites = load_favorites()
    fav_dishes = [f["dish"] for f in favorites]
    menu = result["menu"]

    # セッション初期化
    if "confirm_regen_idx" not in st.session_state:
        st.session_state.confirm_regen_idx = None

    days_per_row = 4
    for row_start in range(0, len(menu), days_per_row):
        cols = st.columns(min(days_per_row, len(menu) - row_start))
        for i, col in enumerate(cols):
            idx = row_start + i
            day_menu = menu[idx]
            is_confirming = st.session_state.confirm_regen_idx == idx

            with col:
                badges = ""
                if day_menu.get("uses_stock"):
                    badges += "🧊"
                if day_menu.get("is_favorite") or day_menu["dish"] in fav_dishes:
                    badges += "⭐"
                st.markdown(f"**{day_menu['day']}** {badges}")
                st.markdown(f"### {day_menu['dish']}")

                with st.expander("食材・レシピを見る"):
                    st.markdown("**使う食材**")
                    for ing in day_menu["ingredients"]:
                        st.markdown(f"- {ing}")
                    st.markdown("**作り方**")
                    st.markdown(day_menu["recipe"])

                if not readonly:
                    # 変更ボタン → 確認 → 再生成
                    if is_confirming:
                        st.warning(f"**{day_menu['day']}**の献立を再生成しますか？")
                        c1, c2 = st.columns(2)
                        with c1:
                            if st.button("✅ 再生成", key=f"regen_ok_{idx}"):
                                settings = load_settings()
                                stock = load_stock()
                                other_dishes = [m["dish"] for j, m in enumerate(menu) if j != idx]
                                with st.spinner(f"{day_menu['day']}の献立を考えています..."):
                                    try:
                                        new_day = generate_single_day(
                                            settings, stock, day_menu["day"], other_dishes
                                        )
                                        st.session_state.current_menu["menu"][idx] = new_day
                                        st.session_state.confirm_regen_idx = None
                                        st.rerun()
                                    except Exception as e:
                                        st.error(f"エラー: {e}")
                        with c2:
                            if st.button("❌ キャンセル", key=f"regen_cancel_{idx}"):
                                st.session_state.confirm_regen_idx = None
                                st.rerun()
                    else:
                        if st.button("✏️ 変更", key=f"edit_{idx}"):
                            st.session_state.confirm_regen_idx = idx
                            st.rerun()

                    # お気に入り
                    dish = day_menu["dish"]
                    if dish not in fav_dishes:
                        if st.button("⭐ お気に入り", key=f"fav_{row_start}_{i}"):
                            favorites.append({"dish": dish, "memo": ""})
                            save_favorites(favorites)
                            st.success(f"「{dish}」をお気に入りに追加しました")
                            st.rerun()
                    else:
                        st.caption("⭐ お気に入り登録済み")

    # 買い物リスト
    st.divider()
    st.subheader("🛒 買い物リスト")
    shopping = result.get("shopping_list", {})
    category_icons = {
        "野菜・果物": "🥦",
        "肉・魚": "🥩",
        "乳製品・卵": "🥚",
        "調味料・その他": "🧂"
    }
    if shopping:
        cols = st.columns(len(shopping))
        for i, (category, items) in enumerate(shopping.items()):
            with cols[i]:
                icon = category_icons.get(category, "📦")
                st.markdown(f"**{icon} {category}**")
                for item in items:
                    st.markdown(f"- {item}")

    if not readonly:
        st.divider()

        col_save, col_copy = st.columns(2)
        with col_save:
            if st.button("💾 この週の献立を保存する", use_container_width=True):
                history = load_history()
                entry = {
                    "saved_at": datetime.now().strftime("%Y年%m月%d日"),
                    "week_label": get_week_label(),
                    "menu": result
                }
                history.insert(0, entry)
                save_history(history)
                st.success("✅ 献立を保存しました！")
        with col_copy:
            if st.button("📋 Notionにコピーする", use_container_width=True):
                st.session_state.show_notion_copy = not st.session_state.get("show_notion_copy", False)

        if st.session_state.get("show_notion_copy", False):
            notion_md = build_notion_md(result)
            st.divider()
            st.subheader("📋 Notionにコピー")
            st.caption("下のテキストをコピーして、Notionのページに貼り付けてください。")

            st.markdown("**📅 献立テーブル**")
            st.code(notion_md["table"], language=None)

            st.markdown("**🛒 買い物リスト（チェックリスト）**")
            st.code(notion_md["checklist"], language=None)

def build_notion_md(result: dict) -> dict:
    """
    献立データからNotion貼り付け用のmarkdownを生成する。
    献立: 土曜始まりのテーブル形式
    買い物: チェックリスト形式
    """
    menu = result.get("menu", [])

    # 曜日を土曜始まりに並び替え
    DAY_ORDER = ["土曜日", "日曜日", "月曜日", "火曜日", "水曜日", "木曜日", "金曜日"]
    day_map = {m["day"]: m["dish"] for m in menu}
    ordered = [(d, day_map.get(d, "")) for d in DAY_ORDER]

    # 行に曜日、列に献立のテーブル（曜日 | 献立）
    SHORT = {"土曜日": "土", "日曜日": "日", "月曜日": "月",
             "火曜日": "火", "水曜日": "水", "木曜日": "木", "金曜日": "金"}
    table_lines = ["| 曜日 | 献立 |", "| --- | --- |"]
    for day, dish in ordered:
        table_lines.append(f"| {SHORT[day]} | {dish if dish else ' '} |")
    table_md = "\n".join(table_lines)

    # 買い物チェックリスト
    shopping = result.get("shopping_list", {})
    checklist_lines = []
    for category, items in shopping.items():
        checklist_lines.append(f"## {category}")
        for item in items:
            checklist_lines.append(f"- [ ]  {item}")
        checklist_lines.append("")
    checklist_md = "\n".join(checklist_lines).strip()

    return {"table": table_md, "checklist": checklist_md}

def get_week_label():
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return f"{monday.strftime('%m/%d')}〜{sunday.strftime('%m/%d')}の週"

# ===== 画面: 履歴 =====

def show_history_page():
    st.title("📚 献立履歴")
    history = load_history()
    if not history:
        st.info("まだ保存された献立がありません。")
        return

    for i, entry in enumerate(history):
        with st.expander(f"📅 {entry['week_label']}（保存日: {entry['saved_at']}）"):
            show_menu_result(entry["menu"], readonly=True)
            if st.button("🗑️ この履歴を削除", key=f"delete_{i}"):
                history.pop(i)
                save_history(history)
                st.rerun()

# ===== 画面: 設定 =====

def show_settings_page():
    st.title("⚙️ 設定")
    st.info("最初に一度だけ設定してください。後からいつでも変更できます。")

    settings = load_settings() or {}

    with st.form("settings_form"):
        st.subheader("👨‍👩‍👧‍👦 家族情報")
        family_size = st.number_input(
            "家族の人数",
            min_value=1, max_value=10,
            value=settings.get("family_size", 2)
        )
        allergies = st.text_area(
            "好き嫌い・アレルギー（自由に記入）",
            value=settings.get("allergies", ""),
            placeholder="例：子どもが魚嫌い、長女が卵アレルギーあり、辛い物は苦手"
        )

        st.subheader("⏱️ 曜日ごとの調理時間")
        days = ["月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日", "日曜日"]
        time_options = ["15分以内", "30分以内", "45分以内", "1時間以内", "1時間以上OK"]
        default_times = settings.get("cooking_times", {})
        cols = st.columns(2)
        cooking_times = {}
        for i, day in enumerate(days):
            with cols[i % 2]:
                default = default_times.get(day, "30分以内")
                cooking_times[day] = st.selectbox(
                    day, time_options,
                    index=time_options.index(default) if default in time_options else 1
                )

        st.subheader("📓 Notion連携（任意）")
        st.markdown("過去の献立データをNotionで管理している場合、連携することで重複を避けた提案が可能になります。")
        notion_token = st.text_input(
            "Notion Integration Token",
            value=settings.get("notion_token", ""),
            type="password",
            placeholder="secret_xxxxxxxxxx"
        )
        notion_database_id = st.text_input(
            "Notion Database ID",
            value=settings.get("notion_database_id", ""),
            placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        )

        st.subheader("🔑 Claude API キー")
        api_key = st.text_input(
            "APIキー",
            value=st.session_state.get("api_key", ""),
            type="password",
            help="https://console.anthropic.com でAPIキーを取得してください"
        )

        submitted = st.form_submit_button("💾 設定を保存", use_container_width=True)
        if submitted:
            if not api_key:
                st.error("APIキーを入力してください")
            else:
                new_settings = {
                    "family_size": family_size,
                    "allergies": allergies,
                    "cooking_times": cooking_times,
                    "notion_token": notion_token,
                    "notion_database_id": notion_database_id
                }
                save_settings(new_settings)
                st.session_state.api_key = api_key
                st.success("✅ 設定を保存しました！")
                st.balloons()


# ===== 画面: 持ち越し献立管理 =====

def show_carryover_page():
    st.title("⏭️ 持ち越し献立")
    st.markdown("今週作りきれなかった献立や、来週に回したい料理を登録しておきます。献立生成時にチェックして組み込めます。")

    carryover = load_carryover()

    # --- 登録済み一覧 ---
    if carryover:
        st.subheader(f"登録中: {len(carryover)}件")
        for i, item in enumerate(carryover):
            col1, col2 = st.columns([5, 1])
            with col1:
                st.markdown(f"**{item['dish']}**")
                if item.get("memo"):
                    st.caption(item["memo"])
            with col2:
                if st.button("削除", key=f"del_carry_{i}"):
                    carryover.pop(i)
                    save_carryover(carryover)
                    st.rerun()
        st.divider()
        if st.button("🗑️ すべて削除", type="secondary"):
            save_carryover([])
            st.rerun()
    else:
        st.info("持ち越し献立はありません。")
        st.divider()

    # --- 新規追加 ---
    st.subheader("➕ 献立を追加")

    # 自由入力
    st.markdown("**手入力で追加**")
    col1, col2 = st.columns([3, 2])
    with col1:
        new_dish = st.text_input("料理名", placeholder="例: 麻婆豆腐、鍋、ハンバーグ", key="carry_new_dish")
    with col2:
        new_memo = st.text_input("メモ（任意）", placeholder="食材が余ってるから など", key="carry_new_memo")
    if st.button("➕ 追加する", key="carry_add_manual"):
        if new_dish.strip():
            # カンマ・読点で複数入力に対応
            dishes = [d.strip() for d in new_dish.replace("、", ",").split(",") if d.strip()]
            existing = {c["dish"] for c in carryover}
            added = 0
            for d in dishes:
                if d not in existing:
                    carryover.append({"dish": d, "memo": new_memo if len(dishes) == 1 else ""})
                    added += 1
            save_carryover(carryover)
            st.success(f"✅ {added}件追加しました")
            st.rerun()

    # 履歴から選んで追加
    history = load_history()
    if history:
        st.divider()
        st.markdown("**前週の履歴から選んで追加**")
        last_week = history[0]
        last_menu = last_week.get("menu", {}).get("menu", [])
        if last_menu:
            st.caption(f"📅 {last_week.get('week_label', '')}の献立")
            existing_dishes = {c["dish"] for c in carryover}
            cols = st.columns(min(4, len(last_menu)))
            for i, day_menu in enumerate(last_menu):
                dish = day_menu["dish"]
                with cols[i % 4]:
                    already = dish in existing_dishes
                    if already:
                        st.caption(f"✅ {dish}（登録済み）")
                    else:
                        if st.button(f"＋ {dish}", key=f"carry_hist_{i}"):
                            carryover.append({"dish": dish, "memo": ""})
                            save_carryover(carryover)
                            st.rerun()
                    st.caption(day_menu["day"])

# ===== 画面: Notion履歴インポート =====

def show_notion_import_page():
    st.title("📥 Notion献立のインポート")
    st.markdown("Notionからエクスポートした `.md` ファイルをアップロードすると、週ごとの献立を自動で読み取ります。内容を確認・修正してからレシピを追加し、取り込めます。")

    # --- ファイルアップロード ---
    st.subheader("📤 mdファイルをアップロード")
    st.markdown("複数ファイルを一度にアップロードできます。")
    uploaded_files = st.file_uploader(
        "mdファイルを選択（複数可）",
        type=["md"],
        accept_multiple_files=True,
        key="notion_uploader"
    )

    if uploaded_files:
        # セッションに読み取り結果を保持（編集のため）
        current_filenames = [f.name for f in uploaded_files]
        if "notion_import_rows" not in st.session_state or st.session_state.get("notion_import_files") != current_filenames:
            all_rows = []
            file_results = {}  # ファイル名 -> 件数（0なら読み取り失敗）
            for f in uploaded_files:
                raw = f.read().decode("utf-8")
                rows = parse_notion_md(raw)
                # ファイル名からタイトル（日付など）を取得
                title = raw.splitlines()[0].lstrip("#").strip() if raw.splitlines() else f.name
                file_results[f.name] = {"title": title, "count": len(rows)}
                for r in rows:
                    r["file"] = f.name
                    r["file_title"] = title
                all_rows.extend(rows)
            st.session_state.notion_import_rows = all_rows
            st.session_state.notion_import_files = current_filenames
            st.session_state.notion_file_results = file_results

        rows = st.session_state.notion_import_rows
        file_results = st.session_state.get("notion_file_results", {})

        # ファイルごとの読み取り結果サマリー
        st.subheader("📁 ファイル別 読み取り結果")
        for fname, info in file_results.items():
            if info["count"] > 0:
                st.success(f"✅ **{info['title']}**（{fname}）→ {info['count']}件の献立を読み取りました")
            else:
                st.error(f"❌ **{info['title']}**（{fname}）→ 献立を読み取れませんでした。形式を確認してください。")
        st.divider()

        if not rows:
            st.warning("すべてのファイルで料理名を読み取れませんでした。")
        else:
            st.subheader(f"📋 献立一覧（合計 {len(rows)}件）")
            st.caption("料理名・レシピを自由に編集できます。不要な行は「除外」にチェックしてください。")
            st.divider()

            # ファイルタイトルごとにグループ表示
            current_file = None
            for i, row in enumerate(rows):
                # ファイルが変わったらヘッダーを表示
                if row.get("file") != current_file:
                    current_file = row.get("file")
                    title = row.get("file_title", current_file)
                    st.markdown(f"#### 📅 {title}")

                col1, col2, col3, col4 = st.columns([1, 2, 3, 1])
                with col1:
                    st.markdown(f"**{row['day']}曜日**")
                    if row.get("lunch"):
                        st.caption(f"昼: {row['lunch']}")
                with col2:
                    rows[i]["dinner"] = st.text_input(
                        "料理名",
                        value=row["dinner"],
                        key=f"dish_{i}",
                        label_visibility="collapsed"
                    )
                with col3:
                    rows[i]["recipe"] = st.text_area(
                        "レシピ・メモ（任意）",
                        value=row.get("recipe", ""),
                        placeholder="作り方のメモや食材など自由に記入",
                        height=68,
                        key=f"recipe_{i}",
                        label_visibility="collapsed"
                    )
                with col4:
                    rows[i]["exclude"] = st.checkbox("除外", key=f"excl_{i}", value=row.get("exclude", False))
                st.divider()

            # 取り込みボタン
            to_import = [r for r in rows if not r.get("exclude") and r.get("dinner")]
            st.info(f"取り込み対象: **{len(to_import)}件**（除外: {len(rows) - len(to_import)}件）")

            if st.button("✅ この内容を取り込む", use_container_width=True, type="primary"):
                existing = load_notion_history()
                # 取り込むレコードを構築（ファイルタイトルを日付として使う）
                new_records = [
                    {
                        "dish": r["dinner"],
                        "count": 1,
                        "date": r.get("file_title", ""),
                        "recipe": r.get("recipe", ""),
                    }
                    for r in to_import
                ]
                merged, added, updated = merge_into_notion_history(existing, new_records)
                save_notion_history(merged)
                st.success(f"✅ {added}件を新規追加、{updated}件を更新しました！")
                del st.session_state["notion_import_rows"]
                st.rerun()

    # --- 取り込み済み一覧 ---
    st.divider()
    st.subheader("📚 取り込み済みの献立一覧")
    st.caption("登場回数が多いほど二人に人気の献立です。人気順に表示しています。")
    history = load_notion_history()
    if history:
        st.caption(f"合計 {len(history)} 件")
        for i, record in enumerate(history):
            col1, col2, col3, col4 = st.columns([2, 1, 3, 1])
            with col1:
                st.markdown(f"**{record['dish']}**")
            with col2:
                count = record.get("count", 1)
                stars = "🌟" * min(count, 5)
                st.caption(f"{stars} {count}回")
            with col3:
                dates = record.get("dates", [])
                if dates:
                    st.caption("📅 " + "、".join(dates[-3:]))  # 直近3件を表示
                elif record.get("recipe"):
                    st.caption(record["recipe"][:40] + ("..." if len(record.get("recipe","")) > 40 else ""))
            with col4:
                if st.button("削除", key=f"del_notion_{i}"):
                    history.pop(i)
                    save_notion_history(history)
                    st.rerun()

        st.divider()
        if st.button("🗑️ すべて削除してリセット", type="secondary"):
            save_notion_history([])
            st.success("削除しました")
            st.rerun()
    else:
        st.info("まだ取り込まれた献立はありません。")

# ===== メインナビゲーション =====

def main():
    if "api_key" not in st.session_state:
        st.session_state.api_key = ""

    with st.sidebar:
        st.title("🍽️ 献立アプリ")
        st.divider()
        page = st.radio(
            "メニュー",
            ["📋 今週の献立を作る", "🧊 食材ストック", "⭐ お気に入り", "⏭️ 持ち越し献立", "📚 履歴を見る", "📥 Notion履歴", "⚙️ 設定"],
            label_visibility="collapsed"
        )
        settings = load_settings()
        if settings:
            st.divider()
            st.markdown("**現在の設定**")
            st.caption(f"👨‍👩‍👧‍👦 {settings['family_size']}人家族")
            if settings.get("allergies"):
                st.caption(f"⚠️ {settings['allergies'][:30]}...")
            stock = load_stock()
            total = len(stock["ingredients"]) + len(stock["retort"])
            st.caption(f"🧊 食材ストック: {total}件")
            favorites = load_favorites()
            st.caption(f"⭐ お気に入り: {len(favorites)}件")
            carryover_items = load_carryover()
            if carryover_items:
                st.caption(f"⏭️ 持ち越し: {len(carryover_items)}件")
            notion_hist = load_notion_history()
            st.caption(f"📥 Notion履歴: {len(notion_hist)}件")

    if page == "⚙️ 設定":
        show_settings_page()
    elif page == "📋 今週の献立を作る":
        if not load_settings():
            st.warning("⚠️ まず設定を完了してください。")
            show_settings_page()
        else:
            show_generate_page()
    elif page == "🧊 食材ストック":
        show_stock_page()
    elif page == "⭐ お気に入り":
        show_favorites_page()
    elif page == "📚 履歴を見る":
        show_history_page()
    elif page == "⏭️ 持ち越し献立":
        show_carryover_page()
    elif page == "📥 Notion履歴":
        show_notion_import_page()

if __name__ == "__main__":
    main()
