"""売上集計アプリのメイン画面。
顧客コード検索、上乗せ活動（セリング／増加／理由あり今スト）の入力フォーム、
管理職チェック（承認・差戻し）、過去データの修正・破棄、CSV一括インポートを実装している。"""
import streamlit as st
import pandas as pd
from datetime import date, datetime, timedelta

from views.common import (
    lookup_customer, post_to_gas, read_csv_cached, get_current_role,
    BRANCH_CUSTOMER_CSV, SALES_SHEET_URL, SALES_SHEET_CSV,
    SALES_IMPORT_SHEET_URL, SALES_IMPORT_SHEET_CSV, JST,
)

CATEGORIES = ["セリング", "増加", "理由あり今スト"]

SELLING_ROWS = 5    # セリングの入力行数
INCREASE_ROWS = 5   # 増加の入力行数
STOP_ROWS = 5        # 理由あり今ストの入力行数

# 💡 登録成功後にキャンペーン実績入力欄をクリアするために消すキー一覧
#    （顧客コード検索欄・検索結果は残す＝同じ顧客に続けて別カテゴリを登録しやすくするため）。
#    sl_category・ic_* は消さない＝選んでいたカテゴリはそのまま維持する。
#    消してしまうと登録のたびに一覧の先頭に戻ってしまい、同じカテゴリで続けて
#    入力している最中に画面が切り替わって入力欄が見えなくなる
#    （campaign-tallyで実際に報告された連続入力時の不具合と同じ原因のため、
#    最初からこの形にしている）。
_ENTRY_FORM_KEYS = (
    [f"sl_{field}_{i}" for i in range(SELLING_ROWS) for field in ("code", "sales", "price")]
    + [f"ic_{field}_{i}" for i in range(INCREASE_ROWS) for field in ("code", "cycle", "price", "qty")]
    + [f"st_{field}_{i}" for i in range(STOP_ROWS) for field in ("code", "qty", "price")]
    + ["st_reason", "st_fax"]
)

# 💡 保存先シートの列（0始まり）。カテゴリごとに使うフィールドが異なるため、
#    全カテゴリ分の列を持つ1枚のシートに、該当しない列は空欄のまま書き込む
#    （kensaku・campaign-tallyの各モードと同じ考え方）。
# 0 タイムスタンプ, 1 申請者, 2 拠点, 3 エリア, 4 顧客コード, 5 顧客名, 6 加盟店名, 7 加盟店コード, 8 カテゴリ,
# 9〜23 セリング 商品①〜⑤（1商品あたり3列＝商品記号/販売数/単価）,
# 24〜43 増加 商品①〜⑤（1商品あたり4列＝商品記号/サイクル/単価/数量）,
# 44〜58 今スト 商品①〜⑤（1商品あたり3列＝商品記号/数量/単価）,
# 59 理由, 60 FAX有無,
# 61 ステータス（申請中／承認済み／破棄）, 62 処理者, 63 処理日時, 64 備考
SELLING_START = 9
INCREASE_START = 24
STOP_START = 44
REASON_COL = 59
FAX_COL = 60
STATUS_COL = 61
APPROVER_COL = 62
APPROVE_TIME_COL = 63
COMMENT_COL = 64

# 💡 管理職チェックタブを表示できる権限（ユーザーマスターF列、kensaku・campaign-tallyと同じ値を流用）。
#    権限0＝全権限（全拠点の申請を確認可）、権限1＝拠点の管理職（自分の拠点の申請のみ確認可）。
MANAGER_ROLES = {"0", "1"}
ALL_BRANCH_ROLES = {"0"}
# CSV一括インポートは権限0（全権限）のみ操作できる。
CSV_IMPORT_ROLES = {"0"}


def _to_float(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return 0.0


def _fmt_amount(v):
    """金額表示用（整数円に丸めてカンマ区切り）。0円は空欄にする（未入力行を目立たせないため）。"""
    if not v:
        return ""
    return f"{round(v):,}"


def _customer_search_section():
    st.write("**🔍 顧客コード検索**")
    col_input, col_btn = st.columns([4, 1])
    cust_code = col_input.text_input("顧客コード", key="sl_cust_code")
    btn_search = col_btn.button("🔍 検索", use_container_width=True)

    if btn_search:
        if not cust_code.strip():
            st.warning("顧客コードを入力してください。")
        elif str(st.session_state.get("user_branch", "")).strip() not in BRANCH_CUSTOMER_CSV:
            st.error(
                f"⚠️ 拠点「{st.session_state.get('user_branch', '(未設定)')}」に対応する顧客データが"
                "設定されていません。管理者にお問い合わせください。"
            )
        else:
            result = lookup_customer(cust_code)
            if result is None:
                st.warning("該当する顧客データが見つかりませんでした。")
                st.session_state.pop("sl_customer", None)
            else:
                st.session_state["sl_customer"] = {"cust_code": cust_code.strip(), **result}
                st.toast("顧客情報を取得しました！", icon="✅")

    customer = st.session_state.get("sl_customer")
    if customer:
        st.write("---")
        c1, c2, c3, c4 = st.columns(4)
        c1.text_input("顧客コード", value=customer["cust_code"], disabled=True, key="sl_v_ccode")
        c2.text_input("顧客名", value=customer["cust_name"], disabled=True, key="sl_v_cname")
        c3.text_input("加盟店名", value=customer["store_name"], disabled=True, key="sl_v_sname")
        c4.text_input("加盟店コード", value=customer["store_code"], disabled=True, key="sl_v_scode")
    return customer


def _entry_form_section(customer):
    st.write("---")
    st.write("**📝 上乗せ活動入力**")

    category = st.radio("カテゴリ", CATEGORIES, horizontal=True, key="sl_category")

    selling_items = [("", "", "")] * SELLING_ROWS
    increase_items = [("", "", "", "")] * INCREASE_ROWS
    stop_items = [("", "", "")] * STOP_ROWS
    reason = fax_flag = ""

    if category == "セリング":
        rows = []
        sl_total = 0.0
        for i in range(SELLING_ROWS):
            st.caption(f"商品 {i + 1}")
            c1, c2, c3, c4 = st.columns(4)
            code = c1.text_input("商品記号", key=f"sl_code_{i}")
            sales = c2.text_input("販売数", key=f"sl_sales_{i}")
            price = c3.text_input("単価", key=f"sl_price_{i}")
            amount = _to_float(sales) * _to_float(price)
            sl_total += amount
            c4.metric("金額", f"{_fmt_amount(amount) or 0} 円")
            rows.append((code, sales, price))
        selling_items = rows

        st.write(f"**総合計：{_fmt_amount(sl_total) or 0} 円**")

    elif category == "増加":
        rows = []
        ic_total = 0.0
        for i in range(INCREASE_ROWS):
            st.caption(f"商品 {i + 1}")
            c1, c2, c3, c4, c5 = st.columns(5)
            code = c1.text_input("商品記号", key=f"ic_code_{i}")
            cycle = c2.text_input("サイクル", key=f"ic_cycle_{i}")
            price = c3.text_input("単価", key=f"ic_price_{i}")
            qty = c4.text_input("数量", key=f"ic_qty_{i}")
            # 💡 増加の金額＝商品単価×(4÷サイクル)×数量（サイクルが未入力・0の場合は計算しない）
            cycle_f = _to_float(cycle)
            amount = _to_float(price) * (4 / cycle_f) * _to_float(qty) if cycle_f else 0.0
            ic_total += amount
            c5.metric("金額", f"{_fmt_amount(amount) or 0} 円")
            rows.append((code, cycle, price, qty))
        increase_items = rows

        st.write(f"**総合計：{_fmt_amount(ic_total) or 0} 円**")

    elif category == "理由あり今スト":
        st.caption("予定に上がっていた商品がストップになった場合に登録してください。")
        rows = []
        st_total = 0.0
        for i in range(STOP_ROWS):
            st.caption(f"商品 {i + 1}")
            c1, c2, c3, c4 = st.columns(4)
            code = c1.text_input("商品記号", key=f"st_code_{i}")
            qty = c2.text_input("数量", key=f"st_qty_{i}")
            price = c3.text_input("単価", key=f"st_price_{i}")
            amount = _to_float(qty) * _to_float(price)
            st_total += amount
            c4.metric("金額", f"{_fmt_amount(amount) or 0} 円")
            rows.append((code, qty, price))
        stop_items = rows

        st.write(f"**総合計：{_fmt_amount(st_total) or 0} 円**")

        st.write("---")
        c1, c2 = st.columns(2)
        reason = c1.text_input("理由（ストップになった理由）", key="st_reason")
        fax_flag = c2.selectbox("FAX有無", ["", "有", "無"], key="st_fax")

    if st.button("✅ 登録する", type="primary", use_container_width=True):
        if not customer:
            st.error("⚠️ 先に顧客コードを検索してください。")
            return
        if category == "理由あり今スト" and not reason.strip():
            st.error("⚠️ 「理由」は必須項目です。入力してください。")
            return

        selling_flat = [v for item in selling_items for v in item]
        increase_flat = [v for item in increase_items for v in item]
        stop_flat = [v for item in stop_items for v in item]

        full_row = [
            datetime.now(JST).strftime("%Y/%m/%d %H:%M:%S"),
            st.session_state.get("user_name", ""),
            st.session_state.get("user_branch", ""),
            st.session_state.get("user_area", ""),
            customer["cust_code"], customer["cust_name"],
            customer["store_name"], customer["store_code"],
            category,
        ] + selling_flat + increase_flat + stop_flat + [
            reason, fax_flag,
            "申請中", "", "", "",
        ]
        with st.spinner("処理中..."):
            res = post_to_gas({
                "action": "SUBMIT_SALES_ENTRY",
                "target_sheet_url": SALES_SHEET_URL,
                "full_row": full_row,
            })
        if res.get("status") == "success":
            for k in _ENTRY_FORM_KEYS:
                st.session_state.pop(k, None)
            st.toast("✅ 登録しました。管理職チェック待ちです。", icon="✅")
            st.rerun()
        else:
            st.error(f"登録に失敗しました: {res.get('message')}")


def _val(row, idx):
    return str(row.iloc[idx]) if len(row) > idx and pd.notna(row.iloc[idx]) else ""


def _render_entry_readonly(row, key_prefix):
    """1件分の上乗せ活動実績を、カテゴリに応じた項目だけ読み取り専用で表示する。"""
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.text_input("拠点", value=_val(row, 2), disabled=True, key=f"{key_prefix}_branch")
    c2.text_input("エリア", value=_val(row, 3), disabled=True, key=f"{key_prefix}_area")
    c3.text_input("顧客コード", value=_val(row, 4), disabled=True, key=f"{key_prefix}_ccode")
    c4.text_input("顧客名", value=_val(row, 5), disabled=True, key=f"{key_prefix}_cname")
    c5.text_input("加盟店名", value=_val(row, 6), disabled=True, key=f"{key_prefix}_sname")
    c6.text_input("加盟店コード", value=_val(row, 7), disabled=True, key=f"{key_prefix}_scode")

    category = _val(row, 8)
    st.text_input("カテゴリ", value=category, disabled=True, key=f"{key_prefix}_category")

    if category == "セリング":
        for i in range(SELLING_ROWS):
            base = SELLING_START + i * 3
            code = _val(row, base)
            if not code.strip():
                continue
            c1, c2, c3 = st.columns(3)
            c1.text_input(f"商品記号 {i+1}", value=code, disabled=True, key=f"{key_prefix}_sl_code_{i}")
            c2.text_input(f"販売数 {i+1}", value=_val(row, base + 1), disabled=True, key=f"{key_prefix}_sl_sales_{i}")
            c3.text_input(f"単価 {i+1}", value=_val(row, base + 2), disabled=True, key=f"{key_prefix}_sl_price_{i}")

    elif category == "増加":
        for i in range(INCREASE_ROWS):
            base = INCREASE_START + i * 4
            code = _val(row, base)
            if not code.strip():
                continue
            c1, c2, c3, c4 = st.columns(4)
            c1.text_input(f"商品記号 {i+1}", value=code, disabled=True, key=f"{key_prefix}_ic_code_{i}")
            c2.text_input(f"サイクル {i+1}", value=_val(row, base + 1), disabled=True, key=f"{key_prefix}_ic_cycle_{i}")
            c3.text_input(f"単価 {i+1}", value=_val(row, base + 2), disabled=True, key=f"{key_prefix}_ic_price_{i}")
            c4.text_input(f"数量 {i+1}", value=_val(row, base + 3), disabled=True, key=f"{key_prefix}_ic_qty_{i}")

    elif category == "理由あり今スト":
        for i in range(STOP_ROWS):
            base = STOP_START + i * 3
            code = _val(row, base)
            if not code.strip():
                continue
            c1, c2, c3 = st.columns(3)
            c1.text_input(f"商品記号 {i+1}", value=code, disabled=True, key=f"{key_prefix}_st_code_{i}")
            c2.text_input(f"数量 {i+1}", value=_val(row, base + 1), disabled=True, key=f"{key_prefix}_st_qty_{i}")
            c3.text_input(f"単価 {i+1}", value=_val(row, base + 2), disabled=True, key=f"{key_prefix}_st_price_{i}")
        st.text_input("理由", value=_val(row, REASON_COL), disabled=True, key=f"{key_prefix}_st_reason")
        st.text_input("FAX有無", value=_val(row, FAX_COL), disabled=True, key=f"{key_prefix}_st_fax")


def _load_csv_match_keys():
    """売上データ（CSV取込）シートから、(顧客コード, 商品記号, 実績金額) の組み合わせを
    集合として読み込む。セリング・理由あり今ストの申請内容がCSV実績データに実在するか
    どうかの突き合わせチェックに使う。実績金額は数量×単価で四捨五入して比較する
    （端数・浮動小数点誤差を吸収するため）。"""
    try:
        df = read_csv_cached(SALES_IMPORT_SHEET_CSV, header=0)
    except Exception:
        return None
    if df.empty or len(df.columns) < 11:
        return set()

    cust_code = df.iloc[:, 3].astype(str).str.strip()
    product_code = df.iloc[:, 5].astype(str).str.strip()
    qty = pd.to_numeric(df.iloc[:, 9], errors="coerce").fillna(0)
    price = pd.to_numeric(df.iloc[:, 10], errors="coerce").fillna(0)
    amount = (qty * price).round().astype(int)

    return set(zip(cust_code, product_code, amount))


def _check_csv_mismatch(row, category, match_keys):
    """この申請（セリング／理由あり今スト）の商品欄それぞれについて、同じ
    （顧客コード・商品記号・金額）の組み合わせがCSV実績データに存在するかを確認し、
    見つからなかった商品の一覧を返す（空リスト＝すべて一致）。"""
    if match_keys is None or category not in ("セリング", "理由あり今スト"):
        return None

    cust_code = _val(row, 4).strip()
    start_col = SELLING_START if category == "セリング" else STOP_START
    mismatches = []
    for i in range(5):
        base = start_col + i * 3
        code = _val(row, base).strip()
        if not code:
            continue
        qty = _to_float(_val(row, base + 1))
        price = _to_float(_val(row, base + 2))
        amount = round(qty * price)
        if (cust_code, code, amount) not in match_keys:
            mismatches.append(f"{code}（{amount:,}円）")
    return mismatches


def _manager_check_section():
    st.write("**🔍 管理職チェック**")
    st.caption("申請中の上乗せ活動実績を確認し、承認または破棄できます。")
    st.caption("セリング・理由あり今ストは、売上データ（CSV取込）に同じ顧客コード・商品記号・金額の実績が無い場合、⚠️アラートを表示します。")

    if st.button("🔄 最新の申請を読み込む", key="sl_check_reload"):
        read_csv_cached.clear()

    try:
        df = read_csv_cached(SALES_SHEET_CSV, header=0)
    except Exception as e:
        st.error(f"データ取得エラー: {e}")
        return

    if df.empty or len(df.columns) <= STATUS_COL:
        st.info("現在、申請データはありません。")
        return

    pending_df = df[df.iloc[:, STATUS_COL].astype(str).str.strip() == "申請中"]

    # 💡 権限0（全権限）以外は、自分の拠点（C列）の申請だけを確認・承認できる。
    if get_current_role() not in ALL_BRANCH_ROLES:
        my_branch = str(st.session_state.get("user_branch", "")).strip()
        pending_df = pending_df[pending_df.iloc[:, 2].astype(str).str.strip() == my_branch]

    if pending_df.empty:
        st.info("現在、承認待ちの申請はありません。")
        return

    st.warning(f"承認待ち: **{len(pending_df)} 件**")

    match_keys = _load_csv_match_keys()

    for idx, row in pending_df.iloc[::-1].iterrows():
        row_id = idx + 2
        applicant = _val(row, 1)
        cust_name = _val(row, 5)
        category = _val(row, 8)
        timestamp = _val(row, 0)

        mismatches = _check_csv_mismatch(row, category, match_keys)
        alert_mark = "⚠️ " if mismatches else ""

        with st.expander(f"{alert_mark}⏳ 【{category}】{cust_name} | 申請者: {applicant} | {timestamp}"):
            if mismatches is None and category in ("セリング", "理由あり今スト"):
                st.caption("（売上データの取得に失敗したため、突き合わせチェックは実施できませんでした）")
            elif mismatches:
                st.warning(
                    "⚠️ 売上データ（CSV）に同じ顧客コード・商品記号・金額の実績が見つかりませんでした: "
                    + "、".join(mismatches)
                )

            _render_entry_readonly(row, key_prefix=f"chk_{row_id}")

            comment = st.text_input("備考（破棄する場合は任意で理由を記入できます）", key=f"chk_reason_{row_id}")
            col_approve, col_discard = st.columns(2)

            if col_approve.button("✅ 承認", key=f"chk_approve_{row_id}", type="primary", use_container_width=True):
                _update_status(row, row_id, "承認済み", comment)

            if col_discard.button("🗑 破棄", key=f"chk_discard_{row_id}", use_container_width=True):
                _update_status(row, row_id, "破棄", comment)


def _update_status(row, row_id, status, comment):
    updated_row = [
        ("" if pd.isna(row.iloc[i]) else str(row.iloc[i])) if i < len(row) else ""
        for i in range(max(len(row), COMMENT_COL + 1))
    ]
    updated_row[STATUS_COL] = status
    updated_row[APPROVER_COL] = st.session_state.get("user_name", "")
    updated_row[APPROVE_TIME_COL] = datetime.now(JST).strftime("%Y/%m/%d %H:%M:%S")
    updated_row[COMMENT_COL] = comment

    with st.spinner("処理中..."):
        res = post_to_gas({
            "action": "UPDATE_SALES_STATUS",
            "target_sheet_url": SALES_SHEET_URL,
            "row_index": row_id,
            "updated_row": updated_row,
        })
    if res.get("status") == "success":
        st.toast(f"{status}にしました。", icon="✅")
        read_csv_cached.clear()
        st.rerun()
    else:
        st.error(f"更新に失敗しました: {res.get('message')}")


def _save_entry_edit(row_id, updated_row):
    with st.spinner("処理中..."):
        res = post_to_gas({
            "action": "UPDATE_SALES_STATUS",
            "target_sheet_url": SALES_SHEET_URL,
            "row_index": row_id,
            "updated_row": updated_row,
        })
    if res.get("status") == "success":
        st.toast("修正を保存しました。", icon="✅")
        read_csv_cached.clear()
        st.rerun()
    else:
        st.error(f"保存に失敗しました: {res.get('message')}")


def _past_data_section():
    st.write("**📋 過去データの修正・破棄**")
    st.caption("承認済み・破棄済みも含めた過去の申請を検索し、内容の修正や破棄ができます（申請中のものは「管理職チェック」タブで対応してください）。")

    if st.button("🔄 最新のデータを読み込む", key="sl_past_reload"):
        read_csv_cached.clear()

    try:
        df = read_csv_cached(SALES_SHEET_CSV, header=0)
    except Exception as e:
        st.error(f"データ取得エラー: {e}")
        return

    if df.empty or len(df.columns) <= STATUS_COL:
        st.info("データがありません。")
        return

    past_df = df[df.iloc[:, STATUS_COL].astype(str).str.strip() != "申請中"]

    if get_current_role() not in ALL_BRANCH_ROLES:
        my_branch = str(st.session_state.get("user_branch", "")).strip()
        past_df = past_df[past_df.iloc[:, 2].astype(str).str.strip() == my_branch]

    if past_df.empty:
        st.info("該当するデータがありません。")
        return

    search = st.text_input("顧客名・申請者名で絞り込み（任意）", key="sl_past_search")
    if search.strip():
        mask = (
            past_df.iloc[:, 5].astype(str).str.contains(search.strip(), na=False)
            | past_df.iloc[:, 1].astype(str).str.contains(search.strip(), na=False)
        )
        past_df = past_df[mask]

    if past_df.empty:
        st.info("該当するデータが見つかりませんでした。")
        return

    shown_df = past_df.iloc[::-1].head(50)
    st.caption(f"該当件数: {len(past_df)} 件（新しい順に最大50件を表示）")

    for idx, row in shown_df.iterrows():
        row_id = idx + 2
        applicant = _val(row, 1)
        cust_name = _val(row, 5)
        category = _val(row, 8)
        timestamp = _val(row, 0)
        status = _val(row, STATUS_COL)
        icon = {"承認済み": "✅", "破棄": "🗑"}.get(status, "・")

        with st.expander(f"{icon} 【{status}】【{category}】{cust_name} | 申請者: {applicant} | {timestamp}"):
            _render_entry_readonly(row, key_prefix=f"past_{row_id}")
            st.write(f"**現在のステータス：** {status}　**処理者：** {_val(row, APPROVER_COL)}　**処理日時：** {_val(row, APPROVE_TIME_COL)}")
            comment = _val(row, COMMENT_COL)
            if comment.strip():
                st.caption(f"備考: {comment}")

            if st.button("🗑 破棄する", key=f"past_discard_{row_id}", use_container_width=True):
                _update_status(row, row_id, "破棄", comment)


def _my_history_section():
    """自分（ログイン中ユーザー）が過去に登録した上乗せ活動実績を、承認待ち・
    承認済み・破棄済みすべて含めて読み取り専用で確認できるようにする。
    権限に関わらず全ユーザーが使える。"""
    st.write("**📋 自分の入力履歴**")
    st.caption("自分が登録した上乗せ活動実績を、承認待ち・承認済み・破棄済みすべて確認できます（確認のみ・修正は管理職にご相談ください）。")

    if st.button("🔄 最新のデータを読み込む", key="sl_myhist_reload"):
        read_csv_cached.clear()

    try:
        df = read_csv_cached(SALES_SHEET_CSV, header=0)
    except Exception as e:
        st.error(f"データ取得エラー: {e}")
        return

    if df.empty or len(df.columns) <= STATUS_COL:
        st.info("データがありません。")
        return

    my_name = str(st.session_state.get("user_name", "")).strip()
    my_df = df[df.iloc[:, 1].astype(str).str.strip() == my_name]

    if my_df.empty:
        st.info("まだ登録履歴がありません。")
        return

    shown_df = my_df.iloc[::-1].head(50)
    st.caption(f"該当件数: {len(my_df)} 件（新しい順に最大50件を表示）")

    for idx, row in shown_df.iterrows():
        cust_name = _val(row, 5)
        category = _val(row, 8)
        timestamp = _val(row, 0)
        status = _val(row, STATUS_COL)
        icon = {"承認済み": "✅", "破棄": "🗑", "申請中": "⏳"}.get(status, "・")

        with st.expander(f"{icon} 【{status}】【{category}】{cust_name} | {timestamp}"):
            _render_entry_readonly(row, key_prefix=f"myh_{idx}")
            st.write(f"**現在のステータス：** {status}")
            if status != "申請中":
                st.write(f"**処理者：** {_val(row, APPROVER_COL)}　**処理日時：** {_val(row, APPROVE_TIME_COL)}")
                comment = _val(row, COMMENT_COL)
                if comment.strip():
                    st.caption(f"備考: {comment}")


# CSVの列（0始まり）：日報入力確定年月日,本来納品予定年月日,担当者名,顧客コード,顧客名,
#                    商品記号,伝票納品結果種別,納品予定数量,売上予定税抜単価,納品実績数量,売上実績税抜単価
_CSV_EXPECTED_COLS = [
    "日報入力確定年月日", "本来納品予定年月日", "担当者名（レンタルサービス担当者）", "顧客コード", "顧客名",
    "商品記号", "伝票納品結果種別", "納品予定数量", "売上予定税抜単価", "納品実績数量", "売上実績税抜単価",
]


def _csv_import_section():
    """日報CSVを一括アップロードし、売上データシートに取り込む（権限0のみ）。
    承認フローは無し＝システムが出力する正式な日報データのため、
    アップロードした時点でそのまま集計対象になる。"""
    st.write("**📥 売上実績CSV一括インポート**")
    st.caption("日報システムから出力したCSVファイルをアップロードすると、売上データシートに追加されます（承認不要・権限0専用）。")

    uploaded = st.file_uploader("CSVファイルを選択", type=["csv"], key="csv_uploader")
    if uploaded is None:
        return

    try:
        raw_bytes = uploaded.read()
        try:
            text = raw_bytes.decode("cp932")
        except UnicodeDecodeError:
            text = raw_bytes.decode("utf-8")
    except Exception as e:
        st.error(f"ファイルの読み込みに失敗しました: {e}")
        return

    import io
    try:
        df_csv = pd.read_csv(io.StringIO(text), dtype=str)
    except Exception as e:
        st.error(f"CSVの解析に失敗しました: {e}")
        return

    if list(df_csv.columns) != _CSV_EXPECTED_COLS:
        st.warning("⚠️ CSVの列構成が想定と異なります。内容を確認してからインポートしてください。")

    st.write(f"**{len(df_csv)} 件のデータを読み込みました。**")
    st.dataframe(df_csv.head(20), use_container_width=True, hide_index=True)
    if len(df_csv) > 20:
        st.caption(f"先頭20件のみ表示しています（全{len(df_csv)}件）。")

    if st.button("📥 このデータを売上データシートに追加する", type="primary"):
        rows = df_csv.fillna("").values.tolist()
        import_time = datetime.now(JST).strftime("%Y/%m/%d %H:%M:%S")
        rows_with_time = [list(r) + [import_time] for r in rows]

        with st.spinner(f"{len(rows_with_time)}件を取り込んでいます..."):
            res = post_to_gas({
                "action": "IMPORT_SALES_CSV",
                "target_sheet_url": SALES_IMPORT_SHEET_URL,
                "rows": rows_with_time,
            })
        if res.get("status") == "success":
            st.toast(f"✅ {len(rows_with_time)}件を取り込みました。", icon="✅")
            read_csv_cached.clear()
        else:
            st.error(f"取り込みに失敗しました: {res.get('message')}")


def sales_screen():
    # 💡 disabled（読み取り専用）入力欄の文字が薄くて読みにくいのを解消
    st.markdown("""
        <style>
        div[data-testid="stTextInput"] input,
        div[data-testid="stTextArea"] textarea,
        div[data-testid="stNumberInput"] input {
            -webkit-text-fill-color: #31333F !important;
            color: #31333F !important;
            opacity: 1 !important;
        }
        input:disabled, input:read-only, input[aria-disabled="true"],
        textarea:disabled, textarea:read-only, textarea[aria-disabled="true"] {
            -webkit-text-fill-color: #31333F !important;
            color: #31333F !important;
            opacity: 1 !important;
        }
        div[data-testid="stTextInput"], div[data-testid="stTextArea"], div[data-testid="stSelectbox"],
        div[data-testid="stTextInput"] label, div[data-testid="stTextArea"] label, div[data-testid="stSelectbox"] label,
        div[data-testid="stWidgetLabel"], div[data-testid="stWidgetLabel"] p, div[data-testid="stWidgetLabel"] label {
            opacity: 1 !important;
            color: #31333F !important;
            -webkit-text-fill-color: #31333F !important;
        }
        div[data-testid="stSelectbox"] div[aria-disabled="true"],
        div[data-testid="stSelectbox"] div[aria-disabled="true"] * {
            opacity: 1 !important;
            color: #31333F !important;
        }
        </style>
    """, unsafe_allow_html=True)

    st.markdown("#### 📊 売上集計")
    st.write("---")

    role = get_current_role()
    tab_labels = ["📝 入力"]
    if role in MANAGER_ROLES:
        tab_labels += ["🔍 管理職チェック", "📋 過去データ修正"]
    tab_labels += ["📋 自分の入力履歴"]
    if role in CSV_IMPORT_ROLES:
        tab_labels += ["📥 CSV一括インポート"]

    tabs = st.tabs(tab_labels)
    tab_map = dict(zip(tab_labels, tabs))

    with tab_map["📝 入力"]:
        customer = _customer_search_section()
        _entry_form_section(customer)

    if "🔍 管理職チェック" in tab_map:
        with tab_map["🔍 管理職チェック"]:
            _manager_check_section()

    if "📋 過去データ修正" in tab_map:
        with tab_map["📋 過去データ修正"]:
            _past_data_section()

    with tab_map["📋 自分の入力履歴"]:
        _my_history_section()

    if "📥 CSV一括インポート" in tab_map:
        with tab_map["📥 CSV一括インポート"]:
            _csv_import_section()
